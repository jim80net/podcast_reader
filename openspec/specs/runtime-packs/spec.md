# runtime-packs Specification

## Purpose
TBD - created by archiving change download-manager. Update Purpose after archive.
## Requirements
### Requirement: CUDA runtime pack
The CUDA pack (Windows-only, per the parent design) SHALL be acquired by downloading the registry-pinned `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` wheels from PyPI (sha256-verified), extracting the complete runtime DLL set — all `nvidia/cublas/bin/*.dll` and `nvidia/cudnn/bin/*.dll`, never a trimmed cuDNN subset — into `<data_dir>/runtime/`, and deleting the wheel archives. The pinned wheel versions SHALL satisfy the frozen ctranslate2's strict pairing (CUDA 12.x + cuDNN 9 for ctranslate2 ≥ 4.5), recorded as the pack's compat range. The runtime directory SHALL be private to the application (no PATH or system-directory installation), and the pack's manifest SHALL carry the required NVIDIA notices (cuBLAS modified-BSD attribution; the cuDNN source-code notice).

#### Scenario: Complete DLL set installed
- **WHEN** the CUDA pack install completes
- **THEN** `<data_dir>/runtime/` contains the full cuDNN 9 DLL family and `cublas64_12` + `cublasLt64_12`

#### Scenario: Version pairing enforced
- **WHEN** an installed CUDA pack's component versions fall outside the engine's ctranslate2 compat range after an app update
- **THEN** the pack is flagged incompatible and the worker path does not use it

#### Scenario: Not offered off-platform
- **WHEN** packs are listed on macOS or Linux
- **THEN** the CUDA pack is not installable there

### Requirement: Whisper model packs
Model packs SHALL download the registry-pinned Hugging Face snapshot files (exact revision, per-file sha256) of the faster-whisper model repositories into `<data_dir>/models/<name>/`, containing every file required for offline loading (model weights, config, tokenizer, vocabulary, preprocessor config). The registry SHALL include at minimum `tiny`, `small`, `medium`, and `large-v3`. Installed model directories SHALL be loadable by the whisper worker with no network access.

#### Scenario: Offline load succeeds
- **WHEN** a model pack is installed and the worker transcribes with that model directory and HF offline mode
- **THEN** transcription succeeds without any network request

#### Scenario: Revision pinned
- **WHEN** a model pack downloads
- **THEN** every file is fetched at the registry's pinned revision and verified against its pinned sha256

### Requirement: Hardware detection
The engine SHALL detect NVIDIA GPU presence by probing `nvidia-smi` (PATH, plus the standard Windows System32 location), caching the result for the process lifetime, and SHALL report `{platform, nvidia_gpu, gpu_names}` with the pack listing. Probe failure of any kind SHALL degrade to `nvidia_gpu: false` — detection must never break the packs endpoint.

#### Scenario: NVIDIA machine detected
- **WHEN** `nvidia-smi` succeeds and reports a GPU name
- **THEN** the pack listing carries `nvidia_gpu: true` with the reported names

#### Scenario: Probe failure degrades cleanly
- **WHEN** `nvidia-smi` is absent or errors
- **THEN** the pack listing carries `nvidia_gpu: false` and the endpoint succeeds

### Requirement: Hardware-derived recommendations
Pack recommendations SHALL be computed engine-side from detected hardware: the CUDA pack recommended iff Windows with an NVIDIA GPU; `large-v3` recommended with a GPU, a CPU-appropriate model (`small` or `medium`) otherwise; the diarization pack never recommended by default (strictly opt-in).

#### Scenario: GPU machine recommendation
- **WHEN** packs are listed on Windows with an NVIDIA GPU
- **THEN** the CUDA pack and `large-v3` are `recommended: true`

#### Scenario: CPU machine recommendation
- **WHEN** packs are listed on a machine without an NVIDIA GPU
- **THEN** the CUDA pack is not recommended and a CPU-appropriate model is

