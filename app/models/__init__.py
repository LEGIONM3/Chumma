from app.db.base import Base
from app.models.approval import ApprovalAction, ApprovalRequest
from app.models.audit import AuditEvent
from app.models.cross_cutting import AppSetting, Notification, NumberSequence, ProcessedEvent
from app.models.deal import Deal
from app.models.enums import (
    AlertStatus,
    AlertType,
    ApprovalActionType,
    ApprovalLevel,
    ApprovalRequestStatus,
    ApprovalState,
    AuditEventType,
    DealStatus,
    FulfillmentPlanStatus,
    HealthStatus,
    MaterialChangeKind,
    NegotiationRequestStatus,
    NegotiationRequestType,
    NextBestActionType,
    RecommendationStatus,
    RecommendationType,
    RiskFactorType,
    RiskSeverity,
    Role,
    TriggerType,
)
from app.models.fulfillment import FulfillmentPlan, FulfillmentPlanLine, WarehouseProfile
from app.models.health import DealAlert, DealHealthSnapshot, RepDiscountStats
from app.models.identity import DealflowUser, MagicLinkToken, User
from app.models.negotiation import NegotiationChange, NegotiationRequest, PortalComment
from app.models.policy import CustomerTier, CustomerTierAssignment, DiscountPolicy
from app.models.recommendation import Recommendation, RecommendationRule
from app.models.risk import RiskAssessment, RiskFactor
from app.odoo.outbox import OdooOutbox

__all__ = [
    "Base",
    "Role",
    "DealStatus",
    "ApprovalState",
    "ApprovalLevel",
    "ApprovalRequestStatus",
    "ApprovalActionType",
    "RiskSeverity",
    "RiskFactorType",
    "HealthStatus",
    "TriggerType",
    "MaterialChangeKind",
    "NegotiationRequestType",
    "NegotiationRequestStatus",
    "FulfillmentPlanStatus",
    "RecommendationType",
    "RecommendationStatus",
    "AlertType",
    "AlertStatus",
    "NextBestActionType",
    "AuditEventType",
    "DealflowUser",
    "User",
    "MagicLinkToken",
    "CustomerTier",
    "CustomerTierAssignment",
    "DiscountPolicy",
    "Deal",
    "RiskAssessment",
    "RiskFactor",
    "ApprovalRequest",
    "ApprovalAction",
    "NegotiationRequest",
    "NegotiationChange",
    "PortalComment",
    "FulfillmentPlan",
    "FulfillmentPlanLine",
    "WarehouseProfile",
    "RecommendationRule",
    "Recommendation",
    "DealHealthSnapshot",
    "DealAlert",
    "RepDiscountStats",
    "AuditEvent",
    "ProcessedEvent",
    "Notification",
    "AppSetting",
    "NumberSequence",
    "OdooOutbox",
]
