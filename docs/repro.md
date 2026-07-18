# One command for walk and browser repros

Run from the repository root. The default `walk` suite is the weekly-walk proof:

```bash
python3 scripts/repro.py
```

The command diagnoses every prerequisite before starting expensive work, prints
the equivalent focused commands, builds app/extension assets when their suites
are selected, and uses Xvfb automatically on headless Linux.

| Suite | What it proves |
|---|---|
| `walk` | Durable walk manifests, generated transcript golden coherence, and the plain-browser artifact geometry/integrity regressions |
| `app` | Desktop production build plus the mock-engine and real-engine Electron Playwright projects |
| `extension` | MV3 production build plus headed extension Playwright against the shared mock engine |
| `all` | All three suites in the order above |

Focus one scenario by Playwright title, inspect the plan, or diagnose only:

```bash
python3 scripts/repro.py walk --grep "extension decoration"
python3 scripts/repro.py app --grep "Library search"
python3 scripts/repro.py all --check-only
python3 scripts/repro.py all --list
```

## Result contract

- exit `0`: every selected proof passed, or `--check-only` found the host ready;
- exit `1`: a build or product assertion failed after the preflight passed;
- exit `3`: the local environment cannot run the selection, and no test started.

An unavailable host is not a relaxed gate. Run the local unit/static coverage and
use exact-SHA hosted E2E for the missing display/platform leg; the hosted app and
extension jobs invoke this same command. Typical remedies are printed directly:
`uv sync --extra dev`, `npm ci`, `npx playwright install chromium`, Node 24, or
Xvfb. An incomplete Electron package points to the explicit
`node node_modules/electron/install.js` repair. Missing committed transcript goldens point to
`uv run python tests/regen_goldens.py`.

The command never installs dependencies or browsers implicitly. Build steps are
part of the selected proof; prerequisite repair remains an explicit developer
action, so a test run cannot silently change the host toolchain.
