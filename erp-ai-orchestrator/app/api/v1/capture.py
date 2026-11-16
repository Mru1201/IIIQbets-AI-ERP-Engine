"""Document extraction endpoint for the separate invoice-capture service.

This endpoint performs inference only. It never opens the reporting database and
never creates or mutates ERP records.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.jwt_handler import get_current_user, get_tenant_id
from app.inference.llm_client import LLMClient, LLMError

router = APIRouter(prefix="/capture", tags=["capture-extraction"])


class ExtractionRequest(BaseModel):
    tenant_id: str = "default"
    file_name: str
    content_type: str
    source_base64: str = Field(min_length=1)
    document_type: str | None = None


class ExtractionResponse(BaseModel):
    status: str
    tenant_id: str
    extraction: dict[str, Any]


@router.post("/extract", response_model=ExtractionResponse)
async def extract_invoice(
    payload: ExtractionRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
    auth_tenant_id: str = Depends(get_tenant_id),
) -> ExtractionResponse:
    effective_tenant_id = auth_tenant_id if auth_tenant_id != "default" else payload.tenant_id
    client = getattr(request.app.state, "llm_client", None) or LLMClient()
    try:
        extraction = await client.generate_invoice_extraction_async(
            tenant_id=effective_tenant_id,
            file_name=payload.file_name,
            content_type=payload.content_type,
            source_base64=payload.source_base64,
            document_type=payload.document_type,
        )
    except LLMError as exc:
        status_code = exc.status_code if exc.status_code in {401, 403, 429} else 502
        raise HTTPException(
            status_code=status_code,
            detail=f"LLM extraction failed ({client.settings.llm_provider}): {exc}",
        ) from exc
    return ExtractionResponse(status="success", tenant_id=effective_tenant_id, extraction=extraction)

