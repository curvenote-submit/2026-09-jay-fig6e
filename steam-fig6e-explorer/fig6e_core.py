"""Core logic for the STEAM-v1 Fig 6e per-coordinate viewer.

Pure Python module — no Streamlit dependency. Used by `app.py` and (optionally)
the notebook. All functions take their parameters explicitly; no module globals
encode the current query.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from matplotlib.patches import Rectangle
from matplotlib.transforms import blended_transform_factory

# --- figure palette ---------------------------------------------------------
# A light, print-friendly palette: near-black text on white, cool grey structure,
# one warm accent. The viridis heatmap and its floor are untouched — they are the
# paper's encoding, not decoration.
FIG_BG = '#ffffff'       # figure / axes background
FIG_FG = '#334155'       # slate-700 — all text, axes, tree branches
FIG_DIM = '#94a3b8'      # slate-400 — coverage track, major gridlines
FIG_FAINT = '#e2e8f0'    # slate-200 — minor gridlines only
FIG_HL = '#dc2626'       # red-600 — anchor marker + highlighted tips
FIG_ACCENT = '#0d9488'   # teal-600 — summed-strength track

FS = 8                   # the single figure font size, used everywhere

mpl.rcParams['font.family'] = 'sans-serif'
mpl.rcParams['font.sans-serif'] = [
    'Helvetica Neue', 'Helvetica', 'Arial', 'Liberation Sans', 'DejaVu Sans']
mpl.rcParams['pdf.fonttype'] = 42
mpl.rcParams.update({
    'figure.facecolor': FIG_BG,
    'axes.facecolor': FIG_BG,
    'savefig.facecolor': FIG_BG,
    'text.color': FIG_FG,
    'axes.edgecolor': FIG_FG,
    'axes.labelcolor': FIG_FG,
    'axes.titlecolor': FIG_FG,
    'xtick.color': FIG_FG,
    'ytick.color': FIG_FG,
    'xtick.labelsize': FS,
    'ytick.labelsize': FS,
    'axes.titlesize': FS,
    'axes.labelsize': FS,
})

import fetchers

try:
    import pyBigWig
except ImportError as e:
    raise RuntimeError('pyBigWig required: pip install pyBigWig') from e

try:
    from Bio import Phylo
    HAVE_BIO = True
except ImportError:
    HAVE_BIO = False


# --- constants ---------------------------------------------------------------

BASE = 'https://shendure-web.gs.washington.edu/content/members/cxqiu/public/nobackup'
HG38_BW_FMT = BASE + '/jax_atac_augmented_241_mammals_hg38/hg38/{species}/{species}.{cell_type}.bw'
HG38_BW_DIR = BASE + '/jax_atac_augmented_241_mammals_hg38/hg38/'
# Human reference predictions on native hg38 (continuous raw predicted accessibility,
# one value per position — no cross-species projection / alignment-depth pooling).
HUMAN_BW_FMT = BASE + '/jax_atac_augmented_human_prediction/hg38/{cell_type}.bw'
# Mouse reference predictions on native mm10 (contiguous reference; chrom names chr1..).
MOUSE_BW_FMT = BASE + '/jax_atac_augmented_mouse_prediction/mm10/{cell_type}.bw'

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

VIRIDIS_FLOOR = '#440154'

REPO_ROOT = Path(__file__).resolve().parent
SPECIES_INDEX_CACHE = REPO_ROOT / 'cache' / 'species_list.txt'
TREE_PATH_DEFAULT = REPO_ROOT / 'data' / 'zoonomia_241.nwk'
NORM_REF_SPECIES = REPO_ROOT / 'data' / 'norm_ref_species.npz'


# --- genome-wide Phred quantile normalisation --------------------------------
# Each (species, cell_type) track is mapped to a common genome-wide Phred distribution:
#   value -> genome-wide percentile within that track -> Q = -10*log10(1 - pct).
# After this, every track has an identical genome-wide distribution and Q10 = top 10%.

_NORM_CACHE: dict = {}


def load_species_norm_ref(path: Path = NORM_REF_SPECIES):
    """Return (levels, qmap) where qmap[(species, cell_type)] -> quantile curve (or None).
    Cached in-process. Returns (None, {}) if the reference has not been built yet."""
    key = str(path)
    if key in _NORM_CACHE:
        return _NORM_CACHE[key]
    if not path.exists():
        _NORM_CACHE[key] = (None, {})
        return _NORM_CACHE[key]
    z = np.load(path, allow_pickle=True)
    levels = z['levels']
    sp = list(z['species']); cts = list(z['cell_types']); qv = z['qvals']
    qmap = {}
    for i, s in enumerate(sp):
        for j, c in enumerate(cts):
            curve = qv[i, j]
            qmap[(s, c)] = None if not np.isfinite(curve).any() else curve
    _NORM_CACHE[key] = (levels, qmap)
    return _NORM_CACHE[key]


def values_to_phred(values: np.ndarray, qcurve: np.ndarray, levels: np.ndarray) -> np.ndarray:
    """Map raw values to genome-wide Phred via this track's quantile curve. NaN-safe."""
    v = np.asarray(values, float)
    pct = np.interp(v, qcurve, levels)
    pct = np.clip(pct, 0.0, 1.0 - 1e-6)
    q = -10.0 * np.log10(1.0 - pct)
    return np.where(np.isfinite(v), q, np.nan)


def norm_ref_available() -> bool:
    return NORM_REF_SPECIES.exists()


def _apply_norm(out: dict, tracks: dict) -> dict:
    """In-place map each array in ``out`` from raw -> genome-wide Phred, using the
    per-(species, cell_type) reference. ``tracks`` maps each key -> (species, cell_type).
    No-op (returns raw) if the reference isn't built yet or a track lacks a curve."""
    levels, qmap = load_species_norm_ref()
    if levels is None:
        return out
    for k, arr in out.items():
        cur = qmap.get(tracks[k])
        if cur is not None:
            out[k] = values_to_phred(arr, cur, levels)
    return out


# --- gene symbol -> hg38 TSS (Ensembl REST) ----------------------------------

ENSEMBL_LOOKUP = 'https://rest.ensembl.org/lookup/symbol/homo_sapiens/{symbol}'


def lookup_gene_tss(symbol: str, timeout: float = 15.0) -> dict:
    """Resolve a human gene symbol to its hg38 TSS via Ensembl REST.

    Returns a dict with keys: chrom (str, with 'chr' prefix), tss (int, 1-based),
    strand ('+' or '-'), gene_name, ensembl_id, biotype, start, end.
    Raises ValueError if the symbol is unknown.
    """
    symbol = symbol.strip()
    if not symbol:
        raise ValueError('Empty gene symbol.')
    r = requests.get(
        ENSEMBL_LOOKUP.format(symbol=symbol),
        headers={'Accept': 'application/json'},
        timeout=timeout,
    )
    if r.status_code in (400, 404):
        raise ValueError(f'Gene symbol "{symbol}" not found in Ensembl (human).')
    r.raise_for_status()
    d = r.json()
    seq = d['seq_region_name']
    chrom = seq if seq.startswith('chr') else f'chr{seq}'
    strand = d['strand']  # 1 or -1
    tss = int(d['start']) if strand == 1 else int(d['end'])
    return {
        'chrom': chrom,
        'tss': tss,
        'strand': '+' if strand == 1 else '-',
        'gene_name': d.get('display_name', symbol),
        'ensembl_id': d.get('id'),
        'biotype': d.get('biotype'),
        'start': int(d['start']),
        'end': int(d['end']),
    }


# --- gene models in a window ------------------------------------------------
# Served from a local refGene table. The Ensembl REST overlap call this replaces
# was the slowest stage of a cold query (~6 s, against ~4 s to fetch all 241
# bigwigs) and went down intermittently; the local table answers in ~10 ms and
# removes a network dependency for anyone running this offline.

_GENE_MODELS: Optional[list] = None
GENE_MODELS_PATH = REPO_ROOT / 'data' / 'gene_models_hg38.json.gz'


def _load_gene_models() -> list:
    global _GENE_MODELS
    if _GENE_MODELS is None:
        if GENE_MODELS_PATH.exists():
            import gzip as _gzip, json as _json
            with _gzip.open(GENE_MODELS_PATH, 'rt') as fh:
                _GENE_MODELS = _json.load(fh)
            for g in _GENE_MODELS:
                g['exons'] = [tuple(e) for e in g['exons']]
        else:
            _GENE_MODELS = []
    return _GENE_MODELS


def local_gene_tss(symbol: str) -> Optional[dict]:
    """Symbol -> hg38 TSS from the bundled refGene table, same shape as
    ``lookup_gene_tss``. Saves a ~1 s Ensembl round trip per new gene."""
    sym = symbol.strip().upper()
    hits = [g for g in _load_gene_models() if g['name'].upper() == sym]
    if not hits:
        return None
    g = max(hits, key=lambda h: h['end'] - h['start'])
    return {
        'chrom': g['chrom'],
        'tss': g['start'] if g['strand'] >= 0 else g['end'],
        'strand': '+' if g['strand'] >= 0 else '-',
        'symbol': g['name'],
    }


def local_gene_models(chrom: str, start: int, end: int) -> list[dict]:
    """Genes overlapping hg38 ``chrom:start-end``, from the bundled refGene table."""
    return [dict(g) for g in _load_gene_models()
            if g['chrom'] == chrom and g['start'] <= end and g['end'] >= start]


# --- gene models via Ensembl (fallback / unused by the app) ------------------

ENSEMBL_OVERLAP = 'https://rest.ensembl.org/overlap/region/homo_sapiens/{region}'


def fetch_gene_models(chrom: str, start: int, end: int,
                      timeout: float = 25.0) -> list[dict]:
    """Genes overlapping hg38 ``chrom:start-end`` with a representative transcript's
    exon structure, for a UCSC-style gene track. Best-effort: returns [] on any error.

    Each gene dict: name, start, end (hg38 bp), strand (+1/-1), biotype,
    exons (list of (start, end) bp, sorted).
    """
    seq = chrom[3:] if chrom.startswith('chr') else chrom
    region = f'{seq}:{int(start)}-{int(end)}'
    url = (ENSEMBL_OVERLAP.format(region=region)
           + '?feature=gene;feature=transcript;feature=exon')
    try:
        r = requests.get(url, headers={'Accept': 'application/json'}, timeout=timeout)
        r.raise_for_status()
        items = r.json()
    except Exception:
        return []

    genes, transcripts, exons = {}, {}, []
    for it in items:
        ft = it.get('feature_type')
        if ft == 'gene':
            genes[it['id']] = {
                'name': it.get('external_name') or it['id'],
                'start': int(it['start']), 'end': int(it['end']),
                'strand': int(it.get('strand', 1)),
                'biotype': it.get('biotype'),
                'exons': [],
            }
        elif ft == 'transcript':
            transcripts[it['id']] = {'gene': it.get('Parent'), 'exons': []}
        elif ft == 'exon':
            exons.append(it)

    for ex in exons:
        tx = transcripts.get(ex.get('Parent'))
        if tx is not None:
            tx['exons'].append((int(ex['start']), int(ex['end'])))

    by_gene: dict = {}
    for tx in transcripts.values():
        by_gene.setdefault(tx['gene'], []).append(tx)
    for gid, g in genes.items():
        txs = by_gene.get(gid, [])
        best = max(txs, key=lambda t: (len(t['exons']),
                                       sum(e - s for s, e in t['exons'])),
                   default=None)
        g['exons'] = sorted(best['exons']) if (best and best['exons']) else []
    return list(genes.values())


# --- species list ------------------------------------------------------------

def list_species(refresh: bool = False) -> list[str]:
    SPECIES_INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if SPECIES_INDEX_CACHE.exists() and not refresh:
        return SPECIES_INDEX_CACHE.read_text().split()
    html = requests.get(HG38_BW_DIR, timeout=60).text
    sp = sorted({m for m in re.findall(r'href="([A-Z][A-Za-z_]+)/"', html)})
    SPECIES_INDEX_CACHE.write_text('\n'.join(sp))
    return sp


# --- per-species hg38-projected signal fetch ---------------------------------

def _fetch_one(species: str, chrom: str, start: int, end: int,
               cell_type: str, n_bins: int) -> Optional[np.ndarray]:
    url = HG38_BW_FMT.format(species=species, cell_type=cell_type)
    try:
        bw = pyBigWig.open(url)
    except Exception:
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


def fetch_signals_multi_ct(chrom: str, pos: int, cell_types: list[str],
                           window_kb: float = 100.0, bin_kb: float = 0.1,
                           species_list: Optional[list[str]] = None,
                           max_workers: int = 64,
                           progress: Optional[Callable[[int, int], None]] = None,
                           normalize: bool = False,
                           ) -> dict[tuple, np.ndarray]:
    """Fetch hg38-projected signals for many (species, cell_type) pairs.

    Returns dict keyed by (species, cell_type) -> 1D array of length n_bins.
    ``normalize`` -> map each track to genome-wide Phred (Q10 = top 10%).
    """
    if species_list is None:
        species_list = list_species()
    window_bp = int(window_kb * 1000)
    bin_bp = bin_kb * 1000
    n_bins = int(round(2 * window_bp / bin_bp))
    start = max(0, int(pos) - window_bp)
    end = int(pos) + window_bp

    out: dict[tuple, np.ndarray] = {}
    tasks = [(sp, ct) for sp in species_list for ct in cell_types]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_fetch_one, sp, chrom, start, end, ct, n_bins): (sp, ct)
                for sp, ct in tasks}
        done = 0
        for fut in as_completed(futs):
            sp, ct = futs[fut]
            arr = fut.result()
            if arr is not None and arr.shape == (n_bins,):
                out[(sp, ct)] = arr
            done += 1
            if progress is not None:
                progress(done, len(futs))
    if normalize:
        out = _apply_norm(out, {k: k for k in out})
    return out


def aggregate_celltype_matrix(signals: dict[tuple, np.ndarray],
                              species_list: list[str],
                              cell_types: list[str],
                              normalisation: str = 'per_row',
                              ) -> tuple[np.ndarray, np.ndarray]:
    """Per (cell_type, bin): sum the raw STEAM-v1 prediction scores across species
    (NaN → 0), then normalise.

    ``normalisation``:
      - 'per_row'  (default): each cell-type row is divided by its own max → reveals
        cell-type-specific patterns; rows are not comparable in magnitude.
      - 'global':  whole matrix divided by global max → preserves cross-cell-type
        magnitudes, but strong broad peaks can dim narrower cell-type-specific peaks.

    Returns (normalised matrix shape (n_ct, n_bins), per-bin synteny coverage shape (n_bins,)).
    """
    n_bins = next(iter(signals.values())).shape[0]
    n_ct = len(cell_types)
    sums = np.zeros((n_ct, n_bins))
    ct_index = {c: i for i, c in enumerate(cell_types)}
    for (sp, ct), arr in signals.items():
        ci = ct_index.get(ct)
        if ci is None:
            continue
        # Raw STEAM-v1 prediction; NaN bins contribute zero (no synteny → no contribution).
        sums[ci] += np.where(np.isfinite(arr), arr, 0.0)

    if normalisation == 'per_row':
        row_max = sums.max(axis=1, keepdims=True)
        row_max = np.where(row_max > 0, row_max, 1.0)
        norm_mat = sums / row_max
    elif normalisation == 'row_then_col':
        row_max = sums.max(axis=1, keepdims=True)
        row_max = np.where(row_max > 0, row_max, 1.0)
        m1 = sums / row_max
        col_max = m1.max(axis=0, keepdims=True)
        col_max = np.where(col_max > 0, col_max, 1.0)
        norm_mat = m1 / col_max
    elif normalisation == 'col_then_row':
        col_max = sums.max(axis=0, keepdims=True)
        col_max = np.where(col_max > 0, col_max, 1.0)
        m1 = sums / col_max
        row_max = m1.max(axis=1, keepdims=True)
        row_max = np.where(row_max > 0, row_max, 1.0)
        norm_mat = m1 / row_max
    elif normalisation == 'col_residual':
        # Subtract per-column mean (the "shared baseline" across cell types at this
        # position); clip negatives; normalise globally so the strongest cell-type
        # outlier in the view = 1.
        col_mean = sums.mean(axis=0, keepdims=True)
        resid = np.clip(sums - col_mean, 0, None)
        mx = resid.max()
        norm_mat = resid / mx if mx > 0 else resid
    elif normalisation == 'col_zscore':
        # Per-column z-score, clipped at 0, normalised.
        col_mean = sums.mean(axis=0, keepdims=True)
        col_std = sums.std(axis=0, keepdims=True) + 1e-9
        z = np.clip((sums - col_mean) / col_std, 0, None)
        mx = z.max()
        norm_mat = z / mx if mx > 0 else z
    else:  # 'global'
        mx = sums.max()
        norm_mat = sums / mx if mx > 0 else sums

    # Synteny coverage per bin: fraction of species with hg38 data, averaged over cell types.
    per_sp_cov = {}
    for (sp, ct), arr in signals.items():
        per_sp_cov.setdefault(sp, []).append(np.isfinite(arr))
    if per_sp_cov:
        species_cov = np.stack([np.mean(np.stack(v), axis=0) for v in per_sp_cov.values()])
        coverage = species_cov.mean(axis=0)
    else:
        coverage = np.zeros(n_bins)
    return norm_mat, coverage


# --- specificity score-band decomposition (analysis "d") ---------------------

# Fine bands: 0-2, 2-6, 6-10, then 5-wide from 10 up to 60, then 60+.
DEFAULT_SCORE_BANDS = (0.0, 2.0, 6.0, 10.0, 15.0, 20.0, 25.0, 30.0,
                       35.0, 40.0, 45.0, 50.0, 55.0, 60.0, np.inf)


def phred_quantile_edges(n_bins: int = 40) -> np.ndarray:
    """Equal-occupancy (2.5%/bin for n=40) score-band edges on the Phred scale.

    Phred: a score Q means a top fraction f = 10^(-Q/10) of sequences (Q10 = top 10%,
    Q20 = top 1%). So the score boundary at the bottom-percentile p is
    Q = -10*log10(1 - p). Bin k (k=1..n) covers bottom percentiles [(k-1)/n, k/n].
    Returns n_bins+1 edges from 0 to +inf (analytic; no sampling needed)."""
    k = np.arange(n_bins + 1)
    topfrac = 1.0 - k / n_bins                      # 1.0 .. 0.0
    with np.errstate(divide='ignore'):
        edges = -10.0 * np.log10(topfrac)           # 0 .. +inf
    edges[0] = 0.0
    edges[-1] = np.inf
    return edges


def specificity_score_bands(signals: dict[tuple, np.ndarray],
                            species_list: list[str],
                            cell_types: list[str],
                            focal: str,
                            band_edges=DEFAULT_SCORE_BANDS,
                            grid_step: float = 0.5):
    """Decompose a focal cell type's aggregate locus specificity across
    predicted-score (phred) bands, at the per-(species, bin) level.

    For every (species s, bin x) the focal prediction ``v = P_focal,s(x)`` carries
    a *specific* contribution ``e = max(0, v - m)`` where ``m`` is the mean
    prediction across ALL cell types at that (s, x) — the same per-position
    baseline the ``col_residual`` cross-section subtracts. Each contribution is
    binned by its own focal score ``v``. This separates "the aggregate is a few
    high-phred punctate enhancers" from "many sub-threshold contributions across
    the locus / across species".

    Returns a dict:
      bands       DataFrame [band, lo, hi, n, total, specific, specific_frac]
      grid        1D score grid (tau)
      cum_spec    fraction of TOTAL specificity from scores <= tau
      cum_total   fraction of TOTAL signal from scores <= tau
      A_total     total positive specificity (sum of e)
      sig_total   total focal signal (sum of v)
    """
    ct_index = {c: i for i, c in enumerate(cell_types)}
    sp_index = {s: i for i, s in enumerate(species_list)}
    if focal not in ct_index or not signals:
        return None
    n_bins = next(iter(signals.values())).shape[0]
    cube = np.full((len(cell_types), len(species_list), n_bins), np.nan)
    for (sp, ct), arr in signals.items():
        ci = ct_index.get(ct); si = sp_index.get(sp)
        if ci is not None and si is not None and arr.shape == (n_bins,):
            cube[ci, si] = arr

    focal_v = cube[ct_index[focal]]                      # (n_sp, n_bins)
    with np.errstate(invalid='ignore', all='ignore'):
        m = np.nanmean(cube, axis=0)                     # per-(s,x) cross-cell-type mean
    excess = np.clip(focal_v - m, 0.0, None)

    finite = np.isfinite(focal_v) & np.isfinite(excess)
    v = focal_v[finite]
    e = excess[finite]
    if v.size == 0:
        return None

    edges = np.asarray(band_edges, float)
    n_band = len(edges) - 1
    idx = np.clip(np.digitize(v, edges, right=False) - 1, 0, n_band - 1)
    rows = []
    for b in range(n_band):
        sel = idx == b
        tb = float(v[sel].sum()); eb = float(e[sel].sum())
        hi = edges[b + 1]
        rows.append({
            'band': (f'{edges[b]:g}-{hi:g}' if np.isfinite(hi) else f'{edges[b]:g}+'),
            'lo': float(edges[b]), 'hi': float(hi),
            'n': int(sel.sum()), 'total': tb, 'specific': eb,
            'specific_frac': (eb / tb if tb > 0 else 0.0),
        })
    bands = pd.DataFrame(rows)

    A_total = float(e.sum())
    sig_total = float(v.sum())

    # cumulative curves over a fine score grid
    order = np.argsort(v)
    vs = v[order]
    cse = np.cumsum(e[order])
    cst = np.cumsum(vs)
    gmax = max(float(np.nanpercentile(v, 99.9)), float(edges[-2]))
    grid = np.arange(0.0, gmax + grid_step, grid_step)
    pos = np.searchsorted(vs, grid, side='right')
    take = np.clip(pos - 1, 0, len(vs) - 1)
    cum_e = np.where(pos > 0, cse[take], 0.0)
    cum_t = np.where(pos > 0, cst[take], 0.0)
    cum_spec = cum_e / A_total if A_total > 0 else cum_e * 0.0
    cum_total = cum_t / sig_total if sig_total > 0 else cum_t * 0.0

    return {'bands': bands, 'grid': grid, 'cum_spec': cum_spec,
            'cum_total': cum_total, 'A_total': A_total, 'sig_total': sig_total}


def plot_score_band_decomposition(result: dict, focal: str, *,
                                   anchor_label: str, window_kb: float,
                                   threshold: float = 10.0):
    """Two-panel figure: (top) fraction of focal specificity per score
    band; (bottom) cumulative fraction of specificity vs. total signal as the
    score threshold sweeps, with the enhancer-calling threshold marked."""
    bands = result['bands']
    grid, cum_spec, cum_total = result['grid'], result['cum_spec'], result['cum_total']
    A = result['A_total']

    fig = plt.figure(figsize=(7.2, 5.2))
    gs = fig.add_gridspec(2, 1, height_ratios=[1.0, 1.0], hspace=0.45)

    # --- (top) specificity fraction per band ---
    ax1 = fig.add_subplot(gs[0])
    fr = bands['specific'].to_numpy() / (A if A > 0 else 1.0)
    xpos = np.arange(len(bands))
    ax1.bar(xpos, fr, color=FIG_FG, edgecolor=FIG_FG, width=0.7)
    for x, f in zip(xpos, fr):
        if f >= 0.02:  # only label non-trivial bars to avoid clutter at 14 bands
            ax1.text(x, f + 0.01, f'{f*100:.0f}%', ha='center', va='bottom',
                     fontsize=FS, color=FIG_FG)
    ax1.set_xticks(xpos)
    _rot = 45 if len(bands) > 8 else 0
    ax1.set_xticklabels(bands['band'], fontsize=FS, rotation=_rot,
                        ha=('right' if _rot else 'center'))
    ax1.set_ylim(0, max(0.001, fr.max()) * 1.18)
    ax1.set_ylabel('frac. of total\nspecificity', fontsize=FS)
    ax1.set_xlabel('predicted accessibility score band', fontsize=FS)
    ax1.set_title(f'{focal}: specificity by score band — {anchor_label} '
                  f'±{window_kb:g} kb', fontsize=FS)
    ax1.tick_params(labelsize=FS, length=2)
    ax1.spines[['top', 'right']].set_visible(False)
    for sp in ax1.spines.values():
        sp.set_color(FIG_FG)

    # --- (bottom) cumulative sweep ---
    ax2 = fig.add_subplot(gs[1])
    ax2.plot(grid, cum_spec, color=FIG_FG, lw=1.2, label='specificity')
    ax2.plot(grid, cum_total, color=FIG_DIM, lw=1.0, ls='--', label='total signal')
    ax2.axvline(threshold, color=FIG_HL, lw=0.9, ls=':')
    # fraction of specificity below the threshold
    fb = float(np.interp(threshold, grid, cum_spec))
    ax2.scatter([threshold], [fb], color=FIG_HL, s=18, zorder=5)
    ax2.annotate(f'{fb*100:.0f}% of specificity\nfrom scores < {threshold:g}',
                 xy=(threshold, fb), xytext=(threshold + (grid[-1] - grid[0]) * 0.04, fb - 0.18),
                 fontsize=FS, color=FIG_HL,
                 arrowprops=dict(arrowstyle='-', color=FIG_HL, lw=0.6))
    ax2.set_xlim(grid[0], grid[-1])
    ax2.set_ylim(0, 1.02)
    ax2.set_xlabel('predicted accessibility score threshold  τ', fontsize=FS)
    ax2.set_ylabel('cum. fraction\n(scores ≤ τ)', fontsize=FS)
    ax2.tick_params(labelsize=FS, length=2)
    ax2.spines[['top', 'right']].set_visible(False)
    for sp in ax2.spines.values():
        sp.set_color(FIG_FG)
    leg = ax2.legend(fontsize=FS, frameon=False, loc='lower right')
    for t in leg.get_texts():
        t.set_color(FIG_FG)
    return fig


def fetch_signals(chrom: str, pos: int, cell_type: str,
                  window_kb: float = 100.0, bin_kb: float = 0.1,
                  species_list: Optional[list[str]] = None,
                  max_workers: int = 16,
                  progress: Optional[Callable[[int, int], None]] = None,
                  normalize: bool = False,
                  ) -> dict[str, np.ndarray]:
    """Fetch hg38-projected `mean` signal in `bin_kb`-sized bins for each species.

    Returns dict {species: 1D array of length n_bins}, dropping species that
    have no data at the locus. ``normalize`` -> genome-wide Phred (Q10 = top 10%).
    """
    if species_list is None:
        species_list = list_species()
    window_bp = int(window_kb * 1000)
    bin_bp = bin_kb * 1000
    n_bins = int(round(2 * window_bp / bin_bp))
    start = max(0, int(pos) - window_bp)
    end = int(pos) + window_bp

    import runner
    out = runner.run_fetch({
        'mode': 'hg38',
        'cell_type': cell_type,
        'chrom': chrom, 'start': start, 'end': end,
        'n_bins': n_bins,
        'workers': max_workers,
        'species': list(species_list),
    })
    if normalize:
        out = _apply_norm(out, {sp: (sp, cell_type) for sp in out})
    return out


# --- synteny proxy -----------------------------------------------------------

def contiguous_span_bins(arr: np.ndarray, max_gap_bins: int) -> int:
    fin = np.isfinite(arr)
    best = cur = gap = 0
    for v in fin:
        if v:
            cur += 1
            gap = 0
        else:
            gap += 1
            cur = cur + 1 if gap <= max_gap_bins else 0
        best = max(best, cur)
    return best


def compute_synteny(signals: dict[str, np.ndarray], bin_bp: float,
                    max_gap_kb: float = 10.0) -> pd.DataFrame:
    max_gap_bins = int(round(max_gap_kb * 1000 / bin_bp))
    rows = []
    for sp, arr in signals.items():
        rows.append({
            'species': sp,
            'syntenic_span_kb': contiguous_span_bins(arr, max_gap_bins) * bin_bp / 1000,
            'coverage_frac': float(np.isfinite(arr).mean()),
            'mean_signal': float(np.nanmean(arr)) if np.isfinite(arr).any() else np.nan,
        })
    return pd.DataFrame(rows).sort_values('syntenic_span_kb', ascending=False)


# --- tree --------------------------------------------------------------------

def ladderize(clade, reverse: bool = True) -> None:
    for c in clade.clades:
        ladderize(c, reverse)
    clade.clades.sort(key=lambda c: c.count_terminals(), reverse=reverse)


def tree_leaf_order(tree_path: Path = TREE_PATH_DEFAULT,
                    restrict_to: Optional[set[str]] = None) -> list[str]:
    """Ladderized leaf order of the Zoonomia tree, optionally restricted to a species set."""
    if not (HAVE_BIO and tree_path.exists()):
        return [] if restrict_to is None else sorted(restrict_to)
    tree = Phylo.read(str(tree_path), 'newick')
    ladderize(tree.root, reverse=True)
    leaves = [t.name for t in tree.get_terminals()]
    if restrict_to is not None:
        leaves = [n for n in leaves if n in restrict_to]
    return leaves


def subsample_evenly(species_pool: list[str], n: int,
                     must_include: tuple[str, ...] = ()) -> list[str]:
    """Pick ~n species evenly along the given order; always keep any must_include present."""
    if n >= len(species_pool):
        return list(species_pool)
    step = len(species_pool) / n
    picked = [species_pool[min(int(i * step), len(species_pool) - 1)] for i in range(n)]
    picked = list(dict.fromkeys(picked))  # de-dup, preserve order
    must = [s for s in must_include if s in species_pool and s not in picked]
    # Insert must-includes at their tree-order position
    pool_idx = {s: i for i, s in enumerate(species_pool)}
    picked = sorted(set(picked) | set(must), key=lambda s: pool_idx[s])
    return picked


def load_pruned_tree(retained_species: list[str],
                     tree_path: Path = TREE_PATH_DEFAULT):
    if not (HAVE_BIO and tree_path.exists()):
        return list(retained_species), None
    tree = Phylo.read(str(tree_path), 'newick')
    keep = set(retained_species)
    for t in [t for t in tree.get_terminals() if t.name not in keep]:
        tree.prune(t)
    ladderize(tree.root, reverse=True)
    order = [t.name for t in tree.get_terminals()]
    order += [s for s in retained_species if s not in order]
    return order, tree


def abbreviate_species(name: str) -> str:
    parts = name.split('_')
    return f'{parts[0][0]}. ' + ' '.join(parts[1:]) if len(parts) >= 2 else name


def _tree_node_coords(tree):
    leaves = tree.get_terminals()
    leaf_y = {id(l): i for i, l in enumerate(leaves)}
    xs, ys = {}, {}

    def depth(clade, x0):
        x = x0 + (clade.branch_length or 0.0)
        xs[id(clade)] = x
        if clade.is_terminal():
            ys[id(clade)] = leaf_y[id(clade)]
        else:
            for c in clade.clades:
                depth(c, x)
            ys[id(clade)] = float(np.mean([ys[id(c)] for c in clade.clades]))

    depth(tree.root, 0.0)
    return xs, ys


def _draw_rect_tree(ax_tree, ax_lab, tree, species_order, fontsize, highlight=()):
    n = len(species_order)
    xs, ys = _tree_node_coords(tree)
    xmax = max(xs.values())

    def seg(clade):
        x0 = xs[id(clade)] - (clade.branch_length or 0.0)
        ax_tree.plot([x0, xs[id(clade)]], [ys[id(clade)], ys[id(clade)]],
                     color=FIG_FG, lw=0.4, solid_capstyle='butt')
        if not clade.is_terminal():
            cy = [ys[id(c)] for c in clade.clades]
            ax_tree.plot([xs[id(clade)], xs[id(clade)]], [min(cy), max(cy)],
                         color=FIG_FG, lw=0.4, solid_capstyle='butt')
            for c in clade.clades:
                seg(c)

    seg(tree.root)
    hl = set(highlight)
    for clade in tree.get_terminals():
        y = ys[id(clade)]
        ax_tree.plot([xs[id(clade)], xmax], [y, y], color=FIG_FAINT, lw=0.2, zorder=0)
        ax_lab.text(0.98, y, abbreviate_species(clade.name), va='center', ha='right',
                    fontsize=fontsize, style='italic',
                    color=(FIG_HL if clade.name in hl else FIG_FG))
    for a in (ax_tree, ax_lab):
        a.set_ylim(n - 0.5, -0.5)
        a.axis('off')
    ax_tree.set_xlim(0, xmax)
    ax_lab.set_xlim(0, 1)


# --- plot --------------------------------------------------------------------

def _genomic_xticks(anchor_pos: int, window_kb: float, target: int = 6):
    """UCSC-style coordinate ruler for the [-window_kb, +window_kb] (relative kb)
    x-axis around ``anchor_pos`` (hg38, 1-based).

    Returns (major_xs_kb, major_labels, minor_xs_kb) where xs are in relative kb
    (0 == anchor) and labels are compact absolute hg38 coordinates.
    """
    start = anchor_pos - window_kb * 1000.0
    end = anchor_pos + window_kb * 1000.0
    span = end - start
    raw = span / max(target, 1)
    mag = 10.0 ** np.floor(np.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag)

    first = np.ceil(start / step) * step
    ticks_bp = np.arange(first, end + 1e-6, step)
    major_xs_kb = (ticks_bp - anchor_pos) / 1000.0

    if step >= 1e6:
        labels = [f'{t / 1e6:,.2f} Mb' for t in ticks_bp]
    elif step >= 1e3:
        dec = 0 if abs(step % 1e3) < 1e-6 else 1
        labels = [f'{t / 1e3:,.{dec}f} kb' for t in ticks_bp]
    else:
        labels = [f'{int(round(t)):,}' for t in ticks_bp]

    minor_step = step / 5.0
    mfirst = np.ceil(start / minor_step) * minor_step
    minor_bp = np.arange(mfirst, end + 1e-6, minor_step)
    minor_xs_kb = (minor_bp - anchor_pos) / 1000.0

    return major_xs_kb, labels, minor_xs_kb


def _apply_coord_ruler(ax, anchor_pos, window_kb, *, fontsize=FS,
                       label_ticks=True, grid_axes=()):
    """Put a UCSC-style genomic-coordinate ruler (major + minor ticks, compact
    hg38 labels) on ``ax``'s x-axis. Draw faint vertical guide lines at the major
    ticks on each axis in ``grid_axes`` (typically the open track panels — not the
    heatmap itself, to avoid veiling the colour data)."""
    major_xs, labels, minor_xs = _genomic_xticks(anchor_pos, window_kb)
    ax.set_xticks(major_xs)
    ax.set_xticks(minor_xs, minor=True)
    if label_ticks:
        ax.set_xticklabels(labels, fontsize=fontsize)
    ax.tick_params(axis='x', which='major', length=4, width=0.6, labelsize=fontsize)
    ax.tick_params(axis='x', which='minor', length=2, width=0.4)
    for gax in grid_axes:
        for gx in major_xs:
            gax.axvline(gx, color=FIG_DIM, lw=0.3, zorder=0)
        for gx in minor_xs:
            gax.axvline(gx, color=FIG_FAINT, lw=0.25, zorder=0)


def _nice_round(x: float) -> float:
    """Largest 1/2/5 × 10ⁿ value ≤ a 'nice' bracket of x (for scale bars)."""
    if x <= 0:
        return 1.0
    mag = 10.0 ** np.floor(np.log10(x))
    for m in (1, 2, 5, 10):
        if x <= m * mag:
            return m * mag
    return 10 * mag


def _draw_scale_bar(ax, window_kb, *, color=FIG_FG, y=0.965, fontsize=FS):
    """UCSC-style scale bar (a round-number genomic length) at top-left of ``ax``,
    drawn in axes-fraction Y / data-kb X."""
    full_kb = 2 * window_kb
    bar_kb = _nice_round(full_kb * 0.16)
    x0 = -window_kb + full_kb * 0.03
    x1 = x0 + bar_kb
    trans = blended_transform_factory(ax.transData, ax.transAxes)
    ax.plot([x0, x1], [y, y], transform=trans, color=color, lw=1.2,
            solid_capstyle='butt', clip_on=False, zorder=6)
    for xx in (x0, x1):
        ax.plot([xx, xx], [y - 0.012, y + 0.012], transform=trans, color=color,
                lw=1.2, clip_on=False, zorder=6)
    lbl = f'{bar_kb:g} kb' if bar_kb < 1000 else f'{bar_kb / 1000:g} Mb'
    ax.text((x0 + x1) / 2, y - 0.035, lbl, transform=trans, color=color,
            ha='center', va='top', fontsize=fontsize, zorder=6)


def _pack_genes(genes, anchor_pos, window_kb):
    """Greedily pack genes into non-overlapping rows (UCSC 'pack' mode). Returns
    (assignment dict id(gene)->row, n_rows)."""
    pad_kb = max(window_kb * 0.05, 0.5)  # spacing to leave room for the label

    def rk(bp):
        return (bp - anchor_pos) / 1000.0

    rows_end: list[float] = []
    assign: dict = {}
    for g in sorted(genes, key=lambda g: g['start']):
        gs, ge = rk(g['start']), rk(g['end'])
        for ri, rend in enumerate(rows_end):
            if gs > rend + pad_kb:
                rows_end[ri] = ge
                assign[id(g)] = ri
                break
        else:
            rows_end.append(ge)
            assign[id(g)] = len(rows_end) - 1
    return assign, max(len(rows_end), 1)


def _draw_gene_track(ax_gene, genes, anchor_pos, window_kb, *, fontsize=FS):
    """Draw a UCSC-style gene-model track: exon boxes, intron line, strand
    chevrons, and italic gene labels — all in the uniform foreground green.
    Non-coding genes are distinguished by thinner exon boxes, not by colour.
    Returns the number of packed rows."""
    ax_gene.set_xlim(-window_kb, window_kb)
    ax_gene.axis('off')
    if not genes:
        ax_gene.set_ylim(0, 1)
        return 1

    def rk(bp):
        return (bp - anchor_pos) / 1000.0

    assign, nrows = _pack_genes(genes, anchor_pos, window_kb)
    ax_gene.set_ylim(nrows - 0.5, -0.5)

    for g in genes:
        ri = assign[id(g)]
        gs, ge = rk(g['start']), rk(g['end'])
        coding = g['biotype'] == 'protein_coding'
        eh = 0.34 if coding else 0.22  # thinner box = non-coding
        # intron line
        ax_gene.plot([gs, ge], [ri, ri], color=FIG_FG, lw=0.6,
                     solid_capstyle='butt', zorder=2)
        # strand chevrons along the body
        span = ge - gs
        n_chev = int(np.clip(span / (window_kb * 0.05 + 1e-9), 1, 14))
        if span > 0:
            mark = '>' if g['strand'] >= 0 else '<'
            for cx in np.linspace(gs, ge, n_chev + 2)[1:-1]:
                ax_gene.text(cx, ri, mark, color=FIG_FG, ha='center', va='center',
                             fontsize=fontsize, zorder=2.5, clip_on=True)
        # exon boxes (min width so single-base exons stay visible)
        min_w = window_kb * 0.0015
        if g['exons']:
            for es, ee in g['exons']:
                x0 = rk(es)
                w = max(rk(ee) - x0, min_w)
                ax_gene.add_patch(Rectangle((x0, ri - eh / 2), w, eh,
                                            facecolor=FIG_FG, edgecolor='none',
                                            zorder=3))
        else:
            ax_gene.add_patch(Rectangle((gs, ri - eh / 4), ge - gs, eh / 2,
                                        facecolor=FIG_FG, edgecolor='none',
                                        zorder=3))
        # label
        xc = float(np.clip((gs + ge) / 2, -window_kb * 0.96, window_kb * 0.96))
        ax_gene.text(xc, ri - 0.46, g['name'], ha='center', va='bottom',
                     fontsize=fontsize, style='italic', color=FIG_FG, clip_on=True,
                     zorder=4)
    return nrows


def plot_fig6e(species_order: list[str],
               signals: dict[str, np.ndarray],
               tree_obj,
               *,
               anchor_label: str,
               anchor_chrom: str,
               anchor_pos: int,
               cell_type: str,
               window_kb: float,
               show_all_species: bool,
               min_syntenic_kb: float,
               score_vmax: float = 30.0,
               highlight_species=(),
               calls: Optional[pd.DataFrame] = None,
               anchor_strand: Optional[str] = None,
               genes: Optional[list[dict]] = None,
               score_label: str = 'STEAM-v1 prediction score'):
    n = len(species_order)
    if n == 0:
        fig = plt.figure(figsize=(6, 2))
        fig.text(0.5, 0.5, 'No species to plot.', ha='center', va='center')
        return fig

    have_tree = tree_obj is not None
    have_dots = (calls is not None) and (not calls.empty)
    # Tip labels share the uniform size FS when species are few; only shrink when
    # there are too many rows to fit at full size.
    fontsize = float(np.clip(560 / max(n, 1), 3.0, FS))

    mat = np.vstack([signals[s] for s in species_order])
    coverage = np.isfinite(mat).mean(axis=0)
    strength = np.nansum(mat, axis=0)
    strength = strength / strength.max() if strength.max() > 0 else strength
    xgrid = np.linspace(-window_kb, window_kb, mat.shape[1])

    # Pre-pack genes (if any) to size the gene-model row.
    have_genes = bool(genes)
    gene_rows = _pack_genes(genes, anchor_pos, window_kb)[1] if have_genes else 0
    gene_h = (0.28 * gene_rows + 0.25) if have_genes else 0.0

    base_h = max(6.0, n * 0.085)
    track_h = 0.75
    # Narrow label column (just wider than longest tip label); near-zero wspace.
    col_w = ([1.0, 0.45] if have_tree else []) + [3.0]
    fig_w = sum(col_w) * 1.7

    # Row layout: synteny track, strength track, [gene track], heatmap.
    row_h = [track_h, track_h] + ([gene_h] if have_genes else []) + [base_h]
    fig = plt.figure(figsize=(fig_w, sum(row_h) + 1.0))
    gs = fig.add_gridspec(len(row_h), len(col_w), width_ratios=col_w,
                          height_ratios=row_h, wspace=0.01, hspace=0.05)
    heat_col = len(col_w) - 1
    heat_row = len(row_h) - 1
    gene_row = heat_row - 1 if have_genes else None

    def style_track(axx, ylabel):
        axx.set_xlim(-window_kb, window_kb)
        axx.set_ylim(0, 1)
        axx.set_xticks([])
        axx.set_yticks([0, 1])
        axx.tick_params(labelsize=FS, length=2)
        axx.set_ylabel(ylabel, fontsize=FS, rotation=0, ha='right', va='center')
        axx.spines[['top', 'right']].set_visible(False)
        axx.spines[['left', 'bottom']].set_color(FIG_FG)

    ax_cov = fig.add_subplot(gs[0, heat_col])
    ax_cov.fill_between(xgrid, coverage, color=FIG_DIM, lw=0)
    ax_cov.plot(xgrid, coverage, color=FIG_FG, lw=0.7)
    style_track(ax_cov, 'synteny\n(frac)')
    syn_note = 'all species' if show_all_species else f'syntenic ≥{min_syntenic_kb:g} kb'
    win_start = int(anchor_pos - window_kb * 1000)
    win_end = int(anchor_pos + window_kb * 1000)
    ax_cov.set_title(
        f'{anchor_label}   {anchor_chrom}:{win_start:,}-{win_end:,}  '
        f'({2 * window_kb:g} kb)\n'
        f'{cell_type}, {n} species ({syn_note})',
        fontsize=FS, pad=16,
    )

    ax_str = fig.add_subplot(gs[1, heat_col], sharex=ax_cov)
    ax_str.fill_between(xgrid, strength, color=FIG_DIM, lw=0)
    ax_str.plot(xgrid, strength, color=FIG_FG, lw=0.7)
    style_track(ax_str, 'signal\n(norm)')

    grid_axes = [ax_cov, ax_str]
    if have_genes:
        ax_gene = fig.add_subplot(gs[gene_row, heat_col], sharex=ax_cov)
        _draw_gene_track(ax_gene, genes, anchor_pos, window_kb, fontsize=FS)
        grid_axes.append(ax_gene)
        # left-column tag for the gene track
        if have_tree:
            fig.add_subplot(gs[gene_row, 1]).axis('off')
            tag = fig.add_subplot(gs[gene_row, 0]); tag.axis('off')
            tag.text(0.98, 0.5, 'genes\n(Ensembl)', ha='right', va='center',
                     fontsize=FS, color=FIG_FG, transform=tag.transAxes)

    if have_tree:
        ax_tree = fig.add_subplot(gs[heat_row, 0])
        ax_lab = fig.add_subplot(gs[heat_row, 1])
        _draw_rect_tree(ax_tree, ax_lab, tree_obj, species_order, fontsize,
                        highlight_species)

    ax = fig.add_subplot(gs[heat_row, heat_col], sharex=ax_cov)
    masked = np.ma.masked_invalid(mat)
    cmap = plt.cm.viridis.copy()
    cmap.set_bad(VIRIDIS_FLOOR)
    vmax = float(score_vmax)
    ax.set_facecolor(VIRIDIS_FLOOR)
    im = ax.imshow(masked, aspect='auto', cmap=cmap, vmin=0, vmax=vmax,
                   extent=[-window_kb, window_kb, n - 0.5, -0.5],
                   interpolation='nearest')
    if have_dots:
        yidx = {s: i for i, s in enumerate(species_order)}
        d = calls[calls['species'].isin(yidx)]
        ax.scatter(d['rel_pos'] / 1000.0, d['species'].map(yidx),
                   s=10, facecolor='none', edgecolor=FIG_HL, linewidths=0.5)

    # TSS / anchor marker
    ax.axvline(0, color=FIG_HL, lw=0.7, ls=':', alpha=0.8)
    trans = blended_transform_factory(ax.transData, ax.transAxes)
    arrow_len_kb = max(8.0, window_kb * 0.12)
    if anchor_strand in ('+', '-'):
        dx = arrow_len_kb if anchor_strand == '+' else -arrow_len_kb
        ax.annotate('', xy=(dx, 1.015), xytext=(0, 1.015), xycoords=trans,
                    arrowprops=dict(arrowstyle='-|>', color=FIG_HL,
                                    lw=0.9, mutation_scale=8), annotation_clip=False)
    ax.scatter([0], [1.015], transform=trans, marker='|', s=30, color=FIG_HL,
               linewidths=1.2, clip_on=False, zorder=5)
    _draw_scale_bar(ax, window_kb)
    ax.set_yticks([])
    _apply_coord_ruler(ax, anchor_pos, window_kb, fontsize=FS, grid_axes=grid_axes)
    ax.set_xlabel(f'{anchor_chrom} (hg38) — dotted line = {anchor_label} anchor', fontsize=FS)

    if have_tree:
        cb_holder = fig.add_subplot(gs[0:2, 0])
        cb_holder.axis('off')
        cax = cb_holder.inset_axes([0.12, 0.45, 0.85, 0.10])
    else:
        cax = ax.inset_axes([0.0, 1.04, 0.32, 0.02])
    cb = fig.colorbar(im, cax=cax, orientation='horizontal')
    cb.set_label(score_label, fontsize=FS, labelpad=2, color=FIG_FG)
    cb.set_ticks([t for t in (0, 10, 20, 30, 40, 50) if t <= vmax + 1e-9])
    cb.ax.tick_params(labelsize=FS, length=2, colors=FIG_FG)
    cb.outline.set_edgecolor(FIG_FG)
    return fig


def plot_celltype_view(cell_types: list[str], mat: np.ndarray, coverage: np.ndarray,
                       *,
                       anchor_label: str, anchor_chrom: str, anchor_pos: int,
                       window_kb: float, n_species_used: int,
                       anchor_strand: Optional[str] = None,
                       normalisation_label: str = 'per cell-type row',
                       genes: Optional[list[dict]] = None):
    """Cell-type cross-section view: 32 rows (cell types) × bins heatmap,
    averaged across species. Synteny coverage track on top.
    """
    n_ct = len(cell_types)
    n_bins = mat.shape[1]
    xgrid = np.linspace(-window_kb, window_kb, n_bins)

    have_genes = bool(genes)
    gene_rows = _pack_genes(genes, anchor_pos, window_kb)[1] if have_genes else 0
    gene_h = (0.28 * gene_rows + 0.25) if have_genes else 0.0

    base_h = max(5.0, n_ct * 0.18)
    track_h = 0.75
    col_w = [3.4]
    fig_w = 9.2
    row_h = [track_h] + ([gene_h] if have_genes else []) + [base_h]
    fig = plt.figure(figsize=(fig_w, sum(row_h) + 1.1))
    gs = fig.add_gridspec(len(row_h), 1, height_ratios=row_h, hspace=0.06)
    gene_row = 1 if have_genes else None
    heat_row = len(row_h) - 1

    # --- synteny coverage track ---
    ax_cov = fig.add_subplot(gs[0])
    ax_cov.fill_between(xgrid, coverage, color=FIG_DIM, lw=0)
    ax_cov.plot(xgrid, coverage, color=FIG_FG, lw=0.7)
    ax_cov.set_xlim(-window_kb, window_kb)
    ax_cov.set_ylim(0, 1)
    ax_cov.set_xticks([])
    ax_cov.set_yticks([0, 1])
    ax_cov.tick_params(labelsize=FS, length=2)
    ax_cov.set_ylabel('synteny\n(frac)', fontsize=FS,
                      rotation=0, ha='right', va='center')
    ax_cov.spines[['top', 'right']].set_visible(False)
    ax_cov.spines[['left', 'bottom']].set_color(FIG_FG)
    win_start = int(anchor_pos - window_kb * 1000)
    win_end = int(anchor_pos + window_kb * 1000)
    ax_cov.set_title(
        f'{anchor_label}   {anchor_chrom}:{win_start:,}-{win_end:,}  '
        f'({2 * window_kb:g} kb)\n'
        f'cell-type cross-section '
        f'(sum of STEAM-v1 prediction scores across {n_species_used} species, '
        f'normalised {normalisation_label})',
        fontsize=FS, pad=12,
    )

    # --- optional gene-model track ---
    grid_axes = [ax_cov]
    if have_genes:
        ax_gene = fig.add_subplot(gs[gene_row], sharex=ax_cov)
        _draw_gene_track(ax_gene, genes, anchor_pos, window_kb, fontsize=FS)
        grid_axes.append(ax_gene)

    # --- 32 × N_BINS heatmap (per-row-normalised sums) ---
    ax = fig.add_subplot(gs[heat_row], sharex=ax_cov)
    cmap = plt.cm.viridis.copy()
    cmap.set_bad(VIRIDIS_FLOOR)
    ax.set_facecolor(VIRIDIS_FLOOR)
    im = ax.imshow(mat, aspect='auto', cmap=cmap, vmin=0, vmax=1.0,
                   extent=[-window_kb, window_kb, n_ct - 0.5, -0.5],
                   interpolation='nearest')

    # TSS line + strand arrow
    ax.axvline(0, color=FIG_HL, lw=0.7, ls=':', alpha=0.8)
    trans = blended_transform_factory(ax.transData, ax.transAxes)
    arrow_len_kb = max(8.0, window_kb * 0.12)
    if anchor_strand in ('+', '-'):
        dx = arrow_len_kb if anchor_strand == '+' else -arrow_len_kb
        ax.annotate('', xy=(dx, 1.015), xytext=(0, 1.015), xycoords=trans,
                    arrowprops=dict(arrowstyle='-|>', color=FIG_HL,
                                    lw=0.9, mutation_scale=8),
                    annotation_clip=False)
    ax.scatter([0], [1.015], transform=trans, marker='|', s=30,
               color=FIG_HL, linewidths=1.2, clip_on=False, zorder=5)
    _draw_scale_bar(ax, window_kb)

    ax.set_yticks(range(n_ct))
    ax.set_yticklabels([c.replace('_', ' ') for c in cell_types], fontsize=FS,
                       color=FIG_FG)
    # Re-enable x ticks (sharex with ax_cov suppressed them) as a genomic ruler.
    _apply_coord_ruler(ax, anchor_pos, window_kb, fontsize=FS, grid_axes=grid_axes)
    ax.set_xlabel(f'{anchor_chrom} (hg38) — dotted line = {anchor_label} anchor', fontsize=FS)
    ax.tick_params(axis='y', length=0)
    for sp in ax.spines.values():
        sp.set_color(FIG_FG)

    # Compact horizontal colorbar inset at top-right of the heatmap.
    cax = ax.inset_axes([0.85, 1.02, 0.14, 0.012])
    cb = fig.colorbar(im, cax=cax, orientation='horizontal')
    cb.set_label('score (norm)', fontsize=FS, labelpad=2, color=FIG_FG)
    cb.set_ticks([0, 0.5, 1.0])
    cb.ax.tick_params(labelsize=FS, length=2, colors=FIG_FG)
    cb.outline.set_edgecolor(FIG_FG)
    return fig
