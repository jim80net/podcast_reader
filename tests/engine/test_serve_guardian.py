from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from podcast_reader.engine.serve_guardian import classify_serve_status, run_guardian


def _status(*, target: str = "http://127.0.0.1:43127", path: str = "/") -> str:
    return json.dumps({"Foreground": {"test-session": {
            "TCP": {"443": {"HTTPS": True}},
            "Web": {
                "desktop.example.ts.net:443": {
                    "Handlers": {path: {"Proxy": target}},
                }
            },
            "AllowFunnel": {},
        }}})


def test_classify_serve_status_extracts_exact_mapping() -> None:
    assert classify_serve_status(_status()) == {
        "kind": "mapping",
        "target": "http://127.0.0.1:43127",
        "url": "https://desktop.example.ts.net",
    }


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        '{"FutureConfig": {}}',
        '{"TCP": [], "Web": {}}',
        '{"TCP": {"future": {"HTTPS": true}}, "Web": {}}',
        '{"TCP":{"443":{"HTTPS":true}},"Web":{"evil.example:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:1"}}}}}',
        '{"AllowFunnel": {"443": true}}',
        _status(path="/notes"),
    ],
)
def test_classify_serve_status_fails_closed_on_ambiguous_input(text: str) -> None:
    assert classify_serve_status(text)["kind"] == "conflict"


def test_classify_serve_status_allows_other_listener() -> None:
    text = json.dumps(
        {
            "TCP": {"8443": {"HTTPS": True}},
            "Web": {
                "desktop.example.ts.net:8443": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:9000"}}
                }
            },
            "AllowFunnel": {},
        }
    )
    assert classify_serve_status(text) == {"kind": "empty"}


def test_guardian_does_not_spawn_or_mutate_after_unparseable_status() -> None:
    events = StringIO()
    with (
        patch(
            "podcast_reader.engine.serve_guardian._query_status",
            return_value={"kind": "conflict", "reason": "unexpected status"},
        ),
        patch("podcast_reader.engine.serve_guardian._spawn_serve") as spawn,
    ):
        code = run_guardian(
            engine_port=43127,
            tailscale_argv=["tailscale"],
            lease=StringIO("GO\n"),
            events=events,
        )
    assert code == 3
    spawn.assert_not_called()
    assert [json.loads(line)["event"] for line in events.getvalue().splitlines()] == ["conflict"]


def test_guardian_reports_unowned_when_mapping_appears_after_bound() -> None:
    events = StringIO()
    with (
        patch(
            "podcast_reader.engine.serve_guardian._query_status",
            side_effect=[
                {"kind": "empty"},
                {
                    "kind": "mapping",
                    "target": "http://127.0.0.1:9999",
                    "url": "https://other.example.ts.net",
                },
            ],
        ),
        patch("podcast_reader.engine.serve_guardian._spawn_serve") as spawn,
    ):
        result = run_guardian(
            engine_port=8000,
            tailscale_argv=["tailscale"],
            lease=StringIO("GO\n"),
            events=events,
        )
    assert result == 3
    spawn.assert_not_called()
    assert [json.loads(line)["event"] for line in events.getvalue().splitlines()] == [
        "bound",
        "unowned",
    ]


def test_spawn_setup_failure_verifies_mapping_cleanup_before_releasing_gate() -> None:
    events = StringIO()
    with (
        patch(
            "podcast_reader.engine.serve_guardian._query_status",
            side_effect=[{"kind": "empty"}, {"kind": "empty"}],
        ),
        patch(
            "podcast_reader.engine.serve_guardian._spawn_serve",
            side_effect=OSError("job assignment failed"),
        ),
        patch(
            "podcast_reader.engine.serve_guardian._wait_until_mapping_safe",
            return_value="absent",
        ) as verify,
    ):
        result = run_guardian(
            engine_port=8000,
            tailscale_argv=["tailscale"],
            lease=StringIO("GO\n"),
            events=events,
        )
    assert result == 2
    verify.assert_called_once()
    assert json.loads(events.getvalue().splitlines()[-1]) == {
        "event": "unowned",
        "severity": "error",
        "message": "Tailscale Serve could not be started safely",
    }


def test_fake_script_fixture_is_not_accidentally_packaged() -> None:
    # The real guardian accepts an argv JSON only as an explicit caller input;
    # no test fake or Tailscale binary becomes package data.
    assert not (Path(__file__).parents[2] / "src" / "podcast_reader" / "fake_tailscale.py").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX lifecycle proof")
def test_real_guardian_gates_proxy_and_reaps_foreground_serve(tmp_path: Path) -> None:
    state = tmp_path / "serve-state.json"
    fake = tmp_path / "fake_tailscale.py"
    fake.write_text(
        """
import json, os, signal, sys, time
state = sys.argv[1]
args = sys.argv[2:]
if args == ['serve', 'status', '--json']:
    try:
        print(open(state, encoding='utf-8').read())
    except FileNotFoundError:
        print('{}')
    raise SystemExit(0)
if args[-1] == 'off':
    try: os.unlink(state)
    except FileNotFoundError: pass
    raise SystemExit(0)
target = args[-1]
host = 'desktop.example.ts.net:443'
payload = {'Foreground': {'test-session': {
    'TCP': {'443': {'HTTPS': True}},
    'Web': {host: {'Handlers': {'/': {'Proxy': target}}}},
    'AllowFunnel': {},
}}}
with open(state, 'w', encoding='utf-8') as handle:
    json.dump(payload, handle)
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

    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen()
    engine_port = upstream.getsockname()[1]

    def answer_once() -> None:
        connection, _ = upstream.accept()
        with connection:
            assert connection.recv(4) == b"ping"
            connection.sendall(b"pong")

    thread = threading.Thread(target=answer_once)
    thread.start()
    command = [sys.executable, str(fake), str(state)]
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from podcast_reader.cli import main; main()",
            "serve-guardian",
            "--engine-port",
            str(engine_port),
            "--tailscale-command-json",
            json.dumps(command),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    bound = json.loads(process.stdout.readline())
    assert bound["event"] == "bound"
    process.stdin.write("GO\n")
    process.stdin.flush()
    ready = json.loads(process.stdout.readline())
    assert ready == {
        "event": "ready",
        "target": bound["target"],
        "url": "https://desktop.example.ts.net/web/",
    }

    gate_port = int(str(bound["target"]).rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", gate_port), timeout=2) as client:
        client.sendall(b"ping")
        assert client.recv(4) == b"pong"
    thread.join(timeout=2)
    upstream.close()

    process.stdin.close()
    assert json.loads(process.stdout.readline()) == {"event": "stopped"}
    assert process.wait(timeout=5) == 0
    assert not state.exists()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", gate_port), timeout=0.2)
