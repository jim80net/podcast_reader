# Packaging Spike Report — Frozen Engine + Whisper Worker

OpenSpec change: `engine-extraction`, task group 5. Parent design:
`docs/superpowers/specs/2026-06-11-desktop-packaging-design.md` (v3), section
"Frozen-bundle / downloadable-pack split". All claims below are backed by
artifacts in this directory or cited sources; commands were run on Linux
(WSL2, x86_64, Python 3.10.10) as the proof-of-mechanism platform.

**Versions frozen:** ctranslate2 **4.8.0**, faster-whisper **1.2.1**,
PyInstaller **6.20.0**, pyinstaller-hooks-contrib **2026.6**, fastapi 0.121.x,
uvicorn 0.49.0. Diarization measurement venv: torch **2.12.0+cpu**,
pyannote.audio **4.0.4**, torchcodec 0.14.0+cpu, torchaudio 2.11.0+cpu.

---

## 1. Frozen engine prototype (two entry points, one onedir) — WORKS

### What was built

- `spike_engine/app.py` — stand-in FastAPI engine: `/health` route, pre-bound
  socket (`bind` → `getsockname` → `ENGINE_READY port=N` sentinel on stdout →
  `uvicorn.Server.serve(sockets=[...])`), i.e. the same handshake shape as
  design decision 6.
- `spike_engine/worker.py` — whisper worker calling
  `faster_whisper.WhisperModel` in-process (CPU, int8), writing
  whisper-ctranslate2-shaped JSON to `--output-dir`, progress lines on stderr,
  final JSON path on stdout.
- `engine.spec` — **two `Analysis`/`EXE` objects feeding ONE `COLLECT`**, which
  is the mechanism that yields a single onedir with both executables sharing
  one `_internal/`:

  ```
  dist/spike-engine/
    spike-engine        (6.1 MB bootloader+PKG)
    whisper-worker      (8.6 MB bootloader+PKG)
    _internal/          (374 MB, shared by both)
  ```

### Hooks needed (the ctranslate2 quirk, confirmed)

pyinstaller-hooks-contrib **2026.6 ships NO hook for `ctranslate2` or
`faster_whisper`** (verified by listing `_pyinstaller_hooks_contrib/stdhooks/`;
it does ship `hook-av` and `hook-onnxruntime`, which we rely on). Two custom
hooks in `hooks/` were required:

- `hook-ctranslate2.py`: `collect_dynamic_libs("ctranslate2")` +
  `collect_data_files("ctranslate2")`. Rationale: on Windows,
  `ctranslate2/__init__.py` (read from the installed 4.8.0 wheel) does
  `os.add_dll_directory(package_dir)` and then `ctypes.CDLL` on **every
  `*.dll` inside the package directory** — so DLLs must stay
  package-relative in the frozen bundle, which `collect_dynamic_libs`
  preserves. On Linux the manylinux wheel keeps `libctranslate2…so` in a
  sibling `ctranslate2.libs/` dir resolved via `$ORIGIN` RPATH; PyInstaller
  reproduced that layout and deduped the root-level copy as a symlink
  (0-byte `_internal/libctranslate2-….so` → `ctranslate2.libs/`).
- `hook-faster_whisper.py`: `collect_data_files("faster_whisper")` for
  `faster_whisper/assets/silero_vad_v6.onnx` (1.2 MB Silero VAD model); the
  frozen bundle contains it (verified by `find`).

No `hiddenimports` were needed for this stack on PyInstaller 6.20. `uvicorn`
needed nothing extra because the app object is passed directly (string-based
`"module:app"` loading would require hidden imports).

### Runtime evidence

| Check | Result |
|---|---|
| Frozen engine boots | `ENGINE_READY port=40043` sentinel printed (engine_boot.log) |
| `/health` answers | `{"status":"ok","frozen":true,"python":"3.10.10 …"}` |
| Frozen worker, 5 s speech WAV, tiny/CPU | exit 0, JSON written: `" And so, my fellow Americans, ask not!"` (out_frozen/fixture_speech.json) |
| Frozen worker, 5 s sine-tone WAV | exit 0, empty `segments` (VAD/no-speech suppression — expected) |
| Frozen worker, `--model <local snapshot dir>` + `HF_HUB_OFFLINE=1` | exit 0, identical transcript — **proves model weights can live outside the bundle and be passed as a directory path, no network, exactly the downloadable-pack pattern** |
| Frozen cold-start, end-to-end (load tiny + transcribe 5 s, CPU) | 1.68 s real — indistinguishable from unfrozen (1.79 s); no freeze penalty |

Fixtures: `fixture_tone.wav` (ffmpeg `sine=frequency=440:duration=5`),
`fixture_speech.wav` (first 5 s of faster-whisper's canonical `jfk.flac`,
16 kHz mono — no local TTS available in this environment).

### Size breakdown (Linux x86_64, CPU-only)

Total: **388 MB raw onedir; 134 MB tar.gz** (full table in
`size_breakdown.txt`). Top contributors:

| Component | Size | Note |
|---|---|---|
| `ctranslate2.libs/` + `ctranslate2/` | 75 + 58 MB | inference runtime |
| `av.libs/` + `av/` | 72 + 33 MB | PyAV — faster-whisper hard dep; **bundles its own FFmpeg shared libs**, so the worker needs no external ffmpeg for decoding |
| `numpy(.libs)` | 41 MB | |
| `onnxruntime/` | 23 MB | Silero VAD execution |
| `libpython3.10` + stdlib | 38 MB | |
| `hf_xet/` | 12 MB | trimmable (HF download accelerator; useless offline) |
| `tokenizers/` | 10 MB | |

Windows sizes will differ but the proportions hold. Consequence for the parent
design's "~150–200 MB installer": the **compressed engine alone is ~134 MB**;
with Electron (~80–100 MB compressed) a realistic installer is **~200–250 MB**
— slightly above the design's estimate. Possible trims: exclude `hf_xet`,
`sympy` (not present here), unused onnxruntime providers.

---

## 2. Output-shape parity — DROP-IN SUPERSET, one field gap

Method: the **same WAV** transcribed by (a) the frozen faster-whisper worker
and (b) real `whisper-ctranslate2` 0.5.7 (`--output_format json`), same model;
programmatic field diff in `parity_diff.txt`.

- Top-level keys: both produce `{"text", "segments", "language"}` — identical.
- Segment keys, whisper-ctranslate2: `id, seek, start, end, text, tokens,
  temperature, avg_logprob, compression_ratio, no_speech_prob, words`.
- Segment keys, our worker: all of the above **except `words`** at spike time
  (whisper-ctranslate2 emits `"words": null` unless `--word_timestamps True`).
  The worker has since been updated to emit `"words": null` per the
  recommendation below (cubic D5, PR #7).
- **Values on all shared keys: bit-identical** (same library underneath —
  whisper-ctranslate2 is itself a faster-whisper wrapper).
- The repo fixture `tests/fixtures/sample_whisper.json` is a hand-simplified
  sample using only `{start, end, text}` per segment; `html.py` consumes only
  `seg["start"]` / `seg["end"]` / `seg["text"]` (and `youtube.py` produces only
  those three). The worker output is therefore a strict superset of everything
  the renderer needs.

**Adaptation the engine must make:** none for rendering. If diarization later
wants word-level alignment, add `word_timestamps=True` and emit `words`
(faster-whisper supports it natively). Recommend the worker always emit
`"words": null` when not computed, for byte-level shape parity.

---

## 3. CUDA DLL mechanism on Windows (research; no GPU build in this env)

### How ctranslate2 finds cuBLAS/cuDNN at runtime

ctranslate2's `ctranslate2.dll` is built with dynamic CUDA loading (the
project exposes `-DCUDA_DYNAMIC_LOADING`; the PyPI wheel uses it — one wheel
serves CPU and GPU). cuBLAS/cuDNN are resolved **by the Windows loader when
the model is first loaded on a CUDA device**, via the standard DLL search
order: directories registered with `os.add_dll_directory()`, the directory of
`ctranslate2.dll` itself, system dirs, then `PATH`. Verified mechanisms that
work in the wild (all from SYSTRAN/faster-whisper issue threads):

1. `os.add_dll_directory(<dir with cudnn/cublas DLLs>)` before import/model
   load — the documented fix in faster-whisper #1230 and the approach
   ctranslate2's own `__init__.py` uses for its bundled DLLs.
2. Copying the DLLs **next to `ctranslate2.dll`** (package dir) — works
   because `__init__.py` eagerly `ctypes.CDLL`s every `*.dll` there.
3. Putting them on `PATH` (Purfview's instruction for the standalone build).

Caveat from faster-whisper #1279: copying only `cudnn_ops64_9.dll` is not
enough — cuDNN 9 is split into many DLLs that load each other
(`cudnn64_9`, `cudnn_graph64_9`, `cudnn_ops64_9`, `cudnn_cnn64_9`,
`cudnn_engines_precompiled64_9`, `cudnn_engines_runtime_compiled64_9`,
`cudnn_heuristic64_9`, `cudnn_adv64_9`) plus `cublas64_12` + `cublasLt64_12`.
**Ship the complete set.**

**Engine mechanism (recommended):** the *worker* (not the engine) calls
`os.add_dll_directory(str(user_data / "runtime"))` at startup, before
`faster_whisper` import, iff the dir exists. On Linux the equivalent is
`LD_LIBRARY_PATH` set by the engine **when spawning** the worker (in-process
mutation does not affect an already-running loader) — moot for desktop v1
(Windows-first), but the spawn-time-env pattern is the portable one.

### Version pin matrix (for ctranslate2 4.8.0, the version frozen here)

| ctranslate2 | CUDA | cuDNN | Source |
|---|---|---|---|
| **>= 4.5.0 (incl. 4.8.0)** | 12.x (cuDNN 9 pairs with >= 12.3 toolchains) | **9.x only** — 4.5.0 changelog: "supports cuDNN 9 and is no longer compatible with cuDNN 8" | CTranslate2 CHANGELOG, issues #1780/#1806 |
| 4.0.0 – 4.4.0 | 12.x | 8.x only (cuDNN 9 lacks `cudnn_ops_infer64_8`) | faster-whisper #1080/#1086 |
| 3.24.0 | 11.x | 8.x | faster-whisper README |

faster-whisper README (current): "GPU execution requires cuBLAS for CUDA 12
and cuDNN 9 for CUDA 12"; install via `pip install nvidia-cublas-cu12
nvidia-cudnn-cu12==9.*`. Note the opennmt.net installation page still says
"cuDNN 8" — it is stale; the changelog and faster-whisper README are
authoritative.

### Repack source + measured pack size

Repack from the official PyPI wheels (extract `nvidia/cublas/bin/*.dll`,
`nvidia/cudnn/bin/*.dll`; binaries unmodified):

| Wheel (win_amd64) | Size |
|---|---|
| `nvidia_cublas_cu12-12.9.2.10` | 553 MB |
| `nvidia_cudnn_cu12-9.23.1.3` | 690 MB |

Empirical prior art for the *delivered pack*: Purfview/whisper-standalone-win
"libs" release, `cuBLAS.and.cuDNN_CUDA12_win_v3.7z` (cuDNN 9 era) =
**849 MB compressed** (v2, cuDNN 8 era: 575 MB; queried from the GitHub
releases API). Budget the CUDA pack at **~0.8–1.2 GB download / ~1.5 GB on
disk**; a trimmed set (drop `cudnn_adv`, training-only DLLs) can shrink it but
must be validated against the #1279 incomplete-set failure mode.

### Redistribution (EULA) — PERMITTED, with conditions

- **cuBLAS**: CUDA EULA Attachment A lists the CUDA BLAS library among
  "Redistributable Software"; version-suffixed filenames (`cublas64_12.dll`)
  are explicitly covered ("variations of these files that have version number
  … embedded"). Conditions: distribute binary-only as a component of your
  application, install into a **private, non-shared directory used only by
  your application** (`<userData>/runtime/` satisfies this), customer terms no
  less restrictive; cuBLAS carries a Vasily Volkov modified-BSD attribution
  (EULA Attachment B) → include in About/licenses.
  Source: https://docs.nvidia.com/cuda/eula/index.html
- **cuDNN**: the cuDNN EULA supplement lists "the runtime files .so and .dll"
  as distributable. Conditions: app must add material functionality, the
  redistributed portions "shall only be accessed by your application", include
  the notice "This software contains source code provided by NVIDIA
  Corporation."
  Source: https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html
- Downloading the packs from PyPI at first-run (rather than shipping in the
  installer) additionally sidesteps installer-size and is the same
  distribution channel NVIDIA itself uses.

---

## 4. Diarization — **GO** (against the parent design's cut-line)

### Measured size (real install, not estimate)

`uv pip install "pyannote.audio>=4.0" torch --index-url
https://download.pytorch.org/whl/cpu` into a clean venv:

- site-packages: **1.2 GB raw**, **326 MB tar.gz** (measured).
- Composition: torch 698 MB, scipy 92 MB, pandas 45 MB, sklearn 35 MB,
  numpy 32+27 MB, sympy 30 MB, matplotlib+fontTools 39 MB (pyannote pulls
  plotting/training deps that a frozen *inference* worker can largely
  exclude — pandas/matplotlib/fontTools/grpc/tensorboard are trim candidates).
- A frozen onedir worker ≈ site-packages + python runtime ≈ **1.3 GB on
  disk, ~350–450 MB compressed download** including pipeline weights
  (segmentation + embedding models are tens of MB, dwarfed by torch).

This is **at or below the parent design's own 1.5–2.5 GB estimate** and well
inside a "downloadable optional pack" envelope. The pack stays a separate
frozen worker — merging it into the engine bundle would more than quadruple
the base install.

### Merge-glue interface (read from whisper-ctranslate2 0.5.7 source, `diarization.py`)

What we lose by leaving whisper-ctranslate2 is ~50 lines of numpy:

1. Run pyannote: `Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")`
   (pyannote 4.x API), called with an **in-memory dict**
   `{"waveform": torch.from_numpy(audio[None, :]), "sample_rate": 16000}` —
   audio decoded separately, *not* by pyannote.
2. Flatten `result.speaker_diarization` into `(turn.start, turn.end, speaker)`
   tuples.
3. For each whisper segment: compute
   `intersection = min(turn.end, seg.end) - max(turn.start, seg.start)` against
   all turns; sum positive intersections per speaker; assign the
   max-overlap speaker as `seg["speaker"]` (optionally renaming
   `SPEAKER_NN` → a friendly name). Segments with no overlap keep no speaker.

Proposed contract: `diarization-worker AUDIO.wav --num-speakers N --output
turns.json` → `{"turns": [{"start": float, "end": float, "speaker": str}]}`;
the **engine** does step 3 (pure stdlib/numpy-free interval math on two JSON
files) and enriches the whisper JSON with `speaker` per segment before
rendering. This keeps the merge testable without torch installed.

### torchcodec / ffmpeg pathing — neutralized

pyannote.audio 4.x uses torchcodec for *file* decoding; torchcodec ships
per-FFmpeg-major shim libs (`libtorchcodec_core{4..8}`) and tries them
newest-first, relying on the OS loader to find the **FFmpeg shared libraries**
(`avcodec`/`avformat`/`avutil` DLLs) — a static `ffmpeg.exe` in
`<userData>/tools/` does **not** satisfy it. However (verified empirically in
this spike):

- `pyannote/audio/core/io.py` wraps the torchcodec import in try/except —
  import of `Pipeline` succeeds without working FFmpeg libs;
- the in-memory waveform path (`Audio()({"waveform": …, "sample_rate": …})`)
  **works with no torchcodec/FFmpeg at all** (ran it: OK);
- whisper-ctranslate2 itself feeds pyannote the in-memory dict for exactly
  this reason.

**Recommendation:** the engine pre-converts input to 16 kHz mono WAV using its
managed `ffmpeg` binary from `<userData>/tools/` (it already has one for
yt-dlp), and the diarization worker reads the WAV with stdlib
`wave`+numpy → tensor. No FFmpeg shared-library shipping, no torchcodec
pathing problem. If file-decoding inside the worker is ever wanted, the pack
must add FFmpeg *shared* DLLs and `os.add_dll_directory` them — avoidable
complexity; don't.

### Verdict: **GO** for desktop v1

- Size: ~350–450 MB download / 1.3 GB disk — under the design's cut-line
  numbers and strictly opt-in.
- Glue: trivially reimplementable (above), engine-side, unit-testable.
- ffmpeg/torchcodec risk: eliminated by pre-decoded WAV input.
- Residual risk: pyannote pulls a wide dep tree (lightning, sklearn, …) that
  PyInstaller has hooks-contrib coverage for, but the diarization worker
  freeze itself was not built in this spike — schedule its build smoke test
  early in the diarization phase, not at the end.

---

## 5. Consequences for the engine implementation

### Frozen bundle tool layout (informs `resolve_tool`, task 4.2)

```
<install dir>/engine/                ← PyInstaller onedir root
  podcast-reader-engine[.exe]        ← sys.executable when frozen
  whisper-worker[.exe]               ← bundled sibling entry point
  _internal/                         ← shared runtime (both EXEs use it)
<userData>/tools/                    ← yt-dlp, ffmpeg, ffprobe (seeded; user-updatable)
<userData>/runtime/                  ← CUDA pack DLLs (cublas64_12, cudnn*_9)
<userData>/models/<name>/            ← whisper model dirs; pass path as --model (proven offline)
<userData>/workers/diarization/      ← optional separate frozen worker pack
```

**⚠ Contradiction with the design's `resolve_tool` wording** (proposal line
"never `Path(sys.executable).parent`", design decision in parent doc): in the
actual onedir layout, **the bundled `whisper-worker` executable lives exactly
at `Path(sys.executable).parent`** — both EXEs sit at the bundle root. The
rule is right for *external* tools (ffmpeg/yt-dlp must come from
`<userData>/tools/`, never the bundle root, so they stay updatable) but wrong
for *bundled entry points*. `resolve_tool` therefore needs two classes:

- **bundled workers** (`whisper-worker`, later `diarization-worker` from its
  own pack dir): frozen → `Path(sys.executable).parent / name`; unfrozen →
  interpreter-sibling (today's behavior).
- **external tools** (`yt-dlp`, `ffmpeg`, `ffprobe`): `tools_dir` param /
  `PODCAST_READER_TOOLS_DIR` → `<userData>/tools/` → PATH; under `sys.frozen`,
  never the bundle dir.

Spec task 4.1 should encode this two-class precedence rather than a single
list.

### Recommended whisper-worker invocation contract (answers design open question 1)

**argv in, file out, line-protocol progress on stderr** — i.e. keep today's
`transcribe()` boundary:

```
whisper-worker AUDIO --model <name-or-dir> --device cpu|cuda \
    --compute-type int8|float16 [--language xx] --output-dir DIR
stderr: "progress segment_end=<sec>" per segment   (engine → SSE progress)
stdout: absolute path of the written JSON on success
exit:   0 ok / non-zero with human-readable stderr tail
```

Rationale, from evidence: (a) JSON-over-stdout for a 3-hour podcast is tens of
MB through a pipe with no incremental value — the engine re-reads it anyway
for chapters/render; (b) file output matches the existing
`transcribe()`/cache contract (`<output-dir>/<stem>.json`) so `pipeline.py`
needs zero shape changes; (c) faster-whisper's segment iterator gives us free
per-segment progress, which maps 1:1 onto `PipelineEvent` — this is strictly
better progress granularity than parsing whisper-ctranslate2's console
output. Model selection: pass a **local model directory** (proven working
frozen + `HF_HUB_OFFLINE=1`) so weights live in `<userData>/models/` per the
pack design; never rely on HF download from inside the frozen worker.

### Other engine consequences

- Add `multiprocessing.freeze_support()` first thing in every frozen entry
  point (Windows re-exec safety).
- Keep the two custom PyInstaller hooks (`hooks/`) under version control;
  upstream has no coverage (checked hooks-contrib 2026.6). Consider
  upstreaming later.
- PyAV inside the bundle (105 MB) means the whisper worker decodes audio
  itself; the engine's ffmpeg binary is needed only for yt-dlp
  post-processing and the diarization pre-convert.
- The `words` field: emit `"words": null` for byte parity with
  whisper-ctranslate2 output (or compute with `word_timestamps=True` when
  diarization is enabled).
- Engine `/health` + sentinel + pre-bound socket all survive freezing
  unchanged — no PyInstaller-specific accommodation was needed.

### Sources

- ctranslate2 4.8.0 `__init__.py` (installed wheel, read directly) — Windows
  `add_dll_directory` + eager `CDLL` of package DLLs.
- whisper-ctranslate2 0.5.7 `diarization.py` (installed wheel) — merge glue.
- pyannote.audio 4.0.4 `core/io.py` (installed wheel) + empirical waveform-path run.
- torchcodec 0.14.0 `_internally_replaced_utils.py` (installed wheel) — FFmpeg shim probing.
- [faster-whisper README — GPU requirements](https://github.com/SYSTRAN/faster-whisper/blob/master/README.md)
- [CTranslate2 CHANGELOG (4.5.0 cuDNN 9 switch)](https://github.com/OpenNMT/CTranslate2/blob/master/CHANGELOG.md)
- [CTranslate2 #1780 — cuDNN 9 support](https://github.com/OpenNMT/CTranslate2/issues/1780),
  [#1806 — torch/ct2 compat matrix](https://github.com/OpenNMT/CTranslate2/issues/1806)
- [faster-whisper #1230 — cudnn_ops64_9.dll fix](https://github.com/SYSTRAN/faster-whisper/issues/1230),
  [#1279 — incomplete DLL set failure](https://github.com/SYSTRAN/faster-whisper/issues/1279),
  [#1080](https://github.com/SYSTRAN/faster-whisper/issues/1080), [#1086](https://github.com/SYSTRAN/faster-whisper/issues/1086)
- [CUDA EULA (Attachment A redistributables)](https://docs.nvidia.com/cuda/eula/index.html)
- [cuDNN EULA (runtime .so/.dll distributable)](https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html)
- [Purfview/whisper-standalone-win releases (pack sizes via GitHub API)](https://github.com/Purfview/whisper-standalone-win/releases)
- [nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheel sizes (PyPI JSON API)](https://pypi.org/project/nvidia-cudnn-cu12/)
- [CTranslate2 installation docs (CUDA 12.x requirement; cuDNN line stale)](https://opennmt.net/CTranslate2/installation.html)
