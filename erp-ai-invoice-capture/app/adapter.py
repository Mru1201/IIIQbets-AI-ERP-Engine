from typing import Any

import httpx

from .config import Settings
from .models import DocumentType, Extraction


class AdapterConfigurationError(RuntimeError):
    pass


class ShreeERPAdapter:
    """Explicit route adapter; no SQL or guessed duplicate endpoint is used."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    def _headers(self, authorization: str | None, tenant_id: str, user_id: str, role: str) -> dict[str, str]:
        headers = {
            "X-Tenant-ID": tenant_id,
            "X-User-ID": user_id,
            "X-Role": role,
            "Content-Type": "application/json",
        }
        if authorization:
            headers["Authorization"] = authorization
        return headers

    async def create(
        self,
        document_type: DocumentType,
        extraction: Extraction,
        *,
        authorization: str | None,
        tenant_id: str,
        user_id: str,
        role: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = self._map_payload(document_type, extraction, idempotency_key)
        if document_type == DocumentType.EXPENSE:
            path = "/expensive"
        elif document_type == DocumentType.CREDIT_NOTE:
            path = "/api/create-credit-note"
        elif document_type == DocumentType.DEBIT_NOTE:
            path = "/api/create-debit-note"
        else:
            path = "/transaction"

        headers = self._headers(authorization, tenant_id, user_id, role)
        if self.client is not None:
            response = await self.client.post(f"{self.settings.shree_base_url.rstrip('/')}{path}", json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_s) as client:
                response = await client.post(f"{self.settings.shree_base_url.rstrip('/')}{path}", json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"Shree ERP write failed with HTTP {response.status_code}: {response.text[:500]}")
        return response.json()

    def _map_payload(self, document_type: DocumentType, extraction: Extraction, idempotency_key: str) -> dict[str, Any]:
        required = extraction.invoice_number.value if extraction.invoice_number else None
        if document_type != DocumentType.EXPENSE and not required:
            raise AdapterConfigurationError("invoice_number is required for ERP transaction creation")
        lines = [
            {
                "product": line.product.value,
                "product_id": line.product_id.value,
                "hsn_code": line.hsn_code.value,
                "quantity": line.quantity.value,
                "price": line.unit_price.value,
                "discount": line.discount.value,
                "gst": line.gst_rate.value,
                "cgst": line.cgst_rate.value,
                "sgst": line.sgst_rate.value,
                "igst": line.igst_rate.value,
                "total": line.total.value,
            }
            for line in extraction.lines
        ]
        if document_type == DocumentType.EXPENSE:
            return {
                "amount": extraction.total_amount.value,
                "date": extraction.invoice_date.value,
                "note": extraction.supplier_name.value,
                "status": "pending",
                "capture_idempotency_key": idempotency_key,
            }
        if document_type in (DocumentType.CREDIT_NOTE, DocumentType.DEBIT_NOTE):
            note_number = extraction.note_number.value
            if not note_number:
                raise AdapterConfigurationError("note_number is required for credit/debit note creation")
            return {
                "invoiceNumber": extraction.invoice_number.value,
                "noteDate": extraction.invoice_date.value,
                "items": lines,
                "totals": {
                    "taxableAmount": extraction.taxable_value.value,
                    "totalGST": extraction.cgst_amount.value + extraction.sgst_amount.value,
                    "totalIGST": extraction.igst_amount.value,
                    "grandTotal": extraction.total_amount.value,
                },
                "creditNoteNumber": note_number,
                "capture_idempotency_key": idempotency_key,
            }
        return {
            "TransactionType": document_type.value.title(),
            "InvoiceNumber": extraction.invoice_number.value,
            "Date": extraction.invoice_date.value,
            "PartyName": (extraction.supplier_name.value if document_type == DocumentType.PURCHASE else extraction.customer_name.value),
            "gstin": (extraction.supplier_gstin.value if document_type == DocumentType.PURCHASE else extraction.customer_gstin.value),
            "BasicAmount": extraction.taxable_value.value,
            "Subtotal": extraction.taxable_value.value,
            "TaxAmount": extraction.total_tax.value,
            "TotalAmount": extraction.total_amount.value,
            "CGSTAmount": extraction.cgst_amount.value,
            "SGSTAmount": extraction.sgst_amount.value,
            "IGSTAmount": extraction.igst_amount.value,
            "items": lines,
            "capture_idempotency_key": idempotency_key,
        }

