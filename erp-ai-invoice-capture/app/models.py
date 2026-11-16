from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentType(StrEnum):
    PURCHASE = "purchase"
    SALES = "sales"
    EXPENSE = "expense"
    CREDIT_NOTE = "credit-note"
    DEBIT_NOTE = "debit-note"


class CaptureStatus(StrEnum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    REVIEW = "review"
    BLOCKED = "blocked"
    POSTING = "posting"
    CREATED = "created"
    FAILED = "failed"


class DuplicateResolution(StrEnum):
    UNRESOLVED = "unresolved"
    NOT_DUPLICATE = "not-duplicate"
    EXISTING_RECORD = "existing-record"
    FORCE_CREATE = "force-create"


class FieldValue(BaseModel):
    value: Any = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    corrected: bool = False
    warning: str | None = None


class InvoiceLine(BaseModel):
    product: FieldValue = Field(default_factory=FieldValue)
    product_id: FieldValue = Field(default_factory=FieldValue)
    hsn_code: FieldValue = Field(default_factory=FieldValue)
    quantity: FieldValue = Field(default_factory=FieldValue)
    unit_price: FieldValue = Field(default_factory=FieldValue)
    discount: FieldValue = Field(default_factory=FieldValue)
    gst_rate: FieldValue = Field(default_factory=FieldValue)
    cgst_rate: FieldValue = Field(default_factory=FieldValue)
    sgst_rate: FieldValue = Field(default_factory=FieldValue)
    igst_rate: FieldValue = Field(default_factory=FieldValue)
    total: FieldValue = Field(default_factory=FieldValue)


class Extraction(BaseModel):
    document_type: DocumentType | None = None
    supplier_name: FieldValue = Field(default_factory=FieldValue)
    customer_name: FieldValue = Field(default_factory=FieldValue)
    supplier_gstin: FieldValue = Field(default_factory=FieldValue)
    customer_gstin: FieldValue = Field(default_factory=FieldValue)
    invoice_number: FieldValue = Field(default_factory=FieldValue)
    note_number: FieldValue = Field(default_factory=FieldValue)
    invoice_date: FieldValue = Field(default_factory=FieldValue)
    taxable_value: FieldValue = Field(default_factory=FieldValue)
    cgst_amount: FieldValue = Field(default_factory=FieldValue)
    sgst_amount: FieldValue = Field(default_factory=FieldValue)
    igst_amount: FieldValue = Field(default_factory=FieldValue)
    total_tax: FieldValue = Field(default_factory=FieldValue)
    total_amount: FieldValue = Field(default_factory=FieldValue)
    lines: list[InvoiceLine] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DuplicateCandidate(BaseModel):
    record_id: str | int | None = None
    invoice_number: str | None = None
    document_type: DocumentType | None = None
    total_amount: float | None = None
    reason: str


class CaptureDocument(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: str
    file_name: str
    content_type: str
    size_bytes: int
    scan_path: str
    status: CaptureStatus = CaptureStatus.UPLOADED
    extraction: Extraction | None = None
    duplicates: list[DuplicateCandidate] = Field(default_factory=list)
    duplicate_resolution: DuplicateResolution = DuplicateResolution.UNRESOLVED
    created_record_id: str | int | None = None
    created_record_type: DocumentType | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CaptureDocumentResponse(BaseModel):
    document: CaptureDocument


class ReviewRequest(BaseModel):
    extraction: Extraction


class ConfirmationRequest(BaseModel):
    extraction: Extraction
    duplicate_resolution: DuplicateResolution
    idempotency_key: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def require_duplicate_resolution(self) -> "ConfirmationRequest":
        if self.duplicate_resolution == DuplicateResolution.UNRESOLVED:
            raise ValueError("duplicate_resolution must be resolved before confirmation")
        return self


class ConfirmationResponse(BaseModel):
    document: CaptureDocument
    backend_response: dict[str, Any] | None = None
    scan_linked: bool = False

