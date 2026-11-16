# AI Invoice Capture

FORM: seed:92d34e38d565cb43

## Job and audience

Finance and authorized ERP users at Shree Shashwatraj Agro need to turn invoice documents into accurate ERP transactions without retyping every field. The surface uses the existing AI copilot as the starting point, then presents a structured review before any write.

## Outcome and proof

Users upload a PDF or phone photo, identify its transaction type, review the extracted header, tax, and line-item details, resolve warnings and likely duplicates, and confirm creation. The proof of success is a transaction created through the Shree ERP backend and linked to its separately retained source scan.

## Selected direction

- Keep the existing AI reporting workspace's enterprise UI language and add invoice capture as a distinct AI vertical in its navigation.
- The first viewport pairs a guided invoice assistant on the left with a structured confirmation panel on the right. The assistant helps users upload, classify, and correct; the structured panel owns exact field review and the final confirmation action.
- The signature interaction is the handoff from conversational upload and classification to an always-visible, editable confirmation panel with field-level warnings.
- Sequence: upload one or a small batch; classify each document; extract and normalize fields; flag uncertain values and possible duplicates; let an authorized user correct and confirm each transaction; create it immediately through the ERP backend; retain and link the original scan separately.
- Preserve modular API and UI boundaries so the vertical can integrate with other client ERPs. Use the built-in AI orchestrator for configurable local models and cloud APIs.

## Scope and boundaries

- Pilot Shree Shashwatraj Agro and cover purchase, sales, expense, credit-note, and debit-note documents.
- Accept PDFs and phone photos, singly or in small batches.
- Prioritize GST invoice fields, including supplier/customer identity and GSTIN, invoice number/date, taxable value, CGST/SGST/IGST, totals, and line items. Support English, Hindi, Kannada, and Telugu; send uncertain extraction to human correction.
- Respect configured ERP roles for review and confirmation. Confirmation creates the backend transaction immediately; no AI-only auto-save.
- Flag likely duplicates and require a user to resolve the warning before creation.
- Keep the source file in separate scan storage and link it to the saved transaction. Storage mechanism and retention period remain open decisions.
- Compare the workbench and contextual-form patterns as future entry-point alternatives; the pilot's leading experience is embedded in the AI copilot.
- Do not redesign or change the AI reporting vertical as part of this surface.

## States and ranges

- Empty/upload-ready, file validation, upload progress, extraction progress, unsupported or unreadable document, field-level uncertainty, duplicate warning, correction, confirmation, backend save, success, and recoverable save error.
- Batch progress and review remain per document so one failure does not hide the status of other files.
- Maximum batch size, expected document page count, and maximum line-item count are not yet specified.

## Interaction and layout

- Web surface, using familiar ERP navigation and controls.
- Keep the conversation and structured field review visible together on desktop; on narrow screens, preserve the review/confirm path with a clear transition from conversation to fields.
- Make transaction type, source file, field confidence/warnings, possible duplicate, totals, and the final confirmation action easy to find.
- Clearly distinguish extracted values from values corrected by the user, and show when the backend save has completed.

## Constraints and open decisions

- Map each transaction type to the Shree backend's exact write endpoint, required fields, tax rules, and posting behavior before implementation.
- Map configured ERP roles to upload, correction, and confirmation permissions.
- Decide source-file storage, retention, access control, and cleanup policy.
- Set a supported batch cap and document size/page limits.
- Confirm extraction model/provider selection and configuration scope per client or deployment.
- Align the final navigation and shell with the host Shree ERP UI; that host frontend is not present in this workspace, so the current AI reporting UI is the available visual reference.
