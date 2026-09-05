from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy.orm import Session
from app.core.errors import NotFoundError
from app.models.health import RepDiscountStats
from app.models.policy import CustomerTier, CustomerTierAssignment
from app.odoo.interface import OdooGateway, RawOrder


def quantize_dec(val: Any, places: int = 2) -> Decimal:
    if val is None:
        val = 0
    d = Decimal(str(val))
    fmt = "0." + "0" * places if places > 0 else "0"
    return d.quantize(Decimal(fmt), rounding=ROUND_HALF_UP)


@dataclass
class DealLineContext:
    line_id: int
    product_id: int
    product_name: str
    category_id: int
    category_path: List[int]
    product_type: str
    qty: Decimal
    price_unit: Decimal
    list_price: Decimal
    discount_pct: Decimal
    unit_cost: Decimal
    subtotal: Decimal
    cost_subtotal: Decimal
    margin_amount: Decimal
    margin_pct: Decimal
    is_recurring: bool
    tax_rate_pct: Decimal
    available_qty: int
    inventory_shortage: bool


@dataclass
class DealContext:
    deal_id: Optional[uuid.UUID]
    odoo_sale_order_id: int
    odoo_order_name: str
    partner_id: int
    partner_name: str
    tier_code: str
    tier_default_ceiling: Decimal
    currency_code: str
    company_id: int
    user_id: int
    team_id: Optional[int]
    state: str
    order_discount_pct: Decimal
    amount_untaxed: Decimal
    amount_total: Decimal
    one_time_total: Decimal
    recurring_first_cycle_total: Decimal
    total_cost: Decimal
    total_margin_amount: Decimal
    total_margin_pct: Decimal
    blended_discount_pct: Decimal
    lines: List[DealLineContext] = field(default_factory=list)
    historical_rep_avg_discount: Optional[Decimal] = None
    historical_rep_stddev: Optional[Decimal] = None
    order_date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    delivery_date: Optional[datetime] = None
    line_count: int = 0


def build_deal_context(
    db: Session,
    gateway: OdooGateway,
    order_id: int,
    deal_id: Optional[uuid.UUID] = None,
) -> DealContext:
    try:
        raw_order: RawOrder = gateway.get_sale_order(order_id)
    except Exception as e:
        raise NotFoundError(f"Sale order {order_id} not found in Odoo: {e}")

    header = raw_order.header

    # 1. Resolve customer tier and ceiling
    assignment = (
        db.query(CustomerTierAssignment)
        .filter(CustomerTierAssignment.odoo_partner_id == header.partner_id)
        .first()
    )
    tier_code = assignment.tier_code if assignment else "STANDARD"
    tier_rec = db.query(CustomerTier).filter(CustomerTier.code == tier_code).first()
    tier_ceiling = tier_rec.default_max_discount_pct if tier_rec else Decimal("5.00")

    # 2. Fetch stock availability for storable products
    product_ids = [l.product_id for l in raw_order.lines]
    availability_map: Dict[int, Dict[int, int]] = {}
    if product_ids:
        try:
            availability_map = gateway.get_availability(product_ids)
        except Exception:
            availability_map = {}

    # 3. Process lines
    lines_ctx: List[DealLineContext] = []
    total_list_revenue = Decimal("0.00")
    total_actual_untaxed = Decimal("0.00")
    total_actual_tax = Decimal("0.00")
    total_cost = Decimal("0.00")
    one_time_total = Decimal("0.00")
    recurring_first_cycle_total = Decimal("0.00")

    for l in raw_order.lines:
        qty = quantize_dec(l.qty, 2)
        price_unit = quantize_dec(l.price_unit, 2)
        disc_pct = quantize_dec(l.discount_pct, 2)
        unit_cost = quantize_dec(l.unit_cost, 2)

        line_list_amt = qty * price_unit
        subtotal = line_list_amt * (Decimal("1") - (disc_pct / Decimal("100")))
        subtotal = quantize_dec(subtotal, 2)

        line_cost = quantize_dec(qty * unit_cost, 2)
        margin_amt = quantize_dec(subtotal - line_cost, 2)
        margin_pct = (
            quantize_dec((margin_amt / subtotal) * Decimal("100"), 2)
            if subtotal > 0
            else Decimal("0.00")
        )

        total_list_revenue += line_list_amt
        total_actual_untaxed += subtotal
        total_cost += line_cost

        tax_rate = quantize_dec(l.tax_rate_pct, 2)
        tax_amt = quantize_dec(subtotal * (tax_rate / Decimal("100")), 2)
        total_actual_tax += tax_amt

        if l.is_recurring:
            recurring_first_cycle_total += subtotal
        else:
            one_time_total += subtotal

        prod_stock = availability_map.get(l.product_id, {})
        wh1_available = prod_stock.get(1, 0)
        total_available = sum(prod_stock.values()) if prod_stock else 0
        is_stockable = str(l.product_type).upper() in ("PRODUCT", "CONSU", "STOCKABLE")
        shortage = (
            is_stockable and (int(qty) > wh1_available)
        )

        lines_ctx.append(
            DealLineContext(
                line_id=l.id,
                product_id=l.product_id,
                product_name=l.product_name,
                category_id=l.category_id,
                category_path=l.category_path,
                product_type=l.product_type,
                qty=qty,
                price_unit=price_unit,
                list_price=price_unit,
                discount_pct=disc_pct,
                unit_cost=unit_cost,
                subtotal=subtotal,
                cost_subtotal=line_cost,
                margin_amount=margin_amt,
                margin_pct=margin_pct,
                is_recurring=l.is_recurring,
                tax_rate_pct=tax_rate,
                available_qty=total_available,
                inventory_shortage=shortage,
            )
        )

    # 4. Blended discount & total margin
    if total_list_revenue > 0:
        blended_disc = (
            (total_list_revenue - total_actual_untaxed) / total_list_revenue
        ) * Decimal("100")
        blended_disc = quantize_dec(blended_disc, 2)
    else:
        blended_disc = Decimal("0.00")

    total_margin_amt = quantize_dec(total_actual_untaxed - total_cost, 2)
    if total_actual_untaxed > 0:
        total_margin_pct = quantize_dec(
            (total_margin_amt / total_actual_untaxed) * Decimal("100"), 2
        )
    else:
        total_margin_pct = Decimal("0.00")

    amount_total = quantize_dec(total_actual_untaxed + total_actual_tax, 2)

    # 5. Fetch sales rep historical baseline
    rep_stats = (
        db.query(RepDiscountStats)
        .filter(RepDiscountStats.odoo_user_id == header.user_id)
        .first()
    )
    rep_avg = rep_stats.avg_weighted_discount_pct if rep_stats else None
    rep_std = rep_stats.stddev if rep_stats else None

    # Parse dates
    order_date = datetime.now(timezone.utc)
    delivery_date = None
    if header.commitment_date:
        try:
            delivery_date = datetime.fromisoformat(header.commitment_date.replace("Z", "+00:00"))
        except Exception:
            pass

    return DealContext(
        deal_id=deal_id,
        odoo_sale_order_id=header.id,
        odoo_order_name=header.name,
        partner_id=header.partner_id,
        partner_name=header.partner_name,
        tier_code=tier_code,
        tier_default_ceiling=tier_ceiling,
        currency_code=header.currency or "INR",
        company_id=header.company_id or 1,
        user_id=header.user_id,
        team_id=header.team_id,
        state=header.state,
        order_discount_pct=Decimal("0.00"),
        amount_untaxed=total_actual_untaxed,
        amount_total=amount_total,
        one_time_total=one_time_total,
        recurring_first_cycle_total=recurring_first_cycle_total,
        total_cost=total_cost,
        total_margin_amount=total_margin_amt,
        total_margin_pct=total_margin_pct,
        blended_discount_pct=blended_disc,
        lines=lines_ctx,
        historical_rep_avg_discount=rep_avg,
        historical_rep_stddev=rep_std,
        order_date=order_date,
        delivery_date=delivery_date,
        line_count=len(lines_ctx),
    )
