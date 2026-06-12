"""PyInstaller entry point for the frozen ``whisper-worker`` executable.

The worker module owns the contract (``multiprocessing.freeze_support()``
first, argv parsing, Windows DLL-path prep, lazy faster-whisper import); this
script only delegates so frozen and console runs share one code path.
"""

from podcast_reader.workers.whisper_worker import main

if __name__ == "__main__":
    main()
