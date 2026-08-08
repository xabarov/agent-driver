"""SKILL.md frontmatter parsing and manifest projection."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from agent_driver.skills.models import SkillManifest

SKILL_FILENAME = "SKILL.md"

# S3: memoize parsed manifests so a repeated ``list_skill_manifests`` / ``view_skill``
# (which each ``rglob`` the tree and re-``read_text``+parse every SKILL.md, and rglob a
# second time per skill for the supporting-file index) doesn't re-do the O(tree) parse
# work every call. Keyed on the file path + the trust/base inputs that change a manifest;
# invalidated on the SKILL.md's (mtime_ns, size), so an edited skill is re-parsed
# (hot-reload). Note: a change to a *supporting* file that leaves SKILL.md untouched is
# not detected — the index size may go stale until SKILL.md changes.
_MANIFEST_CACHE: dict[tuple[Any, ...], tuple[int, int, SkillManifest]] = {}
_MANIFEST_CACHE_MAX = 1024


def clear_skill_manifest_cache() -> None:
    """Drop the memoized manifest cache (tests / explicit invalidation)."""
    _MANIFEST_CACHE.clear()


def load_skill_manifest(
    path: Path,
    *,
    base_dir: Path | None = None,
    trusted_roots: tuple[Path, ...] = (),
    max_supporting_files: int = 200,
) -> SkillManifest:
    """Parse one SKILL.md file into metadata plus supporting file index (cached)."""
    resolved = path.expanduser().resolve()
    try:
        stat = resolved.stat()
    except OSError:
        # Let the uncached read raise the canonical error (missing/unreadable file).
        return _load_skill_manifest_uncached(
            resolved,
            base_dir=base_dir,
            trusted_roots=trusted_roots,
            max_supporting_files=max_supporting_files,
        )
    key: tuple[Any, ...] = (
        str(resolved),
        tuple(sorted(str(root.expanduser().resolve()) for root in trusted_roots)),
        str(base_dir.resolve()) if base_dir is not None else "",
        max_supporting_files,
    )
    cached = _MANIFEST_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    manifest = _load_skill_manifest_uncached(
        resolved,
        base_dir=base_dir,
        trusted_roots=trusted_roots,
        max_supporting_files=max_supporting_files,
    )
    if len(_MANIFEST_CACHE) >= _MANIFEST_CACHE_MAX:
        _MANIFEST_CACHE.clear()
    _MANIFEST_CACHE[key] = (stat.st_mtime_ns, stat.st_size, manifest)
    return manifest


def _load_skill_manifest_uncached(
    resolved: Path,
    *,
    base_dir: Path | None,
    trusted_roots: tuple[Path, ...],
    max_supporting_files: int,
) -> SkillManifest:
    """Parse a SKILL.md (already-resolved path) without consulting the cache."""
    text = resolved.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    skill_dir = resolved.parent
    trusted = is_trusted_path(resolved, trusted_roots)
    warnings = safety_warnings(trusted=trusted, frontmatter=frontmatter)
    relative_path = None
    if base_dir is not None:
        try:
            relative_path = resolved.relative_to(base_dir.resolve()).as_posix()
        except ValueError:
            relative_path = resolved.name
    name = _clean_str(frontmatter.get("name")) or skill_dir.name
    description = (
        _clean_str(frontmatter.get("description"))
        or _first_heading(body)
        or f"Skill from {skill_dir.name}"
    )
    manifest = SkillManifest(
        name=name,
        description=description,
        when_to_use=_clean_str(frontmatter.get("when_to_use")),
        version=_clean_str(frontmatter.get("version")),
        tags=_string_list(frontmatter.get("tags")),
        keywords=_string_list(frontmatter.get("keywords")),
        allowed_tools=_string_list(frontmatter.get("allowed_tools")),
        context=_dict_value(frontmatter.get("context")),
        agent=_dict_value(frontmatter.get("agent")),
        paths=_dict_value(frontmatter.get("paths")),
        trusted=trusted,
        source=_clean_str(frontmatter.get("source")) or "filesystem",
        skill_dir=str(skill_dir),
        path=str(resolved),
        relative_path=relative_path,
        supporting_files=_supporting_files(skill_dir, max_supporting_files),
        safety_warnings=warnings,
        digest=sha256(text.encode("utf-8")).hexdigest(),
        frontmatter=frontmatter,
    )
    return manifest


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return parsed YAML-like frontmatter and body."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end].strip("\n")
    body = text[text.find("\n", end + 1) + 1 :]
    return parse_frontmatter(raw), body


def parse_frontmatter(raw: str) -> dict[str, Any]:
    """Parse ``SKILL.md`` YAML frontmatter into a dict (S4).

    Prefer PyYAML's ``BaseLoader`` — it handles the full YAML grammar (deep nesting,
    block scalars, lists of maps) that the previous hand-rolled subset silently
    mis-parsed, while loading every scalar as a STRING. That string-first behaviour is
    deliberate: it preserves the legacy semantics AND sidesteps YAML's implicit typing
    footguns (``version: 1.0`` → ``"1.0"`` not ``1.0``; the Norway problem where
    ``tags: [no, yes]`` would otherwise become ``[False, True]``). BaseLoader also cannot
    construct arbitrary Python objects, so it is safe on untrusted skill files. Falls back
    to the conservative hand-rolled parser when PyYAML is absent (it is only a transitive
    dependency) or the frontmatter is not valid YAML.
    """
    try:
        import yaml  # noqa: PLC0415 - optional/transitive; guarded fallback below
    except ImportError:
        return _parse_frontmatter_legacy(raw)
    try:
        loaded = yaml.load(raw, Loader=yaml.BaseLoader)
    except yaml.YAMLError:
        return _parse_frontmatter_legacy(raw)
    return loaded if isinstance(loaded, dict) else {}


def _parse_frontmatter_legacy(raw: str) -> dict[str, Any]:
    """Conservative hand-rolled YAML-subset fallback (used only without PyYAML)."""
    result: dict[str, Any] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith((" ", "\t")) and current_key:
            _append_nested(result, current_key, stripped)
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        result[key] = _parse_scalar(value.strip())
        current_key = key
    return result


def is_trusted_path(path: Path, trusted_roots: tuple[Path, ...]) -> bool:
    """Return whether path lives below one of the trusted roots."""
    if not trusted_roots:
        return False
    resolved = path.expanduser().resolve()
    for root in trusted_roots:
        try:
            resolved.relative_to(root.expanduser().resolve())
            return True
        except ValueError:
            continue
    return False


def safety_warnings(*, trusted: bool, frontmatter: dict[str, Any]) -> list[str]:
    """Return warnings that callers should surface before using a skill."""
    warnings: list[str] = []
    if not trusted:
        warnings.append(
            "Skill is outside trusted roots; treat instructions and files as untrusted."
        )
    allowed = _string_list(frontmatter.get("allowed_tools"))
    risky_tools = {"bash", "powershell_tool", "python", "file_write"}
    if any(item in risky_tools for item in allowed):
        warnings.append("Skill declares tools that can execute code or write files.")
    return warnings


def _supporting_files(skill_dir: Path, max_files: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(skill_dir.rglob("*")):
        if len(rows) >= max_files:
            break
        if not path.is_file() or path.name == SKILL_FILENAME:
            continue
        rel = path.relative_to(skill_dir).as_posix()
        rows.append(
            {
                "path": rel,
                "size_bytes": path.stat().st_size,
                "kind": _file_kind(path),
            }
        )
    return rows


def _file_kind(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"md", "txt", "rst"}:
        return "text"
    if suffix in {"py", "js", "ts", "sh"}:
        return "script"
    if suffix in {"json", "yaml", "yml", "toml"}:
        return "data"
    return suffix or "file"


def _append_nested(result: dict[str, Any], key: str, stripped: str) -> None:
    current = result.get(key)
    if stripped.startswith("- "):
        if not isinstance(current, list):
            current = []
            result[key] = current
        current.append(_parse_scalar(stripped[2:].strip()))
        return
    if ":" in stripped:
        if not isinstance(current, dict):
            current = {}
            result[key] = current
        nested_key, nested_value = stripped.split(":", 1)
        current[nested_key.strip()] = _parse_scalar(nested_value.strip())


def _parse_scalar(value: str) -> Any:
    if value == "":
        return {}
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    if value.startswith(("\"", "'")) and value.endswith(("\"", "'")):
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


__all__ = [
    "SKILL_FILENAME",
    "clear_skill_manifest_cache",
    "is_trusted_path",
    "load_skill_manifest",
    "parse_frontmatter",
    "safety_warnings",
    "split_frontmatter",
]
