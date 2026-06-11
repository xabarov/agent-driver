"""N6: SQLite stores consolidated onto SqliteStoreBase expose a real close()."""

from __future__ import annotations

import sqlite3

import pytest

from agent_driver.context.artifacts.sqlite import (
    SqliteArtifactStore,
    SqliteContextStore,
)
from agent_driver.context.planning.artifacts import SqlitePlanArtifactStore
from agent_driver.context.sessions.sqlite import SqliteSessionStore
from agent_driver.persistence import SqliteStoreBase
from agent_driver.runtime.control.sqlite import SqliteCommandQueueStore
from agent_driver.runtime.sqlite_store import SqliteRuntimeStore

_STORE_CLASSES = [
    SqliteArtifactStore,
    SqliteContextStore,
    SqliteSessionStore,
    SqlitePlanArtifactStore,
    SqliteCommandQueueStore,
    SqliteRuntimeStore,
]


@pytest.mark.parametrize("store_cls", _STORE_CLASSES)
def test_store_close_releases_connection(store_cls, tmp_path) -> None:
    """Every migrated store inherits close() and actually shuts the connection."""
    store = store_cls(path=str(tmp_path / f"{store_cls.__name__}.db"))
    assert isinstance(store, SqliteStoreBase)

    store.close()  # must not raise; idempotent base behavior

    # The underlying connection is genuinely closed.
    with pytest.raises(sqlite3.ProgrammingError):
        store._query("SELECT 1")  # noqa: SLF001
