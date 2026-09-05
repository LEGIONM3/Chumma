from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set
import uuid
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, EntityNotFoundError
from app.db.base import utc_now
from app.guardian.fulfillment import (
    FulfillmentCalculationResult,
    PlanAllocation,
    StockableLine,
    WarehouseInfo,
    calculate_fulfillment_plan,
)
from app.models.deal import Deal
from app.models.enums import AuditEventType, DealStatus, FulfillmentPlanStatus
from app.models.fulfillment import FulfillmentPlan, FulfillmentPlanLine, WarehouseProfile
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.fulfillment import (
    FulfillmentOverrideLineItem,
    WarehouseProfileCreate,
    WarehouseProfileUpdate,
)
from app.services import audit_service, deal_service


def propose_fulfillment_plan(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
    strategy: Optional[str] = None,
) -> FulfillmentPlan:
    deal = deal_service.get_deal_by_id(db, deal_id)
    order = gateway.get_sale_order(deal.odoo_sale_order_id)
    if not order.lines:
        raise BusinessRuleError("NO_LINES", "Order has no lines to fulfill.")

    pids = list({l.product_id for l in order.lines})
    products_map = gateway.get_products(pids)

    # Filter stockable lines (exclude services and recurring subscriptions)
    stockable_lines = [
        StockableLine(
            line_id=l.id,
            product_id=l.product_id,
            requested_qty=int(l.qty),
        )
        for l in order.lines
        if products_map.get(l.product_id)
        and products_map[l.product_id].type == "STOCKABLE"
        and not products_map[l.product_id].is_recurring
        and l.qty > 0
    ]

    # Ensure warehouse profiles exist in DB
    db_profiles = db.query(WarehouseProfile).filter(WarehouseProfile.active == True).all()
    if not db_profiles:
        raw_warehouses = gateway.get_warehouses()
        default_weights = {
            1: (Decimal("10.00"), 1),
            2: (Decimal("15.00"), 2),
            3: (Decimal("25.00"), 3),
        }
        for rw in raw_warehouses:
            weight, prio = default_weights.get(rw.id, (Decimal("10.00"), 1))
            prof = WarehouseProfile(
                odoo_warehouse_id=rw.id,
                shipping_cost_weight=weight,
                priority=prio,
                lead_time_days=0,
                active=True,
            )
            db.add(prof)
        db.commit()
        db_profiles = db.query(WarehouseProfile).filter(WarehouseProfile.active == True).all()

    wh_map = {w.id: w.name for w in gateway.get_warehouses()}
    warehouses_info = [
        WarehouseInfo(
            warehouse_id=wp.odoo_warehouse_id,
            name=wh_map.get(wp.odoo_warehouse_id, f"Warehouse {wp.odoo_warehouse_id}"),
            shipping_cost_weight=wp.shipping_cost_weight,
            priority=wp.priority,
            active=wp.active,
        )
        for wp in db_profiles
    ]

    stockable_pids = list({l.product_id for l in stockable_lines})
    wh_ids = [w.warehouse_id for w in warehouses_info]
    availability = gateway.get_availability(stockable_pids, wh_ids)

    calc_res = calculate_fulfillment_plan(
        lines=stockable_lines,
        warehouses=warehouses_info,
        availability=availability,
        force_strategy=strategy,
    )

    # Supersede existing PROPOSED plans for this deal
    existing_proposed = (
        db.query(FulfillmentPlan)
        .filter(
            FulfillmentPlan.deal_id == deal.id,
            FulfillmentPlan.status == FulfillmentPlanStatus.PROPOSED.value,
        )
        .all()
    )
    for p in existing_proposed:
        p.status = FulfillmentPlanStatus.SUPERSEDED.value

    plan = FulfillmentPlan(
        deal_id=deal.id,
        odoo_sale_order_id=deal.odoo_sale_order_id,
        status=FulfillmentPlanStatus.PROPOSED.value,
        estimated_shipments=calc_res.estimated_shipments,
        estimated_shipping_cost=calc_res.estimated_shipping_cost,
        strategy=calc_res.strategy,
        algorithm_version="1.0",
        algorithm_notes=calc_res.algorithm_notes,
        generated_at=utc_now(),
    )
    db.add(plan)
    db.flush()

    for a in calc_res.allocations:
        plan_line = FulfillmentPlanLine(
            fulfillment_plan_id=plan.id,
            odoo_sale_order_line_id=a.line_id,
            odoo_product_id=a.product_id,
            odoo_warehouse_id=a.warehouse_id,
            requested_qty=a.requested_qty,
            allocated_qty=a.allocated_qty,
            backorder_qty=a.backorder_qty,
            shipping_cost=a.shipping_cost,
        )
        db.add(plan_line)

    for b in calc_res.backorders:
        plan_line = FulfillmentPlanLine(
            fulfillment_plan_id=plan.id,
            odoo_sale_order_line_id=b.line_id,
            odoo_product_id=b.product_id,
            odoo_warehouse_id=None,
            requested_qty=b.requested_qty,
            allocated_qty=b.allocated_qty,
            backorder_qty=b.backorder_qty,
            shipping_cost=b.shipping_cost,
        )
        db.add(plan_line)

    deal.last_activity_at = utc_now()

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.FULFILLMENT_PLANNED,
        entity_type="fulfillment_plan",
        entity_id=str(plan.id),
        deal_id=deal.id,
        actor_type="SYSTEM",
        reason=f"Proposed fulfillment plan: {calc_res.strategy} ({calc_res.estimated_shipments} shipments, ₹{calc_res.estimated_shipping_cost})",
    )

    db.commit()
    db.refresh(plan)
    return plan


def accept_fulfillment_plan(
    db: Session,
    deal_id: uuid.UUID,
    plan_id: uuid.UUID,
    actor: DealflowUser,
) -> FulfillmentPlan:
    plan = (
        db.query(FulfillmentPlan)
        .filter(FulfillmentPlan.id == plan_id, FulfillmentPlan.deal_id == deal_id)
        .first()
    )
    if not plan:
        raise EntityNotFoundError("FulfillmentPlan", str(plan_id))

    if plan.status != FulfillmentPlanStatus.PROPOSED.value:
        raise BusinessRuleError("INVALID_PLAN_STATUS", f"Cannot accept plan with status {plan.status}.")

    plan.status = FulfillmentPlanStatus.ACCEPTED.value
    plan.accepted_by_odoo_user_id = actor.odoo_user_id
    plan.accepted_at = utc_now()

    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if deal:
        deal.last_activity_at = utc_now()

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.FULFILLMENT_ACCEPTED,
        entity_type="fulfillment_plan",
        entity_id=str(plan.id),
        deal_id=deal_id,
        actor_type="USER",
        actor_id=actor.odoo_user_id,
        actor_role=actor.role,
        reason="Fulfillment plan accepted.",
    )

    db.commit()
    db.refresh(plan)
    return plan


def override_fulfillment_plan(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
    plan_id: uuid.UUID,
    allocations: List[Any],
    actor: DealflowUser,
    reason: str,
) -> FulfillmentPlan:
    if not reason or not reason.strip():
        raise BusinessRuleError("REASON_REQUIRED", "Reason is required for manual fulfillment override.")

    plan = (
        db.query(FulfillmentPlan)
        .filter(FulfillmentPlan.id == plan_id, FulfillmentPlan.deal_id == deal_id)
        .first()
    )
    if not plan:
        raise EntityNotFoundError("FulfillmentPlan", str(plan_id))

    if plan.status not in (FulfillmentPlanStatus.PROPOSED.value, FulfillmentPlanStatus.ACCEPTED.value):
        raise BusinessRuleError("INVALID_PLAN_STATUS", f"Cannot override plan with status {plan.status}.")

    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    order = gateway.get_sale_order(deal.odoo_sale_order_id)
    order_lines_map = {l.id: l for l in order.lines}

    # Delete existing lines for this plan
    db.query(FulfillmentPlanLine).filter(FulfillmentPlanLine.fulfillment_plan_id == plan.id).delete()

    profiles = {wp.odoo_warehouse_id: wp for wp in db.query(WarehouseProfile).all()}

    warehouses_used = set()
    for item in allocations:
        # Handle dict or Pydantic model
        if hasattr(item, "model_dump"):
            data = item.model_dump()
        elif isinstance(item, dict):
            data = item
        else:
            data = getattr(item, "__dict__", {})

        line_id = data.get("odoo_sale_order_line_id") or data.get("line_id")
        product_id = data.get("odoo_product_id") or data.get("product_id")
        warehouse_id = data.get("odoo_warehouse_id") or data.get("warehouse_id")
        allocated_qty = data.get("allocated_qty") if data.get("allocated_qty") is not None else data.get("qty", 0)
        backorder_qty = data.get("backorder_qty") or 0

        if not line_id or line_id not in order_lines_map:
            raise BusinessRuleError("INVALID_LINE_ID", f"Order line {line_id} not found in order.")

        if not product_id:
            product_id = order_lines_map[line_id].product_id

        req_qty = allocated_qty + backorder_qty
        if req_qty <= 0:
            continue

        cost = Decimal("0.00")
        if warehouse_id and allocated_qty > 0:
            warehouses_used.add(warehouse_id)
            if warehouse_id in profiles:
                cost = profiles[warehouse_id].shipping_cost_weight

        plan_line = FulfillmentPlanLine(
            fulfillment_plan_id=plan.id,
            odoo_sale_order_line_id=line_id,
            odoo_product_id=product_id,
            odoo_warehouse_id=warehouse_id,
            requested_qty=req_qty,
            allocated_qty=allocated_qty,
            backorder_qty=backorder_qty,
            shipping_cost=cost,
        )
        db.add(plan_line)

    est_cost = sum(profiles[wid].shipping_cost_weight for wid in warehouses_used if wid in profiles)

    plan.status = FulfillmentPlanStatus.OVERRIDDEN.value
    plan.strategy = "MANUAL"
    plan.estimated_shipments = len(warehouses_used)
    plan.estimated_shipping_cost = est_cost
    plan.accepted_by_odoo_user_id = actor.odoo_user_id
    plan.accepted_at = utc_now()
    deal.last_activity_at = utc_now()

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.FULFILLMENT_OVERRIDDEN,
        entity_type="fulfillment_plan",
        entity_id=str(plan.id),
        deal_id=deal.id,
        actor_type="USER",
        actor_id=actor.odoo_user_id,
        actor_role=actor.role,
        reason=reason,
    )

    db.commit()
    db.refresh(plan)
    return plan


def apply_fulfillment_plan(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
    plan_id: uuid.UUID,
    actor: DealflowUser,
) -> Dict[str, Any]:
    deal = deal_service.get_deal_by_id(db, deal_id)
    if deal.status not in (DealStatus.CONFIRMED.value, DealStatus.IN_FULFILLMENT.value):
        raise BusinessRuleError("DEAL_NOT_CONFIRMED", "Deal must be CONFIRMED before applying fulfillment plan.")

    plan = (
        db.query(FulfillmentPlan)
        .filter(FulfillmentPlan.id == plan_id, FulfillmentPlan.deal_id == deal_id)
        .first()
    )
    if not plan:
        raise EntityNotFoundError("FulfillmentPlan", str(plan_id))

    if plan.status not in (FulfillmentPlanStatus.ACCEPTED.value, FulfillmentPlanStatus.OVERRIDDEN.value):
        raise BusinessRuleError(
            "INVALID_PLAN_STATUS",
            f"Cannot apply plan in status {plan.status}. Must be ACCEPTED or OVERRIDDEN.",
        )

    # Filter lines with positive allocation to a warehouse
    lines = (
        db.query(FulfillmentPlanLine)
        .filter(
            FulfillmentPlanLine.fulfillment_plan_id == plan.id,
            FulfillmentPlanLine.allocated_qty > 0,
            FulfillmentPlanLine.odoo_warehouse_id.isnot(None),
        )
        .all()
    )

    alloc_payload = [
        {
            "line_id": l.odoo_sale_order_line_id,
            "product_id": l.odoo_product_id,
            "warehouse_id": l.odoo_warehouse_id,
            "qty": l.allocated_qty,
        }
        for l in lines
    ]

    picking_ids = gateway.apply_fulfillment_plan(
        order_id=deal.odoo_sale_order_id,
        allocations=alloc_payload,
    )

    plan.status = FulfillmentPlanStatus.APPLIED.value
    plan.applied_at = utc_now()
    plan.odoo_picking_ids = picking_ids

    deal.status = DealStatus.IN_FULFILLMENT.value
    deal.last_activity_at = utc_now()

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.FULFILLMENT_APPLIED,
        entity_type="fulfillment_plan",
        entity_id=str(plan.id),
        deal_id=deal.id,
        actor_type="USER",
        actor_id=actor.odoo_user_id,
        actor_role=actor.role,
        reason=f"Applied fulfillment plan to Odoo. Pickings created: {picking_ids}",
    )

    db.commit()
    db.refresh(plan)
    return {"plan": plan, "picking_ids": picking_ids}


def consolidate_backorder(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
    plan_id: uuid.UUID,
    actor: DealflowUser,
    warehouse_id: Optional[int] = None,
    qty: Optional[int] = None,
) -> FulfillmentPlan:
    deal = deal_service.get_deal_by_id(db, deal_id)
    new_plan = propose_fulfillment_plan(db=db, gateway=gateway, deal_id=deal.id)

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.FULFILLMENT_REPLANNED,
        entity_type="fulfillment_plan",
        entity_id=str(new_plan.id),
        deal_id=deal.id,
        actor_type="USER",
        actor_id=actor.odoo_user_id,
        actor_role=actor.role,
        reason=f"Consolidated backorder. Fresh plan {new_plan.id} generated.",
    )

    db.commit()
    db.refresh(new_plan)
    return new_plan


def get_deal_fulfillment(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
) -> Dict[str, Any]:
    deal = deal_service.get_deal_by_id(db, deal_id)
    plan = (
        db.query(FulfillmentPlan)
        .filter(
            FulfillmentPlan.deal_id == deal.id,
            FulfillmentPlan.status != FulfillmentPlanStatus.SUPERSEDED.value,
        )
        .order_by(desc(FulfillmentPlan.generated_at))
        .first()
    )

    pickings_raw = gateway.get_pickings(deal.odoo_sale_order_id)
    pickings = [
        {
            "id": p.id,
            "warehouse_id": p.warehouse_id,
            "state": p.state,
            "scheduled_date": p.scheduled_date,
            "date_done": p.date_done,
            "lines": p.lines,
        }
        for p in pickings_raw
    ]

    backorders = []
    if plan:
        backorders = [l for l in plan.lines if l.backorder_qty > 0]

    return {
        "deal_id": deal.id,
        "plan": plan,
        "pickings": pickings,
        "backorders": backorders,
    }


def get_fulfillment_exceptions(
    db: Session,
    company_id: int = 1,
) -> List[Dict[str, Any]]:
    exceptions = (
        db.query(FulfillmentPlanLine, FulfillmentPlan, Deal)
        .join(FulfillmentPlan, FulfillmentPlanLine.fulfillment_plan_id == FulfillmentPlan.id)
        .join(Deal, FulfillmentPlan.deal_id == Deal.id)
        .filter(
            Deal.odoo_company_id == company_id,
            FulfillmentPlanLine.backorder_qty > 0,
            FulfillmentPlan.status.in_([
                FulfillmentPlanStatus.PROPOSED.value,
                FulfillmentPlanStatus.ACCEPTED.value,
                FulfillmentPlanStatus.OVERRIDDEN.value,
                FulfillmentPlanStatus.APPLIED.value,
            ]),
        )
        .order_by(desc(FulfillmentPlan.generated_at))
        .all()
    )

    result = []
    for line, plan, deal in exceptions:
        result.append({
            "deal_id": deal.id,
            "deal_reference": deal.reference,
            "odoo_sale_order_id": deal.odoo_sale_order_id,
            "customer_name": deal.partner_name_cache,
            "plan_id": plan.id,
            "product_id": line.odoo_product_id,
            "backorder_qty": line.backorder_qty,
            "created_at": plan.generated_at,
        })
    return result


def list_warehouse_profiles(db: Session) -> List[WarehouseProfile]:
    return db.query(WarehouseProfile).order_by(WarehouseProfile.priority.asc()).all()


def create_warehouse_profile(db: Session, payload: WarehouseProfileCreate) -> WarehouseProfile:
    existing = db.query(WarehouseProfile).filter(WarehouseProfile.odoo_warehouse_id == payload.odoo_warehouse_id).first()
    if existing:
        raise BusinessRuleError("DUPLICATE_WAREHOUSE", f"Profile for warehouse {payload.odoo_warehouse_id} already exists.")

    prof = WarehouseProfile(
        odoo_warehouse_id=payload.odoo_warehouse_id,
        shipping_cost_weight=payload.shipping_cost_weight,
        priority=payload.priority,
        lead_time_days=payload.lead_time_days,
        active=payload.active,
    )
    db.add(prof)
    db.commit()
    db.refresh(prof)
    return prof


def get_warehouse_profile(db: Session, profile_id: uuid.UUID) -> WarehouseProfile:
    prof = db.query(WarehouseProfile).filter(WarehouseProfile.id == profile_id).first()
    if not prof:
        raise EntityNotFoundError("WarehouseProfile", str(profile_id))
    return prof


def update_warehouse_profile(
    db: Session,
    profile_id: uuid.UUID,
    payload: WarehouseProfileUpdate,
) -> WarehouseProfile:
    prof = get_warehouse_profile(db, profile_id)
    if payload.shipping_cost_weight is not None:
        prof.shipping_cost_weight = payload.shipping_cost_weight
    if payload.priority is not None:
        prof.priority = payload.priority
    if payload.lead_time_days is not None:
        prof.lead_time_days = payload.lead_time_days
    if payload.active is not None:
        prof.active = payload.active

    db.commit()
    db.refresh(prof)
    return prof


def delete_warehouse_profile(db: Session, profile_id: uuid.UUID) -> bool:
    prof = get_warehouse_profile(db, profile_id)
    db.delete(prof)
    db.commit()
    return True
