"""Central env loading: `.env` first, then `.env.local` overrides (local-only, gitignored)."""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load() -> None:
    base = Path(__file__).resolve().parent.parent
    load_dotenv(base / ".env")
    load_dotenv(base / ".env.local", override=True)
