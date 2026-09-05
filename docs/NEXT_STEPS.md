# DealFlow360 — Production Deployment & Next Steps Guide

This document outlines the transition from local development and fake mode to high-availability production deployment with a live Odoo 18 enterprise/community cluster.

---

## 1. Production Deployment Guide

### Architecture Topology
```
                     Internet / Load Balancer (Cloudflare / ALB)
                                      │
                         ┌────────────┴────────────┐
                         │   NGINX Reverse Proxy   │
                         │   SSL Termination / WAF │
                         └────────────┬────────────┘
                                      │
             ┌────────────────────────┼────────────────────────┐
             ▼                        ▼                        ▼
     DealFlow360 API          DealFlow360 API          DealFlow360 Worker
    (Uvicorn Worker 1)       (Uvicorn Worker 2)         (Celery / Redis)
             │                        │                        │
             └───────────┬────────────┴────────────────────────┘
                         │
        ┌────────────────┴────────────────┬────────────────────────┐
        ▼                                 ▼                        ▼
PostgreSQL 16 Multi-AZ              Redis 7 Cluster          Live Odoo 18 Cluster
(DealFlow Governance State)     (Cache & Task Queue)     (Transactional ERP)
```

### Production Environment Variables (`.env.production`)
```ini
# Application Configuration
PROJECT_NAME="DealFlow360"
ENVIRONMENT="production"
DEBUG=false
SECRET_KEY="<generate-64-character-cryptographic-random-string>"
ALGORITHM="HS256"
ACCESS_TOKEN_EXPIRE_MINUTES=720
PORTAL_TOKEN_EXPIRE_MINUTES=240

# Database Configuration (PostgreSQL 15+)
DATABASE_URL="postgresql+psycopg2://dealflow_user:secure_pwd@pg-primary.prod:5432/dealflow360?sslmode=require"
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=30

# Redis & Celery
REDIS_URL="redis://:redis_pwd@redis.prod:6379/0"
CELERY_BROKER_URL="redis://:redis_pwd@redis.prod:6379/1"
CELERY_RESULT_BACKEND="redis://:redis_pwd@redis.prod:6379/2"

# Live Odoo 18 XML-RPC Configuration
DEALFLOW_ODOO_MODE="xmlrpc"
ODOO_URL="https://erp.company.com"
ODOO_DB="production_odoo"
ODOO_API_USER="dealflow_service_account"
ODOO_API_KEY="<odoo-generated-api-secret-key>"
ODOO_VERSION="18.0"

# Security & Webhook Signatures
ODOO_WEBHOOK_SECRET="<generate-32-byte-hmac-secret-key>"
ODOO_PORTAL_TOKEN_SECRET="<generate-32-byte-portal-secret-key>"
DEALFLOW_SIGNING_KEY="<generate-32-byte-outbound-secret-key>"
```

### Running with Gunicorn & Uvicorn Workers
```bash
gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile - \
  --worker-connections 1000 \
  --max-requests 5000 \
  --max-requests-jitter 500
```

---

## 2. Celery / Background Worker Migration Strategy

In the current version, periodic health decay scoring, co-purchase mining, and alert updates can execute synchronously or via lightweight background tasks. For production at scale (>10,000 active deals), migrate to Celery:

### Task Breakdown
1. **Health Decay & Stalled Deal Evaluation (`tasks.evaluate_pipeline_health`)**:
   - Schedule: Cron every 15 minutes (`*/15 * * * *`).
   - Batches active deals in chunks of 500.
   - Computes inactivity decay, updates `deal_health_snapshots`, and triggers alerts.
2. **Co-Purchase Association Rule Mining (`tasks.mine_copurchase_rules`)**:
   - Schedule: Nightly at 02:00 UTC (`0 2 * * *`).
   - Analyzes historical Odoo sales orders (`account.move.line` / `sale.order.line`).
   - Updates `product_recommendations` table with fresh support/confidence metrics.
3. **Outbox Event Publisher (`tasks.flush_outbox_queue`)**:
   - Schedule: Continuous polling every 5 seconds.
   - Reads pending outbox entries, executes HMAC signing, dispatches to webhooks, and manages exponential backoff retries.

### Example Celery Task Definition
```python
# app/workers/tasks.py
from celery import Celery
from app.core.config import settings
from app.db.session import SessionLocal
from app.services import health_service

celery_app = Celery("dealflow_tasks", broker=settings.CELERY_BROKER_URL)

@celery_app.task(bind=True, max_retries=3)
def run_pipeline_health_scan(self):
    db = SessionLocal()
    try:
        updated_count = health_service.scan_all_deals_health(db)
        return {"status": "success", "deals_scanned": updated_count}
    except Exception as exc:
        db.rollback()
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
```

---

## 3. Live Odoo 18 Deployment & Addon Installation

To connect DealFlow360 to a live Odoo 18 instance:

### Step 1: Install `dealflow_odoo` Addon in Odoo
1. Copy the `dealflow_odoo/` directory into your Odoo server's `addons/` or `extra-addons/` directory.
2. Ensure permissions allow the Odoo process user to read the files.
3. Restart Odoo server and update the app list:
   ```bash
   odoo-bin -c /etc/odoo/odoo.conf -u dealflow_odoo -d production_odoo --stop-after-init
   ```
4. Log into Odoo as Administrator, navigate to **Apps**, search for `DealFlow360 Odoo Integration`, and click **Install**.

### Step 2: Configure Dedicated Technical Service User
1. Go to **Settings -> Users & Companies -> Users**.
2. Create a technical user: `dealflow_service_account`.
3. Assign groups:
   - **Sales / Administrator**
   - **Inventory / Administrator**
   - **Accounting / Invoicing Administrator**
   - **DealFlow / Administrator**
4. Under the **API Keys** tab, generate a new API Key. Copy this into `ODOO_API_KEY`.

### Step 3: Configure Outbound Webhooks in Odoo
Configure an Automated Action or Webhook in Odoo:
- **Model**: `sale.order`
- **Trigger**: On Update / State Change
- **URL**: `https://api.dealflow.company.com/api/v1/events/odoo`
- **Headers**:
  - `Content-Type`: `application/json`
  - `X-Odoo-Signature`: `<ODOO_WEBHOOK_SECRET>`

---

## 4. Observability & Monitoring

### Structured JSON Logging
All HTTP requests and audit events emit structured JSON to stdout, easily consumed by Datadog, Grafana Loki, or AWS CloudWatch:
```json
{
  "timestamp": "2026-09-05T12:00:00Z",
  "method": "POST",
  "path": "/api/v1/deals/create",
  "status_code": 200,
  "duration_ms": 18.4,
  "client_ip": "10.0.1.45"
}
```

### Prometheus Metrics Endpoint (`/metrics`)
Enable the Prometheus exporter to monitor:
- `dealflow_http_requests_total{method, path, status}`: Request throughput.
- `dealflow_http_duration_seconds`: Latency percentiles (p50, p95, p99).
- `dealflow_deals_active_count{status}`: Total deals currently in negotiation/approval.
- `dealflow_approval_invalidations_total`: Frequency of rogue counter-offer invalidations.
- `dealflow_odoo_xmlrpc_latency_seconds`: RPC round-trip response times.

---

## 5. Future Roadmap & Enhancements

1. **Multi-Currency Hedging**: Real-time FX exposure calculations on cross-border deals with dynamic margin buffering.
2. **Automated Price Elasticity**: Machine learning model forecasting customer counter-offer probability based on historical discount sensitivity.
3. **AI Voice & Email Assistant**: Generative agent that drafts counter-negotiation responses and syncs quotation discussions from rep emails.
