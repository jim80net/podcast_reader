# tools-seeding Specification (delta)

## ADDED Requirements

### Requirement: Seed reconciliation at engine startup
The frozen bundle SHALL carry yt-dlp/ffmpeg/ffprobe seed binaries plus a build-generated `tools-manifest.json` recording each seed's version. At every engine startup, before serving, the engine SHALL reconcile seeds into `<data_dir>/tools/`: copy a seed (atomically, preserving execute permission) when the user-data copy is absent or the seed's manifest version is newer than the recorded user-data version — implementing the tool-resolution spec's seeding-time newer-wins contract. Versions SHALL be compared via manifests, never by executing binaries. Seeding failure SHALL log and continue — the engine serves regardless.

#### Scenario: First run seeds the tools
- **WHEN** the frozen engine starts with an empty user-data tools directory
- **THEN** yt-dlp, ffmpeg, and ffprobe are copied there with a manifest recording their versions

#### Scenario: Newer seed replaces an older user copy
- **WHEN** an app update ships a seed newer than the recorded user-data version
- **THEN** the next engine start replaces the user-data copy and updates the recorded version

#### Scenario: Self-updated user copy preserved
- **WHEN** the user-data yt-dlp recorded version is newer than the bundled seed (a prior `yt-dlp -U`)
- **THEN** seeding leaves the user-data copy untouched

#### Scenario: Seeding failure does not prevent serving
- **WHEN** a seed copy fails (e.g. permission error)
- **THEN** the engine logs a warning and serves normally

### Requirement: Managed tools directory as effective default
The engine SHALL export `PODCAST_READER_TOOLS_DIR=<data_dir>/tools` for its own process when the variable is unset, so every existing `resolve_tool` call site resolves the managed copies without call-site changes. An explicitly set variable SHALL be respected.

#### Scenario: Engine jobs use the managed tools
- **WHEN** a frozen engine job invokes yt-dlp with no env override configured
- **THEN** the user-data tools copy is the one executed

#### Scenario: Explicit override wins
- **WHEN** `PODCAST_READER_TOOLS_DIR` is already set in the engine's environment
- **THEN** the engine does not overwrite it

### Requirement: Scheduled yt-dlp self-update
The engine SHALL run `yt-dlp -U` against the user-data copy in a background thread at startup when the last successful update check recorded in the user-data tools manifest is older than 24 hours, recording the new version and check time on success. Self-update SHALL run only when the resolved yt-dlp resides in the user-data tools directory (release binaries support `-U`); PATH- or env-resolved copies SHALL never be updated. The signed install directory SHALL never be written.

#### Scenario: Stale check triggers update
- **WHEN** the engine starts more than 24 hours after the last recorded check
- **THEN** `yt-dlp -U` runs against the user-data copy in the background

#### Scenario: Dev environments untouched
- **WHEN** yt-dlp resolves from PATH (no user-data copy)
- **THEN** no self-update is attempted

### Requirement: Extraction-failure self-update with single retry
The retry hook SHALL be implemented in `ytdlp.py` and gated purely on resolved-binary residence — no engine or CLI flag (per Q3): when a download fails with a structured yt-dlp error on a URL source (`download_failed`, per S7) and the resolved yt-dlp resides in the user-data tools directory, `ytdlp.py` SHALL run `yt-dlp -U` once and retry the download exactly once, emitting a warning event describing the self-update attempt. A second failure SHALL surface the normal structured error. Because the gate is residence alone, any caller — engine job or CLI — gets identical behavior whenever the managed copy is in play (per Q3).

#### Scenario: Extractor breakage heals in-job
- **WHEN** a download fails, the self-update installs a newer yt-dlp, and the retry succeeds
- **THEN** the job proceeds normally with a warning recording the recovery

#### Scenario: Persistent failure surfaces once
- **WHEN** the retry after self-update also fails
- **THEN** the job fails with the structured download error and no further retries occur
