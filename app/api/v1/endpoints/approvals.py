from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user, get_db, get_odoo_gateway
from app.core.pagination import PaginationParams
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.approval import (
    ApprovalActionCreate,
    ApprovalRequestRead,
    ApprovalTimelineItem,
    PendingApprovalSummary,
)
from app.schemas.common import DataResponse, PaginatedResponse
from app.services import approval_service

router = APIRouter()


@router.get("/pending", response_model=PaginatedResponse[PendingApprovalSummary])
def get_pending_approvals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    pagination = PaginationParams(page=page, page_size=page_size)
    items, meta = approval_service.list_pending_approvals(
        db=db,
        actor=current_user,
        pagination=pagination,
    )
    return PaginatedResponse(data=items, meta=meta)


@router.post("/{request_id}/action", response_model=DataResponse[ApprovalRequestRead])
def execute_approval_action(
    request_id: uuid.UUID,
    payload: ApprovalActionCreate,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    req = approval_service.decide_approval_request(
        db=db,
        gateway=gateway,
        request_id=request_id,
        actor=current_user,
        action=payload.action,
        reason=payload.reason,
    )
    return DataResponse(data=ApprovalRequestRead.model_validate(req))


@router.get("/deals/{deal_id}/timeline", response_model=DataResponse[List[ApprovalTimelineItem]])
def get_approval_timeline(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    items = approval_service.get_deal_approval_timeline(db=db, deal_id=deal_id)
    return DataResponse(data=items)
