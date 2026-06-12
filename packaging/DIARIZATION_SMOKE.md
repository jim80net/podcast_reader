# Diarization worker freeze smoke — task 5.1 (cut-line gate)

**Verdict: GO.** The frozen pyannote + CPU-torch worker builds, boots, loads
the pipeline fully offline from a pack-sibling `cache/` directory, and
produces `turns.json` — at 341 MB compressed / ~1.0 GB on disk, inside the
spike's 350–450 MB envelope and under the parent design's cut-line numbers.
Tasks 5.2–5.5 proceed. Run on 2026-06-12, linux (WSL2), Python 3.10.

## Build recipe (what 7.5's `build_diarization_pack.py` must reproduce)

```bash
cd packaging
uv venv .venv-diarization --python 3.10
# CPU torch FIRST, from the pytorch CPU index — PyPI torch is the CUDA build
# and quadruples the bundle. torchaudio/torchcodec must come from the same
# index: their PyPI wheels link a different libtorch and fail to load
# (_torchaudio.abi3.so OSError) against torch +cpu.
uv pip install --python .venv-diarization/bin/python \
    torch --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv-diarization/bin/python \
    "pyannote.audio>=4.0" pyinstaller
uv pip install --python .venv-diarization/bin/python \
    --reinstall-package torchaudio --reinstall-package torchcodec \
    torchaudio torchcodec --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv-diarization/bin/python --no-deps ..
.venv-diarization/bin/pyinstaller diarization.spec --noconfirm
```

Pinned stack as built: pyannote.audio **4.0.4**, torch **2.12.0+cpu**,
torchaudio **2.11.0+cpu**, torchcodec **0.14.0+cpu**, PyInstaller **6.20.0**
— the exact combination the spike measured (SPIKE_REPORT.md §4).

No custom hooks were needed (unlike the engine's ctranslate2/faster_whisper
hooks): hooks-contrib 2026.6 covers torch/torchaudio/lightning/sklearn;
`diarization.spec` adds `collect_submodules("pyannote")` (the pipeline class
is resolved dynamically from the checkpoint's `config.yaml`, invisible to
static analysis) plus `copy_metadata` for pyannote.audio and the lightning
stack.

## Evidence

| Check | Result |
|---|---|
| PyInstaller onedir build | success, ~2 min analysis+collect |
| Worker onedir on disk | **1007 MB** (632 MB = torch; 57 MB scipy; 22 MB pandas; 21 MB sklearn) |
| Pre-seeded HF cache (segmentation + embedding + pipeline config) | **32 MB** |
| `tar.gz` of onedir incl. cache | **341 MB** |
| Frozen boot + `turns.json` on real 2-speaker speech (pyannote tutorial sample, 30 s, pre-converted 16 kHz mono s16le) | exit 0; 13 turns, 2 speakers |
| Frozen vs unfrozen output parity | byte-identical turns |
| Frozen boot on synthetic two-tone fixture (ffmpeg `aevalsrc` alternating 220/660 Hz sine, 8 s) | exit 0; `{"turns": []}` — sine tones are not speech; boot-proof per the gate |
| Offline load | ran with `HF_HUB_OFFLINE=1`, no `HUGGINGFACE_HUB_CACHE`, empty `HF_TOKEN`, cache copied to `dist/diarization-worker/cache/` (the pack layout's frozen-sibling default) |
| Frozen run wall time / peak RSS (30 s input, CPU) | 19.1 s / 2.2 GB |
| torchcodec | import fails inside the freeze and is harmlessly swallowed by pyannote's try/except — exactly the spike's prediction; the in-memory waveform path needs no FFmpeg shared libraries |

Synthetic fixture command (engine pre-convert shape):

```bash
ffmpeg -f lavfi \
  -i "aevalsrc=if(lt(mod(t\,4)\,2)\,0.8*sin(2*PI*220*t)\,0.8*sin(2*PI*660*t)):s=16000:d=8" \
  -ac 1 -ar 16000 -c:a pcm_s16le two_tone.wav
```

## Smoke pipeline caveat (model access, not freeze viability)

The worker's default model is `pyannote/speaker-diarization-community-1`
(pyannote 4.x default — what the published pack should pre-seed). The local
HF token used for this smoke has accepted terms for
`pyannote/segmentation-3.0` / `pyannote/speaker-diarization-3.1` (this
project's documented diarization setup) but **not** community-1, and 5.1 must
not accept new gated-model terms on the user's behalf. Additionally,
pyannote.audio 4.x cannot load the 3.1 checkpoint as-is: its
`SpeakerDiarization.__init__` defaults the `plda` component to a gated
community-1 subfolder even though 3.1's `AgglomerativeClustering` never uses
PLDA.

The smoke therefore ran a **local pipeline config** (`--model
<dir>/config.yaml`, a supported `Pipeline.from_pretrained` input): 3.1's
exact params (segmentation-3.0 + wespeaker-voxceleb-resnet34-LM +
AgglomerativeClustering) plus a synthetic identity PLDA
(`mean1/mean2/lda/mu/tr/psi` zeros/identity npz — loadable by `vbx_setup`,
never used by Agglomerative). This exercises every freeze-relevant code path
(dynamic pipeline-class resolution, offline hub cache, segmentation
inference, embedding inference, clustering, in-memory waveform); only the
model weights differ from the shipping pack.

## What task 7.5 needs

1. `HF_TOKEN` CI secret (task 7.4) whose account has accepted
   **`pyannote/speaker-diarization-community-1`** terms (the worker default),
   not just the 3.1-era models.
2. `build_diarization_pack.py`: the recipe above + `snapshot_download` of the
   community-1 pipeline (config + segmentation + embedding + plda) into
   `dist/diarization-worker/cache/`, then compress and publish to a
   `pack-diarization-v*` GitHub Release with sha256 + size, and flip the
   registry entry from `files: None` to the published pin.
3. An offline smoke in the release job mirroring this one: run the frozen
   worker with `HF_HUB_OFFLINE=1` against a fixture WAV **using the default
   model id** before publishing.
4. Windows leg sizing note: expect similar magnitude (torch dominates); the
   GitHub Release asset limit (2 GB) is comfortable.
