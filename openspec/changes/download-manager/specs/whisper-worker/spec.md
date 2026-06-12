# whisper-worker Specification (delta)

## ADDED Requirements

### Requirement: Worker invocation contract
The repository SHALL provide a whisper worker program (`src/podcast_reader/workers/whisper_worker.py`, frozen as the `whisper-worker` entry point of the engine onedir) with the contract: `whisper-worker AUDIO --model <name-or-dir> --device cpu|cuda --compute-type <type> [--language xx] --output-dir DIR`. The worker SHALL transcribe in-process via faster-whisper, write whisper-ctranslate2-shaped JSON to `<output-dir>/<stem>.json`, print the absolute JSON path on stdout on success, and exit non-zero with a human-readable stderr tail on failure. The main package SHALL NOT import the worker module (faster-whisper stays an optional extra).

#### Scenario: Successful transcription writes the JSON artifact
- **WHEN** the worker runs against a speech WAV with a valid model
- **THEN** it exits 0, the JSON exists at `<output-dir>/<stem>.json`, and stdout contains that absolute path

#### Scenario: Failure is diagnosable
- **WHEN** the worker fails (bad model path, unreadable audio)
- **THEN** it exits non-zero and stderr ends with a human-readable error

### Requirement: Output shape parity
Worker output SHALL carry top-level `{text, segments, language}` and per-segment fields matching whisper-ctranslate2's JSON (including `"words": null` when word timestamps are not computed), so the output remains a strict superset of what `html.py` and the chapters step consume.

#### Scenario: Renderer consumes worker output unchanged
- **WHEN** worker-produced JSON is fed to the render step
- **THEN** HTML generation succeeds with no shape adaptation

#### Scenario: words field present as null
- **WHEN** the worker transcribes without word timestamps
- **THEN** every segment carries `"words": null`

### Requirement: Progress line protocol
The worker SHALL emit machine-readable progress on stderr: `progress duration=<sec>` once after model load, then `progress segment_end=<sec>` per transcribed segment. The engine SHALL stream worker stderr incrementally and map these lines onto `step_progress` pipeline events for the transcribe step (carrying seconds and total duration), observable over SSE.

#### Scenario: Per-segment progress reaches SSE clients
- **WHEN** a frozen-path transcription job runs while a client subscribes to `GET /v1/events`
- **THEN** the client receives transcribe `step_progress` events with increasing `segment_end` values and the total duration

### Requirement: CUDA runtime directory injection
On Windows the worker SHALL call `os.add_dll_directory(<data_dir>/runtime)` before importing faster-whisper iff that directory exists; on POSIX the engine SHALL set `LD_LIBRARY_PATH` to include the runtime directory when spawning the worker. Every frozen entry point SHALL call `multiprocessing.freeze_support()` first.

#### Scenario: CUDA pack DLLs resolvable at model load
- **WHEN** the CUDA pack is installed on Windows and the worker runs with `--device cuda`
- **THEN** the runtime directory is on the DLL search path before model load

#### Scenario: Missing runtime dir is harmless
- **WHEN** no runtime directory exists
- **THEN** the worker starts normally on the CPU path

### Requirement: Freeze-aware transcribe switch
The pipeline's transcribe step SHALL prefer the bundled worker: when `resolve_bundled_worker("whisper-worker")` resolves, transcription runs via the worker contract; when it returns `None` (unfrozen), the existing `whisper-ctranslate2` shell-out runs unchanged — CLI and dev behavior byte-identical to today, including HF auto-download and `--hf_token` diarization.

#### Scenario: Frozen engine uses the bundled worker
- **WHEN** the frozen engine runs a transcription job
- **THEN** the sibling `whisper-worker` executable is spawned, not a console script

#### Scenario: Unfrozen path unchanged
- **WHEN** an unfrozen run (CLI or dev engine) transcribes
- **THEN** `whisper-ctranslate2` is invoked exactly as before this change

### Requirement: Model pack resolution on the frozen path
On the worker path, the configured whisper model name SHALL resolve to the installed model pack directory (`<data_dir>/models/<name>`), passed to the worker as `--model <dir>` with offline loading. A missing or incompatible model pack SHALL fail the job with structured error code `model_missing` and a hint pointing at pack installation — the engine SHALL NOT auto-download model weights mid-job.

#### Scenario: Installed model resolves to its directory
- **WHEN** a frozen-path job runs with `whisper_model=tiny` and the tiny pack installed
- **THEN** the worker receives the tiny pack directory as `--model`

#### Scenario: Missing model fails with a hint
- **WHEN** a frozen-path job runs with a model whose pack is not installed
- **THEN** the job fails `model_missing` with a hint directing the user to download the model, and no network download is attempted

### Requirement: Device fallback with visible warning
On the worker path, when the configured device is `cuda` but no NVIDIA GPU is detected or the CUDA pack is not installed (or flagged incompatible), the job SHALL proceed on CPU and emit a warning event naming the specific reason — degrade, not fail. Compute type SHALL derive from the effective device (`float16` for cuda, `int8` for cpu).

#### Scenario: CUDA configured without the pack
- **WHEN** a frozen-path job runs with `whisper_device=cuda` and no usable CUDA pack
- **THEN** the job completes on CPU and the record carries a warning identifying why CUDA was unavailable
