# ERP AI Invoice Capture

Standalone capture API for PDF/photo invoice review and human-confirmed posting.

The service owns capture state and source-scan storage. It does not import or use
the reporting SQL executor. ERP writes are delegated to the configured Shree
adapter only after server-side confirmation checks pass.

## Demo execution

The demo uses four processes:

1. Shree Ledger backend on port `5000`
2. AI orchestrator on port `8000`
3. Invoice capture API on port `8010`
4. Shree frontend on port `3000`

Start the existing ERP backend first:

```bash
cd ledger-backend
npm install
npm start
```

The backend must be able to reach its configured MySQL database. The capture
adapter posts confirmed records through the existing `/transaction`,
`/expensive`, `/api/create-credit-note`, and `/api/create-debit-note` routes.

Start the orchestrator:

```bash
cd erp-ai-orchestrator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Start the capture API in another terminal:

```bash
cd erp-ai-invoice-capture
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export CAPTURE_SHREE_BASE_URL=http://localhost:5000
export CAPTURE_ORCHESTRATOR_URL=http://localhost:8000
uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Start the host frontend:

```bash
cd shreeshashwatraj-frontend
npm install
REACT_APP_AI_API_URL=http://localhost:8000 \
REACT_APP_CAPTURE_API_URL=http://localhost:8010 \
npm start
```

The host page imports the generated web component from
`src/Component/Panels/Admin/AdminReports/erp-ai-reporter.umd.js`. Rebuild it
after UI changes:

```bash
cd erp-ai-workspace-ui
npm run build:wc
cp dist/wc/erp-ai-reporter.umd.js \
  ../shreeshashwatraj-frontend/src/Component/Panels/Admin/AdminReports/erp-ai-reporter.umd.js
```

Open the existing Reports page, select **AI-Reporting**, then switch to
**Invoice Capture**. Upload a PDF, JPEG, PNG, or WebP invoice, review the
extracted values, select the document type, and confirm with an ERP role
configured in `CAPTURE_CONFIRM_ROLES` (default: `admin`).

Configuration uses the `CAPTURE_` prefix. Scans are written to
`./capture-scans` by default. The current demo keeps capture state in memory;
restart the capture service to clear it. Duplicate candidates and durable scan
links require backend-specific persistence configuration before production use.

For a complete browser walkthrough, including health checks, sample upload
commands, confirmation payloads, and common provider or ledger failures, use
the local `ERP-Invoice-Capture-Demo-Runbook.pdf` generated for the demo.

