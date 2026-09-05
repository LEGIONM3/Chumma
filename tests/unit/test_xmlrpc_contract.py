"""Tests for Odoo XML-RPC gateway contract, capability missing exception handling, and interface conformance."""
import os
import unittest.mock as mock
import xmlrpc.client
import pytest

from app.odoo.interface import OdooCapabilityMissing, OdooGateway
from app.odoo.xmlrpc import XmlRpcOdooGateway


def is_live_odoo_available() -> bool:
    """Check if a live Odoo XML-RPC server is configured and reachable."""
    odoo_url = os.getenv("ODOO_URL")
    if not odoo_url or os.getenv("ODOO_LIVE_TEST", "0") != "1":
        return False
    try:
        common = xmlrpc.client.ServerProxy(f"{odoo_url}/xmlrpc/2/common", timeout=2)
        version_info = common.version()
        return bool(version_info)
    except Exception:
        return False


class TestXmlRpcGatewayContract:
    """Contract tests for XmlRpcOdooGateway compliance with OdooGateway ABC."""

    def test_xmlrpc_gateway_implements_odoo_gateway_interface(self):
        """XmlRpcOdooGateway must be a concrete subclass of OdooGateway."""
        assert issubclass(XmlRpcOdooGateway, OdooGateway)

    def test_xmlrpc_gateway_declares_all_abstract_methods(self):
        """Ensure XmlRpcOdooGateway does not have any uninstantiated abstract methods."""
        abstract_methods = getattr(OdooGateway, "__abstractmethods__", set())
        for method_name in abstract_methods:
            assert hasattr(XmlRpcOdooGateway, method_name), f"Missing method {method_name}"

    def test_missing_method_raises_odoo_capability_missing(self):
        """When an Odoo model does not have a required method, OdooCapabilityMissing is raised."""
        gateway = XmlRpcOdooGateway(
            url="http://mock-odoo:8069",
            db="mock_db",
            api_user="admin",
            api_key="secret",
        )

        with mock.patch.object(gateway, "_cached_uid", 1), \
             mock.patch.object(gateway.models, "execute_kw") as mock_exec:
            mock_exec.side_effect = xmlrpc.client.Fault(
                1, "AttributeError: 'sale.order' object has no attribute 'apply_atomic_line_changes'"
            )

            with pytest.raises(OdooCapabilityMissing) as exc_info:
                gateway.execute("sale.order", "apply_atomic_line_changes", [101])

            assert "apply_atomic_line_changes" in str(exc_info.value)
            assert "sale.order" in str(exc_info.value)
            assert exc_info.value.capability == "apply_atomic_line_changes"

    def test_key_error_fault_raises_odoo_capability_missing(self):
        """When Odoo raises a KeyError on a missing custom module capability, raise OdooCapabilityMissing."""
        gateway = XmlRpcOdooGateway(
            url="http://mock-odoo:8069",
            db="mock_db",
            api_user="admin",
            api_key="secret",
        )

        with mock.patch.object(gateway, "_cached_uid", 1), \
             mock.patch.object(gateway.models, "execute_kw") as mock_exec:
            mock_exec.side_effect = xmlrpc.client.Fault(
                1, "KeyError: 'dealflow_odoo.action_lock_order'"
            )

            with pytest.raises(OdooCapabilityMissing) as exc_info:
                gateway.execute("sale.order", "action_lock_order", [101])

            assert "action_lock_order" in str(exc_info.value)
            assert exc_info.value.capability == "action_lock_order"

    def test_standard_xmlrpc_fault_reraised_when_not_capability_related(self):
        """Standard faults (such as ValidationError, UserError, DB constraint) are reraised directly."""
        gateway = XmlRpcOdooGateway(
            url="http://mock-odoo:8069",
            db="mock_db",
            api_user="admin",
            api_key="secret",
        )

        with mock.patch.object(gateway, "_cached_uid", 1), \
             mock.patch.object(gateway.models, "execute_kw") as mock_exec:
            mock_exec.side_effect = xmlrpc.client.Fault(2, "ValidationError: Credit limit exceeded")

            with pytest.raises(xmlrpc.client.Fault) as exc_info:
                gateway.execute("sale.order", "action_confirm", [101])

            assert "Credit limit exceeded" in str(exc_info.value)

    def test_graceful_degradation_on_missing_billing_capability(self):
        """When Odoo does not implement custom get_billing_summary, gateway falls back gracefully."""
        gateway = XmlRpcOdooGateway(
            url="http://mock-odoo:8069",
            db="mock_db",
            api_user="admin",
            api_key="secret",
        )

        with mock.patch.object(gateway, "_cached_uid", 1), \
             mock.patch.object(gateway, "execute") as mock_exec, \
             mock.patch.object(gateway, "get_sale_order") as mock_order:
            def exec_side_effect(model, method, *args, **kwargs):
                if method == "get_billing_summary":
                    raise OdooCapabilityMissing("get_billing_summary", "Missing method")
                if method == "search_read" and model == "account.move":
                    return []
                if method == "search_read" and model == "sale.subscription":
                    return []
                return []

            mock_exec.side_effect = exec_side_effect
            mock_order.return_value = mock.MagicMock(lines=[])

            summary = gateway.get_billing_summary(order_id=123)
            assert summary is not None
            assert isinstance(summary.invoices, list)
            assert isinstance(summary.subscriptions, list)


@pytest.mark.odoo
@pytest.mark.skipif(
    not is_live_odoo_available(),
    reason="Live Odoo instance not reachable or ODOO_LIVE_TEST != 1",
)
class TestLiveOdooXmlRpcContract:
    """Integration contract tests executing against a live Odoo 17/18 instance."""

    def test_live_odoo_connection_and_auth(self):
        """Verify authentication with live Odoo instance."""
        url = os.environ["ODOO_URL"]
        db = os.getenv("ODOO_DB", "dealflow_db")
        user = os.getenv("ODOO_API_USER", "admin")
        key = os.getenv("ODOO_API_KEY", "admin")

        gateway = XmlRpcOdooGateway(url=url, db=db, api_user=user, api_key=key)
        identity = gateway.authenticate(user, key)

        assert identity.uid > 0
        assert identity.name
        assert identity.email
