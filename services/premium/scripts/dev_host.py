from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

STRIPE_VERSION = "1.45.0"
STRIPE_LINUX_ARM64_URL = (
    "https://github.com/stripe/stripe-cli/releases/download/"
    f"v{STRIPE_VERSION}/stripe_{STRIPE_VERSION}_linux_arm64.tar.gz"
)
STRIPE_LINUX_ARM64_SHA256 = "1be10d41ac0712978e1abfe1bae1223af88bebc3929a088ee8fc9d099b570e5d"


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _atomic_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _listener_keys(status: dict[str, Any], port: int) -> list[str]:
    web = status.get("Web", {})
    if not isinstance(web, dict):
        raise RuntimeError("Tailscale Serve status has an invalid Web section")
    return [key for key in web if isinstance(key, str) and key.endswith(f":{port}")]


def assert_serve_listener_unused(status: object, port: int) -> None:
    if not isinstance(status, dict):
        raise RuntimeError("Tailscale Serve status must be an object")
    tcp = status.get("TCP", {})
    if not isinstance(tcp, dict):
        raise RuntimeError("Tailscale Serve status has an invalid TCP section")
    if str(port) in tcp or port in tcp or _listener_keys(status, port):
        raise RuntimeError(f"HTTPS listener {port} is already configured; refusing deployment")


def assert_loopback_port_unused(port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("loopback port must be between 1 and 65535")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"loopback port {port} is already in use; refusing deployment"
            ) from exc


def _read_serve_status(command: str) -> object:
    result = subprocess.run(
        [command, "serve", "status", "--json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(result.stdout)


def _canonical_origin(value: str, https_port: int) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port != https_port
        or value != f"https://{parsed.hostname}:{https_port}"
    ):
        raise ValueError("public origin must be the canonical HTTPS host and listener port")
    return value


def render_unit(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for key, value in replacements.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"unit replacement {key} contains a newline")
        rendered = rendered.replace(f"@{key}@", value)
    if "@" in rendered:
        raise ValueError("systemd unit template has unresolved placeholders")
    return rendered


def install_stripe_cli(destination: Path) -> str:
    if os.uname().sysname != "Linux" or os.uname().machine not in {"aarch64", "arm64"}:
        raise RuntimeError("the pinned Stripe CLI artifact supports Linux arm64 only")
    _private_dir(destination.parent)
    with tempfile.TemporaryDirectory(prefix="premium-stripe-") as temporary_name:
        archive = Path(temporary_name) / "stripe.tar.gz"
        urllib.request.urlretrieve(STRIPE_LINUX_ARM64_URL, archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != STRIPE_LINUX_ARM64_SHA256:
            raise RuntimeError("Stripe CLI archive digest does not match the repository pin")
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if [member.name for member in members] != ["stripe"] or not members[0].isfile():
                raise RuntimeError("Stripe CLI archive contains unexpected members")
            extracted = bundle.extractfile(members[0])
            if extracted is None:
                raise RuntimeError("Stripe CLI binary could not be extracted")
            binary = extracted.read()
        temporary_binary = destination.with_suffix(".tmp")
        temporary_binary.write_bytes(binary)
        temporary_binary.chmod(0o755)
        temporary_binary.replace(destination)
    result = subprocess.run(
        [str(destination), "version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if STRIPE_VERSION not in result.stdout:
        raise RuntimeError("installed Stripe CLI did not report the pinned version")
    return digest


def sqlite_counts(database: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        table: database.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables
    }


def backup_and_verify(database_path: Path, backup_path: Path) -> dict[str, Any]:
    if not database_path.is_file():
        raise RuntimeError("database does not exist; migrate before taking the proof backup")
    backup_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)
        source_counts = sqlite_counts(source)
    backup_path.chmod(0o600)
    with sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True) as restored:
        integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
        restored_counts = sqlite_counts(restored)
    if integrity != "ok" or restored_counts != source_counts:
        raise RuntimeError("SQLite backup restore proof failed")
    return {
        "sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest(),
        "integrity_check": integrity,
        "table_counts": restored_counts,
    }


def stripe_environment(secret_key: str, price_id: str, webhook_secret: str) -> str:
    values = (secret_key, price_id, webhook_secret)
    if any(not value or any(character.isspace() for character in value) for value in values):
        raise ValueError("Stripe credentials must be non-empty single-line values")
    if not secret_key.startswith("sk_test_") or secret_key.startswith("sk_live_"):
        raise ValueError("Stripe secret key must be a test-mode sk_test_ key")
    if not price_id.startswith("price_"):
        raise ValueError("Stripe Price must be a price_ identifier")
    if not webhook_secret.startswith("whsec_"):
        raise ValueError("Stripe webhook secret must be a whsec_ value")
    return (
        f"STRIPE_SECRET_KEY={secret_key}\n"
        f"STRIPE_API_KEY={secret_key}\n"
        f"STRIPE_PRICE_ID={price_id}\n"
        f"STRIPE_WEBHOOK_SECRET={webhook_secret}\n"
    )


def _prepare(args: argparse.Namespace) -> None:
    home = args.home.resolve()
    checkout_root = args.checkout_root.resolve()
    source_root = checkout_root / "services" / "premium"
    if not (source_root / "pyproject.toml").is_file():
        raise SystemExit("service root does not contain the premium project")
    observed_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip()
    if observed_sha != args.build_sha:
        raise SystemExit("build SHA must exactly match the checked-out commit")
    tracked_delta = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", "services/premium"],
        cwd=checkout_root,
        check=False,
        timeout=20,
    )
    if tracked_delta.returncode != 0:
        raise SystemExit("premium source has tracked changes; commit before host preparation")
    origin = _canonical_origin(args.public_origin, args.https_port)
    status = _read_serve_status(args.tailscale_command)
    assert_serve_listener_unused(status, args.https_port)
    assert_loopback_port_unused(args.loopback_port)

    config_dir = home / ".config" / "podcast-reader-premium"
    data_dir = home / ".local" / "share" / "podcast-reader-premium"
    state_dir = home / ".local" / "state" / "podcast-reader-premium"
    unit_dir = home / ".config" / "systemd" / "user"
    tools_dir = data_dir / "tools" / f"stripe-v{STRIPE_VERSION}"
    release_dir = data_dir / "releases" / args.build_sha
    for directory in (config_dir, data_dir, state_dir, tools_dir):
        _private_dir(directory)

    if not release_dir.exists():
        release_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="premium-release-", dir=release_dir.parent) as name:
            temporary_release = Path(name)
            archive = temporary_release / "release.tar"
            subprocess.run(
                [
                    "git",
                    "archive",
                    "--format=tar",
                    "--output",
                    str(archive),
                    args.build_sha,
                    "services/premium",
                ],
                cwd=checkout_root,
                check=True,
                timeout=30,
            )
            with tarfile.open(archive, "r:") as bundle:
                bundle.extractall(temporary_release, filter="data")
            archive.unlink()
            temporary_release.rename(release_dir)
    service_root = release_dir / "services" / "premium"
    subprocess.run(
        [str(args.uv.resolve()), "sync", "--frozen", "--no-dev"],
        cwd=service_root,
        check=True,
        timeout=300,
    )

    baseline = state_dir / "serve-before.json"
    _atomic_private_text(baseline, json.dumps(status, sort_keys=True, separators=(",", ":")) + "\n")
    service_env = config_dir / "service.env"
    pepper = secrets.token_urlsafe(48)
    if service_env.exists():
        for line in service_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("PREMIUM_USER_CODE_PEPPER="):
                pepper = line.partition("=")[2]
    if len(pepper) < 32 or any(character.isspace() for character in pepper):
        raise SystemExit("existing user-code pepper is malformed; refusing to replace it")
    _atomic_private_text(
        service_env,
        f"PREMIUM_USER_CODE_PEPPER={pepper}\nPREMIUM_BUILD_SHA={args.build_sha}\n",
    )

    stripe = tools_dir / "stripe"
    stripe_digest = install_stripe_cli(stripe)
    replacements = {
        "SERVICE_ROOT": str(service_root),
        "SERVICE_ENV": str(service_env),
        "STRIPE_ENV": str(config_dir / "stripe.env"),
        "DATABASE": str(data_dir / "premium.sqlite3"),
        "PUBLIC_ORIGIN": origin,
        "LOOPBACK_PORT": str(args.loopback_port),
        "DATA_DIR": str(data_dir),
        "STATE_DIR": str(state_dir),
        "PREMIUM_DEV": str(service_root / ".venv" / "bin" / "premium-dev"),
        "STRIPE": str(stripe),
    }
    template_dir = service_root / "deploy"
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name in ("premium-dev.service", "premium-stripe-forwarder.service"):
        template = (template_dir / f"{name}.in").read_text(encoding="utf-8")
        (unit_dir / name).write_text(render_unit(template, replacements), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=20)
    evidence = {
        "activation_performed": False,
        "build_sha": args.build_sha,
        "database": str(data_dir / "premium.sqlite3"),
        "loopback_target": f"http://127.0.0.1:{args.loopback_port}",
        "public_origin": origin,
        "serve_baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "serve_listener_conflict": False,
        "stripe_cli": {"version": STRIPE_VERSION, "archive_sha256": stripe_digest},
        "stripe_credentials_installed": (config_dir / "stripe.env").is_file(),
        "units": ["premium-dev.service", "premium-stripe-forwarder.service"],
    }
    evidence_path = state_dir / "prepare-evidence.json"
    _atomic_private_text(evidence_path, json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, indent=2, sort_keys=True))


def _backup(args: argparse.Namespace) -> None:
    proof = backup_and_verify(args.database.resolve(), args.output.resolve())
    manifest = args.output.with_suffix(args.output.suffix + ".json")
    _atomic_private_text(manifest, json.dumps(proof, indent=2, sort_keys=True) + "\n")
    print(json.dumps(proof, indent=2, sort_keys=True))


def _credentials(args: argparse.Namespace) -> None:
    secret_key = getpass.getpass("Stripe test secret key: ")
    price_id = input("Stripe test Price ID: ").strip()
    webhook_secret = getpass.getpass("Stripe CLI webhook signing secret: ")
    contents = stripe_environment(secret_key, price_id, webhook_secret)
    path = args.home.resolve() / ".config" / "podcast-reader-premium" / "stripe.env"
    _atomic_private_text(path, contents)
    print(f"installed validated test-mode Stripe credentials in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the existing-host premium dev service")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--home", type=Path, required=True)
    prepare.add_argument("--checkout-root", type=Path, required=True)
    prepare.add_argument("--public-origin", required=True)
    prepare.add_argument("--https-port", type=int, default=8443)
    prepare.add_argument("--loopback-port", type=int, default=8090)
    prepare.add_argument("--build-sha", required=True)
    prepare.add_argument("--uv", type=Path, required=True)
    prepare.add_argument("--tailscale-command", default="tailscale")
    backup = subparsers.add_parser("backup")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    credentials = subparsers.add_parser("install-stripe-credentials")
    credentials.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        _prepare(args)
    elif args.command == "backup":
        _backup(args)
    else:
        _credentials(args)


if __name__ == "__main__":
    main()
