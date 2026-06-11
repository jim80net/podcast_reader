"""PyInstaller entry script: the whisper-worker executable."""

import multiprocessing

from spike_engine.worker import main

if __name__ == "__main__":
    # Required in frozen bundles on Windows/macOS where multiprocessing
    # re-executes the binary; harmless on Linux.
    multiprocessing.freeze_support()
    main()
