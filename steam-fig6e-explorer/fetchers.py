"""Remote bigwig fetch primitives, deliberately free of heavy imports.

These run in worker *processes*, not threads: pyBigWig holds the GIL across its
network I/O, so a ThreadPoolExecutor gives no parallelism at all (measured: 24
species take ~55 s whether the pool has 1 worker or 24). Processes give a real
speedup, but on macOS they spawn, re-importing this module in each child — so
nothing here may pull in matplotlib or the rest of ``fig6e_core``.
"""
from __future__ import annotations

import multiprocessing as _mp
from typing import Optional

import numpy as np
import pyBigWig

BASE = 'https://shendure-web.gs.washington.edu/content/members/cxqiu/public/nobackup'
HG38_BW_FMT = BASE + '/jax_atac_augmented_241_mammals_hg38/hg38/{species}/{species}.{cell_type}.bw'
NATIVE_BW_FMT = BASE + '/jax_atac_augmented_241_mammals/{species}/{cell_type}.bw'


def fetch_native(species: str, anchor: dict, cell_type: str,
                 window_bp: int, n_bins: int) -> Optional[np.ndarray]:
    """Species' own-genome track around its native anchor, reference-oriented."""
    try:
        bw = pyBigWig.open(NATIVE_BW_FMT.format(species=species, cell_type=cell_type))
    except Exception:
        return None
    if bw is None:
        return None
    try:
        chroms = bw.chroms()
        contig = anchor['contig']
        if contig not in chroms:
            return None
        clen = chroms[contig]
        start = max(0, anchor['pos'] - window_bp)
        end = min(clen, anchor['pos'] + window_bp)
        if end - start < 2 * n_bins:
            return None
        vals = bw.stats(contig, start, end, type='mean', nBins=n_bins)
    except Exception:
        return None
    finally:
        bw.close()
    arr = np.array([np.nan if v is None else v for v in vals], dtype=float)
    # Reorient minus-strand contigs to the reference transcript's orientation.
    return arr[::-1] if anchor['strand'] == '-' else arr


def fetch_hg38(species: str, chrom: str, start: int, end: int,
               cell_type: str, n_bins: int) -> Optional[np.ndarray]:
    """Species' hg38-projected track over a shared human interval."""
    try:
        bw = pyBigWig.open(HG38_BW_FMT.format(species=species, cell_type=cell_type))
    except Exception:
        return None
    if bw is None:
        return None
    try:
        chroms = bw.chroms()
        if chrom not in chroms:
            return None
        end_eff = min(end, chroms[chrom])
        if end_eff <= start:
            return None
        vals = bw.stats(chrom, start, end_eff, type='mean', nBins=n_bins)
    except Exception:
        return None
    finally:
        bw.close()
    return np.array([np.nan if v is None else v for v in vals], dtype=float)


# --- process-pool entry points (must be module-level to be picklable) --------

def _native_task(args):
    species, anchor, cell_type, window_bp, n_bins = args
    return species, fetch_native(species, anchor, cell_type, window_bp, n_bins)


def _hg38_task(args):
    species, chrom, start, end, cell_type, n_bins = args
    return species, fetch_hg38(species, chrom, start, end, cell_type, n_bins)


# --- process pool context ----------------------------------------------------

def pool_context():
    """A multiprocessing context safe to use from inside a Streamlit script.

    The default on macOS is *spawn*, which re-imports the parent's ``__main__``
    in every child. Under ``streamlit run app.py`` that means each worker
    re-executes the whole app script — including the fetch that started the pool
    — and dies with ``NoSessionContext``. ``forkserver`` forks workers from a
    small single-threaded server process instead, so ``__main__`` is never
    re-run, and unlike bare ``fork`` it is safe to use from a threaded parent.
    """
    try:
        ctx = _mp.get_context('forkserver')
        ctx.set_forkserver_preload(['fetchers'])
        return ctx
    except (ValueError, AttributeError, OSError):
        return _mp.get_context('spawn')
