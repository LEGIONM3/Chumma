import logging
from typing import Optional
from app.core.config import settings
from app.odoo.fake import FakeOdooGateway
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
from app.odoo.xmlrpc import XmlRpcOdooGateway

logger = logging.getLogger("dealflow360.odoo")

_gateway_instance: Optional[OdooGateway] = None


def get_odoo_gateway() -> OdooGateway:
    """Dependency provider returning active Odoo Gateway instance."""
    global _gateway_instance
    if _gateway_instance is None:
        if settings.DEALFLOW_ODOO_MODE.lower() == "fake":
            logger.info("Initializing FakeOdooGateway with in-memory fixtures...")
            _gateway_instance = FakeOdooGateway()
        else:
            logger.info(f"Initializing XmlRpcOdooGateway connecting to {settings.ODOO_URL}...")
            _gateway_instance = XmlRpcOdooGateway()
    return _gateway_instance


def reset_fake_odoo_gateway() -> FakeOdooGateway:
    """Reset the fake gateway fixture for test suites."""
    global _gateway_instance
    fake = FakeOdooGateway()
    _gateway_instance = fake
    return fake


__all__ = [
    "OdooGateway",
    "FakeOdooGateway",
    "XmlRpcOdooGateway",
    "OdooCapabilityMissing",
    "OdooIdentity",
    "RawOrder",
    "RawOrderHeader",
    "RawOrderLine",
    "RawPartner",
    "RawProduct",
    "RawWarehouse",
    "RawPicking",
    "RawBillingSummary",
    "get_odoo_gateway",
    "reset_fake_odoo_gateway",
]

get_gateway = get_odoo_gateway
