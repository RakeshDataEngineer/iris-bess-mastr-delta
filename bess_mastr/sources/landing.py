"""Immutable landing zone for raw source pages.

Every page fetched from MaStR is written byte-for-byte to disk together with
a manifest line (URL, params, HTTP status, sha256, record count). Any later
run can be replayed from this directory with ``--mode replay``, which makes
results reproducible and auditable, and means a crash after extraction never
loses data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class LandedPage:
    file: str
    sha256: str
    records: list
    total: int | None


class LandingError(ValueError):
    pass


class LandingZone:
    def __init__(self, root: str | Path, source_system: str, run_id: str):
        self.dir = Path(root) / source_system / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.dir / "manifest.jsonl"

    def write_page(self, page_no: int, body: bytes, *, url: str, params: dict, http_status: int,
                   record_count: int) -> Path:
        path = self.dir / f"page_{page_no:04d}.json"
        path.write_bytes(body)
        entry = {
            "file": path.name,
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "url": url,
            "params": params,
            "http_status": http_status,
            "record_count": record_count,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path


def parse_page_body(body: bytes, origin: str) -> tuple[list, int | None]:
    try:
        doc = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LandingError(f"{origin}: response is not valid JSON ({exc})") from exc
    if isinstance(doc, list):  # tolerate a bare list of records
        return doc, len(doc)
    if not isinstance(doc, dict):
        raise LandingError(f"{origin}: unexpected top-level JSON type {type(doc).__name__}")
    if doc.get("Errors"):
        raise LandingError(f"{origin}: source returned errors: {doc['Errors']}")
    data = doc.get("Data")
    if not isinstance(data, list):
        raise LandingError(f"{origin}: 'Data' is missing or not a list")
    total = doc.get("Total")
    return data, int(total) if isinstance(total, (int, float)) else None


def read_page_dir(path: str | Path) -> Iterator[LandedPage]:
    """Yield landed pages in deterministic (file name) order."""
    root = Path(path)
    if not root.is_dir():
        raise LandingError(f"input directory not found: {root}")
    files = sorted(root.glob("page_*.json"))
    if not files:
        raise LandingError(f"no page_*.json files in {root}")
    for f in files:
        body = f.read_bytes()
        records, total = parse_page_body(body, f.name)
        yield LandedPage(str(f), hashlib.sha256(body).hexdigest(), records, total)
