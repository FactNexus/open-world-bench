from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EvidenceRecord:
    requested_url: str
    final_url: str | None
    retrieved_at: datetime
    status: str
    content_type: str | None
    content_hash: str | None
    extracted_text_path: str | None
    warning: str | None = None
