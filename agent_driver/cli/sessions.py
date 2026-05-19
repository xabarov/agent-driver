"""Persistent session metadata for CLI chat workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """Serializable chat session record."""

    session_id: str
    thread_id: str
    run_ids: tuple[str, ...]
    transcript: tuple[tuple[str, str], ...]
    created_at: str
    updated_at: str


class SessionStore:
    """File-backed session metadata store."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (Path.cwd() / ".agent-driver" / "sessions.json")

    @property
    def path(self) -> Path:
        return self._path

    def list_sessions(self) -> list[SessionRecord]:
        payload = self._read()
        rows = payload.get("sessions", [])
        if not isinstance(rows, list):
            return []
        records: list[SessionRecord] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            records.append(_record_from_dict(row))
        return records

    def get(self, session_id: str) -> SessionRecord | None:
        for record in self.list_sessions():
            if record.session_id == session_id:
                return record
        return None

    def upsert(
        self,
        *,
        session_id: str,
        thread_id: str,
        run_ids: list[str],
        transcript: list[tuple[str, str]],
    ) -> SessionRecord:
        now = datetime.now(UTC).isoformat()
        payload = self._read()
        rows = payload.get("sessions", [])
        if not isinstance(rows, list):
            rows = []
        existing_created_at = now
        updated_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("session_id") == session_id:
                existing_created_at = str(row.get("created_at") or now)
                continue
            updated_rows.append(row)
        record = SessionRecord(
            session_id=session_id,
            thread_id=thread_id,
            run_ids=tuple(run_ids),
            transcript=tuple((str(role), str(text)) for role, text in transcript),
            created_at=existing_created_at,
            updated_at=now,
        )
        updated_rows.append(_record_to_dict(record))
        payload["sessions"] = updated_rows
        self._write(payload)
        return record

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"sessions": []}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"sessions": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _record_from_dict(row: dict[str, Any]) -> SessionRecord:
    transcript_rows = row.get("transcript")
    transcript: tuple[tuple[str, str], ...] = ()
    if isinstance(transcript_rows, list):
        transcript = tuple(
            (str(item[0]), str(item[1]))
            for item in transcript_rows
            if isinstance(item, list) and len(item) >= 2
        )
    run_ids = row.get("run_ids")
    run_list: tuple[str, ...] = ()
    if isinstance(run_ids, list):
        run_list = tuple(str(item) for item in run_ids)
    return SessionRecord(
        session_id=str(row.get("session_id", "")),
        thread_id=str(row.get("thread_id", "")),
        run_ids=run_list,
        transcript=transcript,
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
    )


def _record_to_dict(record: SessionRecord) -> dict[str, Any]:
    return {
        "session_id": record.session_id,
        "thread_id": record.thread_id,
        "run_ids": list(record.run_ids),
        "transcript": [list(item) for item in record.transcript],
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


__all__ = ["SessionRecord", "SessionStore"]
