"""Convert STEAM-v1 hg38-projected bigwigs into the GPS uint8 zarr store the
widget reads (see ../PLAN.md, "The Zarr layout").

    source .venv/bin/activate
    python build_zarr.py --out ../data/steam_v1_gps.zarr --cell-type Hepatocytes --chroms chr4

One array per (genome, cell class), shape (n_species, n_bins_genome), where the
bin axis is every canonical chromosome laid end to end in ``chrom_offsets``
order. Values are ``round(GPS * 4)`` clipped to 0-254; 255 = no alignment.
Chunks are (all species, 2048 bins), so a locus is a handful of requests.

The array is created at full-genome shape on the first run, and chunks are only
written for the chromosomes requested, so phase 0 (chr4) and phase 2 (the rest)
land in the same store. Re-running a chromosome overwrites it.

Workers are processes, not threads: pyBigWig holds the GIL across network I/O
(see fetchers.py). Nothing heavy is imported at module level for that reason.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

BASE = 'https://shendure-web.gs.washington.edu/content/members/cxqiu/public/nobackup'
HG38_BW_FMT = BASE + '/jax_atac_augmented_241_mammals_hg38/hg38/{species}/{species}.{cell_type}.bw'

CANONICAL = [f'chr{i}' for i in range(1, 23)] + ['chrX', 'chrY']
BIN_BP = 100
CHUNK_BINS = 2048
MISSING = 255


# --- worker (runs in a spawned process; keep imports light) ------------------

def _fetch_track(species: str, cell_type: str, chrom: str, chrom_len: int,
                 qcurve: np.ndarray | None, levels: np.ndarray | None) -> tuple[str, np.ndarray | None]:
    import pyBigWig
    n_bins = chrom_len // BIN_BP
    if qcurve is None:
        return species, None
    try:
        bw = pyBigWig.open(HG38_BW_FMT.format(species=species, cell_type=cell_type))
        if bw is None or chrom not in bw.chroms():
            return species, None
        vals = bw.stats(chrom, 0, n_bins * BIN_BP, type='mean', nBins=n_bins)
        bw.close()
    except Exception:
        return species, None
    raw = np.array([np.nan if v is None else v for v in vals], dtype=float)
    # values_to_phred, inlined so the worker does not import fig6e_core
    pct = np.clip(np.interp(raw, qcurve, levels), 0.0, 1.0 - 1e-6)
    gps = -10.0 * np.log10(1.0 - pct)
    q = np.where(np.isfinite(raw), np.clip(np.round(gps * 4), 0, 254), MISSING)
    return species, q.astype(np.uint8)


# --- main --------------------------------------------------------------------

def hg38_chrom_sizes(species: str, cell_type: str) -> dict[str, int]:
    import pyBigWig
    bw = pyBigWig.open(HG38_BW_FMT.format(species=species, cell_type=cell_type))
    sizes = bw.chroms()
    bw.close()
    return {c: sizes[c] for c in CANONICAL if c in sizes}


def open_or_create(root: 'zarr.Group', name: str, n_species: int, n_bins: int):
    import zarr
    from zarr.codecs import GzipCodec
    if name in root:
        return root[name]
    return root.create_array(
        name, shape=(n_species, n_bins), chunks=(n_species, CHUNK_BINS),
        dtype='uint8', fill_value=MISSING,
        # gzip, not zstd: zarrita decodes gzip with the browser's native
        # DecompressionStream, whereas zstd/blosc pull 1.3 MB of WASM into the widget.
        compressors=[GzipCodec(level=6)],
        config={'write_empty_chunks': False},
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--out', required=True, help='zarr store directory')
    ap.add_argument('--cell-type', default='Hepatocytes')
    ap.add_argument('--chroms', nargs='+', default=['chr4'],
                    help="chromosomes to (re)build, or 'all'")
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--species', nargs='*', default=None,
                    help='subset of species (debugging); default all 241')
    ap.add_argument('--cache-dir', default=None,
                    help='per-species row cache so an interrupted run resumes '
                         '(default: <out>/../.build_cache)')
    args = ap.parse_args()

    import zarr
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fig6e_core as core

    species = core.tree_leaf_order()           # canonical row order = ladderized tree
    if args.species:
        keep = set(args.species)
        species = [s for s in species if s in keep]
    levels, qmap = core.load_species_norm_ref()
    ct = args.cell_type

    sizes = hg38_chrom_sizes('Canis_lupus_familiaris', ct)
    offsets, off = {}, 0
    for c, L in sizes.items():
        offsets[c] = off
        off += L // BIN_BP
    n_bins = off
    chroms = list(sizes) if args.chroms == ['all'] else args.chroms
    bad = [c for c in chroms if c not in sizes]
    if bad:
        ap.error(f'unknown chromosomes: {bad}')

    root = zarr.open_group(args.out, mode='a')
    grp = root.require_group('hg38')
    arr = open_or_create(grp, ct, len(species), n_bins)
    if arr.shape != (len(species), n_bins):
        ap.error(f'existing array shape {arr.shape} != {(len(species), n_bins)}')

    grp.attrs.update({
        'genome': 'hg38', 'bin_bp': BIN_BP, 'missing': MISSING, 'scale': 4,
        'value': 'round(GPS * 4); GPS = genome-wide Phred of predicted accessibility',
        'chrom_sizes': sizes, 'chrom_offsets': offsets, 'species': species,
        'source': BASE + '/jax_atac_augmented_241_mammals_hg38/hg38/',
    })
    no_curve = [s for s in species if qmap.get((s, ct)) is None]
    done_chroms = set(arr.attrs.get('chroms_done', []))
    arr.attrs.update({'cell_type': ct, 'species_without_norm_curve': no_curve})

    print(f'{len(species)} species, {n_bins:,} bins genome-wide, '
          f'{len(no_curve)} without a {ct} norm curve: {no_curve}')

    cache = Path(args.cache_dir or Path(args.out).parent / '.build_cache')
    for chrom in chroms:
        L = sizes[chrom]
        nb = L // BIN_BP
        block = np.full((len(species), nb), MISSING, dtype=np.uint8)
        cdir = cache / ct / chrom
        cdir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        done = 0
        todo = []
        for i, sp in enumerate(species):
            f = cdir / f'{sp}.npy'
            if f.exists():
                block[i] = np.load(f)
                done += 1
            else:
                todo.append((i, sp))
        if done:
            print(f'  {chrom}: {done}/{len(species)} species from cache', flush=True)
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_fetch_track, sp, ct, chrom, L, qmap.get((sp, ct)), levels): i
                    for i, sp in todo}
            for fut in as_completed(futs):
                i = futs[fut]
                sp, row = fut.result()
                if row is not None:
                    block[i] = row
                # cache the row (all-missing rows too, so failures are not retried forever)
                np.save(cdir / f'{sp}.npy', block[i])
                done += 1
                if done % 20 == 0 or done == len(species):
                    print(f'  {chrom}: {done}/{len(species)} species  {time.time() - t0:5.0f}s', flush=True)
        arr[:, offsets[chrom]:offsets[chrom] + nb] = block
        done_chroms.add(chrom)
        arr.attrs['chroms_done'] = sorted(done_chroms, key=list(sizes).index)
        filled = (block != MISSING).mean()
        print(f'{chrom}: wrote {nb:,} bins, {filled:.0%} non-missing, {time.time() - t0:.0f}s')

    # Sidecar for the widget: the same metadata as plain JSON next to the store.
    meta = dict(grp.attrs)
    meta['cell_types'] = sorted(k for k in grp.array_keys())
    (Path(args.out) / 'meta.json').write_text(json.dumps(meta, indent=1))
    print('wrote', Path(args.out) / 'meta.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
