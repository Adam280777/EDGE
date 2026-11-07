"""Optional suite-wide offline guard used by CI, including CLI subprocesses."""

import os
import runpy
from pathlib import Path

if os.environ.get("EDGE_TEST_OFFLINE") == "1":
    guard = Path(__file__).parent / "offline"
    # Apply before collection; child interpreters load the same sitecustomize.
    runpy.run_path(str(guard / "sitecustomize.py"))
    os.environ["PYTHONPATH"] = str(guard) + os.pathsep + os.environ.get("PYTHONPATH", "")
