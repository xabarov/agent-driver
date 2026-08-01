"""Epic 046 #1: SQLite durability opener — WAL + busy_timeout + fallback detection."""

from __future__ import annotations

import sqlite3

import pytest

from agent_driver.persistence import (
    DEFAULT_SQLITE_BUSY_TIMEOUT_SECONDS,
    WalUnsupportedError,
    open_sqlite_connection,
)


def test_sets_busy_timeout_and_wal(tmp_path) -> None:
    conn = open_sqlite_connection(tmp_path / "t.db", check_same_thread=False)
    assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == int(
        DEFAULT_SQLITE_BUSY_TIMEOUT_SECONDS * 1000
    )
    assert conn.execute("PRAGMA journal_mode;").fetchone()[0].lower() == "wal"


def test_custom_busy_timeout(tmp_path) -> None:
    conn = open_sqlite_connection(tmp_path / "t.db", busy_timeout_seconds=5)
    assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == 5000


def test_memory_db_skips_wal_but_keeps_busy_timeout() -> None:
    conn = open_sqlite_connection(":memory:")
    # :memory: has no WAL, but the busy_timeout pragma is still applied.
    assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == int(
        DEFAULT_SQLITE_BUSY_TIMEOUT_SECONDS * 1000
    )


def test_row_factory_is_applied(tmp_path) -> None:
    conn = open_sqlite_connection(tmp_path / "t.db", row_factory=sqlite3.Row)
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    row = conn.execute("SELECT a FROM t").fetchone()
    assert row["a"] == 1  # sqlite3.Row keyed access


class _DowngradingCur:
    def fetchone(self):
        return ("delete",)  # filesystem silently ran DELETE mode


class _DowngradingConn:
    """Wraps a real connection but reports 'delete' for PRAGMA journal_mode=WAL."""

    def __init__(self, inner):
        self._inner = inner

    def execute(self, sql, *a):
        if sql.strip().lower().startswith("pragma journal_mode=wal"):
            return _DowngradingCur()
        return self._inner.execute(sql, *a)

    def close(self):
        self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _patch_downgrade(monkeypatch) -> None:
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3, "connect", lambda path, **kw: _DowngradingConn(real_connect(path, **kw))
    )


def test_require_wal_raises_when_fallback(tmp_path, monkeypatch) -> None:
    # Simulate a filesystem that silently downgrades WAL to 'delete' (NFS/SMB/overlay).
    _patch_downgrade(monkeypatch)
    with pytest.raises(WalUnsupportedError):
        open_sqlite_connection(tmp_path / "t.db", require_wal=True)


def test_silent_fallback_warns_without_require(tmp_path, monkeypatch, caplog) -> None:
    _patch_downgrade(monkeypatch)
    with caplog.at_level("WARNING"):
        conn = open_sqlite_connection(tmp_path / "t.db")  # no require_wal
    assert conn is not None
    assert any("degraded concurrency" in r.message for r in caplog.records)
