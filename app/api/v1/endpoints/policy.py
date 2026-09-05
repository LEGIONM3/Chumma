from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from app.core.deps import get_current_active_user, get_db, require_roles
from app.core.errors import ConflictError, NotFoundError
from app.core.rbac import Role
from app.models.identity import DealflowUser
from app.models.policy import CustomerTier, CustomerTierAssignment, DiscountPolicy
from app.schemas.common import DataResponse, MessageResponse
from app.schemas.governance import (
    CustomerTierAssignmentRead,
    CustomerTierAssignmentUpdate,
    CustomerTierCreate,
    CustomerTierRead,
    DiscountPolicyCreate,
    DiscountPolicyRead,
    DiscountPolicyUpdate,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# DISCOUNT POLICIES
# ---------------------------------------------------------------------------


@router.get("/policies", response_model=DataResponse[List[DiscountPolicyRead]])
def list_discount_policies(
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    query = db.query(DiscountPolicy)
    if active_only:
        query = query.filter(DiscountPolicy.active == True)
    policies = query.order_by(DiscountPolicy.priority.asc(), DiscountPolicy.created_at.desc()).all()
    return DataResponse(data=[DiscountPolicyRead.model_validate(p) for p in policies])


@router.post("/policies", response_model=DataResponse[DiscountPolicyRead], status_code=status.HTTP_201_CREATED)
def create_discount_policy(
    payload: DiscountPolicyCreate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN, Role.FINANCE, Role.SALES_MANAGER)),
):
    policy = DiscountPolicy(**payload.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return DataResponse(data=DiscountPolicyRead.model_validate(policy))


@router.get("/policies/{policy_id}", response_model=DataResponse[DiscountPolicyRead])
def get_discount_policy(
    policy_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    policy = db.query(DiscountPolicy).filter(DiscountPolicy.id == policy_id).first()
    if not policy:
        raise NotFoundError(f"Discount policy {policy_id} not found.")
    return DataResponse(data=DiscountPolicyRead.model_validate(policy))


@router.patch("/policies/{policy_id}", response_model=DataResponse[DiscountPolicyRead])
def update_discount_policy(
    policy_id: uuid.UUID,
    payload: DiscountPolicyUpdate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN, Role.FINANCE)),
):
    policy = db.query(DiscountPolicy).filter(DiscountPolicy.id == policy_id).first()
    if not policy:
        raise NotFoundError(f"Discount policy {policy_id} not found.")

    update_data = payload.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(policy, key, val)

    db.commit()
    db.refresh(policy)
    return DataResponse(data=DiscountPolicyRead.model_validate(policy))


@router.delete("/policies/{policy_id}", response_model=DataResponse[MessageResponse])
def delete_discount_policy(
    policy_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN, Role.FINANCE)),
):
    policy = db.query(DiscountPolicy).filter(DiscountPolicy.id == policy_id).first()
    if not policy:
        raise NotFoundError(f"Discount policy {policy_id} not found.")

    policy.active = False
    db.commit()
    return DataResponse(data=MessageResponse(message="Policy deactivated."))


# ---------------------------------------------------------------------------
# CUSTOMER TIERS
# ---------------------------------------------------------------------------


@router.get("/customer-tiers", response_model=DataResponse[List[CustomerTierRead]])
def list_customer_tiers(
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    query = db.query(CustomerTier)
    if active_only:
        query = query.filter(CustomerTier.is_active == True)
    tiers = query.order_by(CustomerTier.rank.asc()).all()
    return DataResponse(data=[CustomerTierRead.model_validate(t) for t in tiers])


@router.post("/customer-tiers", response_model=DataResponse[CustomerTierRead], status_code=status.HTTP_201_CREATED)
def create_customer_tier(
    payload: CustomerTierCreate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN, Role.FINANCE)),
):
    existing = db.query(CustomerTier).filter(CustomerTier.code == payload.code).first()
    if existing:
        raise ConflictError(code="TIER_EXISTS", message=f"Tier with code '{payload.code}' already exists.")

    tier = CustomerTier(**payload.model_dump())
    db.add(tier)
    db.commit()
    db.refresh(tier)
    return DataResponse(data=CustomerTierRead.model_validate(tier))


@router.put("/customer-tiers/assignments/{partner_id}", response_model=DataResponse[CustomerTierAssignmentRead])
def assign_customer_tier(
    partner_id: int,
    payload: CustomerTierAssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN, Role.SALES_MANAGER, Role.FINANCE)),
):
    tier = db.query(CustomerTier).filter(CustomerTier.code == payload.tier_code).first()
    if not tier:
        raise NotFoundError(f"Tier code '{payload.tier_code}' not found.")

    assignment = (
        db.query(CustomerTierAssignment)
        .filter(CustomerTierAssignment.odoo_partner_id == partner_id)
        .first()
    )
    if not assignment:
        assignment = CustomerTierAssignment(
            odoo_partner_id=partner_id,
            tier_code=payload.tier_code,
        )
        db.add(assignment)
    else:
        assignment.tier_code = payload.tier_code

    db.commit()
    db.refresh(assignment)
    return DataResponse(data=CustomerTierAssignmentRead.model_validate(assignment))


@router.get("/customer-tiers/assignments/{partner_id}", response_model=DataResponse[CustomerTierAssignmentRead])
def get_customer_tier_assignment(
    partner_id: int,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    assignment = (
        db.query(CustomerTierAssignment)
        .filter(CustomerTierAssignment.odoo_partner_id == partner_id)
        .first()
    )
    if not assignment:
        raise NotFoundError(f"No tier assignment found for partner {partner_id}.")
    return DataResponse(data=CustomerTierAssignmentRead.model_validate(assignment))
