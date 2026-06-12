"""PyInstaller entry point for the frozen ``podcast-reader-engine`` executable.

Delegates to the real CLI dispatcher so the packaged engine speaks the exact
spawn contract the app uses (``podcast-reader-engine serve``, design decision
2) while one-shot CLI runs keep working from the same binary.
"""

import multiprocessing

from podcast_reader.cli import main

if __name__ == "__main__":
    # FIRST: in frozen bundles on Windows/macOS multiprocessing re-executes
    # this binary; without freeze_support a re-exec would re-run main.
    multiprocessing.freeze_support()
    main()
