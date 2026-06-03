"""Normalize web tool outputs into compact source evidence records."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _clean_text(value: object, *, max_chars: int | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    if not text:
        return None
    if max_chars is not None and len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _canonical_url(raw_url: object) -> str | None:
    if not isinstance(raw_url, str):
        return None
    url = raw_url.strip()
    if not url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    netloc = hostname.lower()
    if parsed.port and not (
        (parsed.scheme.lower() == "http" and parsed.port == 80)
        or (parsed.scheme.lower() == "https" and parsed.port == 443)
    ):
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _domain_from_canonical(canonical_url: str) -> str | None:
    try:
        hostname = urlsplit(canonical_url).hostname
    except ValueError:
        return None
    if not hostname:
        return None
    return hostname.removeprefix("www.")


def _title_from_metadata(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("title", "og:title", "twitter:title"):
        title = _clean_text(metadata.get(key), max_chars=140)
        if title:
            return title
    return None


def _published_from_metadata(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("published_time", "article:published_time", "date", "published"):
        published = _clean_text(metadata.get(key), max_chars=80)
        if published:
            return published
    return None


def _source_record(
    *,
    url: object,
    source_type: str,
    tool_call_id: str | None,
    rank: int | None,
    title: object = None,
    excerpt: object = None,
    published_at: object = None,
) -> dict[str, Any] | None:
    canonical_url = _canonical_url(url)
    if canonical_url is None:
        return None
    record: dict[str, Any] = {
        "id": f"{source_type}:{tool_call_id or 'tool'}:{rank or 1}",
        "url": str(url).strip(),
        "canonical_url": canonical_url,
        "domain": _domain_from_canonical(canonical_url),
        "source_type": source_type,
    }
    clean_title = _clean_text(title, max_chars=140)
    if clean_title:
        record["title"] = clean_title
    clean_excerpt = _clean_text(excerpt, max_chars=280)
    if clean_excerpt:
        record["excerpt"] = clean_excerpt
    clean_published = _clean_text(published_at, max_chars=80)
    if clean_published:
        record["published_at"] = clean_published
    if tool_call_id:
        record["tool_call_id"] = tool_call_id
    if rank is not None:
        record["rank"] = rank
    return record


def source_evidence_from_tool_result(
    *,
    tool_name: str,
    structured_output: object,
    tool_call_id: str | None = None,
) -> list[dict[str, Any]]:
    """Extract compact source records from built-in web tool outputs."""
    if not isinstance(structured_output, dict):
        return []
    if _structured_output_failed(structured_output):
        return []
    if tool_name in {"web_fetch", "source_read", "browser_read"}:
        metadata = structured_output.get("metadata")
        record = _source_record(
            url=structured_output.get("url"),
            source_type=tool_name,
            tool_call_id=tool_call_id,
            rank=1,
            title=_title_from_metadata(metadata),
            excerpt=structured_output.get("excerpt")
            or structured_output.get("summary"),
            published_at=_published_from_metadata(metadata),
        )
        return [record] if record is not None else []
    if tool_name == "pdf_read":
        record = _source_record(
            url=structured_output.get("url"),
            source_type="pdf_read",
            tool_call_id=tool_call_id,
            rank=1,
            title="PDF source",
            excerpt=structured_output.get("excerpt")
            or structured_output.get("summary"),
        )
        return [record] if record is not None else []
    if tool_name != "web_search":
        return []
    results = structured_output.get("results")
    if not isinstance(results, list):
        return []
    records: list[dict[str, Any]] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        record = _source_record(
            url=item.get("url"),
            source_type="web_search",
            tool_call_id=tool_call_id,
            rank=index,
            title=item.get("title"),
            excerpt=item.get("snippet"),
        )
        if record is not None:
            records.append(record)
    return records


def _structured_output_failed(structured_output: dict[str, Any]) -> bool:
    status_code = structured_output.get("status_code")
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and status_code >= 400
    ):
        return True
    if structured_output.get("error") or structured_output.get("error_code"):
        return True
    status = str(structured_output.get("status") or "").lower()
    return status in {"error", "failed", "denied", "timed_out", "timeout"}


def merge_source_evidence(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate sources, preferring fetched pages over search hits."""
    priority = {
        "source_read": 0,
        "pdf_read": 0,
        "browser_read": 0,
        "web_fetch": 1,
        "assistant_link": 2,
        "web_search": 3,
    }
    by_url: dict[str, dict[str, Any]] = {}
    for record in records:
        canonical = record.get("canonical_url")
        if not isinstance(canonical, str) or not canonical:
            continue
        current = by_url.get(canonical)
        if current is None:
            by_url[canonical] = dict(record)
            continue
        current_priority = priority.get(str(current.get("source_type")), 99)
        next_priority = priority.get(str(record.get("source_type")), 99)
        if next_priority < current_priority:
            by_url[canonical] = {**current, **record}
            continue
        for key in ("title", "excerpt", "published_at", "domain"):
            if not current.get(key) and record.get(key):
                current[key] = record[key]
    return sorted(
        by_url.values(),
        key=lambda item: (
            priority.get(str(item.get("source_type")), 99),
            int(item.get("rank") or 9999),
            str(item.get("canonical_url") or ""),
        ),
    )


__all__ = ["merge_source_evidence", "source_evidence_from_tool_result"]
