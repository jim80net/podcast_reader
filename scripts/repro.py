"""Run repository walk and browser repro suites through one diagnosed entry point."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_UNAVAILABLE = 3
SUITE_ORDER = ("walk", "app", "extension")
SUITE_DESCRIPTIONS = {
    "walk": "durable manifests, generated-golden coherence, and artifact browser regressions",
    "app": "desktop build plus mock-engine and real-engine Electron Playwright",
    "extension": "MV3 build plus headed extension Playwright against the mock engine",
}
GOLDEN_FIXTURES = (
    "tests/fixtures/sample_expected.html",
    "tests/fixtures/sample_expected_longform.html",
    "tests/fixtures/sample_expected_near_hour.html",
    "tests/fixtures/sample_expected_search.html",
)


@dataclass(frozen=True)
class Step:
    suite: str
    label: str
    cwd: Path
    argv: tuple[str, ...]
    needs_display: bool = False


def _which(name: str) -> str | None:
    return shutil.which(name)


def _probe(
    argv: Sequence[str], *, cwd: Path, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(argv, 127, "", "")


def _node_major(root: Path) -> int | None:
    result = _probe(("node", "--version"), cwd=root)
    if result.returncode != 0:
        return None
    match = re.fullmatch(r"v(\d+)(?:\.\d+){2}\s*", result.stdout)
    return int(match.group(1)) if match is not None else None


def _python_ready(root: Path) -> bool:
    result = _probe(
        (
            "uv",
            "run",
            "--offline",
            "python",
            "-c",
            "import podcast_reader, pytest",
        ),
        cwd=root,
    )
    return result.returncode == 0


def _node_executable(package_root: Path, expression: str) -> Path | None:
    result = _probe(("node", "-e", expression), cwd=package_root)
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    if not output:
        return None
    executable = Path(output)
    return executable if executable.is_file() else None


def _browser_executable(package_root: Path) -> Path | None:
    return _node_executable(
        package_root,
        "const {chromium}=require('playwright'); console.log(chromium.executablePath())",
    )


def _electron_executable(package_root: Path) -> Path | None:
    return _node_executable(package_root, "console.log(require('electron'))")


def _expand_suites(requested: str) -> tuple[str, ...]:
    return SUITE_ORDER if requested == "all" else (requested,)


def build_steps(root: Path, suites: Sequence[str], grep: str | None = None) -> list[Step]:
    grep_args = ("--grep", grep) if grep is not None else ()
    steps: list[Step] = []
    if "walk" in suites:
        manifests = tuple(
            str(path.relative_to(root))
            for path in sorted((root / "docs/walk-repros").glob("*.json"))
        )
        steps.extend(
            (
                Step(
                    "walk",
                    "verify durable walk manifests",
                    root,
                    ("uv", "run", "python", "scripts/walk_repros.py", "verify", *manifests),
                ),
                Step(
                    "walk",
                    "verify generated transcript goldens",
                    root,
                    ("uv", "run", "pytest", "tests/test_html.py", "-q"),
                ),
                Step(
                    "walk",
                    "run artifact browser regressions",
                    root / "app",
                    (
                        "npx",
                        "--no-install",
                        "playwright",
                        "test",
                        "tests/e2e/artifact-geometry.spec.ts",
                        "--project=e2e",
                        *grep_args,
                    ),
                ),
            )
        )
    if "app" in suites:
        steps.extend(
            (
                Step("app", "build desktop app", root / "app", ("npm", "run", "build")),
                Step(
                    "app",
                    "run desktop Playwright (mock and real engine)",
                    root / "app",
                    ("npx", "--no-install", "playwright", "test", *grep_args),
                    needs_display=True,
                ),
            )
        )
    if "extension" in suites:
        steps.extend(
            (
                Step(
                    "extension", "build MV3 extension", root / "extension", ("npm", "run", "build")
                ),
                Step(
                    "extension",
                    "run headed extension Playwright",
                    root / "extension",
                    ("npx", "--no-install", "playwright", "test", *grep_args),
                    needs_display=True,
                ),
            )
        )
    return steps


def prerequisite_errors(
    root: Path,
    suites: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> list[str]:
    environment = os.environ if environ is None else environ
    host_platform = sys.platform if platform is None else platform
    errors: list[str] = []
    needs_python = "walk" in suites or "app" in suites
    package_roots = [root / name for name in ("app", "extension") if name in suites]
    if "walk" in suites:
        package_roots.append(root / "app")

    if needs_python:
        if _which("uv") is None:
            errors.append(
                "uv is missing; install uv and run `uv sync --extra dev` from the repo root"
            )
        elif not _python_ready(root):
            errors.append(
                "the Python dev environment is unavailable; run `uv sync --extra dev` "
                "from the repo root"
            )

    needs_node = bool(package_roots)
    if needs_node and (_which("node") is None or _which("npm") is None or _which("npx") is None):
        errors.append("Node/npm/npx are missing; install Node 24")
        node_major = None
    else:
        node_major = _node_major(root) if needs_node else None
    minimum_node = 24 if "app" in suites or "extension" in suites else 20
    if needs_node and node_major is not None and node_major < minimum_node:
        errors.append(
            f"Node {node_major} is too old for this selection; install Node {minimum_node}+"
        )
    if (
        needs_node
        and node_major is None
        and not any(item.startswith("Node/npm") for item in errors)
    ):
        errors.append("Node version could not be determined; install Node 24")

    seen_packages: set[Path] = set()
    for package_root in package_roots:
        if package_root in seen_packages:
            continue
        seen_packages.add(package_root)
        package_name = package_root.name
        if not (package_root / "node_modules/@playwright/test/package.json").is_file():
            errors.append(
                f"{package_name} dependencies are missing; run `cd {package_name} && npm ci`"
            )
            continue
        if _browser_executable(package_root) is None:
            errors.append(
                f"{package_name} Chromium is missing; run "
                f"`cd {package_name} && npx playwright install chromium`"
            )

    if "app" in suites:
        electron_package = root / "app/node_modules/electron/package.json"
        if not electron_package.is_file():
            errors.append("Electron is missing; run `cd app && npm ci`")
        elif _electron_executable(root / "app") is None:
            errors.append("Electron is incomplete; run `cd app && npm ci`")

    if "walk" in suites:
        manifests = sorted((root / "docs/walk-repros").glob("*.json"))
        if not manifests:
            errors.append("no durable walk manifests exist under docs/walk-repros/")
        for relative in GOLDEN_FIXTURES:
            if not (root / relative).is_file():
                errors.append(
                    f"generated golden is missing: {relative}; run "
                    "`uv run python tests/regen_goldens.py`"
                )

    needs_display = "app" in suites or "extension" in suites
    has_display = bool(environment.get("DISPLAY") or environment.get("WAYLAND_DISPLAY"))
    if (
        needs_display
        and host_platform.startswith("linux")
        and not has_display
        and _which("xvfb-run") is None
    ):
        errors.append("no display or xvfb-run is available; install Xvfb or run on hosted E2E")
    return errors


def _materialize(step: Step, environment: Mapping[str, str]) -> tuple[str, ...]:
    has_display = bool(environment.get("DISPLAY") or environment.get("WAYLAND_DISPLAY"))
    if step.needs_display and sys.platform.startswith("linux") and not has_display:
        return ("xvfb-run", "-a", *step.argv)
    return step.argv


def _print_plan(steps: Sequence[Step], environment: Mapping[str, str]) -> None:
    print("[repro] drill-down commands:")
    for step in steps:
        relative = step.cwd.relative_to(REPO_ROOT)
        location = "." if not relative.parts else relative.as_posix()
        print(f"  ({location}) {shlex.join(_materialize(step, environment))}")


def run_steps(steps: Sequence[Step], environment: Mapping[str, str]) -> int:
    _print_plan(steps, environment)
    for step in steps:
        argv = _materialize(step, environment)
        print(f"[repro] RUN {step.suite}: {step.label}", flush=True)
        try:
            result = subprocess.run(argv, cwd=step.cwd, env=dict(environment), check=False)
        except OSError as exc:
            print(
                f"[repro] ENVIRONMENT BECAME UNAVAILABLE in {step.suite}: {exc}",
                file=sys.stderr,
            )
            return ENVIRONMENT_UNAVAILABLE
        if result.returncode != 0:
            print(
                f"[repro] PRODUCT ASSERTION FAILED in {step.suite}: {step.label} "
                f"(exit {result.returncode})",
                file=sys.stderr,
            )
            return 1
    print("[repro] PASS: every selected repro suite completed")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "suite",
        nargs="?",
        choices=(*SUITE_ORDER, "all"),
        default="walk",
        help="suite to run (default: walk)",
    )
    parser.add_argument("--grep", metavar="PATTERN", help="focus Playwright tests by title")
    parser.add_argument(
        "--check-only", action="store_true", help="diagnose prerequisites without running tests"
    )
    parser.add_argument("--list", action="store_true", help="list suite purpose and commands")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    suites = _expand_suites(args.suite)
    steps = build_steps(REPO_ROOT, suites, args.grep)
    if args.list:
        for suite in suites:
            print(f"{suite}: {SUITE_DESCRIPTIONS[suite]}")
        _print_plan(steps, os.environ)
        return 0

    errors = prerequisite_errors(REPO_ROOT, suites)
    if errors:
        print("[repro] ENVIRONMENT UNAVAILABLE; no tests were started:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        _print_plan(steps, os.environ)
        print(
            "[repro] exit 3 means missing local capability, not a product-test failure",
            file=sys.stderr,
        )
        return ENVIRONMENT_UNAVAILABLE
    if args.check_only:
        print(f"[repro] READY: prerequisites satisfied for {', '.join(suites)}")
        _print_plan(steps, os.environ)
        return 0
    return run_steps(steps, os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
