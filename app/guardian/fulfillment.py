from dataclasses import dataclass, field
from decimal import Decimal
import itertools
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class StockableLine:
    line_id: int
    product_id: int
    requested_qty: int


@dataclass
class WarehouseInfo:
    warehouse_id: int
    name: str
    shipping_cost_weight: Decimal
    priority: int = 1
    active: bool = True


@dataclass
class PlanAllocation:
    line_id: int
    product_id: int
    warehouse_id: Optional[int]
    requested_qty: int
    allocated_qty: int
    backorder_qty: int
    shipping_cost: Decimal = Decimal("0.00")


@dataclass
class FulfillmentCalculationResult:
    strategy: str  # FULL_COVERAGE, EXHAUSTIVE, GREEDY, NO_STOCKABLE_ITEMS
    estimated_shipments: int
    estimated_shipping_cost: Decimal
    allocations: List[PlanAllocation]
    backorders: List[PlanAllocation]
    algorithm_notes: Dict[str, Any] = field(default_factory=dict)


def calculate_fulfillment_plan(
    lines: List[StockableLine],
    warehouses: List[WarehouseInfo],
    availability: Dict[int, Dict[int, int]],
    force_strategy: Optional[str] = None,
) -> FulfillmentCalculationResult:
    """Pure-Python multi-warehouse fulfillment optimization algorithm.
    
    Rules:
    1. Filter to positive stockable lines.
    2. Check single warehouse full coverage (lowest shipping cost weight).
    3. If |W| <= 6: exhaustive subset search minimizing shipments (|S|), then sum of weights.
    4. If |W| > 6: greedy selection picking warehouse with max additional coverage.
    5. Allocate variant/line demand in ascending shipping cost weight.
    6. Generate backorder lines for any shortfall (allocated_qty + backorder_qty = requested_qty).
    """
    valid_lines = [l for l in lines if l.requested_qty > 0]
    if not valid_lines:
        return FulfillmentCalculationResult(
            strategy="NO_STOCKABLE_ITEMS",
            estimated_shipments=0,
            estimated_shipping_cost=Decimal("0.00"),
            allocations=[],
            backorders=[],
            algorithm_notes={"reason": "No stockable items with positive demand in order."},
        )

    active_warehouses = [w for w in warehouses if w.active]
    if not active_warehouses:
        backorders = [
            PlanAllocation(
                line_id=l.line_id,
                product_id=l.product_id,
                warehouse_id=None,
                requested_qty=l.requested_qty,
                allocated_qty=0,
                backorder_qty=l.requested_qty,
                shipping_cost=Decimal("0.00"),
            )
            for l in valid_lines
        ]
        return FulfillmentCalculationResult(
            strategy="NO_ACTIVE_WAREHOUSES",
            estimated_shipments=0,
            estimated_shipping_cost=Decimal("0.00"),
            allocations=[],
            backorders=backorders,
            algorithm_notes={"reason": "No active warehouses available."},
        )

    total_demand: Dict[int, int] = {}
    for l in valid_lines:
        total_demand[l.product_id] = total_demand.get(l.product_id, 0) + l.requested_qty

    def get_avail(wid: int, pid: int) -> int:
        return max(0, availability.get(pid, {}).get(wid, 0))

    sorted_warehouses = sorted(
        active_warehouses,
        key=lambda w: (w.shipping_cost_weight, w.priority, w.warehouse_id),
    )

    chosen_warehouses: List[WarehouseInfo] = []
    strategy = "FULL_COVERAGE"

    # 1. Full-coverage check
    if force_strategy != "GREEDY" and force_strategy != "EXHAUSTIVE":
        full_coverage_candidates = []
        for w in sorted_warehouses:
            can_cover = all(get_avail(w.warehouse_id, pid) >= dem for pid, dem in total_demand.items())
            if can_cover:
                full_coverage_candidates.append(w)

        if full_coverage_candidates:
            best_w = min(
                full_coverage_candidates,
                key=lambda w: (w.shipping_cost_weight, w.priority, w.warehouse_id),
            )
            chosen_warehouses = [best_w]
            strategy = "FULL_COVERAGE"

    # 2. Multi-warehouse optimization if no single warehouse full coverage
    if not chosen_warehouses:
        if len(sorted_warehouses) <= 6 and force_strategy != "GREEDY":
            strategy = "EXHAUSTIVE"
            found_subset = None
            for k in range(2, len(sorted_warehouses) + 1):
                subsets_k = list(itertools.combinations(sorted_warehouses, k))
                subsets_k.sort(
                    key=lambda s: (
                        sum(w.shipping_cost_weight for w in s),
                        sum(w.priority for w in s),
                    )
                )
                for s in subsets_k:
                    can_cover = all(
                        sum(get_avail(w.warehouse_id, pid) for w in s) >= dem
                        for pid, dem in total_demand.items()
                    )
                    if can_cover:
                        found_subset = list(s)
                        break
                if found_subset:
                    break

            if found_subset:
                chosen_warehouses = found_subset
            else:
                relevant = [
                    w for w in sorted_warehouses
                    if any(get_avail(w.warehouse_id, pid) > 0 for pid in total_demand)
                ]
                chosen_warehouses = relevant if relevant else sorted_warehouses
        else:
            strategy = "GREEDY"
            remaining = dict(total_demand)
            pool = list(sorted_warehouses)
            selected = []

            while any(rem > 0 for rem in remaining.values()) and pool:
                def coverage_gain(w: WarehouseInfo) -> int:
                    return sum(min(get_avail(w.warehouse_id, pid), remaining[pid]) for pid in remaining)

                best_w = max(
                    pool,
                    key=lambda w: (
                        coverage_gain(w),
                        -float(w.shipping_cost_weight),
                        -w.priority,
                    ),
                )
                if coverage_gain(best_w) == 0:
                    break

                selected.append(best_w)
                for pid in remaining:
                    remaining[pid] -= min(get_avail(best_w.warehouse_id, pid), remaining[pid])
                pool.remove(best_w)

            chosen_warehouses = selected if selected else sorted_warehouses

    chosen_warehouses.sort(key=lambda w: (w.shipping_cost_weight, w.priority, w.warehouse_id))

    current_avail: Dict[int, Dict[int, int]] = {
        w.warehouse_id: {pid: get_avail(w.warehouse_id, pid) for pid in total_demand}
        for w in chosen_warehouses
    }

    allocations: List[PlanAllocation] = []
    backorders: List[PlanAllocation] = []
    warehouses_used: Set[int] = set()

    for l in valid_lines:
        needed = l.requested_qty
        pid = l.product_id

        for w in chosen_warehouses:
            avail_here = current_avail[w.warehouse_id][pid]
            if avail_here > 0 and needed > 0:
                alloc_qty = min(needed, avail_here)
                allocations.append(
                    PlanAllocation(
                        line_id=l.line_id,
                        product_id=pid,
                        warehouse_id=w.warehouse_id,
                        requested_qty=alloc_qty,
                        allocated_qty=alloc_qty,
                        backorder_qty=0,
                        shipping_cost=w.shipping_cost_weight,
                    )
                )
                warehouses_used.add(w.warehouse_id)
                current_avail[w.warehouse_id][pid] -= alloc_qty
                needed -= alloc_qty

            if needed == 0:
                break

        if needed > 0:
            backorders.append(
                PlanAllocation(
                    line_id=l.line_id,
                    product_id=pid,
                    warehouse_id=None,
                    requested_qty=needed,
                    allocated_qty=0,
                    backorder_qty=needed,
                    shipping_cost=Decimal("0.00"),
                )
            )

    wh_map = {w.warehouse_id: w for w in chosen_warehouses}
    estimated_shipments = len(warehouses_used)
    estimated_shipping_cost = sum(wh_map[wid].shipping_cost_weight for wid in warehouses_used) if warehouses_used else Decimal("0.00")

    algorithm_notes = {
        "strategy": strategy,
        "considered_warehouse_ids": [w.warehouse_id for w in sorted_warehouses],
        "chosen_warehouse_ids": [w.warehouse_id for w in chosen_warehouses],
        "used_warehouse_ids": list(warehouses_used),
        "shipment_count": estimated_shipments,
        "estimated_shipping_cost": float(estimated_shipping_cost),
        "shortfall_backorders_count": len(backorders),
    }

    return FulfillmentCalculationResult(
        strategy=strategy,
        estimated_shipments=estimated_shipments,
        estimated_shipping_cost=estimated_shipping_cost,
        allocations=allocations,
        backorders=backorders,
        algorithm_notes=algorithm_notes,
    )
