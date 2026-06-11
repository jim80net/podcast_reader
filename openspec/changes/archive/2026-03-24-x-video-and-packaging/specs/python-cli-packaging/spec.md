## ADDED Requirements

### Requirement: Installable CLI via uv or pip
Users SHALL be able to install the tool as a proper Python package and invoke it as `podcast-reader`.

#### Scenario: uv tool install
- **WHEN** a user runs `uv tool install .` in the project
- **THEN** `podcast-reader` becomes available on PATH and runs the CLI

#### Scenario: Development run
- **WHEN** a developer runs `uv run podcast-reader --help`
- **THEN** the CLI usage is shown without manual PYTHONPATH manipulation

### Requirement: Console script entry point
The package SHALL declare a console script entry point so the CLI works after installation without `python -m`.

#### Scenario: Entry point resolution
- **WHEN** the package is installed in an environment
- **THEN** `podcast-reader` resolves to `podcast_reader.cli:main`

### Requirement: Clear separation of runtime vs dev dependencies
Heavy optional backends (whisper, diarization) SHALL be installable via extras so base users do not pull torch/CUDA.

#### Scenario: Base install
- **WHEN** a user does `uv sync` (no extras)
- **THEN** whisper-ctranslate2 and pyannote are not installed

#### Scenario: Whisper extra
- **WHEN** a user does `uv sync --extra whisper`
- **THEN** whisper-ctranslate2 becomes available
