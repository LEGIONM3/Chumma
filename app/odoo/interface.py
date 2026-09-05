from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional


class OdooCapabilityMissing(Exception):
    def __init__(self, method_name: str, message: Optional[str] = None):
        self.method_name = method_name
        self.message = message or f"Required Odoo method or capability '{method_name}' is not implemented on the Odoo instance."
        super().__init__(self.message)

    @property
    def capability(self) -> str:
        return self.method_name


@dataclass
class OdooIdentity:
    uid: int
    name: str
    login: str
    email: Optional[str]
    groups: List[str]
    is_share: bool
    partner_id: Optional[int]
    company_id: int


@dataclass
class RawOrderLine:
    id: int
    product_id: int
    product_name: str
    category_id: int
    category_path: List[int]
    product_type: str  # 'STOCKABLE', 'SERVICE', 'SUBSCRIPTION'
    qty: float
    price_unit: float
    discount_pct: float
    unit_cost: float
    tax_rate_pct: float
    is_recurring: bool = False
    plan_name: Optional[str] = None


@dataclass
class RawOrderHeader:
    id: int
    name: str
    partner_id: int
    partner_name: str
    state: str  # 'draft', 'sent', 'sale', 'cancel'
    currency: str
    user_id: int
    team_id: Optional[int]
    company_id: int
    validity_date: Optional[str]
    commitment_date: Optional[str]
    write_date: Optional[str]
    amount_untaxed: float
    amount_tax: float
    amount_total: float
    dealflow_deal_id: Optional[str] = None
    dealflow_approval_state: Optional[str] = None
    dealflow_risk_score: Optional[float] = None
    dealflow_locked: Optional[bool] = None


@dataclass
class RawOrder:
    header: RawOrderHeader
    lines: List[RawOrderLine] = field(default_factory=list)


@dataclass
class RawPartner:
    id: int
    name: str
    tier_code: Optional[str]
    payment_term_days: int
    email: Optional[str]
    portal_user_ids: List[int] = field(default_factory=list)

    @property
    def odoo_partner_id(self) -> int:
        return self.id


@dataclass
class RawProduct:
    id: int
    name: str
    category_id: int
    category_path: List[int]
    type: str
    standard_price: float
    list_price: float
    tags: List[str] = field(default_factory=list)
    is_recurring: bool = False
    plan_info: Optional[Dict[str, Any]] = None


@dataclass
class RawWarehouse:
    id: int
    name: str
    code: str


@dataclass
class RawPicking:
    id: int
    warehouse_id: int
    state: str
    scheduled_date: Optional[str]
    date_done: Optional[str]
    lines: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RawBillingSummary:
    one_time_lines: List[int] = field(default_factory=list)
    recurring_lines: List[int] = field(default_factory=list)
    invoices: List[Dict[str, Any]] = field(default_factory=list)
    payments: List[Dict[str, Any]] = field(default_factory=list)
    subscriptions: List[Dict[str, Any]] = field(default_factory=list)
    schedule: List[Dict[str, Any]] = field(default_factory=list)


class OdooGateway(ABC):
    @abstractmethod
    def authenticate(self, login: str, password: str) -> OdooIdentity:
        """Authenticate user with Odoo and return identity and security groups."""
        pass

    @abstractmethod
    def get_sale_order(self, order_id: int) -> RawOrder:
        """Fetch raw sale order and lines from Odoo."""
        pass

    @abstractmethod
    def list_sale_orders(
        self,
        since_write_date: Optional[str] = None,
        ids: Optional[List[int]] = None,
        partner_id: Optional[int] = None,
        state: Optional[str] = None,
    ) -> List[RawOrderHeader]:
        """List sale order headers matching filters."""
        pass

    @abstractmethod
    def create_order(
        self,
        partner_id: int,
        user_id: int = 4,
        team_id: Optional[int] = 1,
        currency: str = "INR",
        lines: Optional[List[Dict[str, Any]]] = None,
    ) -> RawOrder:
        """Create a new sale order in draft state."""
        pass

    @abstractmethod
    def get_partner(self, partner_id: int) -> RawPartner:
        """Fetch customer partner details."""
        pass

    def get_customer_by_email(self, email: str) -> Optional[RawPartner]:
        """Fetch customer partner by email (optional override)."""
        return None

    @abstractmethod
    def get_products(self, product_ids: List[int]) -> Dict[int, RawProduct]:
        """Fetch multiple product records."""
        pass

    @abstractmethod
    def get_price(self, partner_id: int, product_id: int, qty: float = 1.0) -> Decimal:
        """Resolve unit price for customer pricelist and quantity."""
        pass

    @abstractmethod
    def get_warehouses(self) -> List[RawWarehouse]:
        """Fetch all warehouses."""
        pass

    @abstractmethod
    def get_availability(
        self, product_ids: List[int], warehouse_ids: Optional[List[int]] = None
    ) -> Dict[int, Dict[int, int]]:
        """Fetch stock availability (quant.quantity - reserved) per product per warehouse."""
        pass

    @abstractmethod
    def get_pickings(self, order_id: int) -> List[RawPicking]:
        """Fetch stock pickings generated for order."""
        pass

    @abstractmethod
    def get_billing_summary(self, order_id: int) -> RawBillingSummary:
        """Fetch invoices, payments, subscriptions, and schedule for order."""
        pass

    @abstractmethod
    def apply_line_changes(self, order_id: int, changes: List[Dict[str, Any]]) -> bool:
        """Apply atomic line discount, quantity, or removal updates."""
        pass

    @abstractmethod
    def add_line(self, order_id: int, product_id: int, qty: float = 1.0, discount: float = 0.0) -> int:
        """Append a new sale order line and return its ID."""
        pass

    @abstractmethod
    def set_governance(
        self, order_id: int, approval_state: str, risk_score: float, locked: bool
    ) -> bool:
        """Write DealFlow overlay governance fields to Odoo."""
        pass

    @abstractmethod
    def confirm(self, order_id: int) -> bool:
        """Confirm sale order in Odoo respecting governance locks."""
        pass

    @abstractmethod
    def cancel(self, order_id: int) -> bool:
        """Cancel sale order in Odoo."""
        pass

    @abstractmethod
    def apply_fulfillment_plan(
        self, order_id: int, allocations: List[Dict[str, Any]]
    ) -> List[int]:
        """Execute warehouse split plan creating pickings and backorders in Odoo."""
        pass

    @abstractmethod
    def register_payment(
        self, invoice_id: int, amount: float, journal_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Register payment against invoice."""
        pass

    @abstractmethod
    def get_users(self) -> List[OdooIdentity]:
        """Fetch active Odoo internal users for user mirror synchronization."""
        pass

    @abstractmethod
    def get_confirmed_orders_for_mining(
        self, since: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch confirmed sale orders with product sets for co-purchase mining."""
        pass
