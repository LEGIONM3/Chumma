import json
from decimal import Decimal
from typing import Any, Dict, List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow",
    )

    PROJECT_NAME: str = "DealFlow360"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    API_V1_STR: str = "/api/v1"
    PORTAL_API_STR: str = "/api/v1/portal"

    SECRET_KEY: str = "replace-with-a-secure-random-secret-key-dealflow360-min-32-chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720  # 12 hours
    PORTAL_TOKEN_EXPIRE_MINUTES: int = 240  # 4 hours

    DATABASE_URL: str = "postgresql://dealflow:dealflow_secret@localhost:5432/dealflow360"
    TEST_DATABASE_URL: str = "postgresql://dealflow:dealflow_secret@localhost:5432/dealflow360_test"

    CORS_ORIGINS: Union[str, List[str]] = "*"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return v
        return ["*"]

    # Odoo Integration Configuration (§3)
    DEALFLOW_ODOO_MODE: str = "fake"  # 'fake' or 'xmlrpc'
    ODOO_URL: str = "http://localhost:8069"
    ODOO_DB: str = "dealflow_db"
    ODOO_VERSION: str = "17.0"
    ODOO_API_USER: str = "admin"
    ODOO_API_KEY: str = "admin"
    ODOO_DEALFLOW_MODULE: str = "dealflow_odoo"
    ODOO_WEBHOOK_SECRET: str = "dealflow-webhook-hmac-secret-key-min-32-bytes"
    ODOO_PORTAL_TOKEN_SECRET: str = "dealflow-portal-token-hmac-secret-key-min-32-bytes"

    # Custom Field Names on Odoo Models
    PARTNER_TIER_FIELD: str = "x_dealflow_tier"
    PARTNER_TEAM_FIELD: str = "team_id"
    ORDER_DEAL_ID_FIELD: str = "dealflow_deal_id"
    ORDER_APPROVAL_STATE_FIELD: str = "dealflow_approval_state"
    ORDER_RISK_SCORE_FIELD: str = "dealflow_risk_score"
    ORDER_LOCK_FIELD: str = "dealflow_locked"
    ORDER_GLOBAL_DISCOUNT_FIELD: str = ""
    LINE_COST_FIELD: str = "purchase_price"
    SUBSCRIPTION_MODEL: str = "sale.order"
    SUBSCRIPTION_PLAN_FIELD: str = "recurrence_id"

    # Role Mapping from Odoo Security Groups
    ROLE_GROUP_MAP: Union[str, Dict[str, List[str]]] = {
        "ADMIN": ["base.group_system", "dealflow_odoo.group_dealflow_admin"],
        "SALES_MANAGER": ["sales_team.group_sale_manager", "dealflow_odoo.group_dealflow_sales_manager"],
        "SALES_REP": ["sales_team.group_sale_salesman", "dealflow_odoo.group_dealflow_sales_rep"],
        "FINANCE": ["account.group_account_manager", "dealflow_odoo.group_dealflow_finance"],
    }

    @field_validator("ROLE_GROUP_MAP", mode="before")
    @classmethod
    def assemble_role_group_map(cls, v: Union[str, Dict[str, List[str]]]) -> Dict[str, List[str]]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v

    # Governance Engine Defaults (§4.12)
    MANAGER_THRESHOLD: Decimal = Decimal("20.0")
    FINANCE_THRESHOLD: Decimal = Decimal("50.0")
    SINGLE_LINE_FINANCE_PTS: Decimal = Decimal("10.0")

    W_DISCOUNT_MAX: Decimal = Decimal("5.0")
    W_DISCOUNT_WEIGHTED: Decimal = Decimal("2.0")
    W_DISCOUNT_RATIO: Decimal = Decimal("10.0")
    DISCOUNT_CAP: Decimal = Decimal("60.0")

    W_MARGIN: Decimal = Decimal("3.0")
    MARGIN_CAP: Decimal = Decimal("25.0")

    INV_SPLIT_POINTS: Decimal = Decimal("8.0")
    INV_BACKORDER_POINTS: Decimal = Decimal("20.0")
    INV_CAP: Decimal = Decimal("20.0")

    W_APPROVAL_DELAY: Decimal = Decimal("2.0")
    APPROVAL_DELAY_CAP: Decimal = Decimal("10.0")
    NEGOTIATION_PRESSURE_POINTS: Decimal = Decimal("5.0")

    STALLED_DAYS: int = 7
    ANOMALY_FACTOR: Decimal = Decimal("1.5")
    ANOMALY_ABS_POINTS: Decimal = Decimal("5.0")
    ANOMALY_MIN_SAMPLE: int = 5
    ANOMALY_HISTORY_WINDOW: int = 20
    ANOMALY_MIN_DISCOUNT_PCT: Decimal = Decimal("3.0")

    DEFAULT_PROMISED_DELIVERY_DAYS: int = 5
    SLIPPAGE_GRACE_DAYS: int = 0

    REC_MIN_MARGIN_PCT: Decimal = Decimal("15.0")
    REC_MAX: int = 5
    REC_PROMOTION_SCORE: Decimal = Decimal("0.10")
    REC_WEIGHTS: Dict[str, float] = {"co": 0.40, "promo": 0.15, "margin": 0.30, "rel": 0.15}

    HEALTH_THRESHOLDS: Dict[str, int] = {"healthy": 30, "watch": 60}
    COVERAGE_SCORE_TOLERANCE: Decimal = Decimal("0.0")


settings = Settings()
