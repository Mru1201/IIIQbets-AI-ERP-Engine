import base64
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware

from .adapter import AdapterConfigurationError, ShreeERPAdapter
from .config import Settings, get_settings
from .models import (
    CaptureDocumentResponse,
    CaptureStatus,
    ConfirmationRequest,
    ConfirmationResponse,
    DocumentType,
    DuplicateResolution,
    ReviewRequest,
)
from .storage import CaptureStore

app = FastAPI(title="ERP AI Invoice Capture", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
settings = get_settings()
store = CaptureStore(settings.scan_dir)


def current_context(
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
    x_role: Annotated[str | None, Header()] = None,
) -> tuple[str, str, str, str | None]:
    return x_tenant_id or settings.tenant_id, x_user_id or "unknown", (x_role or "").lower(), authorization


def authorized_for_confirmation(role: str) -> bool:
    return role in settings.confirmation_roles


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "service": "invoice-capture"}


@app.post("/api/v1/capture/documents", response_model=CaptureDocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    context: tuple[str, str, str, str | None] = Depends(current_context),
) -> CaptureDocumentResponse:
    tenant_id, _, _, _ = context
    if file.content_type not in {"application/pdf", "image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="Only PDF, JPEG, PNG, and WebP files are supported")
    payload = await file.read()
    if len(payload) > settings.max_file_size_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds the configured capture limit")
    document = store.save_scan(tenant_id, file.filename or "invoice", file.content_type, payload)
    return CaptureDocumentResponse(document=document)


@app.post("/api/v1/capture/documents/{document_id}/review", response_model=CaptureDocumentResponse)
async def review_document(
    document_id: UUID,
    request: ReviewRequest,
    context: tuple[str, str, str, str | None] = Depends(current_context),
) -> CaptureDocumentResponse:
    tenant_id, _, _, _ = context
    try:
        document = store.get(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if document.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Capture document not found")
    document.extraction = request.extraction
    document.status = CaptureStatus.REVIEW
    document.updated_at = datetime.now(timezone.utc)
    store.save(document)
    return CaptureDocumentResponse(document=document)


@app.post("/api/v1/capture/documents/{document_id}/extract", response_model=CaptureDocumentResponse)
async def extract_document(
    document_id: UUID,
    context: tuple[str, str, str, str | None] = Depends(current_context),
) -> CaptureDocumentResponse:
    tenant_id, _, _, authorization = context
    try:
        document = store.get(document_id)
        payload = store.read_scan(document_id)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if document.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Capture document not found")
    document.status = CaptureStatus.EXTRACTING
    store.save(document)
    headers = {"X-Tenant-ID": tenant_id, "Content-Type": "application/json"}
    if authorization:
        headers["Authorization"] = authorization
    request_payload = {
        "tenant_id": tenant_id,
        "file_name": document.file_name,
        "content_type": document.content_type,
        "source_base64": base64.b64encode(payload).decode("ascii"),
    }
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_s) as client:
            response = await client.post(
                f"{settings.orchestrator_url.rstrip('/')}/api/v1/capture/extract",
                json=request_payload,
                headers=headers,
            )
            response.raise_for_status()
            extraction = response.json().get("extraction")
        document.extraction = extraction
        document.status = CaptureStatus.REVIEW
        store.save(document)
    except httpx.HTTPStatusError as exc:
        document.status = CaptureStatus.FAILED
        store.save(document)
        try:
            upstream_detail = exc.response.json().get("detail")
        except ValueError:
            upstream_detail = exc.response.text[:300]
        raise HTTPException(
            status_code=502,
            detail=f"Extraction failed ({exc.response.status_code}): {upstream_detail or 'orchestrator rejected the request'}",
        ) from exc
    except httpx.TimeoutException as exc:
        document.status = CaptureStatus.FAILED
        store.save(document)
        raise HTTPException(
            status_code=504,
            detail="Extraction failed: the orchestrator timed out while waiting for Gemini",
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        document.status = CaptureStatus.FAILED
        store.save(document)
        raise HTTPException(status_code=502, detail=f"Extraction failed before a result was returned: {exc}") from exc
    return CaptureDocumentResponse(document=document)


@app.post("/api/v1/capture/documents/{document_id}/confirm", response_model=ConfirmationResponse)
async def confirm_document(
    document_id: UUID,
    request: ConfirmationRequest,
    context: tuple[str, str, str, str | None] = Depends(current_context),
) -> ConfirmationResponse:
    tenant_id, user_id, role, authorization = context
    if not authorized_for_confirmation(role):
        raise HTTPException(status_code=403, detail="This role cannot confirm invoice capture")
    try:
        document = store.get(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if document.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Capture document not found")
    existing = store.idempotency.get((tenant_id, request.idempotency_key))
    if existing is not None:
        return ConfirmationResponse(document=store.get(existing), scan_linked=False)
    if document.duplicates and request.duplicate_resolution == DuplicateResolution.UNRESOLVED:
        raise HTTPException(status_code=409, detail="Resolve suspected duplicate before confirmation")
    document.extraction = request.extraction
    document.duplicate_resolution = request.duplicate_resolution
    document.status = CaptureStatus.POSTING
    store.save(document)
    adapter = ShreeERPAdapter(settings)
    try:
        backend_response = await adapter.create(
            request.extraction.document_type or DocumentType.PURCHASE,
            request.extraction,
            authorization=authorization,
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            idempotency_key=request.idempotency_key,
        )
    except (AdapterConfigurationError, RuntimeError) as exc:
        document.status = CaptureStatus.FAILED
        store.save(document)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    document.status = CaptureStatus.CREATED
    document.created_record_id = backend_response.get("voucherId") or backend_response.get("id") or backend_response.get("creditNoteId")
    document.created_record_type = request.extraction.document_type
    document.updated_at = datetime.now(timezone.utc)
    store.idempotency[(tenant_id, request.idempotency_key)] = document.id
    store.save(document)
    return ConfirmationResponse(document=document, backend_response=backend_response, scan_linked=True)

