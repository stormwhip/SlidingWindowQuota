from os import environ
from pathlib import Path


RESULTS = Path(environ.get("SLIDING_WINDOW_QUOTA_RESULTS",
                           Path(__file__).resolve().parents[1] / "Results")).resolve()
