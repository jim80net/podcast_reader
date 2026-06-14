# job-pipeline Specification (delta)

## ADDED Requirements

### Requirement: Transcript artifact carries playback-sync metadata
The rendered transcript HTML SHALL carry machine-readable timestamp metadata and a playback-sync script so a host media player can synchronize with it. Each passage element and chapter section SHALL include `data-start` (and `data-end`) seconds. The artifact SHALL include an inline sync script that, when hosted inside the app, posts a seek message on passage click and highlights/scrolls the current passage on time updates; when the artifact is opened standalone (no parent window), the script SHALL no-op so the file remains self-contained and behaves exactly as before. The output SHALL be identical whether produced by the CLI or the engine, and the additions SHALL be inert for any existing consumer that ignores them.

#### Scenario: Passages carry timestamps
- **WHEN** the transcript HTML is rendered
- **THEN** each passage and chapter section carries `data-start` (and `data-end`) seconds

#### Scenario: Sync script is inert standalone
- **WHEN** the rendered artifact is opened directly in a browser with no parent player
- **THEN** the sync script does nothing and the page renders and behaves as it did before this change

#### Scenario: CLI and engine output match
- **WHEN** the same transcript is rendered by the CLI and by the engine
- **THEN** the produced HTML, including the sync metadata and script, is identical
