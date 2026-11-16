# ERP AI Chat

Standalone, read-only chat classifier for the ERP AI Chat v1 boundary.

## Run locally

```bash
export ERP_AI_CHAT_INTERNAL_TOKEN="$(openssl rand -hex 32)"
uvicorn app.main:app --reload --port 8010
```

The ledger backend is the policy and data gateway. It verifies the user token,
invokes only allowlisted ERP handlers, and may call this service using the
`Authorization: Bearer $ERP_AI_CHAT_INTERNAL_TOKEN` header. The service must not
be given database credentials or raw audit storage access.

The reporting orchestrator already supports an OpenAI-compatible Hugging Face
endpoint without code changes:

```bash
export ERP_LLM_PROVIDER=huggingface
export ERP_HF_BASE_URL=https://router.huggingface.co/v1
export ERP_LLM_MODEL=<approved-cheapest-model>
export ERP_HF_TOKEN=<set-locally>
```

Keep the token in the process environment or a secret manager; never commit it.
For financial answers, response caching is disabled by design. If Redis is
introduced later, cache only permission-scoped, parameterized results with a
short TTL and never raw prompts, transcripts, OCR, attachments, or answer
content. LangChain is not required for this allowlisted tool path, and
Langfuse should not be enabled without a redacted, metadata-only observability
policy.
