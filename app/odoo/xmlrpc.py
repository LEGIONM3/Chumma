import logging
import xmlrpc.client
from decimal import Decimal
from typing import Any, Dict, List, Optional
from app.core.config import settings
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

logger = logging.getLogger("dealflow360.odoo.xmlrpc")


class XmlRpcOdooGateway(OdooGateway):
    """Production XML-RPC gateway connecting to live Odoo ERP instance."""

    def __init__(
        self,
        url: Optional[str] = None,
        db: Optional[str] = None,
        api_user: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.url = (url or settings.ODOO_URL).rstrip("/")
        self.db = db or settings.ODOO_DB
        self.api_user = api_user or settings.ODOO_API_USER
        self.api_key = api_key or settings.ODOO_API_KEY
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        self._cached_uid: Optional[int] = None

    @property
    def uid(self) -> int:
        if self._cached_uid is None:
            uid = self.common.authenticate(self.db, self.api_user, self.api_key, {})
            if not uid:
                raise ConnectionError(f"Failed to authenticate technical user '{self.api_user}' with Odoo at {self.url}")
            self._cached_uid = uid
        return self._cached_uid

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return self.models.execute_kw(self.db, self.uid, self.api_key, model, method, args, kwargs)
        except xmlrpc.client.Fault as fault:
            fault_str = str(fault)
            if "has no attribute" in fault_str or "object has no attribute" in fault_str or "KeyError" in fault_str:
                logger.error(f"Odoo method '{method}' missing on model '{model}': {fault}")
                raise OdooCapabilityMissing(method, f"Odoo model '{model}' does not implement required method '{method}'.")
            raise

    def authenticate(self, login: str, password: str) -> OdooIdentity:
        uid = self.common.authenticate(self.db, login, password, {})
        if not uid:
            raise ValueError("Invalid Odoo credentials")

        user_fields = ["id", "name", "login", "email", "share", "partner_id", "company_id", "groups_id"]
        users = self.execute("res.users", "read", [uid], {"fields": user_fields})
        if not users:
            raise ValueError("User record not found in Odoo")
        u = users[0]

        groups_data = self.execute("res.groups", "read", u.get("groups_id", []), {"fields": ["full_name"]})
        group_names = [g["full_name"] for g in groups_data if "full_name" in g]

        partner_id = u["partner_id"][0] if isinstance(u.get("partner_id"), (list, tuple)) else u.get("partner_id")
        company_id = u["company_id"][0] if isinstance(u.get("company_id"), (list, tuple)) else (u.get("company_id") or 1)

        return OdooIdentity(
            uid=u["id"],
            name=u["name"],
            login=u["login"],
            email=u.get("email"),
            groups=group_names,
            is_share=bool(u.get("share")),
            partner_id=partner_id,
            company_id=company_id,
        )

    def get_sale_order(self, order_id: int) -> RawOrder:
        order_fields = [
            "id", "name", "partner_id", "state", "currency_id", "user_id", "team_id",
            "company_id", "validity_date", "commitment_date", "write_date",
            "amount_untaxed", "amount_tax", "amount_total",
            settings.ORDER_DEAL_ID_FIELD,
            settings.ORDER_APPROVAL_STATE_FIELD,
            settings.ORDER_RISK_SCORE_FIELD,
            settings.ORDER_LOCK_FIELD,
            "order_line",
        ]
        orders = self.execute("sale.order", "read", [order_id], {"fields": order_fields})
        if not orders:
            raise ValueError(f"Sale order {order_id} not found in Odoo")
        o = orders[0]

        line_ids = o.get("order_line", [])
        lines_data = []
        if line_ids:
            line_fields = [
                "id", "product_id", "name", "product_uom_qty", "price_unit", "discount",
                settings.LINE_COST_FIELD, "tax_id", "display_type",
            ]
            lines_data = self.execute("sale.order.line", "read", line_ids, {"fields": line_fields})

        product_ids = [l["product_id"][0] for l in lines_data if l.get("product_id") and not l.get("display_type")]
        products_map = self.get_products(product_ids) if product_ids else {}

        raw_lines: List[RawOrderLine] = []
        for l in lines_data:
            if l.get("display_type"):
                continue
            pid = l["product_id"][0]
            prod = products_map.get(pid)
            cost_val = l.get(settings.LINE_COST_FIELD) or (prod.standard_price if prod else 0.0)

            raw_lines.append(
                RawOrderLine(
                    id=l["id"],
                    product_id=pid,
                    product_name=l["product_id"][1] if isinstance(l["product_id"], (list, tuple)) else str(pid),
                    category_id=prod.category_id if prod else 1,
                    category_path=prod.category_path if prod else [1],
                    product_type=prod.type if prod else "STOCKABLE",
                    qty=float(l.get("product_uom_qty", 0.0)),
                    price_unit=float(l.get("price_unit", 0.0)),
                    discount_pct=float(l.get("discount", 0.0)),
                    unit_cost=float(cost_val or 0.0),
                    tax_rate_pct=18.0 if (prod and prod.type == "SERVICE") else 0.0,
                    is_recurring=prod.is_recurring if prod else False,
                    plan_name=prod.plan_info["name"] if (prod and prod.plan_info) else None,
                )
            )

        header = RawOrderHeader(
            id=o["id"],
            name=o["name"],
            partner_id=o["partner_id"][0] if isinstance(o["partner_id"], (list, tuple)) else o["partner_id"],
            partner_name=o["partner_id"][1] if isinstance(o["partner_id"], (list, tuple)) else "",
            state=o.get("state", "draft"),
            currency=o["currency_id"][1] if isinstance(o.get("currency_id"), (list, tuple)) else "INR",
            user_id=o["user_id"][0] if isinstance(o.get("user_id"), (list, tuple)) else (o.get("user_id") or 1),
            team_id=o["team_id"][0] if isinstance(o.get("team_id"), (list, tuple)) else o.get("team_id"),
            company_id=o["company_id"][0] if isinstance(o.get("company_id"), (list, tuple)) else (o.get("company_id") or 1),
            validity_date=str(o.get("validity_date")) if o.get("validity_date") else None,
            commitment_date=str(o.get("commitment_date")) if o.get("commitment_date") else None,
            write_date=str(o.get("write_date")) if o.get("write_date") else None,
            amount_untaxed=float(o.get("amount_untaxed", 0.0)),
            amount_tax=float(o.get("amount_tax", 0.0)),
            amount_total=float(o.get("amount_total", 0.0)),
            dealflow_deal_id=o.get(settings.ORDER_DEAL_ID_FIELD),
            dealflow_approval_state=o.get(settings.ORDER_APPROVAL_STATE_FIELD),
            dealflow_risk_score=float(o.get(settings.ORDER_RISK_SCORE_FIELD) or 0.0),
            dealflow_locked=bool(o.get(settings.ORDER_LOCK_FIELD)),
        )
        return RawOrder(header=header, lines=raw_lines)

    def create_order(
        self,
        partner_id: int,
        user_id: int = 4,
        team_id: Optional[int] = 1,
        currency: str = "INR",
        lines: Optional[List[Dict[str, Any]]] = None,
    ) -> RawOrder:
        order_vals: Dict[str, Any] = {
            "partner_id": partner_id,
            "user_id": user_id,
        }
        if team_id:
            order_vals["team_id"] = team_id
        order_id = self.execute("sale.order", "create", [order_vals])
        if isinstance(order_id, list):
            order_id = order_id[0]
        if lines:
            for l in lines:
                self.add_line(
                    order_id=order_id,
                    product_id=l["product_id"],
                    qty=float(l.get("qty", 1.0)),
                    discount=float(l.get("discount_pct", l.get("discount", 0.0))),
                )
        return self.get_sale_order(order_id)

    def list_sale_orders(
        self,
        since_write_date: Optional[str] = None,
        ids: Optional[List[int]] = None,
        partner_id: Optional[int] = None,
        state: Optional[str] = None,
    ) -> List[RawOrderHeader]:
        domain: List[Any] = []
        if ids:
            domain.append(("id", "in", ids))
        if partner_id:
            domain.append(("partner_id", "=", partner_id))
        if state:
            domain.append(("state", "=", state))
        if since_write_date:
            domain.append(("write_date", ">=", since_write_date))

        fields = [
            "id", "name", "partner_id", "state", "currency_id", "user_id", "team_id",
            "company_id", "validity_date", "commitment_date", "write_date",
            "amount_untaxed", "amount_tax", "amount_total",
            settings.ORDER_DEAL_ID_FIELD,
            settings.ORDER_APPROVAL_STATE_FIELD,
            settings.ORDER_RISK_SCORE_FIELD,
            settings.ORDER_LOCK_FIELD,
        ]
        orders = self.execute("sale.order", "search_read", domain, {"fields": fields})
        headers: List[RawOrderHeader] = []
        for o in orders:
            headers.append(
                RawOrderHeader(
                    id=o["id"],
                    name=o["name"],
                    partner_id=o["partner_id"][0] if isinstance(o["partner_id"], (list, tuple)) else o["partner_id"],
                    partner_name=o["partner_id"][1] if isinstance(o["partner_id"], (list, tuple)) else "",
                    state=o.get("state", "draft"),
                    currency=o["currency_id"][1] if isinstance(o.get("currency_id"), (list, tuple)) else "INR",
                    user_id=o["user_id"][0] if isinstance(o.get("user_id"), (list, tuple)) else (o.get("user_id") or 1),
                    team_id=o["team_id"][0] if isinstance(o.get("team_id"), (list, tuple)) else o.get("team_id"),
                    company_id=o["company_id"][0] if isinstance(o.get("company_id"), (list, tuple)) else (o.get("company_id") or 1),
                    validity_date=str(o.get("validity_date")) if o.get("validity_date") else None,
                    commitment_date=str(o.get("commitment_date")) if o.get("commitment_date") else None,
                    write_date=str(o.get("write_date")) if o.get("write_date") else None,
                    amount_untaxed=float(o.get("amount_untaxed", 0.0)),
                    amount_tax=float(o.get("amount_tax", 0.0)),
                    amount_total=float(o.get("amount_total", 0.0)),
                    dealflow_deal_id=o.get(settings.ORDER_DEAL_ID_FIELD),
                    dealflow_approval_state=o.get(settings.ORDER_APPROVAL_STATE_FIELD),
                    dealflow_risk_score=float(o.get(settings.ORDER_RISK_SCORE_FIELD) or 0.0),
                    dealflow_locked=bool(o.get(settings.ORDER_LOCK_FIELD)),
                )
            )
        return headers

    def get_partner(self, partner_id: int) -> RawPartner:
        partner_fields = ["id", "name", "email", "property_payment_term_id", settings.PARTNER_TIER_FIELD, "user_ids"]
        partners = self.execute("res.partner", "read", [partner_id], {"fields": partner_fields})
        if not partners:
            raise ValueError(f"Partner {partner_id} not found in Odoo")
        p = partners[0]

        return RawPartner(
            id=p["id"],
            name=p["name"],
            tier_code=p.get(settings.PARTNER_TIER_FIELD),
            payment_term_days=30,  # Default terms
            email=p.get("email"),
            portal_user_ids=p.get("user_ids", []),
        )

    def get_products(self, product_ids: List[int]) -> Dict[int, RawProduct]:
        if not product_ids:
            return {}
        prod_fields = ["id", "name", "categ_id", "detailed_type", "type", "standard_price", "list_price", "product_tag_ids"]
        prods = self.execute("product.product", "read", product_ids, {"fields": prod_fields})

        category_ids = list({p["categ_id"][0] for p in prods if p.get("categ_id")})
        cat_parents_map = self._get_category_hierarchies(category_ids)

        res: Dict[int, RawProduct] = {}
        for p in prods:
            cid = p["categ_id"][0] if isinstance(p.get("categ_id"), (list, tuple)) else (p.get("categ_id") or 1)
            raw_type = p.get("detailed_type") or p.get("type") or "product"
            norm_type = "STOCKABLE" if raw_type == "product" else "SERVICE"

            is_rec = False
            plan_info = None
            if "support" in p["name"].lower() or "backup" in p["name"].lower() or "subscription" in p["name"].lower():
                norm_type = "SUBSCRIPTION"
                is_rec = True
                plan_info = {"plan_id": 1, "name": "Monthly", "cadence": "MONTHLY"}

            res[p["id"]] = RawProduct(
                id=p["id"],
                name=p["name"],
                category_id=cid,
                category_path=cat_parents_map.get(cid, [cid]),
                type=norm_type,
                standard_price=float(p.get("standard_price", 0.0)),
                list_price=float(p.get("list_price", 0.0)),
                tags=[],
                is_recurring=is_rec,
                plan_info=plan_info,
            )
        return res

    def _get_category_hierarchies(self, category_ids: List[int]) -> Dict[int, List[int]]:
        hierarchy: Dict[int, List[int]] = {}
        for cid in category_ids:
            curr: Optional[int] = cid
            path = []
            visited = set()
            while curr and curr not in visited:
                visited.add(curr)
                path.append(curr)
                data = self.execute("product.category", "read", [curr], {"fields": ["parent_id"]})
                if data and data[0].get("parent_id"):
                    curr = data[0]["parent_id"][0]
                else:
                    curr = None
            hierarchy[cid] = path
        return hierarchy

    def get_price(self, partner_id: int, product_id: int, qty: float = 1.0) -> Decimal:
        prods = self.get_products([product_id])
        if product_id in prods:
            return Decimal(str(prods[product_id].list_price))
        return Decimal("0.00")

    def get_warehouses(self) -> List[RawWarehouse]:
        whs = self.execute("stock.warehouse", "search_read", [], {"fields": ["id", "name", "code"]})
        return [RawWarehouse(id=w["id"], name=w["name"], code=w["code"]) for w in whs]

    def get_availability(
        self, product_ids: List[int], warehouse_ids: Optional[List[int]] = None
    ) -> Dict[int, Dict[int, int]]:
        domain = [("product_id", "in", product_ids)]
        quants = self.execute(
            "stock.quant",
            "search_read",
            domain,
            {"fields": ["product_id", "warehouse_id", "location_id", "quantity", "reserved_quantity"]},
        )
        res: Dict[int, Dict[int, int]] = {pid: {} for pid in product_ids}
        for q in quants:
            pid = q["product_id"][0]
            avail = int(q.get("quantity", 0)) - int(q.get("reserved_quantity", 0))
            wid = 1
            if q.get("warehouse_id"):
                wid = q["warehouse_id"][0]
            res[pid][wid] = res[pid].get(wid, 0) + max(0, avail)
        return res

    def get_pickings(self, order_id: int) -> List[RawPicking]:
        domain = [("sale_id", "=", order_id)]
        pickings = self.execute(
            "stock.picking",
            "search_read",
            domain,
            {"fields": ["id", "warehouse_id", "state", "scheduled_date", "date_done", "move_ids_without_package"]},
        )
        res: List[RawPicking] = []
        for p in pickings:
            wid = p["warehouse_id"][0] if isinstance(p.get("warehouse_id"), (list, tuple)) else (p.get("warehouse_id") or 1)
            res.append(
                RawPicking(
                    id=p["id"],
                    warehouse_id=wid,
                    state=p["state"],
                    scheduled_date=str(p.get("scheduled_date")),
                    date_done=str(p.get("date_done")) if p.get("date_done") else None,
                )
            )
        return res

    def get_billing_summary(self, order_id: int) -> RawBillingSummary:
        try:
            summary = self.execute("sale.order", "dealflow_billing_summary", [order_id])
            if isinstance(summary, dict):
                return RawBillingSummary(**summary)
            return RawBillingSummary()
        except OdooCapabilityMissing:
            return RawBillingSummary()

    def apply_line_changes(self, order_id: int, changes: List[Dict[str, Any]]) -> bool:
        try:
            return self.execute("sale.order", "dealflow_apply_line_changes", [order_id], {"changes": changes})
        except OdooCapabilityMissing:
            for c in changes:
                lid = c.get("line_id") or c.get("id")
                vals = {}
                if "discount" in c:
                    vals["discount"] = c["discount"]
                if "discount_pct" in c:
                    vals["discount"] = c["discount_pct"]
                if "qty" in c:
                    vals["product_uom_qty"] = c["qty"]
                if "product_uom_qty" in c:
                    vals["product_uom_qty"] = c["product_uom_qty"]
                if vals and lid:
                    self.execute("sale.order.line", "write", [lid], vals)
            return True

    def add_line(self, order_id: int, product_id: int, qty: float = 1.0, discount: float = 0.0) -> int:
        prod = self.get_products([product_id]).get(product_id)
        vals = {
            "order_id": order_id,
            "product_id": product_id,
            "product_uom_qty": qty,
            "discount": discount,
            "price_unit": prod.list_price if prod else 0.0,
        }
        return self.execute("sale.order.line", "create", [vals])

    def set_governance(
        self, order_id: int, approval_state: str, risk_score: float, locked: bool
    ) -> bool:
        try:
            return self.execute("sale.order", "dealflow_set_governance", [order_id], {
                "approval_state": approval_state,
                "risk_score": risk_score,
                "locked": locked,
            })
        except OdooCapabilityMissing:
            vals = {
                settings.ORDER_APPROVAL_STATE_FIELD: approval_state,
                settings.ORDER_RISK_SCORE_FIELD: risk_score,
                settings.ORDER_LOCK_FIELD: locked,
            }
            return self.execute("sale.order", "write", [order_id], vals)

    def confirm(self, order_id: int) -> bool:
        try:
            return self.execute("sale.order", "dealflow_confirm", [order_id])
        except OdooCapabilityMissing:
            return self.execute("sale.order", "action_confirm", [order_id])

    def cancel(self, order_id: int) -> bool:
        return self.execute("sale.order", "action_cancel", [order_id])

    def apply_fulfillment_plan(
        self, order_id: int, allocations: List[Dict[str, Any]]
    ) -> List[int]:
        try:
            return self.execute("sale.order", "dealflow_apply_fulfillment_plan", [order_id], {"allocations": allocations})
        except OdooCapabilityMissing as e:
            raise e

    def register_payment(
        self, invoice_id: int, amount: float, journal_id: Optional[int] = None
    ) -> Dict[str, Any]:
        try:
            return self.execute("account.move", "dealflow_register_payment", [invoice_id], {
                "amount": amount,
                "journal_id": journal_id,
            })
        except OdooCapabilityMissing as e:
            raise e

    def get_users(self) -> List[OdooIdentity]:
        user_ids = self.execute("res.users", "search", [("active", "=", True), ("share", "=", False)])
        user_records = self.execute(
            "res.users",
            "read",
            user_ids,
            {"fields": ["id", "name", "login", "email", "share", "partner_id", "company_id", "groups_id"]},
        )
        results = []
        for u in user_records:
            pid = u["partner_id"][0] if isinstance(u.get("partner_id"), (list, tuple)) else u.get("partner_id")
            cid = u["company_id"][0] if isinstance(u.get("company_id"), (list, tuple)) else (u.get("company_id") or 1)
            results.append(
                OdooIdentity(
                    uid=u["id"],
                    name=u["name"],
                    login=u["login"],
                    email=u.get("email"),
                    groups=[],
                    is_share=bool(u.get("share")),
                    partner_id=pid,
                    company_id=cid,
                )
            )
        return results

    def get_confirmed_orders_for_mining(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        domain: List[Any] = [("state", "in", ["sale", "done"])]
        if since:
            domain.append(("date_order", ">=", since))
        orders = self.execute("sale.order", "search_read", domain, {"fields": ["id", "order_line"]})
        results = []
        for o in orders:
            line_ids = o.get("order_line", [])
            if line_ids:
                lines = self.execute("sale.order.line", "read", line_ids, {"fields": ["product_id", "display_type"]})
                pids = list({l["product_id"][0] for l in lines if l.get("product_id") and not l.get("display_type")})
                if len(pids) >= 2:
                    results.append({"id": o["id"], "product_ids": pids})
        return results
