"""Load the Phase-0 source directory (sources.json) once."""
from __future__ import annotations

import json
from functools import lru_cache

from .config import DATA_DIR


@lru_cache(maxsize=1)
def load_sources() -> dict:
    with open(DATA_DIR / "sources.json", encoding="utf-8") as f:
        return json.load(f)
