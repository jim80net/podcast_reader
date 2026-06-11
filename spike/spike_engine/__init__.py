"""Stand-in engine package for the PyInstaller packaging spike.

Not production code. Mimics the shape the real engine will have:
- a FastAPI app with /health (engine entry point)
- a whisper worker invoking faster_whisper.WhisperModel directly
  (worker entry point, frozen into the same onedir bundle)
"""
