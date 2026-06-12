"""PyInstaller entry point for the frozen ``diarization-worker`` executable.

The worker module owns the contract (freeze_support first, argv parsing,
offline cache resolution); this script only delegates so frozen and console
runs share one code path.
"""

from podcast_reader.workers.diarization_worker import main

if __name__ == "__main__":
    main()
