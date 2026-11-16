# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

ERP users who create or review financial records, including finance/accounting staff and users with configured approval permissions.

## Product Purpose

Extend the Shree Shashwatraj Agro test ERP with modular AI feature verticals. AI reporting is the first vertical; invoice capture is the next. Success for invoice capture means users can upload invoice documents, inspect and correct extracted details, and confirm creation of the corresponding ERP record.

## Positioning

AI capabilities are separate, add-on modules that can be integrated into client ERPs. Invoice capture uses the built-in AI orchestrator, which must support configurable cloud APIs and locally hosted models.

## Operating Context

The first invoice-capture pilot targets the Shree Shashwatraj Agro ERP. The pilot covers purchase, sales, expense, credit-note, and debit-note documents, uses GST-first extraction, and requires an authorized user to review and confirm extracted details before saving. Suspected duplicates must be flagged and resolved by a user. The original scan is retained separately from the posted ERP record.

## Capabilities and Constraints

- Accept PDFs and phone photos.
- Use configurable ERP roles and permissions for review and confirmation.
- Integrate as a distinct AI module alongside the AI reporting vertical, with a design that can be reused by other ERP clients.
- Lead the Shree Agro pilot with invoice capture in the AI copilot, followed by a structured review and confirmation screen. A dedicated workbench and contextual form actions remain alternate entry points for later evaluation.
- Store scans separately and link them to the corresponding ERP record.

## Evidence on Hand

- `erp-ai-orchestrator/` contains the existing AI reporting orchestrator.
- `erp-ai-workspace-ui/` contains the reporting UI and embeddable web component.
- `ledger-backend/` contains the Shree Agro Express backend, including voucher, credit-note, debit-note, and invoice-file routes/assets.
- `ledger (34).sql` is the ERP database dump used by the reporting project.

## Product Principles

- Keep AI verticals modular and integrable into different client ERPs.
- Keep invoice creation under human review and confirmation.
- Let each client configure its AI model provider, including local and cloud inference.
- Preserve a traceable link to the source scan and surface possible duplicates for review.

## Accessibility & Inclusion

No product-specific accessibility standard has been established.
