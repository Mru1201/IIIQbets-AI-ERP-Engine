import os

os.environ["ERP_AI_CHAT_INTERNAL_TOKEN"] = "internal-test"

from fastapi.testclient import TestClient

from app.main import app, detect_language


client = TestClient(app)


def test_health_and_internal_authentication() -> None:
    assert client.get("/health").json()["service"] == "erp-ai-chat"
    response = client.post("/v1/respond", json={"message": "show sales", "role": "staff", "tenant_id": "tenant-a"})
    assert response.status_code == 401


def test_supported_languages_and_write_refusal() -> None:
    assert detect_language("बिक्री दिखाएं") == "hi"
    assert detect_language("ಮಾರಾಟ ತೋರಿಸಿ") == "kn"
    assert detect_language("అమ్మకాలు చూపించు") == "te"
    response = client.post(
        "/v1/respond",
        headers={"Authorization": "Bearer internal-test"},
        json={"message": "delete an invoice", "role": "admin", "tenant_id": "tenant-a"},
    )
    assert response.json()["status"] == "unsupported"
    assert response.json()["reason"] == "write_action"
