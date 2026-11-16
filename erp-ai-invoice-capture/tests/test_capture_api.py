from pathlib import Path
import asyncio

from fastapi.testclient import TestClient

from app.adapter import AdapterConfigurationError, ShreeERPAdapter
from app.config import Settings
from app.models import DocumentType, Extraction, FieldValue
from app.main import app, settings, store


def test_upload_review_and_role_gate(tmp_path: Path) -> None:
    store.scan_dir = tmp_path
    client = TestClient(app)
    uploaded = client.post(
        "/api/v1/capture/documents",
        files={"file": ("invoice.jpg", b"image", "image/jpeg")},
        headers={"X-Tenant-ID": "tenant-a"},
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["document"]["id"]
    extraction = {"document_type": "purchase", "invoice_number": {"value": "P-1"}}
    reviewed = client.post(
        f"/api/v1/capture/documents/{document_id}/review",
        json={"extraction": extraction},
        headers={"X-Tenant-ID": "tenant-a"},
    )
    assert reviewed.status_code == 200
    denied = client.post(
        f"/api/v1/capture/documents/{document_id}/confirm",
        json={"extraction": extraction, "duplicate_resolution": "not-duplicate", "idempotency_key": "idempotent-1"},
        headers={"X-Tenant-ID": "tenant-a", "X-Role": "staff"},
    )
    assert denied.status_code == 403


def test_confirmation_requires_duplicate_resolution() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/capture/documents/00000000-0000-0000-0000-000000000000/confirm",
        json={"extraction": {}, "duplicate_resolution": "unresolved", "idempotency_key": "idempotent-2"},
        headers={"X-Role": "admin"},
    )
    assert response.status_code == 422


def test_adapter_maps_purchase_payload_and_route() -> None:
    class FakeResponse:
        status_code = 201

        def json(self):
            return {"voucherId": 42}

    class FakeClient:
        async def post(self, url, json, headers):
            self.request = (url, json, headers)
            return FakeResponse()

    fake = FakeClient()
    adapter = ShreeERPAdapter(Settings(), client=fake)
    extraction = Extraction(
        document_type=DocumentType.PURCHASE,
        invoice_number=FieldValue(value="P-1"),
        supplier_name=FieldValue(value="Supplier"),
        total_amount=FieldValue(value=118),
    )
    result = asyncio.run(
        adapter.create(
            DocumentType.PURCHASE,
            extraction,
            authorization=None,
            tenant_id="tenant-a",
            user_id="user-a",
            role="admin",
            idempotency_key="capture-123",
        )
    )

    assert result == {"voucherId": 42}
    assert fake.request[0].endswith("/transaction")
    assert fake.request[1]["InvoiceNumber"] == "P-1"
    assert fake.request[1]["capture_idempotency_key"] == "capture-123"


def test_adapter_requires_note_number_for_notes() -> None:
    adapter = ShreeERPAdapter(Settings())
    extraction = Extraction(
        document_type=DocumentType.CREDIT_NOTE,
        invoice_number=FieldValue(value="P-1"),
    )

    try:
        adapter._map_payload(DocumentType.CREDIT_NOTE, extraction, "capture-123")
    except AdapterConfigurationError as exc:
        assert "note_number" in str(exc)
    else:
        raise AssertionError("credit-note mapping must require note_number")
