import hashlib
import secrets
from pathlib import Path
from uuid import UUID

from .models import CaptureDocument


class CaptureStore:
    def __init__(self, scan_dir: Path) -> None:
        self.scan_dir = scan_dir
        self.scan_dir.mkdir(parents=True, exist_ok=True)
        self.documents: dict[UUID, CaptureDocument] = {}
        self.idempotency: dict[tuple[str, str], UUID] = {}

    def save_scan(self, tenant_id: str, file_name: str, content_type: str, payload: bytes) -> CaptureDocument:
        document_id = secrets.token_hex(16)
        digest = hashlib.sha256(payload).hexdigest()
        suffix = Path(file_name).suffix.lower() or ".bin"
        path = self.scan_dir / tenant_id / f"{document_id}-{digest[:16]}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        document = CaptureDocument(
            id=UUID(document_id),
            tenant_id=tenant_id,
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(payload),
            scan_path=str(path),
        )
        self.documents[document.id] = document
        return document

    def get(self, document_id: UUID) -> CaptureDocument:
        try:
            return self.documents[document_id]
        except KeyError as exc:
            raise KeyError(f"capture document {document_id} was not found") from exc

    def read_scan(self, document_id: UUID) -> bytes:
        document = self.get(document_id)
        return Path(document.scan_path).read_bytes()

    def save(self, document: CaptureDocument) -> CaptureDocument:
        self.documents[document.id] = document
        return document

