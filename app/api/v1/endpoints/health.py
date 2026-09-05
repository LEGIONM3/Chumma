from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.deps import get_current_active_user, get_db, get_odoo_gateway
from app.core.errors import NotFoundError
from app.core.pagination import PaginationParams
from app.models.deal import Deal
from app.models.health import DealAlert, DealHealthSnapshot
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse, MessageResponse, PaginatedResponse
from app.schemas.health import (
    ControlTowerResponse,
    DealAlertActionRequest,
    DealAlertRead,
    DealHealthDetailResponse,
    NextBestActionRead,
    RecomputeAlertsResponse,
)
from app.services import deal_service, health_service

router = APIRouter()


@router.get("/dashboard/control-tower", response_model=DataResponse[ControlTowerResponse])
def get_control_tower_dashboard(
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    data = health_service.get_control_tower_data(db=db)
    return DataResponse(data=data)


@router.get("/dashboard/deal-health", response_model=DataResponse[ControlTowerResponse])
def get_deal_health_dashboard_alias(
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    data = health_service.get_control_tower_data(db=db)
    return DataResponse(data=data)


@router.get("/deals/{deal_id}/health", response_model=DataResponse[DealHealthDetailResponse])
def get_deal_health_detail(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal = deal_service.get_deal_by_id(db, deal_id)

    # Recompute live health & NBA
    calc_res, nba = health_service.recompute_deal_health(
        db=db,
        gateway=gateway,
        deal=deal,
        user_role=current_user.role,
    )
    db.commit()

    # Get deal alerts
    alerts = (
        db.query(DealAlert)
        .filter(DealAlert.deal_id == deal.id)
        .order_by(DealAlert.raised_at.desc())
        .all()
    )

    resp = DealHealthDetailResponse(
        deal_id=deal.id,
        health_status=calc_res.health_status,
        overall_score=calc_res.overall_score,
        components={
            "stalled_score": calc_res.stalled_score,
            "approval_delay_score": calc_res.approval_delay_score,
            "discount_anomaly_score": calc_res.discount_anomaly_score,
            "delivery_risk_score": calc_res.delivery_risk_score,
            "negotiation_score": calc_res.negotiation_score,
        },
        detail=calc_res.detail,
        alerts=[DealAlertRead.model_validate(a) for a in alerts],
        next_best_action=NextBestActionRead(
            type=nba.type,
            priority=nba.priority,
            title=nba.title,
            explanation=nba.explanation,
            payload=nba.payload,
            cta_endpoint=nba.cta_endpoint,
        ),
        calculated_at=calc_res.detail.get("calculated_at") or deal.last_activity_at,
    )
    return DataResponse(data=resp)


@router.get("/alerts", response_model=DataResponse[List[DealAlertRead]])
def list_alerts_endpoint(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
    sales_team_id: Optional[int] = Query(None),
    deal_id: Optional[uuid.UUID] = Query(None),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    query = db.query(DealAlert).join(Deal, DealAlert.deal_id == Deal.id)

    if type:
        query = query.filter(DealAlert.type == type)
    if status:
        query = query.filter(DealAlert.status == status)
    if severity:
        query = query.filter(DealAlert.severity == severity)
    if owner_id is not None:
        query = query.filter(Deal.owner_odoo_user_id == owner_id)
    if sales_team_id is not None:
        query = query.filter(Deal.sales_team_odoo_id == sales_team_id)
    if deal_id is not None:
        query = query.filter(DealAlert.deal_id == deal_id)

    alerts = query.order_by(DealAlert.raised_at.desc()).all()
    return DataResponse(data=[DealAlertRead.model_validate(a) for a in alerts])


@router.post("/alerts/{alert_id}/acknowledge", response_model=DataResponse[DealAlertRead])
def acknowledge_alert_endpoint(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    alert = health_service.acknowledge_alert(
        db=db,
        alert_id=alert_id,
        user_id=current_user.odoo_user_id,
    )
    return DataResponse(data=DealAlertRead.model_validate(alert))


@router.post("/alerts/{alert_id}/resolve", response_model=DataResponse[DealAlertRead])
def resolve_alert_endpoint(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    alert = health_service.resolve_alert(
        db=db,
        alert_id=alert_id,
        user_id=current_user.odoo_user_id,
    )
    return DataResponse(data=DealAlertRead.model_validate(alert))


@router.post("/alerts/{alert_id}/actions", response_model=DataResponse[DealAlertRead])
def alert_action_endpoint(
    alert_id: uuid.UUID,
    payload: DealAlertActionRequest,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    if payload.action in ("ACKNOWLEDGE", "RESOLVE"):
        if payload.action == "ACKNOWLEDGE":
            alert = health_service.acknowledge_alert(db=db, alert_id=alert_id, user_id=current_user.odoo_user_id)
        else:
            alert = health_service.resolve_alert(db=db, alert_id=alert_id, user_id=current_user.odoo_user_id)
    else:
        alert = health_service.nudge_or_escalate_alert(
            db=db,
            alert_id=alert_id,
            action=payload.action,
            message=payload.message or payload.note,
            actor_id=current_user.odoo_user_id,
            actor_role=current_user.role,
        )
    return DataResponse(data=DealAlertRead.model_validate(alert))


@router.post("/alerts/recompute", response_model=DataResponse[RecomputeAlertsResponse])
def recompute_alerts_endpoint(
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    res = health_service.recompute_all_alerts(db=db, gateway=gateway)
    return DataResponse(data=res)
