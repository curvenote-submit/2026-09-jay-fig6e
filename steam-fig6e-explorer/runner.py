"""Drive ``fetch_worker.py`` as a subprocess and read back its .npz.

Keeps the process pool out of the Streamlit script — see fetch_worker's module
docstring for why an in-process pool cannot work there.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
WORKER = HERE / 'fetch_worker.py'


def run_fetch(request: dict, timeout: float = 900.0) -> dict[str, np.ndarray]:
    """Run one parallel fetch. Returns {species: array}; empty dict on failure."""
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / 'signals.npz'
        req = dict(request, out=str(out_path))
        try:
            proc = subprocess.run(
                [sys.executable, str(WORKER)],
                input=json.dumps(req), capture_output=True, text=True,
                cwd=str(HERE), timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {}
        if proc.returncode != 0 or not out_path.exists():
            sys.stderr.write(proc.stderr[-2000:] if proc.stderr else 'fetch worker failed\n')
            return {}
        z = np.load(out_path, allow_pickle=False)
        species = [str(x) for x in z['species']]
        mat = z['mat']
        return {sp: mat[i] for i, sp in enumerate(species)}
