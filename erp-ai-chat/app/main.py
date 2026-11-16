from __future__ import annotations

import re
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="ERP AI Chat", version="1.0.0")


class RespondRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    role: str
    tenant_id: str
    attachment_context: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "erp-ai-chat"}


def detect_language(message: str) -> str:
    if re.search(r"[\u0900-\u097f]", message):
        return "hi"
    if re.search(r"[\u0c80-\u0cff]", message):
        return "kn"
    if re.search(r"[\u0c00-\u0c7f]", message):
        return "te"
    return "en"


@app.post("/v1/respond")
def respond(request: RespondRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    expected = os.getenv("ERP_AI_CHAT_INTERNAL_TOKEN")
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Internal service authorization required")
    normalized = request.message.lower()
    if re.search(r"\b(create|edit|delete|approve|post|upload|send|update)\b", normalized):
        return {"language": detect_language(request.message), "status": "unsupported", "reason": "write_action"}
    return {
        "language": detect_language(request.message),
        "status": "classify",
        "intent": "report" if any(word in normalized for word in ("sales", "revenue", "stock", "inventory", "invoice", "profit")) else "navigation",
        "grounding": "approved_erp_tools",
    }
