"""Persistent cache for fetched signal matrices and lifted anchors.

Streamlit's ``st.cache_data`` lives only as long as the server process, so every
restart re-pulls hundreds of remote bigwigs. These helpers put the same results
under ``cache/`` so repeat queries are instant across restarts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import numpy as np

CACHE_DIR = Path(__file__).resolve().parent / 'cache'
SIGNALS_DIR = CACHE_DIR / 'signals'
JSON_DIR = CACHE_DIR / 'json'


def _key(**parts) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:20]


def load_signals(**parts) -> Optional[dict[str, np.ndarray]]:
    f = SIGNALS_DIR / f'{_key(**parts)}.npz'
    if not f.exists():
        return None
    try:
        z = np.load(f, allow_pickle=False)
        species = [str(s) for s in z['species']]
        mat = z['mat']
        return {sp: mat[i] for i, sp in enumerate(species)}
    except Exception:
        return None


def save_signals(signals: dict[str, np.ndarray], **parts) -> None:
    if not signals:
        return
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    species = sorted(signals)
    try:
        np.savez_compressed(SIGNALS_DIR / f'{_key(**parts)}.npz',
                            species=np.array(species),
                            mat=np.vstack([signals[s] for s in species]))
    except Exception:
        pass          # a cache write must never break a query


def load_json(**parts):
    """Small JSON payloads (Ensembl TSS lookups, gene models). Ensembl is both
    slow and intermittently unavailable, so these are worth persisting."""
    f = JSON_DIR / f'{_key(**parts)}.json'
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def save_json(payload, **parts) -> None:
    if payload is None:
        return
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (JSON_DIR / f'{_key(**parts)}.json').write_text(json.dumps(payload))
    except Exception:
        pass


def stats() -> tuple[int, float]:
    """(number of cached entries, total MB)."""
    files = list(SIGNALS_DIR.glob('*.npz')) + list(JSON_DIR.glob('*.json'))
    return len(files), sum(f.stat().st_size for f in files) / 1e6
