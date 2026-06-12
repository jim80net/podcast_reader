# PyInstaller spec: production frozen engine onedir (task 7.1).
#
# Two entry points (`podcast-reader-engine` running the real serve_engine via
# the CLI dispatcher, and `whisper-worker`) sharing ONE _internal/ via
# MERGE/COLLECT — the mechanism the packaging spike proved
# (spike/SPIKE_REPORT.md §1). Custom hooks for ctranslate2/faster_whisper live
# in hooks/ (pyinstaller-hooks-contrib ships neither).
#
# copy_metadata("podcast-reader") is collected (per S3) so the frozen
# engine's importlib.metadata version lookup (engine_version()) reports the
# real project version on /v1/health instead of "0.0.0-dev".
#
# Build (see build_engine.py, which also stages the tool seeds):
#   uv venv .venv-engine --python 3.10
#   uv pip install --python .venv-engine/bin/python '..[worker]' pyinstaller
#   .venv-engine/bin/pyinstaller engine.spec --noconfirm
# Output: dist/engine/ — podcast-reader-engine[.exe], whisper-worker[.exe],
#         _internal/ (the layout engine-cmd.ts and dist.mjs --engine-dir expect).

from PyInstaller.utils.hooks import copy_metadata

block_cipher = None

common_kwargs = dict(
    pathex=["."],
    hookspath=["hooks"],  # custom hooks: ctranslate2, faster_whisper
    hiddenimports=[],
    datas=[
        # Frozen importlib.metadata version lookup (per S3): without the
        # dist-info, /v1/health reports the "0.0.0-dev" placeholder and the
        # app's MIN_ENGINE_VERSION gate would reject the packaged engine.
        *copy_metadata("podcast-reader"),
    ],
    excludes=[
        # Not needed by the engine or worker; keeps the bundle honest on size.
        "tkinter",
    ],
    noarchive=False,
)

engine_a = Analysis(["engine_entry.py"], **common_kwargs)
worker_a = Analysis(["whisper_worker_entry.py"], **common_kwargs)

# MERGE dedupes shared dependencies between the two analyses so the second
# executable references the first bundle's copies (one shared _internal/).
MERGE(
    (engine_a, "podcast-reader-engine", "podcast-reader-engine"),
    (worker_a, "whisper-worker", "whisper-worker"),
)

engine_pyz = PYZ(engine_a.pure, engine_a.zipped_data, cipher=block_cipher)
worker_pyz = PYZ(worker_a.pure, worker_a.zipped_data, cipher=block_cipher)

engine_exe = EXE(
    engine_pyz,
    engine_a.scripts,
    [],
    exclude_binaries=True,
    name="podcast-reader-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

worker_exe = EXE(
    worker_pyz,
    worker_a.scripts,
    [],
    exclude_binaries=True,
    name="whisper-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    engine_exe,
    engine_a.binaries,
    engine_a.zipfiles,
    engine_a.datas,
    worker_exe,
    worker_a.binaries,
    worker_a.zipfiles,
    worker_a.datas,
    strip=False,
    upx=False,
    name="engine",
)
