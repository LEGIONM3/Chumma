from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user, get_db, get_odoo_gateway, require_roles
from app.core.rbac import Role
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse, MessageResponse
from app.schemas.fulfillment import (
    DealFulfillmentResponse,
    FulfillmentConsolidateRequest,
    FulfillmentExceptionRead,
    FulfillmentPlanGenerateRequest,
    FulfillmentPlanLineRead,
    FulfillmentPlanOverrideRequest,
    FulfillmentPlanRead,
    WarehouseProfileCreate,
    WarehouseProfileRead,
    WarehouseProfileUpdate,
)
from app.services import fulfillment_service

router = APIRouter()


# ---------------------------------------------------------------------------
# DEAL FULFILLMENT ROUTES
# ---------------------------------------------------------------------------


@router.get("/deals/{deal_id}/fulfillment", response_model=DataResponse[DealFulfillmentResponse])
def get_deal_fulfillment_endpoint(
    deal_id: uuid.UUID,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    result = fulfillment_service.get_deal_fulfillment(db=db, gateway=gateway, deal_id=deal_id)
    plan_read = FulfillmentPlanRead.model_validate(result["plan"]) if result["plan"] else None
    bo_reads = [FulfillmentPlanLineRead.model_validate(b) for b in result["backorders"]]
    return DataResponse(
        data=DealFulfillmentResponse(
            deal_id=deal_id,
            plan=plan_read,
            pickings=result["pickings"],
            backorders=bo_reads,
        )
    )


@router.post("/deals/{deal_id}/fulfillment/propose", response_model=DataResponse[FulfillmentPlanRead])
def propose_fulfillment_plan_endpoint(
    deal_id: uuid.UUID,
    payload: Optional[FulfillmentPlanGenerateRequest] = None,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    strat = payload.strategy if payload and payload.strategy else None
    plan = fulfillment_service.propose_fulfillment_plan(
        db=db,
        gateway=gateway,
        deal_id=deal_id,
        strategy=strat,
    )
    return DataResponse(data=FulfillmentPlanRead.model_validate(plan))


from sqlalchemy import desc
from app.core.errors import EntityNotFoundError
from app.models.fulfillment import FulfillmentPlan
from app.models.enums import FulfillmentPlanStatus


def _find_target_plan(db: Session, deal_id: uuid.UUID, plan_id: Optional[uuid.UUID] = None) -> FulfillmentPlan:
    if plan_id:
        plan = db.query(FulfillmentPlan).filter(FulfillmentPlan.id == plan_id, FulfillmentPlan.deal_id == deal_id).first()
        if not plan:
            raise EntityNotFoundError("FulfillmentPlan", str(plan_id))
        return plan
    plan = (
        db.query(FulfillmentPlan)
        .filter(
            FulfillmentPlan.deal_id == deal_id,
            FulfillmentPlan.status.in_([
                FulfillmentPlanStatus.PROPOSED.value,
                FulfillmentPlanStatus.ACCEPTED.value,
                FulfillmentPlanStatus.OVERRIDDEN.value,
            ]),
        )
        .order_by(desc(FulfillmentPlan.generated_at))
        .first()
    )
    if not plan:
        raise EntityNotFoundError("FulfillmentPlan", f"No active plan for deal {deal_id}")
    return plan


@router.post("/deals/{deal_id}/fulfillment/accept", response_model=DataResponse[FulfillmentPlanRead])
@router.post("/deals/{deal_id}/fulfillment/{plan_id}/accept", response_model=DataResponse[FulfillmentPlanRead])
def accept_fulfillment_plan_endpoint(
    deal_id: uuid.UUID,
    plan_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    target_plan = _find_target_plan(db, deal_id, plan_id)
    plan = fulfillment_service.accept_fulfillment_plan(
        db=db,
        deal_id=deal_id,
        plan_id=target_plan.id,
        actor=current_user,
    )
    return DataResponse(data=FulfillmentPlanRead.model_validate(plan))


@router.post("/deals/{deal_id}/fulfillment/override", response_model=DataResponse[FulfillmentPlanRead])
@router.post("/deals/{deal_id}/fulfillment/{plan_id}/override", response_model=DataResponse[FulfillmentPlanRead])
def override_fulfillment_plan_endpoint(
    deal_id: uuid.UUID,
    payload: FulfillmentPlanOverrideRequest,
    plan_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    target_plan = _find_target_plan(db, deal_id, plan_id)
    allocs = payload.allocations if payload.allocations is not None else (payload.lines or [])
    plan = fulfillment_service.override_fulfillment_plan(
        db=db,
        gateway=gateway,
        deal_id=deal_id,
        plan_id=target_plan.id,
        allocations=allocs,
        actor=current_user,
        reason=payload.reason,
    )
    return DataResponse(data=FulfillmentPlanRead.model_validate(plan))


@router.post("/deals/{deal_id}/fulfillment/apply", response_model=DataResponse[Dict[str, Any]])
@router.post("/deals/{deal_id}/fulfillment/{plan_id}/apply", response_model=DataResponse[Dict[str, Any]])
def apply_fulfillment_plan_endpoint(
    deal_id: uuid.UUID,
    plan_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    target_plan = _find_target_plan(db, deal_id, plan_id)
    result = fulfillment_service.apply_fulfillment_plan(
        db=db,
        gateway=gateway,
        deal_id=deal_id,
        plan_id=target_plan.id,
        actor=current_user,
    )
    return DataResponse(
        data={
            "plan": FulfillmentPlanRead.model_validate(result["plan"]).model_dump(),
            "picking_ids": result["picking_ids"],
        }
    )


@router.post("/deals/{deal_id}/fulfillment/consolidate", response_model=DataResponse[FulfillmentPlanRead])
@router.post("/deals/{deal_id}/fulfillment/{plan_id}/consolidate", response_model=DataResponse[FulfillmentPlanRead])
def consolidate_backorder_endpoint(
    deal_id: uuid.UUID,
    plan_id: Optional[uuid.UUID] = None,
    payload: Optional[FulfillmentConsolidateRequest] = None,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    target_plan = _find_target_plan(db, deal_id, plan_id)
    wh_id = payload.warehouse_id if payload else None
    qty = payload.qty if payload else None
    new_plan = fulfillment_service.consolidate_backorder(
        db=db,
        gateway=gateway,
        deal_id=deal_id,
        plan_id=target_plan.id,
        actor=current_user,
        warehouse_id=wh_id,
        qty=qty,
    )
    return DataResponse(data=FulfillmentPlanRead.model_validate(new_plan))


@router.get("/fulfillment/exceptions", response_model=DataResponse[List[FulfillmentExceptionRead]])
def list_fulfillment_exceptions_endpoint(
    company_id: int = Query(1),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    exceptions = fulfillment_service.get_fulfillment_exceptions(db=db, company_id=company_id)
    return DataResponse(data=[FulfillmentExceptionRead.model_validate(e) for e in exceptions])


# ---------------------------------------------------------------------------
# WAREHOUSE PROFILES CRUD
# ---------------------------------------------------------------------------


@router.get("/warehouse-profiles", response_model=DataResponse[List[WarehouseProfileRead]])
def list_warehouse_profiles_endpoint(
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    profiles = fulfillment_service.list_warehouse_profiles(db=db)
    return DataResponse(data=[WarehouseProfileRead.model_validate(p) for p in profiles])


@router.post("/warehouse-profiles", response_model=DataResponse[WarehouseProfileRead], status_code=status.HTTP_201_CREATED)
def create_warehouse_profile_endpoint(
    payload: WarehouseProfileCreate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN)),
):
    profile = fulfillment_service.create_warehouse_profile(db=db, payload=payload)
    return DataResponse(data=WarehouseProfileRead.model_validate(profile))


@router.get("/warehouse-profiles/{profile_id}", response_model=DataResponse[WarehouseProfileRead])
def get_warehouse_profile_endpoint(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    profile = fulfillment_service.get_warehouse_profile(db=db, profile_id=profile_id)
    return DataResponse(data=WarehouseProfileRead.model_validate(profile))


@router.patch("/warehouse-profiles/{profile_id}", response_model=DataResponse[WarehouseProfileRead])
def update_warehouse_profile_endpoint(
    profile_id: uuid.UUID,
    payload: WarehouseProfileUpdate,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN)),
):
    profile = fulfillment_service.update_warehouse_profile(db=db, profile_id=profile_id, payload=payload)
    return DataResponse(data=WarehouseProfileRead.model_validate(profile))


@router.delete("/warehouse-profiles/{profile_id}", response_model=MessageResponse)
def delete_warehouse_profile_endpoint(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(require_roles(Role.ADMIN)),
):
    fulfillment_service.delete_warehouse_profile(db=db, profile_id=profile_id)
    return MessageResponse(message=f"Warehouse profile {profile_id} deleted successfully.")
