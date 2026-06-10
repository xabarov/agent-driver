"""Filesystem backends: route paths to scratch vs durable storage.

The `agent_driver.fs` building block is a uniform FileBackend (read/write/edit/
ls/glob/grep/delete) with three implementations. A CompositeBackend routes by
path prefix — here `/tmp` is ephemeral in-memory and `/memories` is on disk —
so one surface spans scratch + durable storage. Embedders can target this from
their own tools/code.

    python examples/cookbook/14_filesystem_backends.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent_driver.fs import (
    CompositeBackend,
    FileBackendError,
    LocalFilesystemBackend,
    StateBackend,
)


async def main() -> None:
    durable_dir = Path(tempfile.mkdtemp())
    fs = CompositeBackend(
        routes={
            "/tmp": StateBackend(),  # ephemeral scratch
            "/memories": LocalFilesystemBackend(durable_dir),  # durable on disk
        },
        default=StateBackend(),
    )

    fs.write("/memories/facts.md", "deploy target = eu-west-3\nowner = platform")
    fs.write("/tmp/scratch.txt", "throwaway")
    fs.write("notes.txt", "lands in the default backend")

    print("ls:", fs.ls())
    print("grep deploy:", fs.grep("deploy", path_glob="/memories/*"))
    # Durable file is really on disk under the routed root.
    print(
        "on disk:",
        (durable_dir / "facts.md").read_text(encoding="utf-8").splitlines()[0],
    )

    # Standardized, recoverable errors (not raw OSError).
    try:
        fs.read("/memories/missing.md")
    except FileBackendError as exc:
        print("error:", exc.code.value, "->", exc.path)


if __name__ == "__main__":
    asyncio.run(main())
