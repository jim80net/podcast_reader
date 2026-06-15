# app-views

## ADDED Requirements

### Requirement: Library empty state is a branded first-transcript CTA
When the library is empty, the Library view SHALL show a branded, welcoming empty state: the
app mark, a one-line value proposition, and a primary "Transcribe your first episode" call-to-action
that routes to the New view. The non-empty library (the cards listing) is unchanged. The
empty state SHALL be built via the renderer's `el()`/textContent DOM construction (no
`innerHTML`).

#### Scenario: Empty library invites the first transcript
- **WHEN** the Library view renders with no entries
- **THEN** it shows the app mark, a value-prop line, and a primary CTA labelled "Transcribe
  your first episode"

#### Scenario: The CTA routes to New
- **WHEN** the user activates the empty-state CTA
- **THEN** the app navigates to the New view
