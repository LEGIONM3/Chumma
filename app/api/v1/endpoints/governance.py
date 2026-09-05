from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.deps import get_current_active_user, get_db, get_odoo_gateway
from app.core.errors import NotFoundError
from app.guardian import evaluator
from app.models.approval import ApprovalRequest
from app.models.deal import Deal
from app.models.identity import DealflowUser
from app.models.risk import RiskAssessment, RiskFactor
from app.odoo.interface import OdooGateway
from app.schemas.approval import ApprovalRequestRead
from app.schemas.common import DataResponse
from app.schemas.governance import (
    EvaluationRequest,
    EvaluationResponse,
    RiskAssessmentRead,
    RiskFactorRead,
    WhatIfSimulateRequest,
    WhatIfSimulateResponse,
)
from app.services import deal_service

router = APIRouter()


@router.post("/evaluate", response_model=DataResponse[EvaluationResponse])
def evaluate_deal_endpoint(
    payload: EvaluationRequest,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    eval_resp = evaluator.evaluate_deal(
        db=db,
        gateway=gateway,
        deal_id=payload.deal_id,
        odoo_sale_order_id=payload.odoo_sale_order_id,
        trigger_type=payload.trigger_type,
        actor_id=current_user.odoo_user_id,
        actor_role=current_user.role,
    )
    return DataResponse(data=eval_resp)


@router.post("/what-if", response_model=DataResponse[WhatIfSimulateResponse])
def simulate_what_if_endpoint(
    payload: WhatIfSimulateRequest,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    sim_resp = evaluator.simulate_deal_what_if(
        db=db,
        gateway=gateway,
        deal_id=payload.deal_id,
        order_discount_pct=payload.order_discount_pct,
        line_discounts=payload.line_discounts,
        line_quantities=payload.line_quantities,
    )
    return DataResponse(data=sim_resp)


@router.get("/deals/{deal_id}/assessment", response_model=DataResponse[RiskAssessmentRead])
def get_latest_assessment_endpoint(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal = deal_service.get_deal_by_id(db, deal_id)
    if not deal.current_assessment_id:
        raise NotFoundError("Deal has not yet been evaluated.")

    assessment = db.query(RiskAssessment).filter(RiskAssessment.id == deal.current_assessment_id).first()
    if not assessment:
        raise NotFoundError("Assessment record not found.")

    factors = db.query(RiskFactor).filter(RiskFactor.risk_assessment_id == assessment.id).all()
    factors_read = [
        RiskFactorRead(
            factor_type=f.factor_type,
            source_reference=f.source_reference,
            raw_value=f.raw_value,
            weight=f.weight,
            contribution=f.contribution,
            reason=f.reason,
            detail=f.detail,
        )
        for f in factors
    ]

    resp = RiskAssessmentRead(
        id=assessment.id,
        deal_id=deal.id,
        risk_score=assessment.risk_score,
        severity=assessment.severity,
        required_level=assessment.required_level,
        decision=assessment.decision,
        trigger_type=assessment.trigger_type,
        policy_version=assessment.policy_version,
        resolved_policy=assessment.resolved_policy,
        totals=assessment.totals,
        line_snapshot=assessment.line_snapshot,
        factors=factors_read,
        calculated_at=assessment.calculated_at,
    )
    return DataResponse(data=resp)


@router.get("/deals/{deal_id}/workspace", response_model=DataResponse[Dict[str, Any]])
def get_deal_workspace_bundle(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal, lines, nbas = deal_service.get_deal_detail(db=db, gateway=gateway, deal_id=deal_id)
    
    assessment_data = None
    if deal.current_assessment_id:
        assessment = db.query(RiskAssessment).filter(RiskAssessment.id == deal.current_assessment_id).first()
        if assessment:
            factors = db.query(RiskFactor).filter(RiskFactor.risk_assessment_id == assessment.id).all()
            assessment_data = RiskAssessmentRead(
                id=assessment.id,
                deal_id=deal.id,
                risk_score=assessment.risk_score,
                severity=assessment.severity,
                required_level=assessment.required_level,
                decision=assessment.decision,
                trigger_type=assessment.trigger_type,
                policy_version=assessment.policy_version,
                resolved_policy=assessment.resolved_policy,
                totals=assessment.totals,
                line_snapshot=assessment.line_snapshot,
                factors=[
                    RiskFactorRead(
                        factor_type=f.factor_type,
                        source_reference=f.source_reference,
                        raw_value=f.raw_value,
                        weight=f.weight,
                        contribution=f.contribution,
                        reason=f.reason,
                        detail=f.detail,
                    )
                    for f in factors
                ],
                calculated_at=assessment.calculated_at,
            ).model_dump()

    # Active approval requests
    approval_requests = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.deal_id == deal.id)
        .order_by(ApprovalRequest.sequence.asc())
        .all()
    )

    # Fulfillment
    from sqlalchemy import desc
    from app.models.fulfillment import FulfillmentPlan
    from app.models.enums import FulfillmentPlanStatus
    from app.schemas.fulfillment import FulfillmentPlanRead
    plan_obj = (
        db.query(FulfillmentPlan)
        .filter(
            FulfillmentPlan.deal_id == deal.id,
            FulfillmentPlan.status != FulfillmentPlanStatus.SUPERSEDED.value,
        )
        .order_by(desc(FulfillmentPlan.generated_at))
        .first()
    )
    fulfillment_data = FulfillmentPlanRead.model_validate(plan_obj).model_dump() if plan_obj else None

    # Recommendations
    from app.schemas.recommendation import RecommendationRead
    from app.services import recommendation_service
    recs = recommendation_service.list_deal_recommendations(db=db, deal_id=deal.id)

    # Health
    from app.models.health import DealHealthSnapshot
    latest_snapshot = (
        db.query(DealHealthSnapshot)
        .filter(DealHealthSnapshot.deal_id == deal.id)
        .order_by(desc(DealHealthSnapshot.calculated_at))
        .first()
    )
    health_data = None
    if latest_snapshot:
        health_data = {
            "health_status": latest_snapshot.health_status,
            "overall_score": float(latest_snapshot.overall_score),
            "stalled_score": float(latest_snapshot.stalled_score),
            "approval_delay_score": float(latest_snapshot.approval_delay_score),
            "discount_anomaly_score": float(latest_snapshot.discount_anomaly_score),
            "delivery_risk_score": float(latest_snapshot.delivery_risk_score),
            "negotiation_score": float(latest_snapshot.negotiation_score),
            "detail": latest_snapshot.detail,
            "calculated_at": latest_snapshot.calculated_at.isoformat() if latest_snapshot.calculated_at else None,
        }

    approval_req_list = [
        {
            "id": str(r.id),
            "sequence": r.sequence,
            "required_level": r.required_level,
            "status": r.status,
            "requested_at": r.requested_at.isoformat() if r.requested_at else None,
        }
        for r in approval_requests
    ]

    return DataResponse(
        data={
            "deal": {
                "id": str(deal.id),
                "reference": deal.reference,
                "odoo_sale_order_id": deal.odoo_sale_order_id,
                "odoo_order_name": deal.odoo_order_name,
                "partner_name": deal.partner_name_cache,
                "status": deal.status,
                "approval_state": deal.approval_state,
                "health_status": deal.health_status,
                "risk_score": float(deal.current_risk_score) if deal.current_risk_score is not None else None,
                "severity": deal.current_severity,
                "required_level": deal.required_level,
                "amount_untaxed": float(deal.amount_untaxed_cache),
                "amount_total": float(deal.amount_total_cache),
                "margin_pct": float(deal.margin_pct_cache),
                "order_discount_pct": float(deal.order_discount_pct),
                "version": deal.version,
            },
            "lines": [l.model_dump() for l in lines],
            "assessment": assessment_data,
            "risk": {
                "score": float(deal.current_risk_score) if deal.current_risk_score is not None else 0.0,
                "severity": deal.current_severity or "LOW",
                "factors": assessment_data["factors"] if assessment_data else [],
            },
            "approval": {
                "level": deal.required_level,
                "state": deal.approval_state,
                "requests": approval_req_list,
            },
            "approval_requests": approval_req_list,
            "fulfillment": fulfillment_data,
            "recommendations": [RecommendationRead.model_validate(r).model_dump() for r in recs],
            "health": health_data,
            "next_best_action": nbas[0] if nbas else None,
            "next_best_actions": nbas,
        }
    )

