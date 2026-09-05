from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user, get_db, get_odoo_gateway
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse
from app.schemas.negotiation import (
    NegotiationRequestRead,
    NegotiationRespondRequest,
    PortalCommentCreate,
    PortalCommentRead,
)
from app.services import negotiation_service

router = APIRouter()


@router.get("/deals/{deal_id}", response_model=DataResponse[List[NegotiationRequestRead]])
def list_deal_negotiations(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    reqs = negotiation_service.list_negotiation_requests(db=db, deal_id=deal_id)
    return DataResponse(data=[NegotiationRequestRead.model_validate(r) for r in reqs])


@router.post("/{request_id}/respond", response_model=DataResponse[NegotiationRequestRead])
def respond_to_negotiation(
    request_id: uuid.UUID,
    payload: NegotiationRespondRequest,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    req = negotiation_service.respond_negotiation_request(
        db=db,
        gateway=gateway,
        request_id=request_id,
        actor=current_user,
        action=payload.action,
        response_message=payload.response_message,
        counter_value=payload.counter_value,
    )
    return DataResponse(data=NegotiationRequestRead.model_validate(req))


@router.post("/deals/{deal_id}/comments", response_model=DataResponse[PortalCommentRead])
def add_deal_comment(
    deal_id: uuid.UUID,
    payload: PortalCommentCreate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    comment = negotiation_service.add_portal_comment(
        db=db,
        deal_id=deal_id,
        author_odoo_user_id=current_user.odoo_user_id,
        author_role=current_user.role,
        body=payload.body,
        is_internal=payload.is_internal,
        odoo_sale_order_line_id=payload.odoo_sale_order_line_id,
    )
    return DataResponse(data=PortalCommentRead.model_validate(comment))


@router.get("/deals/{deal_id}/comments", response_model=DataResponse[List[PortalCommentRead]])
def list_deal_comments(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    comments = negotiation_service.list_portal_comments(db=db, deal_id=deal_id, include_internal=True)
    return DataResponse(data=[PortalCommentRead.model_validate(c) for c in comments])
