# Download Manager & Packs — Tasks

## 1. Pack manager core (engine)

- [ ] 1.1 `engine/packs.py` registry: pack dataclasses/TypedDicts (id, kind, platform gate, download spec with pinned URLs+sha256+sizes, component versions, compat range, license notices); entries for `cuda-runtime` (pinned nvidia wheels), `model-{tiny,small,medium,large-v3}` (pinned HF revisions + per-file sha256), `diarization` (GitHub Release URL placeholder until 7.5 publishes); pure-function platform/compat evaluation; unit tests without network
- [ ] 1.2 Downloader: httpx streaming to `.part` staging with HTTP Range resume, sha256 verify on completion (mismatch → delete + structured `failed`), wheel-extraction step for the CUDA pack (complete `nvidia/*/bin/*.dll` set, archives deleted after extraction); unit tests with a local httpx mock transport incl. resume-from-offset and hash-mismatch
- [ ] 1.3 Installer: `PackManager` with a dedicated FIFO installer thread (one transfer at a time, independent of the job worker), `pack-manifest.json` written last (atomic-by-construction install), state derived from disk (manifest → installed; partials → resumable) plus in-memory installing state; idempotent install requests; uninstall (refuse 409 while a job runs or pack installing); unit tests incl. crash-mid-install → not installed
- [ ] 1.4 Startup compat validation in `serve_engine`: validate installed manifests against the registry (pack_schema + component pairings, e.g. ctranslate2↔cuDNN), flag `incompatible` (treated as absent by the pipeline); tests for range-moved-by-update and clean-pass cases

## 2. Pack API, events, hardware

- [ ] 2.1 Hardware detection: `nvidia-smi` probe (PATH + Windows System32), process-lifetime cache, failure → `nvidia_gpu: false`; recommendation mapping (CUDA iff win+NVIDIA; large-v3 with GPU else small/medium; diarization never default); unit tests with mocked probe
- [ ] 2.2 `engine/app.py` routes: `GET /v1/packs` (hardware + per-pack status/recommended/progress/error), `POST /v1/packs/{id}/install` (202, 404 unknown, idempotent), `DELETE /v1/packs/{id}` (404/409 semantics); TestClient tests incl. auth rejection
- [ ] 2.3 Pack events on the existing SSE fan-out: self-describing pack progress/state events interleaved with job events; `types.py` event additions (incl. the new `step_progress` kind for 3.3 and `diarize` step name for 5.x); tests: pack events observable on `/v1/events`, job-event consumers unaffected by unknown kinds
- [ ] 2.4 Engine version → 0.3.0; app `MIN_ENGINE_VERSION` → 0.3.0 (established bump-together pattern)

## 3. Whisper worker & pipeline switch

- [ ] 3.1 `workers/whisper_worker.py`: argv contract (`AUDIO --model <name-or-dir> --device --compute-type [--language] --output-dir`), faster-whisper in-process, whisper-ctranslate2-shaped JSON with `"words": null`, stderr `progress duration=` / `progress segment_end=` lines, JSON path on stdout, `multiprocessing.freeze_support()` first, Windows `os.add_dll_directory(<data_dir>/runtime)` before import iff present; lazy imports so the main package never pulls faster-whisper; new `worker` extra; unit tests with mocked WhisperModel
- [ ] 3.2 `transcribe.py`/`pipeline.py` switch: prefer `resolve_bundled_worker("whisper-worker")`; worker path maps model name → `<data_dir>/models/<name>` (missing/incompatible → structured `model_missing` failure with pack hint, no mid-job download), effective-device fallback cuda→cpu with reason-naming warning, compute type derived from effective device, POSIX spawn-time `LD_LIBRARY_PATH`; unfrozen path byte-identical (regression tests); unit tests for resolution/fallback/failure
- [ ] 3.3 Streaming progress: incremental worker-stderr consumption mapped to `step_progress` events (seconds + total duration) through the job store to SSE; tests with a scripted fake worker process

## 4. Tools seeding & yt-dlp self-update

- [ ] 4.1 Seeding at engine startup: reconcile bundle `tools/` + `tools-manifest.json` into `<data_dir>/tools/` (absent-or-newer wins, atomic copy preserving exec bits, user-data manifest updated; failures log-and-serve); export `PODCAST_READER_TOOLS_DIR` when unset; unit tests for first-run / newer-seed / newer-user-copy / failure cases
- [ ] 4.2 Scheduled self-update: background `yt-dlp -U` against the user-data copy when last check > 24 h (recorded in the user-data manifest; version re-read from `yt-dlp --version` after success); only when yt-dlp resolves inside the user-data tools dir; tests with mocked subprocess
- [ ] 4.3 Failure-triggered self-update: download-step yt-dlp failure on a URL source → one `-U` + one retry with a warning event; second failure surfaces the normal structured error; pipeline tests for heal and persistent-failure paths

## 5. Diarization (cut-line group — 5.1 gates 5.2–5.5)

- [ ] 5.1 [CUT-LINE GATE] Diarization worker freeze smoke: dedicated CPU-torch venv, `packaging/diarization.spec`, build the frozen worker, run it against the fixture WAV, measure size; if non-viable in size/effort, document the cut-line decision in this file and detach 5.2–5.5 + 7.5 to post-v1 (engine merge/setting code stays dormant behind the absent pack)
- [ ] 5.2 `workers/diarization_worker.py`: WAV via stdlib `wave`+numpy → in-memory waveform → pyannote pipeline from the pack's offline cache → `turns.json`; lazy imports; unit tests with mocked pipeline
- [ ] 5.3 Engine glue: ffmpeg pre-convert to staged 16 kHz mono WAV; `diarize.py` max-overlap merge (pure stdlib, torch-free unit tests); `diarize` step in the pipeline — atomic in-place JSON enrichment, speakers-present = cache hit, worker failure → warning not job failure
- [ ] 5.4 `diarize: bool` setting (default false): `types.py`/`settings.py`/`SettingsBody`, merge-over-defaults upgrade test, warn-and-skip when the pack is absent/incompatible; Settings view toggle
- [ ] 5.5 `html.py` speaker rendering: visible attribution at speaker changes; speakerless output byte-identical (regression test)

## 6. App: wizard & Settings packs

- [ ] 6.1 `src/shared/{types,ipc}.ts`: pack status/event mirrors, `packs:list|install|uninstall` channels, preload `listPacks`/`installPack`/`uninstallPack`; pack events forwarded over the existing engine-event push channel; mock engine (`app/tests/mock-engine/`) grows a scriptable packs surface (states, progress sequences, 409s)
- [ ] 6.2 Setup wizard view: first-run flag (app config; set on complete or skip), hardware summary, recommended packs pre-checked with sizes, install/resume with live progress, skip; lossless navigation (state hydrated from `GET /v1/packs`); re-run entry from Settings
- [ ] 6.3 Settings Packs section: per-pack state/version/size/progress, install/uninstall with engine 409 reasons surfaced, incompatible → re-download affordance, failed → structured error, license attributions from manifests
- [ ] 6.4 Playwright e2e (mock engine): wizard first-run flow, skip-and-rerun, progress hydration after navigation, resumable resume, Settings install/uninstall/incompatible flows; integration smoke key-set parity extended to the pack payloads

## 7. Packaging & CI

- [ ] 7.1 `packaging/`: production `engine.spec` (real `serve_engine` entry + `whisper_worker_entry`, MERGE/COLLECT, hooks-ctranslate2/faster_whisper under version control), `build_engine.py` (downloads/stages tool seeds + writes `tools-manifest.json` into the bundle tools dir, runs PyInstaller, emits the `dist.mjs --engine-dir`-ready layout); local proof: built engine boots and handshakes
- [ ] 7.2 CI `frozen-smoke` rewrite: ubuntu+windows matrix building the real engine; boot with temp data dir → authed handshake (token from `engine-state.json`, sentinel, discovery, health) → `POST /v1/packs/model-tiny/install` → 5 s fixture WAV job → assert `done` + non-empty HTML; HF download + uv caches; spike build retired from CI (spike dir stays as evidence)
- [ ] 7.3 `release.yml`: engine-build steps feeding `npm run dist -- --engine-dir` on the windows/macos legs (publishing still signing-gated per Phase 3 — no change to that gate)
- [ ] 7.4 [USER-BLOCKING] Provision `HF_TOKEN` CI secret with accepted pyannote gated-model terms (segmentation + diarization pipelines) — required by the diarization pack release job; cannot proceed without the user
- [ ] 7.5 [BLOCKED ON 5.1/7.4] `packaging/build_diarization_pack.py` + release job: CPU-torch build env, frozen worker, pre-seeded offline pipeline cache, compressed archive + manifest published to a `pack-diarization-v*` GitHub Release; fail-fast when the secret is absent; registry entry (1.1) updated with the published URL+sha256

## 8. Docs & wrap-up

- [ ] 8.1 Docs: README (packs/first-run, packaging commands, CUDA/model/diarization sizes), CLAUDE.md (new modules, `/v1/packs` surface, `packaging/` rows), `app/README.md` (wizard, packaged-engine dev posture now real); attribution texts (NVIDIA notices, pyannote licenses) wired through pack manifests to the Settings section
- [ ] 8.2 Full gates: pytest (unit), mypy strict, ruff check+format, `tsc --noEmit`, eslint, vitest, Playwright e2e, `openspec validate download-manager`
- [ ] 8.3 Systems-review of the implementation diff; PR referencing this change
