"""PyInstaller entry point; all child modes dispatch before any window creation."""

import multiprocessing

from edge.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
