# tools-seeding

## MODIFIED Requirements

### Requirement: Extraction-failure self-update with single retry
The retry hook SHALL be implemented in `ytdlp.py` and gated purely on resolved-binary residence — no engine or CLI flag (per Q3): when a download fails with a structured yt-dlp error on a URL source (`download_failed`, per S7) and the resolved yt-dlp resides in the user-data tools directory, `ytdlp.py` SHALL run `yt-dlp -U` once and retry the download exactly once, emitting a warning event describing the self-update attempt. A second failure SHALL surface the normal structured error. Because the gate is residence alone, any caller — engine job or CLI — gets identical behavior whenever the managed copy is in play (per Q3). The retry SHALL remain scoped to `download_failed` (per U2, adjudicating the chrome-extension change's error-code split): authentication-required failures, which now raise the distinct code `download_auth_required`, SHALL surface immediately with no self-update attempt — a yt-dlp update cannot conjure missing credentials. (`pack_manager`'s `download_failed` is a separate pack-error namespace and is untouched by this adjudication.)

#### Scenario: Extractor breakage heals in-job
- **WHEN** a download fails, the self-update installs a newer yt-dlp, and the retry succeeds
- **THEN** the job proceeds normally with a warning recording the recovery

#### Scenario: Persistent failure surfaces once
- **WHEN** the retry after self-update also fails
- **THEN** the job fails with the structured download error and no further retries occur

#### Scenario: Auth-required failure skips the self-update retry (per U2)
- **WHEN** a download on the managed yt-dlp copy fails with `download_auth_required`
- **THEN** no `yt-dlp -U` runs and no retry occurs — the structured auth error surfaces immediately
