"""Standalone parallel fetcher. Reads a JSON request on stdin, writes an .npz.

Why a separate script rather than a pool started inside the app:

* pyBigWig holds the GIL across its network I/O, so a thread pool fetches
  strictly serially — 24 species take ~55 s whether the pool has 1 worker or 24.
  Only real processes parallelise this.
* But a process pool started from inside ``streamlit run app.py`` breaks: both
  *spawn* and *forkserver* hand children an ``init_main_from_path`` pointing at
  ``app.py``, so every worker re-executes the whole Streamlit script and dies
  with ``NoSessionContext``. Streamlit scripts cannot carry an
  ``if __name__ == '__main__'`` guard to prevent it.
* *fork* would avoid the re-import, but forking a process with live Tornado
  threads risks inheriting a held lock and deadlocking.

Running the pool from this script gives it a real ``__main__`` guard, so the
children re-import *this* module harmlessly instead of the app.

Request shape:
    {"mode": "native", "out": "/path/x.npz", "workers": 16,
     "cell_type": "Hepatocytes", "window_bp": 100000, "n_bins": 2000,
     "anchors": {species: {contig, pos, strand, ...}, ...}}
    {"mode": "hg38", "out": ..., "workers": ..., "cell_type": ...,
     "chrom": "chr4", "start": ..., "end": ..., "n_bins": ...,
     "species": [...]}
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

import fetchers


def main() -> int:
    req = json.load(sys.stdin)
    mode = req['mode']
    n_bins = int(req['n_bins'])

    if mode == 'native':
        anchors = req['anchors']
        payload = [(sp, anchors[sp], req['cell_type'], int(req['window_bp']), n_bins)
                   for sp in sorted(anchors)]
        task = fetchers._native_task
    elif mode == 'hg38':
        payload = [(sp, req['chrom'], int(req['start']), int(req['end']),
                    req['cell_type'], n_bins) for sp in req['species']]
        task = fetchers._hg38_task
    else:
        print(f'unknown mode {mode!r}', file=sys.stderr)
        return 2

    out: dict[str, np.ndarray] = {}
    with ProcessPoolExecutor(max_workers=int(req.get('workers', 16))) as ex:
        for sp, arr in ex.map(task, payload, chunksize=1):
            if arr is not None and arr.shape == (n_bins,):
                out[sp] = arr

    species = sorted(out)
    np.savez_compressed(req['out'],
                        species=np.array(species),
                        mat=np.vstack([out[s] for s in species])
                        if species else np.zeros((0, n_bins)))
    print(json.dumps({'ok': len(species), 'requested': len(payload)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
