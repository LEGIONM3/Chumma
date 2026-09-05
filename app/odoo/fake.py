from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from app.odoo.interface import (
    OdooCapabilityMissing,
    OdooGateway,
    OdooIdentity,
    RawBillingSummary,
    RawOrder,
    RawOrderHeader,
    RawOrderLine,
    RawPartner,
    RawPicking,
    RawProduct,
    RawWarehouse,
)


class FakeOdooGateway(OdooGateway):
    """Deterministic, stateful in-memory Odoo gateway for offline dev & test execution."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset the in-memory fixtures to initial baseline."""
        self.users: Dict[str, Dict[str, Any]] = {
            "admin": {
                "uid": 1,
                "name": "System Admin",
                "login": "admin",
                "password": "admin",
                "email": "admin@dealflow.test",
                "groups": ["base.group_system", "dealflow_odoo.group_dealflow_admin"],
                "is_share": False,
                "partner_id": None,
                "company_id": 1,
            },
            "admin@dealflow.test": {
                "uid": 1,
                "name": "System Admin",
                "login": "admin@dealflow.test",
                "password": "Password123!",
                "email": "admin@dealflow.test",
                "groups": ["base.group_system", "dealflow_odoo.group_dealflow_admin"],
                "is_share": False,
                "partner_id": None,
                "company_id": 1,
            },
            "manager1@dealflow.test": {
                "uid": 2,
                "name": "Sales Manager North",
                "login": "manager1@dealflow.test",
                "password": "Password123!",
                "email": "manager1@dealflow.test",
                "groups": ["sales_team.group_sale_manager", "dealflow_odoo.group_dealflow_sales_manager"],
                "is_share": False,
                "partner_id": None,
                "company_id": 1,
                "team_id": 1,
            },
            "manager2@dealflow.test": {
                "uid": 3,
                "name": "Sales Manager South",
                "login": "manager2@dealflow.test",
                "password": "Password123!",
                "email": "manager2@dealflow.test",
                "groups": ["sales_team.group_sale_manager", "dealflow_odoo.group_dealflow_sales_manager"],
                "is_share": False,
                "partner_id": None,
                "company_id": 1,
                "team_id": 2,
            },
            "rep1@dealflow.test": {
                "uid": 4,
                "name": "Sales Rep One",
                "login": "rep1@dealflow.test",
                "password": "Password123!",
                "email": "rep1@dealflow.test",
                "groups": ["sales_team.group_sale_salesman", "dealflow_odoo.group_dealflow_sales_rep"],
                "is_share": False,
                "partner_id": None,
                "company_id": 1,
                "team_id": 1,
            },
            "rep2@dealflow.test": {
                "uid": 5,
                "name": "Sales Rep Two",
                "login": "rep2@dealflow.test",
                "password": "Password123!",
                "email": "rep2@dealflow.test",
                "groups": ["sales_team.group_sale_salesman", "dealflow_odoo.group_dealflow_sales_rep"],
                "is_share": False,
                "partner_id": None,
                "company_id": 1,
                "team_id": 2,
            },
            "finance@dealflow.test": {
                "uid": 6,
                "name": "Finance Officer",
                "login": "finance@dealflow.test",
                "password": "Password123!",
                "email": "finance@dealflow.test",
                "groups": ["account.group_account_manager", "dealflow_odoo.group_dealflow_finance"],
                "is_share": False,
                "partner_id": None,
                "company_id": 1,
            },
            "buyer@acme.test": {
                "uid": 10,
                "name": "Acme Buyer",
                "login": "buyer@acme.test",
                "password": "Password123!",
                "email": "buyer@acme.test",
                "groups": ["base.group_portal", "dealflow_odoo.group_dealflow_portal"],
                "is_share": True,
                "partner_id": 1,
                "company_id": 1,
            },
            "buyer@beta.test": {
                "uid": 11,
                "name": "Beta Buyer",
                "login": "buyer@beta.test",
                "password": "Password123!",
                "email": "buyer@beta.test",
                "groups": ["base.group_portal", "dealflow_odoo.group_dealflow_portal"],
                "is_share": True,
                "partner_id": 2,
                "company_id": 1,
            },
            "buyer@gamma.test": {
                "uid": 12,
                "name": "Gamma Buyer",
                "login": "buyer@gamma.test",
                "password": "Password123!",
                "email": "buyer@gamma.test",
                "groups": ["base.group_portal", "dealflow_odoo.group_dealflow_portal"],
                "is_share": True,
                "partner_id": 3,
                "company_id": 1,
            },
        }

        self.partners: Dict[int, Dict[str, Any]] = {
            1: {
                "id": 1,
                "name": "Acme Corp",
                "tier_code": "GOLD",
                "payment_term_days": 30,
                "email": "buyer@acme.test",
                "portal_user_ids": [10],
            },
            2: {
                "id": 2,
                "name": "Beta Industries",
                "tier_code": "SILVER",
                "payment_term_days": 30,
                "email": "buyer@beta.test",
                "portal_user_ids": [11],
            },
            3: {
                "id": 3,
                "name": "Gamma LLC",
                "tier_code": "BRONZE",
                "payment_term_days": 30,
                "email": "buyer@gamma.test",
                "portal_user_ids": [12],
            },
        }

        self.products: Dict[int, Dict[str, Any]] = {
            101: {
                "id": 101,
                "name": "Laptop Pro 14\"",
                "category_id": 1,
                "category_path": [1],
                "type": "STOCKABLE",
                "standard_price": 35000.0,
                "list_price": 50000.0,
                "tags": [],
                "is_recurring": False,
                "plan_info": None,
            },
            102: {
                "id": 102,
                "name": "Laptop Pro 16\"",
                "category_id": 1,
                "category_path": [1],
                "type": "STOCKABLE",
                "standard_price": 42000.0,
                "list_price": 60000.0,
                "tags": [],
                "is_recurring": False,
                "plan_info": None,
            },
            103: {
                "id": 103,
                "name": "Monitor 27\"",
                "category_id": 1,
                "category_path": [1],
                "type": "STOCKABLE",
                "standard_price": 10000.0,
                "list_price": 15000.0,
                "tags": [],
                "is_recurring": False,
                "plan_info": None,
            },
            104: {
                "id": 104,
                "name": "Docking Station",
                "category_id": 4,
                "category_path": [4],
                "type": "STOCKABLE",
                "standard_price": 3000.0,
                "list_price": 6000.0,
                "tags": ["DealFlow Promo"],
                "is_recurring": False,
                "plan_info": None,
            },
            105: {
                "id": 105,
                "name": "Laptop Bag",
                "category_id": 4,
                "category_path": [4],
                "type": "STOCKABLE",
                "standard_price": 750.0,
                "list_price": 2000.0,
                "tags": ["DealFlow Promo"],
                "is_recurring": False,
                "plan_info": None,
            },
            106: {
                "id": 106,
                "name": "Wireless Mouse",
                "category_id": 4,
                "category_path": [4],
                "type": "STOCKABLE",
                "standard_price": 400.0,
                "list_price": 1200.0,
                "tags": [],
                "is_recurring": False,
                "plan_info": None,
            },
            107: {
                "id": 107,
                "name": "Setup Service",
                "category_id": 2,
                "category_path": [2],
                "type": "SERVICE",
                "standard_price": 80000.0,
                "list_price": 100000.0,
                "tax_rate": 18.0,
                "tags": [],
                "is_recurring": False,
                "plan_info": None,
            },
            108: {
                "id": 108,
                "name": "Training Day",
                "category_id": 2,
                "category_path": [2],
                "type": "SERVICE",
                "standard_price": 25000.0,
                "list_price": 40000.0,
                "tags": [],
                "is_recurring": False,
                "plan_info": None,
            },
            109: {
                "id": 109,
                "name": "Premium Support",
                "category_id": 3,
                "category_path": [3],
                "type": "SUBSCRIPTION",
                "standard_price": 6000.0,
                "list_price": 20000.0,
                "tags": [],
                "is_recurring": True,
                "plan_info": {"plan_id": 1, "name": "Monthly", "cadence": "MONTHLY"},
            },
            110: {
                "id": 110,
                "name": "Cloud Backup",
                "category_id": 3,
                "category_path": [3],
                "type": "SUBSCRIPTION",
                "standard_price": 1000.0,
                "list_price": 5000.0,
                "tags": [],
                "is_recurring": True,
                "plan_info": {"plan_id": 1, "name": "Monthly", "cadence": "MONTHLY"},
            },
        }

        self.warehouses: Dict[int, Dict[str, Any]] = {
            1: {"id": 1, "name": "Main Warehouse", "code": "MAIN"},
            2: {"id": 2, "name": "East Depot", "code": "EAST"},
            3: {"id": 3, "name": "West Hub", "code": "WEST"},
        }

        # Available stock per product_id -> warehouse_id -> qty
        self.stock: Dict[int, Dict[int, int]] = {
            101: {1: 8, 2: 5, 3: 0},
            102: {1: 3, 2: 2, 3: 0},
            103: {1: 20, 2: 0, 3: 0},
            104: {1: 0, 2: 30, 3: 0},
            105: {1: 50, 2: 0, 3: 0},
            106: {1: 2, 2: 2, 3: 10},
        }

        # Sale orders
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.order_seq = 100
        self.line_seq = 1000
        self.picking_seq = 500
        self.pickings: Dict[int, List[Dict[str, Any]]] = {}
        self.invoices: Dict[int, List[Dict[str, Any]]] = {}
        self.payments: Dict[int, List[Dict[str, Any]]] = {}
        self.subscriptions: Dict[int, List[Dict[str, Any]]] = {}
        self.outbox: List[Dict[str, Any]] = []

    def get_customer_by_email(self, email: str) -> Optional[RawPartner]:
        for p in self.partners.values():
            if p.get("email") == email:
                return self.get_partner(p["id"])
        for u in self.users.values():
            if u.get("email") == email and u.get("partner_id"):
                return self.get_partner(u["partner_id"])
        return None

    def authenticate(self, login: str, password: str) -> OdooIdentity:
        user = self.users.get(login)
        if not user or user["password"] != password:
            raise ValueError("Invalid Odoo credentials")
        return OdooIdentity(
            uid=user["uid"],
            name=user["name"],
            login=user["login"],
            email=user.get("email"),
            groups=user.get("groups", []),
            is_share=user.get("is_share", False),
            partner_id=user.get("partner_id"),
            company_id=user.get("company_id", 1),
        )

    def create_order(
        self,
        partner_id: int,
        user_id: int = 4,
        team_id: Optional[int] = 1,
        currency: str = "INR",
        lines: Optional[List[Dict[str, Any]]] = None,
    ) -> RawOrder:
        self.order_seq += 1
        order_id = self.order_seq
        partner = self.partners[partner_id]
        now_str = datetime.now(timezone.utc).isoformat()

        order_data = {
            "id": order_id,
            "name": f"SO{order_id:05d}",
            "partner_id": partner_id,
            "partner_name": partner["name"],
            "state": "draft",
            "currency": currency,
            "user_id": user_id,
            "team_id": team_id,
            "company_id": 1,
            "validity_date": None,
            "commitment_date": None,
            "write_date": now_str,
            "dealflow_deal_id": None,
            "dealflow_approval_state": "draft",
            "dealflow_risk_score": 0.0,
            "dealflow_locked": False,
            "lines": [],
        }

        self.orders[order_id] = order_data
        if lines:
            for l in lines:
                self.add_line(
                    order_id=order_id,
                    product_id=l["product_id"],
                    qty=float(l.get("qty", 1.0)),
                    discount=float(l.get("discount_pct", 0.0)),
                )

        return self.get_sale_order(order_id)

    def get_sale_order(self, order_id: int) -> RawOrder:
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Sale order {order_id} not found in fake Odoo")

        lines_list: List[RawOrderLine] = []
        untaxed = 0.0
        tax_total = 0.0

        for l in order["lines"]:
            prod = self.products[l["product_id"]]
            net = (l["price_unit"] * l["qty"]) * (1.0 - l["discount_pct"] / 100.0)
            tax = net * (l.get("tax_rate_pct", 0.0) / 100.0)
            untaxed += net
            tax_total += tax

            lines_list.append(
                RawOrderLine(
                    id=l["id"],
                    product_id=l["product_id"],
                    product_name=prod["name"],
                    category_id=prod["category_id"],
                    category_path=prod["category_path"],
                    product_type=prod["type"],
                    qty=l["qty"],
                    price_unit=l["price_unit"],
                    discount_pct=l["discount_pct"],
                    unit_cost=prod["standard_price"],
                    tax_rate_pct=l.get("tax_rate_pct", 18.0 if prod["type"] == "SERVICE" else 0.0),
                    is_recurring=prod.get("is_recurring", False),
                    plan_name=prod["plan_info"]["name"] if prod.get("plan_info") else None,
                )
            )

        header = RawOrderHeader(
            id=order["id"],
            name=order["name"],
            partner_id=order["partner_id"],
            partner_name=order["partner_name"],
            state=order["state"],
            currency=order["currency"],
            user_id=order["user_id"],
            team_id=order.get("team_id"),
            company_id=order["company_id"],
            validity_date=order.get("validity_date"),
            commitment_date=order.get("commitment_date"),
            write_date=order.get("write_date"),
            amount_untaxed=round(untaxed, 2),
            amount_tax=round(tax_total, 2),
            amount_total=round(untaxed + tax_total, 2),
            dealflow_deal_id=order.get("dealflow_deal_id"),
            dealflow_approval_state=order.get("dealflow_approval_state"),
            dealflow_risk_score=order.get("dealflow_risk_score"),
            dealflow_locked=order.get("dealflow_locked"),
        )
        return RawOrder(header=header, lines=lines_list)

    def list_sale_orders(
        self,
        since_write_date: Optional[str] = None,
        ids: Optional[List[int]] = None,
        partner_id: Optional[int] = None,
        state: Optional[str] = None,
    ) -> List[RawOrderHeader]:
        headers = []
        for oid in self.orders:
            order = self.get_sale_order(oid).header
            if ids and order.id not in ids:
                continue
            if partner_id and order.partner_id != partner_id:
                continue
            if state and order.state != state:
                continue
            headers.append(order)
        return headers

    def get_partner(self, partner_id: int) -> RawPartner:
        partner = self.partners.get(partner_id)
        if not partner:
            raise ValueError(f"Partner {partner_id} not found in fake Odoo")
        return RawPartner(
            id=partner["id"],
            name=partner["name"],
            tier_code=partner.get("tier_code"),
            payment_term_days=partner.get("payment_term_days", 30),
            email=partner.get("email"),
            portal_user_ids=partner.get("portal_user_ids", []),
        )

    def get_products(self, product_ids: List[int]) -> Dict[int, RawProduct]:
        res = {}
        for pid in product_ids:
            p = self.products.get(pid)
            if p:
                res[pid] = RawProduct(
                    id=p["id"],
                    name=p["name"],
                    category_id=p["category_id"],
                    category_path=p["category_path"],
                    type=p["type"],
                    standard_price=p["standard_price"],
                    list_price=p["list_price"],
                    tags=p.get("tags", []),
                    is_recurring=p.get("is_recurring", False),
                    plan_info=p.get("plan_info"),
                )
        return res

    def get_price(self, partner_id: int, product_id: int, qty: float = 1.0) -> Decimal:
        prod = self.products.get(product_id)
        if not prod:
            raise ValueError(f"Product {product_id} not found in fake Odoo")
        return Decimal(str(prod["list_price"]))

    def get_warehouses(self) -> List[RawWarehouse]:
        return [
            RawWarehouse(id=w["id"], name=w["name"], code=w["code"])
            for w in self.warehouses.values()
        ]

    def get_availability(
        self, product_ids: List[int], warehouse_ids: Optional[List[int]] = None
    ) -> Dict[int, Dict[int, int]]:
        res: Dict[int, Dict[int, int]] = {}
        for pid in product_ids:
            res[pid] = {}
            w_map = self.stock.get(pid, {})
            for wid in (warehouse_ids or [1, 2, 3]):
                res[pid][wid] = w_map.get(wid, 0)
        return res

    def get_pickings(self, order_id: int) -> List[RawPicking]:
        raw_list = self.pickings.get(order_id, [])
        return [
            RawPicking(
                id=p["id"],
                warehouse_id=p["warehouse_id"],
                state=p["state"],
                scheduled_date=p.get("scheduled_date"),
                date_done=p.get("date_done"),
                lines=p.get("lines", []),
            )
            for p in raw_list
        ]

    def get_billing_summary(self, order_id: int) -> RawBillingSummary:
        order = self.orders.get(order_id)
        if not order:
            return RawBillingSummary()

        one_time = []
        recurring = []
        for l in order["lines"]:
            prod = self.products[l["product_id"]]
            if prod.get("is_recurring"):
                recurring.append(l["id"])
            else:
                one_time.append(l["id"])

        invs = self.invoices.get(order_id, [])
        payments = self.payments.get(order_id, [])
        subs = self.subscriptions.get(order_id, [])
        schedule = []
        if subs:
            # Generate sample upcoming schedule rows
            schedule = [
                {
                    "period": 1,
                    "date": "2026-10-01",
                    "amount": 20000.0,
                    "status": "SCHEDULED",
                },
                {
                    "period": 2,
                    "date": "2026-11-01",
                    "amount": 20000.0,
                    "status": "SCHEDULED",
                },
            ]

        return RawBillingSummary(
            one_time_lines=one_time,
            recurring_lines=recurring,
            invoices=invs,
            payments=payments,
            subscriptions=subs,
            schedule=schedule,
        )

    def apply_line_changes(self, order_id: int, changes: List[Dict[str, Any]]) -> bool:
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Sale order {order_id} not found in fake Odoo")

        for c in changes:
            lid = c.get("line_id") or c.get("id")
            for l in order["lines"]:
                if l["id"] == lid:
                    if "discount" in c:
                        l["discount_pct"] = float(c["discount"])
                    if "discount_pct" in c:
                        l["discount_pct"] = float(c["discount_pct"])
                    if "qty" in c:
                        l["qty"] = float(c["qty"])
                    if "product_uom_qty" in c:
                        l["qty"] = float(c["product_uom_qty"])
        order["write_date"] = datetime.now(timezone.utc).isoformat()
        return True

    def add_line(
        self, order_id: int, product_id: int, qty: float = 1.0, discount: float = 0.0
    ) -> int:
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Sale order {order_id} not found in fake Odoo")

        self.line_seq += 1
        line_id = self.line_seq
        prod = self.products[product_id]

        new_line = {
            "id": line_id,
            "product_id": product_id,
            "qty": qty,
            "price_unit": prod["list_price"],
            "discount_pct": discount,
            "tax_rate_pct": 18.0 if prod["type"] == "SERVICE" else 0.0,
        }
        order["lines"].append(new_line)
        order["write_date"] = datetime.now(timezone.utc).isoformat()
        return line_id

    def set_governance(
        self, order_id: int, approval_state: str, risk_score: float, locked: bool
    ) -> bool:
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Sale order {order_id} not found in fake Odoo")

        order["dealflow_approval_state"] = approval_state
        order["dealflow_risk_score"] = risk_score
        order["dealflow_locked"] = locked
        order["write_date"] = datetime.now(timezone.utc).isoformat()
        return True

    def confirm(self, order_id: int) -> bool:
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Sale order {order_id} not found in fake Odoo")

        if order.get("dealflow_locked"):
            raise ValueError("Order locked pending DealFlow approval")

        order["state"] = "sale"
        order["write_date"] = datetime.now(timezone.utc).isoformat()

        # Seed initial invoice & subscription upon confirmation
        has_recurring = False
        has_onetime = False
        total_recurring = 0.0
        total_onetime = 0.0

        for l in order["lines"]:
            prod = self.products[l["product_id"]]
            net = (l["price_unit"] * l["qty"]) * (1.0 - l["discount_pct"] / 100.0)
            if prod.get("is_recurring"):
                has_recurring = True
                total_recurring += net
            else:
                has_onetime += True
                total_onetime += net

        if has_onetime:
            self.invoices[order_id] = [
                {
                    "id": order_id * 10 + 1,
                    "number": f"INV/{order_id}/001",
                    "type": "out_invoice",
                    "state": "posted",
                    "amount_total": round(total_onetime, 2),
                    "amount_residual": round(total_onetime, 2),
                    "amount_paid": 0.0,
                }
            ]

        if has_recurring:
            self.subscriptions[order_id] = [
                {
                    "id": order_id * 10 + 2,
                    "code": f"SUB/{order_id}/001",
                    "state": "in_progress",
                    "recurring_total": round(total_recurring, 2),
                }
            ]
        return True

    def cancel(self, order_id: int) -> bool:
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Sale order {order_id} not found in fake Odoo")
        order["state"] = "cancel"
        order["write_date"] = datetime.now(timezone.utc).isoformat()
        return True

    def apply_fulfillment_plan(
        self, order_id: int, allocations: List[Dict[str, Any]]
    ) -> List[int]:
        order = self.orders.get(order_id)
        if not order:
            raise ValueError(f"Sale order {order_id} not found in fake Odoo")

        created_pickings = []
        # Group allocations by warehouse
        by_wh: Dict[int, List[Dict[str, Any]]] = {}
        for a in allocations:
            wid = a.get("warehouse_id")
            if wid:
                if wid not in by_wh:
                    by_wh[wid] = []
                by_wh[wid].append(a)

        for wid, alloc_list in by_wh.items():
            self.picking_seq += 1
            pid = self.picking_seq
            picking_lines = []
            for item in alloc_list:
                prod_id = item["product_id"]
                qty = item["qty"]
                # Deduct stock
                if prod_id in self.stock and wid in self.stock[prod_id]:
                    self.stock[prod_id][wid] = max(0, self.stock[prod_id][wid] - int(qty))
                picking_lines.append({"product_id": prod_id, "qty": qty})

            picking = {
                "id": pid,
                "warehouse_id": wid,
                "state": "assigned",
                "scheduled_date": datetime.now(timezone.utc).isoformat(),
                "date_done": None,
                "lines": picking_lines,
            }
            if order_id not in self.pickings:
                self.pickings[order_id] = []
            self.pickings[order_id].append(picking)
            created_pickings.append(pid)

        return created_pickings

    def register_payment(
        self, invoice_id: int, amount: float, journal_id: Optional[int] = None
    ) -> Dict[str, Any]:
        for oid, inv_list in self.invoices.items():
            for inv in inv_list:
                if inv["id"] == invoice_id:
                    paid = inv.get("amount_paid", 0.0) + amount
                    residual = max(0.0, inv["amount_total"] - paid)
                    inv["amount_paid"] = round(paid, 2)
                    inv["amount_residual"] = round(residual, 2)
                    if residual == 0.0:
                        inv["payment_state"] = "paid"
                    else:
                        inv["payment_state"] = "partial"

                    pay_rec = {
                        "id": invoice_id * 100 + 1,
                        "invoice_id": invoice_id,
                        "amount": amount,
                        "payment_date": datetime.now(timezone.utc).isoformat(),
                    }
                    if oid not in self.payments:
                        self.payments[oid] = []
                    self.payments[oid].append(pay_rec)
                    return pay_rec

        return {"status": "ok", "invoice_id": invoice_id, "amount": amount}

    def get_users(self) -> List[OdooIdentity]:
        return [
            OdooIdentity(
                uid=u["uid"],
                name=u["name"],
                login=u["login"],
                email=u.get("email"),
                groups=u.get("groups", []),
                is_share=u.get("is_share", False),
                partner_id=u.get("partner_id"),
                company_id=u.get("company_id", 1),
            )
            for u in self.users.values()
            if not u.get("is_share")
        ]

    def get_confirmed_orders_for_mining(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        # Pre-seed historical pairs
        return [
            {"id": 1, "product_ids": [101, 104]},  # Laptop -> Docking
            {"id": 2, "product_ids": [101, 105]},  # Laptop -> Bag
            {"id": 3, "product_ids": [101, 109]},  # Laptop -> Premium Support
            {"id": 4, "product_ids": [103, 104]},  # Monitor -> Docking
            {"id": 5, "product_ids": [101, 106]},  # Laptop -> Mouse
            {"id": 6, "product_ids": [107, 108]},  # Setup Service -> Training Day
        ]
