"""Shared pieces for the STEAM-v1 -> Zarr conversion: source URLs, reference
files (norm curves, tree), the per-track read, and the store layout.

Kept free of heavy imports at module level: worker processes import this.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

import numpy as np

# --- source ------------------------------------------------------------------

BASE = 'https://shendure-web.gs.washington.edu/content/members/cxqiu/public/nobackup'
HG38_BW_FMT = BASE + '/jax_atac_augmented_241_mammals_hg38/hg38/{species}/{species}.{cell_type}.bw'

CELL_TYPES = [
    'Adipocyte_cells', 'Adipocyte_cells_Cyp2e1', 'B_cells',
    'Brain_capillary_endothelial_cells', 'CNS_neurons', 'Cardiomyocytes',
    'Corticofugal_neurons', 'Endocardial_cells', 'Endothelium',
    'Epithelial_cells', 'Erythroid_cells', 'Eye', 'Glia',
    'Glomerular_endothelial_cells', 'Gut_epithelial_cells', 'Hepatocytes',
    'Intermediate_neuronal_progenitors', 'Kidney',
    'Lateral_plate_and_intermediate_mesoderm',
    'Liver_sinusoidal_endothelial_cells', 'Lung_and_airway',
    'Lymphatic_vessel_endothelial_cells', 'Melanocyte_cells', 'Mesoderm',
    'Neural_crest_PNS_neurons', 'Neuroectoderm_and_glia',
    'Olfactory_ensheathing_cells', 'Olfactory_neurons', 'Oligodendrocytes',
    'Skeletal_muscle_cells', 'T_cells', 'White_blood_cells',
]

CANONICAL_CHROMS = [f'chr{i}' for i in range(1, 23)] + ['chrX', 'chrY']

# --- store layout ------------------------------------------------------------

BIN_BP = 100
CHUNK_BINS = 2048
MISSING = 255
SCALE = 4                    # stored value = round(GPS * 4), 0..254
WINDOW_MB = 20               # per-request window: 20 Mb -> 80 MB float32 per worker

# --- reference files ---------------------------------------------------------

def default_ref_dir() -> Path:
    """steam-fig6e-explorer/data in this repo (norm curves, tree, gene models)."""
    env = os.environ.get('STEAM_REF_DIR')
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / 'steam-fig6e-explorer' / 'data'


def load_norm_ref(ref_dir: Path):
    """(levels, {(species, cell_type): quantile curve or None}) from norm_ref_species.npz."""
    z = np.load(ref_dir / 'norm_ref_species.npz', allow_pickle=True)
    levels = z['levels']
    sp, cts, qv = list(z['species']), list(z['cell_types']), z['qvals']
    qmap = {}
    for i, s in enumerate(sp):
        for j, c in enumerate(cts):
            curve = qv[i, j]
            qmap[(s, c)] = None if not np.isfinite(curve).any() else curve
    return levels, qmap


def tree_leaf_order(ref_dir: Path) -> list[str]:
    """Ladderized leaf order of the Zoonomia tree — the store's row order."""
    from Bio import Phylo

    def ladderize(clade):
        for c in clade.clades:
            ladderize(c)
        clade.clades.sort(key=lambda c: c.count_terminals(), reverse=True)

    tree = Phylo.read(str(ref_dir / 'zoonomia_241.nwk'), 'newick')
    ladderize(tree.root)
    return [t.name for t in tree.get_terminals()]


def hg38_chrom_sizes(cell_type: str = 'Hepatocytes', species: str = 'Canis_lupus_familiaris') -> dict[str, int]:
    import pyBigWig
    bw = pyBigWig.open(HG38_BW_FMT.format(species=species, cell_type=cell_type))
    try:
        sizes = bw.chroms()
    finally:
        bw.close()
    return {c: sizes[c] for c in CANONICAL_CHROMS if c in sizes}


def chrom_offsets(sizes: dict[str, int]) -> tuple[dict[str, int], int]:
    off, total = {}, 0
    for c, L in sizes.items():
        off[c] = total
        total += L // BIN_BP
    return off, total


# --- the per-track read ------------------------------------------------------

def values_to_phred(raw: np.ndarray, qcurve: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Raw prediction -> genome-wide Phred via this track's quantile curve (NaN-safe)."""
    pct = np.clip(np.interp(raw, qcurve, levels), 0.0, 1.0 - 1e-6)
    q = -10.0 * np.log10(1.0 - pct)
    return np.where(np.isfinite(raw), q, np.nan)


def quantise(gps: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(gps), np.clip(np.round(gps * SCALE), 0, MISSING - 1), MISSING).astype(np.uint8)


def read_track_chrom(species: str, cell_type: str, chrom: str, chrom_len: int,
                     qcurve: np.ndarray | None, levels: np.ndarray | None,
                     window_mb: int = WINDOW_MB) -> tuple[np.ndarray | None, dict]:
    """One species × cell class × chromosome -> uint8 GPS row of chrom_len // 100 bins.

    Reads per-base values in windows and bins them with a numpy reshape-mean,
    which matches pyBigWig.stats(type='mean', nBins=...) to ~1e-6 at 8x the
    speed and a fraction of the CPU. Returns (row | None, timing dict).
    """
    import pyBigWig
    t0 = time.time()
    n_bins = chrom_len // BIN_BP
    if qcurve is None:
        return None, {'wall': 0.0, 'skipped': 'no norm curve'}
    try:
        bw = pyBigWig.open(HG38_BW_FMT.format(species=species, cell_type=cell_type))
        if bw is None or chrom not in bw.chroms():
            return None, {'wall': time.time() - t0, 'skipped': 'chrom absent'}
        out = np.empty(n_bins, dtype=np.float32)
        win = window_mb * 1_000_000 // BIN_BP * BIN_BP
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)     # nanmean of all-NaN bins
            for start in range(0, n_bins * BIN_BP, win):
                end = min(start + win, n_bins * BIN_BP)
                v = bw.values(chrom, start, end, numpy=True)
                out[start // BIN_BP: end // BIN_BP] = np.nanmean(v.reshape(-1, BIN_BP), axis=1)
        bw.close()
    except Exception as e:  # network / server errors: the caller retries or records
        return None, {'wall': time.time() - t0, 'error': repr(e)}
    row = quantise(values_to_phred(out.astype(np.float64), qcurve, levels))
    return row, {'wall': time.time() - t0}


# --- process-pool entry point (module-level so it pickles) --------------------

def _task(args):
    species, cell_type, chrom, chrom_len, qcurve, levels, window_mb, retries = args
    for attempt in range(retries + 1):
        row, info = read_track_chrom(species, cell_type, chrom, chrom_len, qcurve, levels, window_mb)
        if row is not None or 'error' not in info:
            return species, row, info
        time.sleep(min(30, 2 ** attempt))
    return species, None, info


def write_meta(store_path: Path, grp_attrs: dict, cell_types: Iterable[str]) -> None:
    meta = dict(grp_attrs)
    meta['cell_types'] = sorted(cell_types)
    (store_path / 'meta.json').write_text(json.dumps(meta, indent=1))
