"""Shared test fixtures read from seed.json — no hardcoded demo names in tests."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SEED_PATH = os.environ.get("SEED_FILE", "seed.json")
_SEED_FILE = Path(_SEED_PATH) if Path(_SEED_PATH).is_absolute() else ROOT / _SEED_PATH
SEED = json.loads(_SEED_FILE.read_text())


def _member(role: str) -> dict:
    return next(m for m in SEED["members"] if m.get("role") == role)


ORG = _member("organizer")
MEM = _member("member")
ORG_NAME = ORG["name"]
MEM_NAME = MEM["name"]


def org_code() -> str:
    return os.environ.get(ORG.get("passcode_env", ""), ORG.get("default", ""))


def mem_code() -> str:
    return os.environ.get(MEM.get("passcode_env", ""), MEM.get("default", ""))


def first_vendor() -> dict:
    return SEED["vendors"][0]


def first_token() -> str:
    att = SEED["attendees"]
    return att.get("token_pattern", "QR-%04d") % 1


def first_guest() -> str:
    att = SEED["attendees"]
    return att.get("name_pattern", "Guest %02d") % 1
