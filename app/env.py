"""Central env loading with precedence: real environment > `.env.local` > `.env`.

Explicitly exported vars (and test-set vars) always win: we snapshot the
live environment, load both files, then restore pre-existing keys.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load() -> None:
    base = Path(__file__).resolve().parent.parent
    live = dict(os.environ)
    load_dotenv(base / ".env", override=True)
    load_dotenv(base / ".env.local", override=True)
    os.environ.update(live)
