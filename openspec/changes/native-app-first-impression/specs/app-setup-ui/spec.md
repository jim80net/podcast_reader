# app-setup-ui

## ADDED Requirements

### Requirement: First-run wizard presents polished onboarding
The first-run setup wizard SHALL present a polished, welcoming onboarding: a hero with the
app mark and a welcome heading, a brief intro explaining that transcription runs locally and
the one-time component download, and clearly-labelled sections for the hardware summary and
the components to install. This is a presentation requirement layered on the existing wizard
behavior — the hardware summary, the recommended-pack pre-selection with sizes, install with
live progress, resume for resumable packs, the skip action, and the first-run-flag gate are
unchanged. All wizard content SHALL be built via the renderer's `el()`/textContent DOM
construction (no `innerHTML`).

#### Scenario: Welcoming hero on first run
- **WHEN** the setup wizard opens on first run
- **THEN** it shows the app mark, a welcome heading, an intro, and labelled hardware and
  components sections

#### Scenario: Polish does not change wizard behavior
- **WHEN** the user selects recommended packs and installs from the polished wizard
- **THEN** install proceeds with live progress and completing or skipping sets the first-run
  flag exactly as before
