from dataclasses import asdict
from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.core.deps import get_current_active_user, get_db, get_odoo_gateway
from app.core.errors import BusinessRuleError
from app.core.pagination import PaginationParams
from app.db.base import utc_now
from app.guardian import evaluator
from app.models.approval import ApprovalRequest
from app.models.enums import ApprovalActionType, ApprovalRequestStatus, AuditEventType, DealStatus
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse, PaginatedResponse
from app.schemas.deal import (
    DealCreateDirectRequest,
    DealCreateFromOdoo,
    DealDetailRead,
    DealFilterParams,
    DealPatchRequest,
    DealRead,
    DealSyncRequest,
)
from app.schemas.governance import RiskAssessmentRead
from app.schemas.negotiation import NegotiationRequestRead, NegotiationRespondRequest
from app.services import approval_service, audit_service, deal_service, negotiation_service

router = APIRouter()


class ApprovalReasonPayload(BaseModel):
    reason: Optional[str] = None


class ApprovalRejectPayload(BaseModel):
    reason: Optional[str] = None


class InvoicePaymentPayload(BaseModel):
    amount: float


@router.post("/create", response_model=DataResponse[DealRead])
@router.post("", response_model=DataResponse[DealRead])
def create_deal_endpoint(
    payload: DealCreateDirectRequest,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    order_lines = []
    if payload.lines:
        for l in payload.lines:
            order_lines.append({
                "product_id": l.product_id,
                "qty": float(l.qty),
                "discount_pct": float(l.discount_pct),
            })
    raw_order = gateway.create_order(
        partner_id=payload.partner_id,
        user_id=current_user.odoo_user_id,
        team_id=current_user.sales_team_odoo_id or 1,
        currency=payload.currency or "INR",
        lines=order_lines,
    )
    so_id = raw_order.header.id if hasattr(raw_order, "header") else raw_order.id
    deal = deal_service.sync_deal_from_odoo(
        db=db,
        gateway=gateway,
        odoo_sale_order_id=so_id,
        actor_id=current_user.odoo_user_id,
        actor_role=current_user.role,
    )
    evaluator.evaluate_deal(
        db=db,
        gateway=gateway,
        deal_id=deal.id,
        odoo_sale_order_id=deal.odoo_sale_order_id,
        trigger_type="CREATE",
        actor_id=current_user.odoo_user_id,
        actor_role=current_user.role,
    )
    db.refresh(deal)
    return DataResponse(data=DealRead.model_validate(deal))


@router.get("", response_model=PaginatedResponse[DealRead])
def list_deals_endpoint(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: Optional[str] = Query(None),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    status: Optional[str] = Query(None),
    approval_state: Optional[str] = Query(None),
    health_status: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
    sales_team_id: Optional[int] = Query(None),
    partner_id: Optional[int] = Query(None),
    min_risk_score: Optional[float] = Query(None),
    max_risk_score: Optional[float] = Query(None),
    query: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    from decimal import Decimal
    filter_params = DealFilterParams(
        owner_id=owner_id,
        sales_team_id=sales_team_id,
        partner_id=partner_id,
        min_risk_score=Decimal(str(min_risk_score)) if min_risk_score is not None else None,
        max_risk_score=Decimal(str(max_risk_score)) if max_risk_score is not None else None,
        query=query,
    )
    pagination = PaginationParams(page=page, page_size=page_size, sort=sort, order=order)
    deals, meta = deal_service.list_deals(db=db, params=filter_params, pagination=pagination)
    return PaginatedResponse(
        data=[DealRead.model_validate(d) for d in deals],
        meta=meta,
    )


@router.post("/from-odoo", response_model=DataResponse[DealRead])
def create_deal_from_odoo(
    payload: DealCreateFromOdoo,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal = deal_service.sync_deal_from_odoo(
        db=db,
        gateway=gateway,
        odoo_sale_order_id=payload.odoo_sale_order_id,
        actor_id=current_user.odoo_user_id,
        actor_role=current_user.role,
    )
    return DataResponse(data=DealRead.model_validate(deal))


@router.get("/{deal_id}", response_model=DataResponse[DealDetailRead])
def get_deal_detail_endpoint(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal, lines, nbas = deal_service.get_deal_detail(db=db, gateway=gateway, deal_id=deal_id)
    deal_read = DealRead.model_validate(deal)
    detail = DealDetailRead(
        **deal_read.model_dump(),
        lines=lines,
        next_best_actions=nbas,
    )
    return DataResponse(data=detail)


@router.post("/{deal_id}/sync", response_model=DataResponse[DealRead])
def sync_deal_endpoint(
    deal_id: uuid.UUID,
    payload: DealSyncRequest = DealSyncRequest(),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal = deal_service.get_deal_by_id(db, deal_id)
    synced = deal_service.sync_deal_from_odoo(
        db=db,
        gateway=gateway,
        odoo_sale_order_id=deal.odoo_sale_order_id,
        expected_version=None if payload.force else deal.version,
        actor_id=current_user.odoo_user_id,
        actor_role=current_user.role,
    )
    return DataResponse(data=DealRead.model_validate(synced))


@router.patch("/{deal_id}", response_model=DataResponse[DealRead])
def patch_deal_endpoint(
    deal_id: uuid.UUID,
    payload: DealPatchRequest,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    updated = deal_service.patch_deal(
        db=db,
        deal_id=deal_id,
        patch=payload,
        actor_id=current_user.odoo_user_id,
        actor_role=current_user.role,
    )
    return DataResponse(data=DealRead.model_validate(updated))


@router.get("/{deal_id}/timeline", response_model=DataResponse[List[Dict[str, Any]]])
def get_deal_timeline_endpoint(
    deal_id: uuid.UUID,
    is_activity_only: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    events = audit_service.get_deal_timeline(db=db, deal_id=deal_id, is_activity_only=is_activity_only)
    data = [
        {
            "id": str(e.id),
            "deal_id": str(e.deal_id) if e.deal_id else None,
            "event_type": e.event_type,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "actor_role": e.actor_role,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "reason": e.reason,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
    return DataResponse(data=data)


@router.get("/{deal_id}/workspace", response_model=DataResponse[Dict[str, Any]])
def get_deal_workspace_endpoint(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    from app.api.v1.endpoints.governance import get_deal_workspace_bundle
    return get_deal_workspace_bundle(deal_id=deal_id, db=db, gateway=gateway, current_user=current_user)


@router.get("/{deal_id}/assessment", response_model=DataResponse[RiskAssessmentRead])
def get_deal_assessment_endpoint(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    from app.api.v1.endpoints.governance import get_latest_assessment_endpoint
    return get_latest_assessment_endpoint(deal_id=deal_id, db=db, current_user=current_user)


@router.post("/{deal_id}/approval/approve", response_model=DataResponse[DealRead])
def approve_deal_endpoint(
    deal_id: uuid.UUID,
    payload: ApprovalReasonPayload = ApprovalReasonPayload(),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal = deal_service.get_deal_by_id(db, deal_id)
    if deal.owner_odoo_user_id == current_user.odoo_user_id:
        raise BusinessRuleError(code="OWN_DEAL", message="Sales reps cannot approve deals they own.")

    req = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.deal_id == deal.id,
            ApprovalRequest.status == ApprovalRequestStatus.PENDING.value,
        )
        .order_by(ApprovalRequest.sequence.asc())
        .first()
    )
    if not req:
        raise BusinessRuleError(code="NO_PENDING_APPROVAL", message="No pending approval request found.")

    approval_service.decide_approval_request(
        db=db,
        gateway=gateway,
        request_id=req.id,
        actor=current_user,
        action=ApprovalActionType.APPROVE,
        reason=payload.reason,
    )
    db.refresh(deal)
    return DataResponse(data=DealRead.model_validate(deal))


@router.post("/{deal_id}/approval/reject", response_model=DataResponse[DealRead])
def reject_deal_endpoint(
    deal_id: uuid.UUID,
    payload: ApprovalRejectPayload,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    if not payload or not payload.reason or not payload.reason.strip():
        raise BusinessRuleError(code="REASON_REQUIRED", message="Rejection reason is required.")

    deal = deal_service.get_deal_by_id(db, deal_id)
    req = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.deal_id == deal.id,
            ApprovalRequest.status == ApprovalRequestStatus.PENDING.value,
        )
        .order_by(ApprovalRequest.sequence.asc())
        .first()
    )
    if not req:
        raise BusinessRuleError(code="NO_PENDING_APPROVAL", message="No pending approval request found.")

    approval_service.decide_approval_request(
        db=db,
        gateway=gateway,
        request_id=req.id,
        actor=current_user,
        action=ApprovalActionType.REJECT,
        reason=payload.reason,
    )
    db.refresh(deal)
    return DataResponse(data=DealRead.model_validate(deal))


@router.post("/{deal_id}/approval/return", response_model=DataResponse[DealRead])
def return_deal_endpoint(
    deal_id: uuid.UUID,
    payload: ApprovalRejectPayload,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    if not payload or not payload.reason or not payload.reason.strip():
        raise BusinessRuleError(code="REASON_REQUIRED", message="Return reason is required.")

    deal = deal_service.get_deal_by_id(db, deal_id)
    req = (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.deal_id == deal.id,
            ApprovalRequest.status == ApprovalRequestStatus.PENDING.value,
        )
        .order_by(ApprovalRequest.sequence.asc())
        .first()
    )
    if not req:
        raise BusinessRuleError(code="NO_PENDING_APPROVAL", message="No pending approval request found.")

    approval_service.decide_approval_request(
        db=db,
        gateway=gateway,
        request_id=req.id,
        actor=current_user,
        action=ApprovalActionType.RETURN,
        reason=payload.reason,
    )
    db.refresh(deal)
    return DataResponse(data=DealRead.model_validate(deal))


@router.post("/{deal_id}/send", response_model=DataResponse[DealRead])
def send_deal_endpoint(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal = deal_service.get_deal_by_id(db, deal_id)
    deal.status = DealStatus.SENT.value
    deal.sent_at = utc_now()
    deal.last_activity_at = utc_now()
    db.commit()
    db.refresh(deal)

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.QUOTATION_SENT,
        entity_type="deal",
        entity_id=str(deal.id),
        deal_id=deal.id,
        actor_type="USER",
        actor_id=current_user.odoo_user_id,
        actor_role=current_user.role,
        reason="Quotation sent to customer",
    )
    return DataResponse(data=DealRead.model_validate(deal))


@router.post("/{deal_id}/negotiations/{request_id}/respond", response_model=DataResponse[NegotiationRequestRead])
def respond_deal_negotiation(
    deal_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: NegotiationRespondRequest,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    action = payload.get_action()
    msg = payload.get_message()
    req = negotiation_service.respond_negotiation_request(
        db=db,
        gateway=gateway,
        request_id=request_id,
        actor=current_user,
        action=action,
        response_message=msg,
        counter_value=payload.counter_value,
    )
    return DataResponse(data=NegotiationRequestRead.model_validate(req))


@router.get("/{deal_id}/billing", response_model=DataResponse[Dict[str, Any]])
def get_deal_billing(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal = deal_service.get_deal_by_id(db, deal_id)
    billing_summary = gateway.get_billing_summary(deal.odoo_sale_order_id)
    return DataResponse(
        data={
            "one_time_lines": [
                asdict(l) if hasattr(l, "__dataclass_fields__") else (l if isinstance(l, (int, str, dict)) else dict(l))
                for l in billing_summary.one_time_lines
            ],
            "recurring_lines": [
                asdict(l) if hasattr(l, "__dataclass_fields__") else (l if isinstance(l, (int, str, dict)) else dict(l))
                for l in billing_summary.recurring_lines
            ],
            "invoices": billing_summary.invoices,
            "payments": billing_summary.payments,
            "subscriptions": billing_summary.subscriptions,
            "schedule": billing_summary.schedule,
        }
    )


@router.post("/{deal_id}/billing/invoices/{inv_id}/payments", response_model=DataResponse[Dict[str, Any]])
def record_invoice_payment(
    deal_id: uuid.UUID,
    inv_id: int,
    payload: InvoicePaymentPayload,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    deal = deal_service.get_deal_by_id(db, deal_id)
    res = gateway.register_payment(invoice_id=inv_id, amount=payload.amount)

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.PAYMENT_RECEIVED,
        entity_type="invoice",
        entity_id=str(inv_id),
        deal_id=deal.id,
        actor_type="USER",
        actor_id=current_user.odoo_user_id,
        actor_role=current_user.role,
        reason=f"Payment received: {payload.amount}",
        metadata={"invoice_id": inv_id, "amount": payload.amount},
    )
    return DataResponse(data=res)
