"""Engine process model: socket binding, discovery handshake, serve loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def serve_engine(*, discovery_file: Path | None = None) -> None:
    """Bind the engine socket, write the discovery file, and serve the API.

    Placeholder until the engine process model lands (engine-extraction task 9).
    """
    raise NotImplementedError("the engine serve command is not implemented yet")
