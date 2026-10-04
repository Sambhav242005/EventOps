"""SearchAdapter: LocalCatalog (reliable demo default) | web stubs.

Real web results are noisy: stored as LEADS in vendor_candidates with
source_url + fetched_at + confidence; never presented as verified.
Only public business contact details are collected.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "vendors_catalog.json"


class SearchAdapter:
    async def find_vendors(self, category: str, lat: float, lng: float,
                           limit: int = 5) -> list[dict]:
        raise NotImplementedError


class LocalCatalogAdapter(SearchAdapter):
    async def find_vendors(self, category: str, lat: float, lng: float,
                           limit: int = 5) -> list[dict]:
        try:
            items = json.loads(CATALOG.read_text())
        except Exception:
            items = []
        out = [x for x in items if x.get("category", "").lower() == category.lower()][:limit]
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        return [{**x, "source_url": "local:vendors_catalog.json",
                 "fetched_at": ts, "confidence": 0.6, "status": "new"} for x in out]


class WebSearchAdapter(SearchAdapter):
    """Optional live web grounding (Gemini grounding / SearXNG / lib). Mock-safe."""

    async def find_vendors(self, category: str, lat: float, lng: float,
                           limit: int = 5) -> list[dict]:
        base = await LocalCatalogAdapter().find_vendors(category, lat, lng, limit)
        for b in base:  # mark provenance honestly
            b["source_url"] = os.environ.get("SEARCH_PROVIDER_URL", "web:unconfigured-fallback")
            b["confidence"] = 0.3
        return base


def get_search() -> SearchAdapter:
    return WebSearchAdapter() if os.environ.get("SEARCH_BACKEND", "local") == "web" \
        else LocalCatalogAdapter()
