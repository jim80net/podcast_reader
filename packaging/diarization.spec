# PyInstaller spec: frozen `diarization-worker` onedir bundle (task 5.1).
#
# Build venv (CPU torch — PyPI torch is the CUDA build and quadruples the
# bundle; see packaging/DIARIZATION_SMOKE.md):
#   uv venv .venv-diarization --python 3.10
#   uv pip install --python .venv-diarization/bin/python \
#       torch --index-url https://download.pytorch.org/whl/cpu
#   uv pip install --python .venv-diarization/bin/python \
#       "pyannote.audio>=4.0" pyinstaller
#   uv pip install --python .venv-diarization/bin/python \
#       torchaudio torchcodec --index-url https://download.pytorch.org/whl/cpu \
#       --reinstall-package torchaudio --reinstall-package torchcodec
#   uv pip install --python .venv-diarization/bin/python --no-deps ..
#
# Build:  .venv-diarization/bin/pyinstaller diarization.spec --noconfirm
# Output: dist/diarization-worker/ — the worker onedir; the published pack
#         (task 7.5) adds the pre-seeded HF `cache/` directory next to the
#         executable.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

block_cipher = None

a = Analysis(
    ["diarization_worker_entry.py"],
    pathex=["."],
    hiddenimports=[
        # The pipeline class is resolved dynamically from the checkpoint's
        # config.yaml (pyannote.audio.pipelines.SpeakerDiarization), so static
        # analysis never sees it: collect every pyannote submodule.
        *collect_submodules("pyannote"),
    ],
    datas=[
        # pyannote.audio resolves its version via importlib.metadata at import
        # time; lightning stacks read their metadata for version checks.
        *copy_metadata("pyannote.audio"),
        *copy_metadata("pytorch-lightning"),
        *copy_metadata("lightning"),
        *copy_metadata("lightning-utilities"),
        *collect_data_files("pyannote.audio"),
        *collect_data_files("lightning_fabric"),
    ],
    excludes=[
        # Not needed by the inference worker; keeps the bundle honest about
        # size (spike §4 trim candidates that prove safe to drop).
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="diarization-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="diarization-worker",
)
