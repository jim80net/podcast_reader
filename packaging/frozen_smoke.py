#!/usr/bin/env python3
"""End-to-end smoke for the frozen engine (task 7.2; stdlib only).

Boots the built engine with a temporary (or supplied) data dir, completes the
authenticated handshake exactly like the app supervisor does (ready sentinel
on stdout -> discovery file -> bearer token from ``engine-state.json`` ->
``GET /v1/health``), asserts the health-reported version equals the
``pyproject.toml`` version (guarding the frozen ``copy_metadata`` collection,
per S3), installs a pack through ``POST /v1/packs/{id}/install``, and — for
model packs — switches settings to that model on CPU, submits the fixture WAV
as a job, and asserts it reaches ``done`` with a non-empty HTML artifact.

Modes:
  default            full e2e: install --pack, transcribe --fixture
  --boot-only        handshake + version assert only (the Q1 flake fallback)
  --pack cuda-runtime --no-transcribe
                     the S9 workflow_dispatch job: install the CUDA pack on a
                     stock Windows runner, verify wheel extraction + manifest
                     contents, and make the frozen worker load cuBLAS/cuDNN by
                     basename through its private DLL path (no GPU needed)

Exit code 0 on success; non-zero with a reason on stderr otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
READY_SENTINEL = "PODCAST_READER_READY"

#: The complete cuDNN 9 + cuBLAS DLL stems the CUDA pack must extract
#: (spike: faster-whisper #1279 — a partial set fails at model load).
CUDA_DLL_STEMS = (
    "cublas64",
    "cublasLt64",
    "cudnn64",
    "cudnn_graph64",
    "cudnn_ops64",
    "cudnn_cnn64",
    "cudnn_adv64",
    "cudnn_engines_precompiled64",
    "cudnn_engines_runtime_compiled64",
    "cudnn_heuristic64",
)


class SmokeError(Exception):
    """A smoke assertion failed."""


def _pid_is_alive(pid: int) -> bool:
    """Probe liveness without Windows ``os.kill(pid, 0)`` side effects."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        queried = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code)))
        return queried and code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def pyproject_version() -> str:
    """The project version from pyproject.toml (no tomllib on 3.10)."""
    text = (REPO_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"$', text, re.MULTILINE)
    if match is None:
        raise SmokeError("could not find the version in pyproject.toml")
    return match.group(1)


class Engine:
    """The booted frozen engine plus its authed HTTP plumbing."""

    def __init__(self, binary: Path, data_dir: Path, timeout: float) -> None:
        self.data_dir = data_dir
        print(f"booting {binary} (data dir {data_dir})")
        self.process = subprocess.Popen(
            [str(binary), "serve"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "PODCAST_READER_DATA_DIR": str(data_dir)},
        )
        self._output: list[str] = []
        self._ready = threading.Event()

        def pump() -> None:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self._output.append(line)
                if line.strip() == READY_SENTINEL:
                    self._ready.set()
            self._ready.set()  # EOF: unblock the waiter so it can fail fast

        self._pump = threading.Thread(target=pump, daemon=True)
        self._pump.start()
        if not self._ready.wait(timeout=timeout) or self.process.poll() is not None:
            self.kill()
            raise SmokeError(
                "engine never printed the ready sentinel; output:\n" + "".join(self._output)
            )
        try:
            discovery = json.loads((data_dir / "engine.json").read_text())
            state = json.loads((data_dir / "engine-state.json").read_text())
        except (FileNotFoundError, PermissionError) as exc:
            # The child can print its post-discovery sentinel and then fail
            # quickly enough for serve_engine's finally block to remove the
            # file before this process reads it. Preserve the actual child
            # traceback instead of reporting only the secondary file race.
            self._pump.join(timeout=2)
            exit_code = self.process.poll()
            self.kill()
            raise SmokeError(
                f"engine handshake files unavailable ({exc}); child exit={exit_code}; output:\n"
                + "".join(self._output)
            ) from exc
        self.port = int(discovery["port"])
        self.token = str(state["token"])
        print(f"handshake complete: port {self.port}, pid {discovery['pid']}")

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                payload = response.read()
                return response.status, json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            return exc.code, json.loads(payload) if payload else None

    def expect(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        status, payload = self.request(method, path, body)
        if status >= 400:
            raise SmokeError(f"{method} {path} -> {status}: {payload}")
        return payload

    def shutdown(self, timeout: float = 30) -> None:
        with contextlib.suppress(OSError):
            self.request("POST", "/v1/shutdown")
        try:
            self.process.wait(timeout=timeout)
            print("engine exited cleanly after POST /v1/shutdown")
        except subprocess.TimeoutExpired:
            self.kill()
            raise SmokeError("engine did not exit after POST /v1/shutdown") from None

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()

    def tail(self) -> str:
        return "".join(self._output[-40:])


def assert_version(engine: Engine, expected: str) -> None:
    health = engine.expect("GET", "/v1/health")
    if health["version"] != expected:
        raise SmokeError(
            f"/v1/health version {health['version']!r} != pyproject version {expected!r} "
            "(frozen importlib.metadata collection broken? per S3)"
        )
    print(f"health ok: version {health['version']}")


def assert_web_assets(engine: Engine) -> None:
    """The actual frozen engine serves every packaged browser shell asset."""
    expected = {
        "/web/": b'<main id="app"',
        "/web/assets/app.js": b"loadLibrary()",
        "/web/assets/app.css": b".library",
    }
    for path, marker in expected.items():
        request = urllib.request.Request(f"http://127.0.0.1:{engine.port}{path}")
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            if response.status != 200 or marker not in body:
                raise SmokeError(f"packaged web asset failed at {path}: {response.status}")
    print("packaged private-web shell assets served")


def assert_serve_guardian(engine_binary: Path, engine: Engine, timeout: float = 30) -> None:
    """The frozen guardian proxies, revokes, and reaps a fake foreground Serve."""
    fixture_dir = Path(tempfile.mkdtemp(prefix="guardian-smoke-"))
    state = fixture_dir / "status.json"
    child_pid = fixture_dir / "child.pid"
    fake = fixture_dir / "fake_tailscale.py"
    fake.write_text(
        """
import ctypes, json, os, signal, sys, time
state, pid_file = sys.argv[1:3]
args = sys.argv[3:]
def alive(pid):
    if os.name != 'nt':
        try: os.kill(pid, 0)
        except OSError: return False
        return True
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle: return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally: kernel32.CloseHandle(handle)
if args == ['serve', 'status', '--json']:
    try:
        pid = int(open(pid_file, encoding='utf-8').read())
    except (FileNotFoundError, ValueError):
        pid = None
    if pid is not None and not alive(pid):
        try: os.unlink(state)
        except FileNotFoundError: pass
    try: print(open(state, encoding='utf-8').read())
    except FileNotFoundError: print('{}')
    raise SystemExit(0)
if args[-1] == 'off':
    try: os.unlink(state)
    except FileNotFoundError: pass
    raise SystemExit(0)
target = args[-1]
payload = {'Foreground': {'smoke-session': {
    'TCP': {'443': {'HTTPS': True}},
    'Web': {'smoke.example.ts.net:443': {'Handlers': {'/': {'Proxy': target}}}},
    'AllowFunnel': {},
}}}
with open(pid_file, 'w', encoding='utf-8') as handle: handle.write(str(os.getpid()))
with open(state, 'w', encoding='utf-8') as handle: json.dump(payload, handle)
def stop(*_args):
    try: os.unlink(state)
    except FileNotFoundError: pass
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while True: time.sleep(0.1)
""",
        encoding="utf-8",
    )
    command = json.dumps([sys.executable, str(fake), str(state), str(child_pid)])
    guardian = subprocess.Popen(
        [
            str(engine_binary),
            "serve-guardian",
            "--engine-port",
            str(engine.port),
            "--tailscale-command-json",
            command,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert guardian.stdin is not None
    assert guardian.stdout is not None
    try:
        bound = json.loads(guardian.stdout.readline())
        if bound.get("event") != "bound":
            raise SmokeError(f"frozen guardian did not bind: {bound}")
        guardian.stdin.write("GO\n")
        guardian.stdin.flush()
        ready = json.loads(guardian.stdout.readline())
        if ready.get("event") != "ready" or ready.get("target") != bound.get("target"):
            raise SmokeError(f"frozen guardian did not become ready: {ready}")
        with urllib.request.urlopen(f'{bound["target"]}/web/', timeout=10) as response:
            if response.status != 200 or b'<main id="app"' not in response.read():
                raise SmokeError("frozen guardian gate did not proxy the packaged web shell")
        gate_port = int(str(bound["target"]).rsplit(":", 1)[1])
        guardian.stdin.close()
        stopped = json.loads(guardian.stdout.readline())
        if stopped.get("event") != "stopped":
            raise SmokeError(f"frozen guardian cleanup was not verified: {stopped}")
        if guardian.wait(timeout=timeout) != 0 or state.exists():
            raise SmokeError("frozen guardian or fake Serve child survived cleanup")
        serve_pid = int(child_pid.read_text(encoding="utf-8"))
        if _pid_is_alive(serve_pid):
            raise SmokeError(f"fake foreground Serve pid {serve_pid} survived guardian cleanup")
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", gate_port)) == 0:
                raise SmokeError("frozen guardian gate remained reachable after lease EOF")
        print("frozen Serve guardian gate + lease cleanup verified")
    finally:
        if guardian.poll() is None:
            guardian.kill()
            guardian.wait()

    # Model abrupt Electron death separately: a tiny parent owns the guardian's
    # stdin lease, then is killed without closing it. OS pipe teardown must make
    # the still-running guardian revoke Serve and close the gate itself.
    child_pid.unlink(missing_ok=True)
    state.unlink(missing_ok=True)
    guardian_pid_file = fixture_dir / "guardian.pid"
    parent = fixture_dir / "guardian_parent.py"
    parent.write_text(
        """
import subprocess, sys, time
engine, engine_port, command, pid_file = sys.argv[1:]
child = subprocess.Popen(
    [engine, 'serve-guardian', '--engine-port', engine_port,
     '--tailscale-command-json', command],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True,
)
open(pid_file, 'w', encoding='utf-8').write(str(child.pid))
assert child.stdin is not None and child.stdout is not None
print(child.stdout.readline(), end='', flush=True)
child.stdin.write(sys.stdin.readline()); child.stdin.flush()
print(child.stdout.readline(), end='', flush=True)
while True: time.sleep(0.1)
""",
        encoding="utf-8",
    )
    lease_parent = subprocess.Popen(
        [
            sys.executable,
            str(parent),
            str(engine_binary),
            str(engine.port),
            command,
            str(guardian_pid_file),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert lease_parent.stdin is not None
    assert lease_parent.stdout is not None
    try:
        bound = json.loads(lease_parent.stdout.readline())
        if bound.get("event") != "bound":
            raise SmokeError(f"parent-death guardian did not bind: {bound}")
        lease_parent.stdin.write("GO\n")
        lease_parent.stdin.flush()
        ready = json.loads(lease_parent.stdout.readline())
        if ready.get("event") != "ready":
            raise SmokeError(f"parent-death guardian did not become ready: {ready}")
        gate_port = int(str(bound["target"]).rsplit(":", 1)[1])
        serve_pid = int(child_pid.read_text(encoding="utf-8"))
        guardian_pid = int(guardian_pid_file.read_text(encoding="utf-8"))
        if not _pid_is_alive(serve_pid) or not _pid_is_alive(guardian_pid):
            raise SmokeError("parent-death fixture was not live before parent kill")
        lease_parent.kill()
        lease_parent.wait(timeout=timeout)

        deadline = time.monotonic() + timeout
        status = ""
        while time.monotonic() < deadline:
            status = subprocess.run(
                [
                    sys.executable,
                    str(fake),
                    str(state),
                    str(child_pid),
                    "serve",
                    "status",
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if (
                not _pid_is_alive(serve_pid)
                and not _pid_is_alive(guardian_pid)
                and status == "{}"
            ):
                break
            time.sleep(0.05)
        else:
            raise SmokeError(
                "abrupt lease-parent death left guardian, Serve child, or mapping alive: "
                f"guardian={guardian_pid}, serve={serve_pid}, status={status}"
            )
        with socket.socket() as replacement:
            replacement.bind(("127.0.0.1", gate_port))
        print("abrupt lease-parent death revoked guardian, mapping, and gate")
    finally:
        if lease_parent.poll() is None:
            lease_parent.kill()
            lease_parent.wait()

    # Kill the guardian without closing its lease. On POSIX the Serve child is
    # in the guardian's process group with a parent-death signal; on Windows it
    # is assigned to a kill-on-close Job Object. The fake status command models
    # tailscaled dropping a foreground mapping after that CLI session dies.
    child_pid.unlink(missing_ok=True)
    state.unlink(missing_ok=True)
    guardian = subprocess.Popen(
        [
            str(engine_binary),
            "serve-guardian",
            "--engine-port",
            str(engine.port),
            "--tailscale-command-json",
            command,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert guardian.stdin is not None
    assert guardian.stdout is not None
    try:
        bound = json.loads(guardian.stdout.readline())
        if bound.get("event") != "bound":
            raise SmokeError(f"forced-death guardian did not bind: {bound}")
        guardian.stdin.write("GO\n")
        guardian.stdin.flush()
        ready = json.loads(guardian.stdout.readline())
        if ready.get("event") != "ready":
            raise SmokeError(f"forced-death guardian did not become ready: {ready}")
        gate_port = int(str(bound["target"]).rsplit(":", 1)[1])
        serve_pid = int(child_pid.read_text(encoding="utf-8"))
        if not _pid_is_alive(serve_pid):
            raise SmokeError("fake foreground Serve was not live before guardian kill")
        guardian.kill()
        guardian.wait(timeout=timeout)

        deadline = time.monotonic() + timeout
        status = ""
        while time.monotonic() < deadline:
            status = subprocess.run(
                [
                    sys.executable,
                    str(fake),
                    str(state),
                    str(child_pid),
                    "serve",
                    "status",
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if not _pid_is_alive(serve_pid) and status == "{}":
                break
            time.sleep(0.05)
        else:
            raise SmokeError(
                "forced guardian death left Serve child or mapping alive: "
                f"pid={serve_pid}, status={status}"
            )

        with socket.socket() as replacement:
            replacement.bind(("127.0.0.1", gate_port))
            status = subprocess.run(
                [
                    sys.executable,
                    str(fake),
                    str(state),
                    str(child_pid),
                    "serve",
                    "status",
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if status != "{}":
                raise SmokeError("old Serve mapping survived reuse of the guardian gate port")
        print("forced guardian death revoked Serve child, mapping, and gate")
    finally:
        if guardian.poll() is None:
            guardian.kill()
            guardian.wait()


def assert_engine_state_permissions(engine: Engine) -> None:
    """Verify the platform's real bearer-file protection in the frozen run."""
    state_path = engine.data_dir / "engine-state.json"
    if sys.platform == "win32":
        from podcast_reader.engine.settings import verify_windows_private_file

        verify_windows_private_file(state_path)
        print("engine state owner+SYSTEM DACL verified")
    elif stat.S_IMODE(state_path.stat().st_mode) != 0o600:
        raise SmokeError("engine state is not mode 0600")


def install_pack(engine: Engine, pack_id: str, timeout: float) -> None:
    engine.expect("POST", f"/v1/packs/{pack_id}/install")
    print(f"install of {pack_id} accepted; polling")
    deadline = time.monotonic() + timeout
    last_state = ""
    while time.monotonic() < deadline:
        packs = {p["id"]: p for p in engine.expect("GET", "/v1/packs")["packs"]}
        pack = packs[pack_id]
        if pack["state"] != last_state:
            last_state = str(pack["state"])
            print(f"pack {pack_id}: {last_state}")
        if pack["state"] == "installed":
            return
        if pack["state"] == "failed":
            raise SmokeError(f"pack install failed: {pack['error']}")
        time.sleep(2)
    raise SmokeError(f"pack {pack_id} not installed within {timeout}s (state: {last_state})")


def assert_pack_manifest(engine: Engine, pack_id: str) -> None:
    """The installed manifest lists real files that exist with recorded sizes."""
    manifest_glob = list(engine.data_dir.rglob("pack-manifest.json"))
    manifests = [json.loads(p.read_text()) for p in manifest_glob]
    matching = [
        (path.parent, m)
        for path, m in zip(manifest_glob, manifests, strict=True)
        if m["id"] == pack_id
    ]
    if not matching:
        raise SmokeError(f"no pack-manifest.json found for {pack_id}")
    pack_dir, manifest = matching[0]
    if not manifest["files"]:
        raise SmokeError(f"pack manifest for {pack_id} lists no files")
    for recorded in manifest["files"]:
        on_disk = pack_dir / recorded["path"]
        if not on_disk.is_file() or on_disk.stat().st_size != recorded["size"]:
            raise SmokeError(f"manifest file missing or wrong size: {recorded['path']}")
    print(f"pack manifest verified: {len(manifest['files'])} file(s) in {pack_dir}")
    if pack_id == "cuda-runtime":
        names = {recorded["path"] for recorded in manifest["files"]}
        missing = [stem for stem in CUDA_DLL_STEMS if not any(n.startswith(stem) for n in names)]
        if missing:
            raise SmokeError(f"CUDA pack is missing required DLLs: {missing}")
        print("complete cuBLAS + cuDNN 9 DLL set present")


def assert_cuda_loader(engine_binary: Path, data_dir: Path) -> None:
    """The frozen worker can resolve the installed CUDA DLLs by basename."""
    suffix = ".exe" if engine_binary.suffix.lower() == ".exe" else ""
    worker = engine_binary.parent / f"whisper-worker{suffix}"
    if not worker.is_file():
        raise SmokeError(f"frozen whisper worker not found: {worker}")
    result = subprocess.run(
        [str(worker), "--check-cuda-runtime"],
        capture_output=True,
        text=True,
        env={**os.environ, "PODCAST_READER_DATA_DIR": str(data_dir)},
        check=False,
    )
    if result.returncode != 0:
        raise SmokeError(
            "frozen worker could not load the CUDA runtime through its DLL search path:\n"
            + result.stderr.strip()
        )
    if result.stdout.strip() != "cuda-runtime ready":
        raise SmokeError(f"unexpected CUDA loader check output: {result.stdout!r}")
    print("frozen worker loaded cuBLAS + cuDNN through the runtime DLL path")


def transcribe_fixture(engine: Engine, fixture: Path, model: str, timeout: float) -> None:
    settings = engine.expect("GET", "/v1/settings")
    settings["whisper_model"] = model
    settings["whisper_device"] = "cpu"
    engine.expect("PUT", "/v1/settings", settings)
    job = engine.expect("POST", "/v1/jobs", {"source": str(fixture.resolve())})
    job_id = job["id"]
    print(f"job {job_id} submitted for {fixture.name}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = engine.expect("GET", f"/v1/jobs/{job_id}")
        if record["state"] == "done":
            html_path = Path(record["result"]["html_path"])
            if not html_path.is_file() or html_path.stat().st_size == 0:
                raise SmokeError(f"job done but HTML artifact missing/empty: {html_path}")
            print(f"job done; HTML artifact {html_path} ({html_path.stat().st_size} bytes)")
            return
        if record["state"] in ("failed", "interrupted"):
            raise SmokeError(f"job {record['state']}: {record['error']}")
        time.sleep(2)
    raise SmokeError(f"job not done within {timeout}s")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Frozen engine end-to-end smoke.")
    parser.add_argument("engine", type=Path, help="path to podcast-reader-engine[.exe]")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / "fixture_speech.wav",
        help="audio file to transcribe (default: tests/fixtures/fixture_speech.wav)",
    )
    parser.add_argument("--pack", default="model-tiny", help="pack id to install")
    parser.add_argument("--boot-only", action="store_true", help="handshake + version only")
    parser.add_argument("--no-transcribe", action="store_true", help="install the pack only")
    parser.add_argument("--data-dir", type=Path, default=None, help="reusable engine data dir")
    parser.add_argument("--timeout", type=float, default=900, help="per-phase timeout (s)")
    args = parser.parse_args(argv)

    if not args.engine.is_file():
        raise SmokeError(f"engine binary not found: {args.engine}")
    if args.data_dir is not None:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        data_dir = args.data_dir
    else:
        data_dir = Path(tempfile.mkdtemp(prefix="frozen-smoke-"))

    engine = Engine(args.engine.resolve(), data_dir, timeout=60)
    try:
        assert_version(engine, pyproject_version())
        assert_engine_state_permissions(engine)
        assert_web_assets(engine)
        assert_serve_guardian(args.engine.resolve(), engine)
        if not args.boot_only:
            install_pack(engine, args.pack, args.timeout)
            assert_pack_manifest(engine, args.pack)
            if args.pack == "cuda-runtime":
                assert_cuda_loader(args.engine.resolve(), data_dir)
            if not args.no_transcribe:
                model = args.pack.removeprefix("model-")
                transcribe_fixture(engine, args.fixture, model, args.timeout)
        engine.shutdown()
    except BaseException:
        print("--- engine output tail ---\n" + engine.tail(), file=sys.stderr)
        engine.kill()
        raise
    print("frozen smoke PASSED")


if __name__ == "__main__":
    try:
        main()
    except SmokeError as exc:
        print(f"frozen smoke FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
