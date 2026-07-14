"""Fail-closed foreground Tailscale Serve guardian for the private web app.

The guardian binds a product-owned loopback gate before asking its Electron
parent to persist ownership. It mutates Serve only after receiving ``GO``, and
stdin EOF is its lease revocation signal. No Tailscale binary is bundled.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from typing import IO, TYPE_CHECKING, Literal, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class EmptyStatus(TypedDict):
    kind: Literal["empty"]


class MappingStatus(TypedDict):
    kind: Literal["mapping"]
    target: str
    url: str


class ConflictStatus(TypedDict):
    kind: Literal["conflict"]
    reason: str


ServeStatus = EmptyStatus | MappingStatus | ConflictStatus


def _object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast("dict[str, object]", value)


def _conflict(reason: str) -> ConflictStatus:
    return {"kind": "conflict", "reason": reason}


def _valid_tailnet_hostname(host: str) -> bool:
    if len(host) > 253 or not host.endswith(".ts.net"):
        return False
    return all(
        1 <= len(label) <= 63
        and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is not None
        for label in host.split(".")
    )


def classify_serve_status(text: str) -> ServeStatus:
    """Strictly classify node-level ``tailscale serve status --json`` output."""
    try:
        root = _object(json.loads(text))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _conflict("Tailscale Serve returned malformed JSON")
    root_keys = {"TCP", "Web", "AllowFunnel", "Foreground", "Services"}
    if root is None or not set(root).issubset(root_keys):
        return _conflict("Tailscale Serve returned an unexpected status shape")
    services = _object(root.get("Services", {}))
    foreground = _object(root.get("Foreground", {}))
    if services is None or foreground is None:
        return _conflict("Tailscale Serve returned an unexpected status shape")
    if services:
        return _conflict("Tailscale Services are configured; private access will not modify them")

    config_keys = {"TCP", "Web", "AllowFunnel"}
    background = {key: value for key, value in root.items() if key in config_keys}
    configs = [background]
    for raw_config in foreground.values():
        config = _object(raw_config)
        if config is None or not set(config).issubset(config_keys):
            return _conflict("Tailscale Serve returned an unexpected foreground status shape")
        configs.append(config)

    classified = [_classify_config(config) for config in configs]
    for status in classified:
        if status["kind"] == "conflict":
            return status
    mappings = [status for status in classified if status["kind"] == "mapping"]
    if not mappings:
        return {"kind": "empty"}
    if len(mappings) > 1:
        return _conflict("HTTPS 443 has multiple background or foreground owners")
    return mappings[0]


def _classify_config(root: dict[str, object]) -> ServeStatus:
    tcp = _object(root.get("TCP", {}))
    web = _object(root.get("Web", {}))
    funnel = _object(root.get("AllowFunnel", {}))
    if tcp is None or web is None or funnel is None:
        return _conflict("Tailscale Serve returned an unexpected status shape")
    if funnel:
        return _conflict("Tailscale Funnel is configured; private access will not modify it")

    for port_text, raw_listener in tcp.items():
        if (
            not re.fullmatch(r"[1-9]\d{0,4}", port_text)
            or int(port_text) > 65535
        ):
            return _conflict("Tailscale Serve returned an unexpected TCP listener name")
        listener = _object(raw_listener)
        if (
            listener is None
            or not set(listener).issubset({"HTTP", "HTTPS", "TCP", "TLS"})
            or not listener
            or any(flag is not True for flag in listener.values())
        ):
            return _conflict("Tailscale Serve returned an unexpected TCP listener shape")

    candidates: list[tuple[str, str, bool]] = []
    for host_port, raw_config in web.items():
        host, separator, port_text = host_port.rpartition(":")
        if (
            not separator
            or not _valid_tailnet_hostname(host)
            or re.fullmatch(r"[1-9]\d{0,4}", port_text) is None
            or int(port_text) > 65535
        ):
            return _conflict("Tailscale Serve returned an unexpected web listener name")
        config = _object(raw_config)
        if config is None or set(config) != {"Handlers"}:
            return _conflict("Tailscale Serve returned an unexpected web listener shape")
        handlers = _object(config.get("Handlers"))
        if handlers is None or not handlers:
            return _conflict("Tailscale Serve returned an unexpected handler shape")
        for raw_handler in handlers.values():
            handler = _object(raw_handler)
            if handler is None or set(handler) != {"Proxy"} or not isinstance(
                handler.get("Proxy"), str
            ):
                return _conflict("Tailscale Serve returned an unexpected handler shape")
        if port_text == "443":
            root_handler = _object(handlers.get("/"))
            target = root_handler.get("Proxy") if root_handler is not None else ""
            candidates.append(
                (host, target if isinstance(target, str) else "", set(handlers) == {"/"})
            )

    listener_443 = _object(tcp.get("443"))
    has_https_443 = (
        listener_443 is not None
        and set(listener_443) == {"HTTPS"}
        and listener_443.get("HTTPS") is True
    )
    if not has_https_443 and not candidates:
        return {"kind": "empty"}
    if not has_https_443 or not candidates:
        return _conflict("HTTPS 443 status is internally inconsistent")
    if len(candidates) != 1 or not candidates[0][2] or not candidates[0][1]:
        return _conflict("HTTPS 443 has a non-root or ambiguous web handler")
    host, target, _ = candidates[0]
    return {"kind": "mapping", "target": target, "url": f"https://{host}"}


class Gate:
    """A bound loopback TCP proxy whose listener outlives its accepting phase."""

    def __init__(self, engine_port: int) -> None:
        self._engine_port = engine_port
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen()
        self._listener.settimeout(0.2)
        self._accepting = threading.Event()
        self._accepting.set()
        self._connections: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return cast("tuple[str, int]", self._listener.getsockname())[1]

    def start(self) -> None:
        self._thread = threading.Thread(target=self._accept_loop, name="serve-gate", daemon=True)
        self._thread.start()

    def stop_accepting(self) -> None:
        self._accepting.clear()
        with self._lock:
            connections = tuple(self._connections)
        for connection in connections:
            with suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def close(self) -> None:
        self.stop_accepting()
        self._listener.close()

    def _accept_loop(self) -> None:
        while self._accepting.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._proxy, args=(client,), daemon=True).start()

    def _proxy(self, client: socket.socket) -> None:
        try:
            upstream = socket.create_connection(("127.0.0.1", self._engine_port), timeout=2)
        except OSError:
            client.close()
            return
        with self._lock:
            self._connections.update((client, upstream))
        selector = selectors.DefaultSelector()
        try:
            selector.register(client, selectors.EVENT_READ, upstream)
            selector.register(upstream, selectors.EVENT_READ, client)
            while self._accepting.is_set():
                for key, _ in selector.select(timeout=0.2):
                    source = cast("socket.socket", key.fileobj)
                    destination = cast("socket.socket", key.data)
                    try:
                        chunk = source.recv(64 * 1024)
                        if not chunk:
                            return
                        destination.sendall(chunk)
                    except OSError:
                        return
        finally:
            selector.close()
            with self._lock:
                self._connections.discard(client)
                self._connections.discard(upstream)
            client.close()
            upstream.close()


def _posix_child_setup() -> None:
    os.setsid()
    libc = ctypes.CDLL(None, use_errno=True)
    pr_set_pdeathsig = 1
    if libc.prctl(pr_set_pdeathsig, signal.SIGTERM) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
    if os.getppid() == 1:
        os.kill(os.getpid(), signal.SIGTERM)


class _Kernel32(Protocol):
    def AssignProcessToJobObject(self, job: int, process: int) -> int: ...  # noqa: N802

    def CloseHandle(self, handle: int) -> int: ...  # noqa: N802


def _windows_bindings() -> tuple[_Kernel32, Callable[[], int]]:
    # Imported lazily so non-Windows type checking and tests never touch WinDLL.
    from podcast_reader.engine import process as engine_process

    kernel32 = cast("_Kernel32", engine_process._kernel32)  # type: ignore[attr-defined]
    windows_job = cast(
        "Callable[[], int]", engine_process._windows_job  # type: ignore[attr-defined]
    )
    return kernel32, windows_job


def _assign_windows_kill_job(process: subprocess.Popen[bytes]) -> int:
    kernel32, windows_job = _windows_bindings()

    job = windows_job()
    process_handle = int(process._handle)  # type: ignore[attr-defined]
    if not kernel32.AssignProcessToJobObject(job, process_handle):
        kernel32.CloseHandle(job)
        raise OSError("AssignProcessToJobObject failed for the Tailscale Serve child")
    return job


def _spawn_serve(argv: Sequence[str], target: str) -> tuple[subprocess.Popen[bytes], int | None]:
    if sys.platform.startswith("linux"):
        process = subprocess.Popen(
            [*argv, "serve", "--yes", "--https=443", target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=_posix_child_setup,
        )
    else:
        process = subprocess.Popen(
            [*argv, "serve", "--yes", "--https=443", target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if sys.platform == "win32":
        try:
            return process, _assign_windows_kill_job(process)
        except OSError:
            process.kill()
            process.wait(timeout=5)
            raise
    return process, None


def _terminate_serve(process: subprocess.Popen[bytes], job: int | None) -> None:
    if process.poll() is None:
        if sys.platform.startswith("linux"):
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if sys.platform.startswith("linux"):
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)
    if job is not None:
        kernel32, _ = _windows_bindings()
        kernel32.CloseHandle(job)


def _query_status(argv: Sequence[str]) -> ServeStatus:
    try:
        completed = subprocess.run(
            [*argv, "serve", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _conflict("Tailscale Serve status is unavailable")
    if completed.returncode != 0:
        return _conflict("Tailscale Serve status is unavailable")
    return classify_serve_status(completed.stdout)


def _disable_owned_listener(argv: Sequence[str]) -> bool:
    try:
        completed = subprocess.run(
            [*argv, "serve", "--yes", "--https=443", "off"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _wait_until_mapping_safe(argv: Sequence[str], target: str) -> Literal["absent", "changed"]:
    """Retain the gate until status proves it absent or no longer targets it."""
    last_disable_attempt = 0.0
    while True:
        observed = _query_status(argv)
        if observed["kind"] == "empty":
            return "absent"
        if observed["kind"] == "mapping" and observed["target"] != target:
            return "changed"
        now = time.monotonic()
        if observed["kind"] == "mapping" and now - last_disable_attempt >= 2:
            _disable_owned_listener(argv)
            last_disable_attempt = now
        # Malformed/unavailable status is not absence proof. Keep the listener
        # bound and retry rather than making its port reusable under a stale map.
        time.sleep(0.1)


def _emit(stream: IO[str], event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":")), file=stream, flush=True)


def run_guardian(
    *,
    engine_port: int,
    tailscale_argv: Sequence[str],
    lease: IO[str] = sys.stdin,
    events: IO[str] = sys.stdout,
    timeout: float = 10,
) -> int:
    """Run the bound->GO->ready lease protocol; return a process exit code."""
    if sys.platform != "win32" and not sys.platform.startswith("linux"):
        _emit(events, "error", message="Private web access is unsupported on this platform")
        return 2
    gate = Gate(engine_port)
    target = f"http://127.0.0.1:{gate.port}"
    # Read-only collision gate happens before Electron is invited to persist
    # pending ownership. A conflict therefore cannot strand a false journal.
    before_bound = _query_status(tailscale_argv)
    if before_bound["kind"] != "empty":
        _emit(
            events,
            "conflict",
            message=before_bound.get("reason", "HTTPS 443 is already in use"),
        )
        gate.close()
        return 3
    _emit(events, "bound", target=target)
    if lease.readline().strip() != "GO":
        gate.close()
        return 0

    before_mutation = _query_status(tailscale_argv)
    if before_mutation["kind"] != "empty":
        _emit(
            events,
            "unowned",
            severity="conflict",
            message=before_mutation.get("reason", "HTTPS 443 is already in use"),
        )
        gate.close()
        return 3

    try:
        process, job = _spawn_serve(tailscale_argv, target)
    except (OSError, subprocess.SubprocessError):
        cleanup = _wait_until_mapping_safe(tailscale_argv, target)
        _emit(
            events,
            "unowned",
            severity="error" if cleanup == "absent" else "conflict",
            message="Tailscale Serve could not be started safely",
        )
        gate.close()
        return 2 if cleanup == "absent" else 3

    deadline = time.monotonic() + timeout
    ready: MappingStatus | None = None
    while time.monotonic() < deadline and process.poll() is None:
        observed = _query_status(tailscale_argv)
        if observed["kind"] == "mapping" and observed["target"] == target:
            ready = observed
            break
        if observed["kind"] == "conflict":
            break
        time.sleep(0.1)
    if ready is None:
        _terminate_serve(process, job)
        cleanup = _wait_until_mapping_safe(tailscale_argv, target)
        _emit(
            events,
            "unowned",
            severity="error" if cleanup == "absent" else "conflict",
            message="Tailscale Serve did not establish the private mapping",
        )
        gate.close()
        return 2 if cleanup == "absent" else 3

    gate.start()
    _emit(events, "ready", target=target, url=f'{ready["url"]}/web/')
    lease_closed = threading.Event()

    def watch_lease() -> None:
        while lease.read(4096):
            pass
        lease_closed.set()

    threading.Thread(target=watch_lease, name="serve-lease", daemon=True).start()
    while not lease_closed.wait(0.1) and process.poll() is None:
        pass

    # Stop routing first but deliberately retain the listener until status
    # proves the mapping absent, preventing a stale target from being reused.
    gate.stop_accepting()
    _terminate_serve(process, job)
    cleanup = _wait_until_mapping_safe(tailscale_argv, target)
    if cleanup == "absent":
        _emit(events, "stopped")
        gate.close()
        return 0
    _emit(
        events,
        "unowned",
        severity="conflict",
        message="Serve mapping changed during cleanup; the new owner was preserved",
    )
    gate.close()
    return 3
