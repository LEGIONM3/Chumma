from typing import Any, Optional
import uuid
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import get_current_portal_user, get_db, get_odoo_gateway
from app.models.enums import Role
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse
from app.schemas.identity import MagicLinkRequest, TokenResponse
from app.schemas.negotiation import PortalCommentCreate
from app.schemas.portal import (
    PortalCommentPublicRead,
    PortalConfirmDealRequest,
    PortalDealRead,
    PortalNegotiationRead,
    PortalNegotiationSubmit,
)
from app.services import auth_service, negotiation_service, portal_service

router = APIRouter()


@router.post("/auth/magic-link", status_code=202)
def request_portal_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
):
    client_ip = request.client.host if request.client else None
    raw_token, portal_url, partner_id = auth_service.create_magic_link(
        db=db,
        gateway=gateway,
        email=payload.email,
        partner_id=payload.partner_id,
        deal_id=payload.deal_id,
        ip=client_ip,
    )
    if hasattr(gateway, "outbox"):
        gateway.outbox.append({
            "recipient": payload.email,
            "subject": "Your DealFlow360 Portal Access Link",
            "body": f"Click here to access your deal: {portal_url}?token={raw_token}",
        })
    return {"status": "accepted", "message": "Magic link dispatched."}


@router.get("/auth/verify", response_model=DataResponse[TokenResponse])
def verify_portal_magic_link(
    token: str = Query(...),
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
):
    token_resp = auth_service.verify_magic_link(
        db=db,
        gateway=gateway,
        raw_token=token,
    )
    return DataResponse(data=token_resp)


@router.get("/deals/{deal_id}", response_model=DataResponse[PortalDealRead])
def get_portal_deal_view(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    portal_user: Any = Depends(get_current_portal_user),
):
    deal_read = portal_service.get_deal_for_portal(
        db=db,
        gateway=gateway,
        deal_id=deal_id,
        customer_partner_id=portal_user.partner_id,
    )
    return DataResponse(data=deal_read)


@router.post("/deals/{deal_id}/negotiate", response_model=DataResponse[PortalNegotiationRead])
@router.post("/deals/{deal_id}/negotiations", response_model=DataResponse[PortalNegotiationRead])
def submit_portal_negotiation(
    deal_id: uuid.UUID,
    payload: PortalNegotiationSubmit,
    db: Session = Depends(get_db),
    portal_user: Any = Depends(get_current_portal_user),
):
    req = negotiation_service.submit_negotiation_request(
        db=db,
        deal_id=deal_id,
        customer_partner_id=portal_user.partner_id,
        actor_user_id=getattr(portal_user, "odoo_user_id", 0),
        payload=payload,
    )
    return DataResponse(
        data=PortalNegotiationRead(
            id=req.id,
            type=req.type,
            status=req.status,
            message=req.message,
            response_message=req.response_message,
            counter_value=req.counter_value,
            created_at=req.created_at,
            processed_at=req.processed_at,
        )
    )


@router.post("/deals/{deal_id}/confirm", response_model=DataResponse[PortalDealRead])
def confirm_portal_deal(
    deal_id: uuid.UUID,
    payload: Optional[PortalConfirmDealRequest] = None,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    portal_user: Any = Depends(get_current_portal_user),
):
    actual_payload = payload or PortalConfirmDealRequest(accepted=True)
    deal_read = portal_service.confirm_deal_by_customer(
        db=db,
        gateway=gateway,
        deal_id=deal_id,
        customer_partner_id=portal_user.partner_id,
        payload=actual_payload,
    )
    return DataResponse(data=deal_read)


@router.post("/deals/{deal_id}/comments", response_model=DataResponse[PortalCommentPublicRead])
def add_portal_public_comment(
    deal_id: uuid.UUID,
    payload: PortalCommentCreate,
    db: Session = Depends(get_db),
    portal_user: Any = Depends(get_current_portal_user),
):
    # Customer comments are always public (is_internal=False)
    comment = negotiation_service.add_portal_comment(
        db=db,
        deal_id=deal_id,
        author_odoo_user_id=getattr(portal_user, "odoo_user_id", 0),
        author_role=Role.CUSTOMER.value,
        body=payload.body,
        is_internal=False,
        odoo_sale_order_line_id=payload.odoo_sale_order_line_id,
    )
    return DataResponse(
        data=PortalCommentPublicRead(
            id=comment.id,
            odoo_sale_order_line_id=comment.odoo_sale_order_line_id,
            author_role=comment.author_role,
            body=comment.body,
            created_at=comment.created_at,
        )
    )
