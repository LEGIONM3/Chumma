# DealFlow360 Frontend

Enterprise Sales Governance and Decision-Support Platform for Odoo 18.

## Quickstart

### Prerequisites
- Node.js 18+
- npm 9+

### Installation
```bash
cd d:/odoo/frontend
npm install
```

### Running Locally (Development with MSW Mock Mode)
By default, Vite runs with the MSW mock engine enabled when the backend is offline:
```bash
npm run dev
```
The application will launch on `http://localhost:5173`.

### Connecting to Live FastAPI Backend
Set the backend URL environment variable:
```bash
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_USE_MOCK=false
npm run dev
```

### Running Tests

#### Unit Tests (Memory-Governed Single Worker)
```bash
npm run test
```
Vitest is strictly configured with single-worker execution (`--pool=forks --poolOptions.forks.maxForks=1`) to prevent system memory overload.

#### Typecheck
```bash
npm run typecheck
```

#### Production Build
```bash
npm run build
```

#### End-to-End Tests (Playwright)
```bash
npm run e2e
```

## Demo Credentials

### Internal Sales Operations
| Role | Email | Password | Access |
| :--- | :--- | :--- | :--- |
| **Sales Rep** | `rep1@dealflow.test` | `password` | Rep Dashboard, Quotations, Counter-offers |
| **Sales Manager** | `manager1@dealflow.test` | `password` | Control Tower, Approval Queue, Overrides |
| **Finance** | `finance@dealflow.test` | `password` | Operations, High-Risk Approvals, Billing |
| **Admin** | `admin@dealflow.test` | `password` | Full Platform & System Configuration |

### Zero-Trust Customer Portal
- Magic Link: `/portal/verify?token=magic_token_acme_buyer`
- Acme Buyer Email: `buyer@acme.test`
