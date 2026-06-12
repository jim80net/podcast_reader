# diarization-worker Specification (delta)

## ADDED Requirements

### Requirement: Diarization worker pack contract
The diarization worker SHALL be a separately frozen executable pack (pyannote.audio + CPU torch, plus a pre-seeded local HF cache containing the full pipeline: segmentation and embedding models with pipeline config, loaded offline — users need no Hugging Face account), installed under `<data_dir>/workers/diarization/` and hosted on GitHub Releases. Its contract: `diarization-worker AUDIO.wav --output turns.json [--num-speakers N]` producing `{"turns": [{"start": float, "end": float, "speaker": str}]}`; exit non-zero with a human-readable stderr tail on failure. The worker SHALL read pre-converted WAV input via stdlib decoding (no torchcodec/FFmpeg shared-library dependency), feeding pyannote an in-memory waveform.

#### Scenario: Worker emits turns JSON
- **WHEN** the worker runs against a 16 kHz mono WAV
- **THEN** it exits 0 and `turns.json` contains speaker turns with start/end floats and speaker labels

#### Scenario: Offline pipeline load
- **WHEN** the worker runs with no network access
- **THEN** the pyannote pipeline loads from the pack's pre-seeded cache

### Requirement: Engine-side pre-convert and speaker merge
For a diarization-enabled job, the engine SHALL pre-convert the input audio to 16 kHz mono WAV using its managed ffmpeg (staged, not retained in the entry), invoke the diarization worker, and perform the speaker merge itself: assign each whisper segment the speaker with maximal positive time-overlap across turns (segments with no overlap keep no speaker), implemented in pure stdlib so the merge is unit-testable without torch. The enriched JSON (segments carrying `speaker`) SHALL be written atomically in place; segments already carrying speakers make the step a cache hit.

#### Scenario: Segments enriched with speakers
- **WHEN** a diarization-enabled job completes
- **THEN** the transcript JSON's segments carry `speaker` labels assigned by maximal overlap

#### Scenario: Merge is idempotent
- **WHEN** a diarization-enabled job re-runs over a transcript whose segments already carry speakers
- **THEN** the diarize step is a cache hit and the worker is not invoked

### Requirement: Diarize setting with graceful skip
`EngineSettings` SHALL gain `diarize: bool` (default false), settable via `PUT /v1/settings` and snapshotted at job dequeue like all settings; settings files predating the field SHALL upgrade cleanly. When `diarize` is true but the diarization pack is not installed (or is incompatible), the job SHALL skip the step with a warning event naming the missing pack — never fail.

#### Scenario: Disabled by default
- **WHEN** a job runs with default settings
- **THEN** no diarization step executes

#### Scenario: Enabled without the pack
- **WHEN** `diarize` is true and the pack is absent
- **THEN** the job completes with a warning that diarization was skipped for lack of the pack

#### Scenario: Worker failure does not kill the job
- **WHEN** the diarization worker exits non-zero during an enabled job
- **THEN** the job records a structured warning and proceeds to render without speakers

### Requirement: Speaker rendering
`build_html` SHALL render speaker labels when segments carry `speaker` (visible attribution at speaker changes), and SHALL render exactly as today when no segment carries one — the field is optional end to end.

#### Scenario: Speakers visible in the transcript
- **WHEN** HTML is built from segments carrying speaker labels
- **THEN** the rendered transcript displays speaker attribution at speaker changes

#### Scenario: Speakerless transcripts unchanged
- **WHEN** HTML is built from segments without speaker fields
- **THEN** the output is unchanged from current behavior

### Requirement: Cut-line on the worker freeze
The diarization worker's frozen build SHALL be validated (build + run smoke) before the rest of the diarization work proceeds. If the freeze proves non-viable in reasonable size or effort, the diarization capability detaches to post-v1 without blocking this change's other capabilities — desktop diarization slips, CLI diarization via whisper-ctranslate2 remains, and the engine merge/setting code paths stay dormant behind the absent pack.

#### Scenario: Freeze smoke gates the group
- **WHEN** the diarization implementation begins
- **THEN** the first delivered artifact is a frozen worker that transcodes the fixture WAV's turns successfully, or a documented cut-line decision
