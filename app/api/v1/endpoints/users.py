from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.deps import get_current_active_user, get_db, require_roles
from app.core.errors import NotFoundError
from app.core.pagination import PaginationParams, paginate_query
from app.core.rbac import Role
from app.models.identity import DealflowUser
from app.schemas.common import DataResponse, PaginatedResponse
from app.schemas.identity import UserRead, UserUpdate

router = APIRouter()


@router.get("", response_model=PaginatedResponse[UserRead])
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: Optional[Role] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER)),
):
    query = db.query(DealflowUser)
    if role:
        query = query.filter(DealflowUser.role == role.value)
    if is_active is not None:
        query = query.filter(DealflowUser.is_active == is_active)

    items, meta = paginate_query(
        query.order_by(DealflowUser.odoo_user_id.asc()),
        PaginationParams(page=page, page_size=page_size),
    )
    return PaginatedResponse(
        data=[UserRead.model_validate(u) for u in items],
        meta=meta,
    )


@router.get("/{odoo_user_id}", response_model=DataResponse[UserRead])
def get_user(
    odoo_user_id: int,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    user = db.query(DealflowUser).filter(DealflowUser.odoo_user_id == odoo_user_id).first()
    if not user:
        raise NotFoundError(f"User with Odoo ID {odoo_user_id} not found.")
    return DataResponse(data=UserRead.model_validate(user))


@router.patch("/{odoo_user_id}", response_model=DataResponse[UserRead])
def update_user(
    odoo_user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN)),
):
    user = db.query(DealflowUser).filter(DealflowUser.odoo_user_id == odoo_user_id).first()
    if not user:
        raise NotFoundError(f"User with Odoo ID {odoo_user_id} not found.")

    if payload.role is not None:
        user.role = payload.role.value
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.sales_team_odoo_id is not None:
        user.sales_team_odoo_id = payload.sales_team_odoo_id

    db.commit()
    db.refresh(user)
    return DataResponse(data=UserRead.model_validate(user))
