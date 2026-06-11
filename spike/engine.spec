# PyInstaller spec: spike engine onedir bundle with TWO entry points
# (`spike-engine` and `whisper-worker`) sharing one _internal directory.
#
# Build:  .venv/bin/pyinstaller engine.spec --noconfirm
# Output: dist/spike-engine/ containing both executables + _internal/.

block_cipher = None

common_kwargs = dict(
    pathex=["."],
    hookspath=["hooks"],  # custom hooks: ctranslate2, faster_whisper
    hiddenimports=[],
    excludes=[
        # Not needed by the spike; keeps the bundle honest about size.
        "tkinter",
    ],
    noarchive=False,
)

engine_a = Analysis(["engine_entry.py"], **common_kwargs)
worker_a = Analysis(["worker_entry.py"], **common_kwargs)

# MERGE dedupes shared dependencies between the two analyses so the
# second executable references the first bundle's copies.
MERGE((engine_a, "spike-engine", "spike-engine"), (worker_a, "whisper-worker", "whisper-worker"))

engine_pyz = PYZ(engine_a.pure, engine_a.zipped_data, cipher=block_cipher)
worker_pyz = PYZ(worker_a.pure, worker_a.zipped_data, cipher=block_cipher)

engine_exe = EXE(
    engine_pyz,
    engine_a.scripts,
    [],
    exclude_binaries=True,
    name="spike-engine",
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
    name="spike-engine",
)
