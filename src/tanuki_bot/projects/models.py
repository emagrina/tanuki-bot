from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

@dataclass
class Project:
    id: str
    name: str
    repo_path: str
    created_at: str
    last_used_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"