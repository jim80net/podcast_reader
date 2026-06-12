"""Standalone worker programs frozen as sibling executables of the engine.

The main package never imports anything from this package: workers carry
heavy optional dependencies (faster-whisper) installed only via their extras
and, in the packaged app, run as separate frozen executables spawned through
``tools.resolve_bundled_worker``.
"""
