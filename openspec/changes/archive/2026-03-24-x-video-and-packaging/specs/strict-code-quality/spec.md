## ADDED Requirements

### Requirement: Strict static typing on all library code
All Python source under `src/podcast_reader/` SHALL type-check cleanly under mypy --strict.

#### Scenario: CI / dev check
- **WHEN** `uv run mypy src/` is run
- **THEN** exit code is 0 with no errors

### Requirement: Consistent formatting and linting
All committed Python code SHALL pass ruff check and ruff format --check.

#### Scenario: Pre-commit or CI gate
- **WHEN** ruff is run on src/ and tests/
- **THEN** no violations are reported

### Requirement: Unit vs integration test separation
Fast unit tests (no network, no external CLIs except mocked) SHALL be runnable without the integration marker; slow/network tests SHALL be marked.

#### Scenario: Fast feedback
- **WHEN** `uv run pytest -m "not integration"`
- **THEN** only pure unit tests (with subprocess mocks) execute and complete in < 30s

#### Scenario: Full suite
- **WHEN** `uv run pytest`
- **THEN** all tests including integration (marked) run
