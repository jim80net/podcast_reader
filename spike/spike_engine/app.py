"""Minimal stand-in FastAPI engine for the packaging spike."""

from __future__ import annotations

import sys

from fastapi import FastAPI
from typing_extensions import TypedDict


class HealthResponse(TypedDict):
    status: str
    frozen: bool
    python: str


app = FastAPI(title="podcast-reader spike engine")


@app.get("/health")
def health() -> HealthResponse:
    return {
        "status": "ok",
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version,
    }


def main() -> None:
    """Engine entry point: bind a pre-bound socket and serve (spike-simplified)."""
    import socket

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)  # connections queue in the backlog until uvicorn accepts
    port = sock.getsockname()[1]
    # Ready sentinel on stdout, printed only once the port is listening,
    # mirroring the real engine's handshake design.
    print(f"ENGINE_READY port={port}", flush=True)
    config = uvicorn.Config(app, log_level="warning")
    server = uvicorn.Server(config)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
