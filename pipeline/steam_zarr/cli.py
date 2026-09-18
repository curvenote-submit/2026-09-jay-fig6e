"""steam-zarr: build the STEAM-v1 GPS Zarr store from the Shendure lab bigwigs.

  steam-zarr bench    --workers 48                 5-minute throughput test + projection
  steam-zarr build    --all --workers 48 --out DIR [--upload s3://bucket/prefix/]
  steam-zarr sidecars --out DIR [--upload ...]     tree, gene index, gene models
  steam-zarr status   --out DIR                    what is done, what remains, ETA
  steam-zarr verify   --out DIR [--n 20]           spot-check stored values against the source
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from . import core

MB_PER_TRACK = 3088          # hg38 canonical chromosomes, Mb
N_TRACKS = 241 * len(core.CELL_TYPES)


def log(msg: str) -> None:
    print(time.strftime('%H:%M:%S'), msg, flush=True)


def fmt_h(hours: float) -> str:
    return f'{hours * 60:.0f} min' if hours < 1 else f'{hours:.1f} h'


# --- store -------------------------------------------------------------------

def open_store(out: Path, mode: str = 'a'):
    import zarr
    root = zarr.open_group(str(out), mode=mode)
    return root, root.require_group('hg38') if mode != 'r' else root['hg38']


def open_or_create_array(grp, cell_type: str, n_species: int, n_bins: int):
    import zarr
    from zarr.codecs import GzipCodec
    if cell_type in grp:
        arr = grp[cell_type]
        if arr.shape != (n_species, n_bins):
            sys.exit(f'{cell_type}: existing array shape {arr.shape} != {(n_species, n_bins)}')
        return arr
    return grp.create_array(
        cell_type, shape=(n_species, n_bins), chunks=(n_species, core.CHUNK_BINS),
        dtype='uint8', fill_value=core.MISSING,
        # gzip, not zstd: the widget decodes gzip natively; zstd needs 1.3 MB of WASM
        compressors=[GzipCodec(level=6)],
        config={'write_empty_chunks': False},
    )


def upload(out: Path, s3_prefix: str, what: str = '') -> None:
    """aws s3 sync the store (or a sub-path) to <prefix>/steam_v1_gps.zarr/."""
    if not shutil.which('aws'):
        log('WARNING: aws CLI not found; skipping upload (run `aws s3 sync` later)')
        return
    dest = s3_prefix.rstrip('/') + '/steam_v1_gps.zarr/'
    log(f'uploading {what or out} -> {dest}')
    r = subprocess.run(['aws', 's3', 'sync', str(out), dest, '--only-show-errors', '--no-progress'])
    log('upload ' + ('done' if r.returncode == 0 else f'FAILED (exit {r.returncode})'))


# --- bench -------------------------------------------------------------------

def cmd_bench(a) -> int:
    """Read one chromosome for --workers species in parallel; report Mb/s and project the whole job."""
    ref = Path(a.ref_dir)
    levels, qmap = core.load_norm_ref(ref)
    species = [s for s in core.tree_leaf_order(ref) if qmap.get((s, a.cell_type)) is not None]
    sizes = core.hg38_chrom_sizes(a.cell_type)
    chrom = a.chrom
    L = sizes[chrom]
    picked = species[: a.workers * a.rounds]
    log(f'bench: {chrom} ({L / 1e6:.0f} Mb) × {len(picked)} species, {a.workers} workers, {a.cell_type}')
    t0 = time.time()
    walls = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(core._task, (sp, a.cell_type, chrom, L, qmap[(sp, a.cell_type)], levels, a.window_mb, 1)) for sp in picked]
        for i, f in enumerate(as_completed(futs), 1):
            sp, row, info = f.result()
            walls.append(info['wall'])
            if 'error' in info:
                log(f'  {sp}: ERROR {info["error"]}')
    elapsed = time.time() - t0
    mb_done = L / 1e6 * len(picked)
    agg = mb_done / elapsed                                   # Mb of genome per second, all workers
    per_conn = (L / 1e6) / float(np.median(walls))            # Mb/s for one connection
    total_mb = MB_PER_TRACK * N_TRACKS
    hours = total_mb / agg / 3600
    log(f'done in {elapsed:.0f}s: aggregate {agg:.1f} Mb/s ({agg * 0.094:.0f} MB/s of transfer), '
        f'median per connection {per_conn:.1f} Mb/s (single-connection baseline ~15 Mb/s)')
    log(f'projection at {a.workers} workers: all {N_TRACKS} tracks in ~{fmt_h(hours)}; '
        f'one cell class (241 tracks) in ~{fmt_h(hours / len(core.CELL_TYPES))}')
    if per_conn < 6:
        log('per-connection rate is well below the single-connection baseline: the server is throttling — try fewer workers')
    elif agg / a.workers > 10:
        log('per-connection rate is holding: you can likely try more workers')
    return 0


# --- build -------------------------------------------------------------------

def build_cell_type(a, cell_type: str, species: list[str], levels, qmap, sizes, offsets, n_bins) -> None:
    out = Path(a.out)
    root, grp = open_store(out)
    arr = open_or_create_array(grp, cell_type, len(species), n_bins)
    grp.attrs.update({
        'genome': 'hg38', 'bin_bp': core.BIN_BP, 'missing': core.MISSING, 'scale': core.SCALE,
        'value': 'round(GPS * 4); GPS = genome-wide Phred of predicted accessibility',
        'chrom_sizes': sizes, 'chrom_offsets': offsets, 'species': species,
        'source': core.BASE + '/jax_atac_augmented_241_mammals_hg38/hg38/',
    })
    no_curve = [s for s in species if qmap.get((s, cell_type)) is None]
    arr.attrs.update({'cell_type': cell_type, 'species_without_norm_curve': no_curve})
    done = set(arr.attrs.get('chroms_done', []))
    chroms = [c for c in (sizes if a.chroms == ['all'] else a.chroms) if c not in done]
    if not chroms:
        log(f'{cell_type}: all chromosomes already done')
        return
    cache = out.parent / '.build_cache' / cell_type
    failures: dict[str, list[str]] = dict(arr.attrs.get('failures', {}))
    t_ct = time.time()
    mb_total = sum(sizes[c] for c in chroms) / 1e6 * len(species)
    mb_done = 0.0

    for chrom in chroms:
        L = sizes[chrom]
        nb = L // core.BIN_BP
        cdir = cache / chrom
        cdir.mkdir(parents=True, exist_ok=True)
        block = np.full((len(species), nb), core.MISSING, dtype=np.uint8)
        todo = []
        for i, sp in enumerate(species):
            f = cdir / f'{sp}.npy'
            if f.exists():
                block[i] = np.load(f)
            elif qmap.get((sp, cell_type)) is not None and (a.limit_species is None or i < a.limit_species):
                todo.append((i, sp))
        t0 = time.time()
        n_done = 0
        errs = []
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(core._task, (sp, cell_type, chrom, L, qmap[(sp, cell_type)], levels, a.window_mb, a.retries)): i
                    for i, sp in todo}
            for fut in as_completed(futs):
                i = futs[fut]
                sp, row, info = fut.result()
                if row is not None:
                    block[i] = row
                    np.save(cdir / f'{sp}.npy', row)
                elif 'error' in info:
                    errs.append(sp)
                n_done += 1
                if n_done % 40 == 0 or n_done == len(todo):
                    log(f'  {cell_type} {chrom}: {n_done}/{len(todo)} species  {time.time() - t0:5.0f}s')
        arr[:, offsets[chrom]: offsets[chrom] + nb] = block
        done.add(chrom)
        arr.attrs['chroms_done'] = sorted(done, key=list(sizes).index)
        if errs:
            failures[chrom] = errs
            arr.attrs['failures'] = failures
        shutil.rmtree(cdir, ignore_errors=True)          # the zarr now holds it
        mb_done += L / 1e6 * len(species)
        rate = mb_done / (time.time() - t_ct)
        eta = (mb_total - mb_done) / rate / 3600 if rate else float('nan')
        log(f'{cell_type} {chrom}: wrote {nb:,} bins, {(block != core.MISSING).mean():.0%} non-missing, '
            f'{time.time() - t0:.0f}s{f", {len(errs)} species failed" if errs else ""} · {rate:.0f} Mb/s · ETA for this cell class {fmt_h(eta)}')
    core.write_meta(out, dict(grp.attrs), grp.array_keys())
    log(f'{cell_type}: complete in {fmt_h((time.time() - t_ct) / 3600)}')


def cmd_build(a) -> int:
    ref = Path(a.ref_dir)
    out = Path(a.out)
    cell_types = core.CELL_TYPES if a.all else a.cell_types
    if not cell_types:
        sys.exit('give --all or --cell-types A B C')
    bad = [c for c in cell_types if c not in core.CELL_TYPES]
    if bad:
        sys.exit(f'unknown cell types: {bad}')
    levels, qmap = core.load_norm_ref(ref)
    species = core.tree_leaf_order(ref)
    sizes = core.hg38_chrom_sizes(cell_types[0])
    offsets, n_bins = core.chrom_offsets(sizes)
    bad = [c for c in a.chroms if c != 'all' and c not in sizes]
    if bad:
        sys.exit(f'unknown chromosomes: {bad}')
    log(f'build: {len(cell_types)} cell class(es), {len(species)} species, {n_bins:,} bins, {a.workers} workers -> {out}')
    t0 = time.time()
    for k, ct in enumerate(cell_types, 1):
        log(f'=== [{k}/{len(cell_types)}] {ct} ===')
        build_cell_type(a, ct, species, levels, qmap, sizes, offsets, n_bins)
        if a.upload:
            upload(out, a.upload, what=ct)
        elapsed = (time.time() - t0) / 3600
        log(f'elapsed {fmt_h(elapsed)} · ETA for the remaining {len(cell_types) - k} cell class(es): {fmt_h(elapsed / k * (len(cell_types) - k))}')
    log('build finished')
    return 0


# --- sidecars ----------------------------------------------------------------

def cmd_sidecars(a) -> int:
    """tree.nwk, gene_tss.json, genes/<chrom>.json next to the store (what the widget loads besides the zarr)."""
    import gzip
    from collections import defaultdict
    ref = Path(a.ref_dir)
    out = Path(a.out).parent if Path(a.out).name.endswith('.zarr') else Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(ref / 'zoonomia_241.nwk', out / 'tree.nwk')
    with gzip.open(ref / 'gene_models_hg38.json.gz', 'rt') as fh:
        genes = json.load(fh)
    tss, by_chrom = {}, defaultdict(list)
    for g in genes:
        sym = g['name'].upper()
        if sym not in tss or (g['end'] - g['start']) > tss[sym][3]:
            tss[sym] = [g['chrom'], g['start'] if g['strand'] >= 0 else g['end'], '+' if g['strand'] >= 0 else '-', g['end'] - g['start']]
        by_chrom[g['chrom']].append({k: g[k] for k in ('name', 'start', 'end', 'strand', 'exons')})
    (out / 'gene_tss.json').write_text(json.dumps({k: v[:3] for k, v in sorted(tss.items())}, separators=(',', ':')))
    (out / 'genes').mkdir(exist_ok=True)
    for c, gs in by_chrom.items():
        (out / 'genes' / f'{c}.json').write_text(json.dumps(gs, separators=(',', ':')))
    log(f'sidecars: {len(tss)} symbols, {len(by_chrom)} chromosomes -> {out}')
    if a.upload and shutil.which('aws'):
        dest = a.upload.rstrip('/') + '/'
        for p in ['tree.nwk', 'gene_tss.json']:
            subprocess.run(['aws', 's3', 'cp', str(out / p), dest + p, '--only-show-errors'])
        subprocess.run(['aws', 's3', 'sync', str(out / 'genes'), dest + 'genes/', '--only-show-errors'])
        log(f'sidecars uploaded to {dest}')
    return 0


# --- status ------------------------------------------------------------------

def cmd_status(a) -> int:
    out = Path(a.out)
    if not (out / 'zarr.json').exists():
        print(f'no store at {out}')
        return 1
    root, grp = open_store(out, 'r')
    sizes = grp.attrs['chrom_sizes']
    total_mb = sum(sizes.values()) / 1e6
    print(f'{out}\n{len(grp.attrs["species"])} species · {len(sizes)} chromosomes · {total_mb:.0f} Mb')
    print(f'{"cell class":40s} {"chroms":>7s} {"Mb done":>8s} {"failures":>9s}')
    done_ct = 0
    for ct in core.CELL_TYPES:
        if ct not in grp:
            print(f'{ct:40s} {"-":>7s} {"-":>8s} {"":>9s}')
            continue
        arr = grp[ct]
        done = arr.attrs.get('chroms_done', [])
        fails = sum(len(v) for v in arr.attrs.get('failures', {}).values())
        mb = sum(sizes[c] for c in done) / 1e6
        mark = ' ✓' if len(done) == len(sizes) else ''
        done_ct += len(done) == len(sizes)
        print(f'{ct:40s} {len(done):>3d}/{len(sizes):<3d} {mb:8.0f} {fails:>9d}{mark}')
    print(f'\n{done_ct}/{len(core.CELL_TYPES)} cell classes complete')
    return 0


# --- verify ------------------------------------------------------------------

def cmd_verify(a) -> int:
    """Re-read n random (cell class, species, 2 Mb window) triples from the source and compare."""
    ref = Path(a.ref_dir)
    levels, qmap = core.load_norm_ref(ref)
    root, grp = open_store(Path(a.out), 'r')
    meta = dict(grp.attrs)
    species = meta['species']
    rng = random.Random(a.seed)
    cts = [c for c in core.CELL_TYPES if c in grp and grp[c].attrs.get('chroms_done')]
    if not cts:
        print('nothing to verify')
        return 1
    bad = checked = 0
    for k in range(a.n):
        ct = rng.choice(cts)
        arr = grp[ct]
        chrom = rng.choice(arr.attrs['chroms_done'])
        sp = rng.choice([s for s in species if qmap.get((s, ct)) is not None])
        L = meta['chrom_sizes'][chrom]
        win = 2_000_000
        start = rng.randrange(0, max(1, L - win)) // core.BIN_BP * core.BIN_BP
        import pyBigWig
        bw = pyBigWig.open(core.HG38_BW_FMT.format(species=sp, cell_type=ct))
        v = bw.values(chrom, start, start + win, numpy=True)
        bw.close()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            fresh = core.quantise(core.values_to_phred(np.nanmean(v.reshape(-1, core.BIN_BP), axis=1).astype(np.float64), qmap[(sp, ct)], levels))
        b0 = meta['chrom_offsets'][chrom] + start // core.BIN_BP
        stored = arr[species.index(sp), b0: b0 + len(fresh)]
        if (stored == core.MISSING).all() and not (fresh == core.MISSING).all():
            print(f'--  {ct:32s} {sp:28s} {chrom}:{start:,}  row not built (all missing) — skipped')
            continue
        diff = int((stored != fresh).sum())
        ok = diff == 0
        bad += not ok
        checked += 1
        print(f'{"ok " if ok else "BAD"} {ct:32s} {sp:28s} {chrom}:{start:,}  {diff} of {len(fresh)} bins differ')
    print(f'\n{checked - bad}/{checked} checked windows identical to a fresh read of the source')
    return 0 if bad == 0 else 1


# --- main --------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog='steam-zarr', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--ref-dir', default=str(core.default_ref_dir()), help='folder with norm_ref_species.npz, zoonomia_241.nwk, gene_models_hg38.json.gz (default: steam-fig6e-explorer/data in this repo, or $STEAM_REF_DIR)')
    sub = p.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('bench', help='throughput test against the Shendure server, with a projection for the full job')
    b.add_argument('--workers', type=int, default=48)
    b.add_argument('--rounds', type=int, default=1, help='species per worker (1 = one task each, ~2-5 min)')
    b.add_argument('--chrom', default='chr21')
    b.add_argument('--cell-type', default='Hepatocytes')
    b.add_argument('--window-mb', type=int, default=core.WINDOW_MB)
    b.set_defaults(fn=cmd_bench)

    c = sub.add_parser('build', help='build the store, cell class by cell class (resumable)')
    c.add_argument('--out', required=True, help='store directory, e.g. /data/steam_v1_gps.zarr')
    g = c.add_mutually_exclusive_group()
    g.add_argument('--all', action='store_true', help='all 32 cell classes')
    g.add_argument('--cell-types', nargs='+', metavar='CT')
    c.add_argument('--chroms', nargs='+', default=['all'])
    c.add_argument('--workers', type=int, default=48)
    c.add_argument('--window-mb', type=int, default=core.WINDOW_MB)
    c.add_argument('--retries', type=int, default=3)
    c.add_argument('--limit-species', type=int, metavar='N', help='smoke test: only the first N species (rows for the rest stay missing)')
    c.add_argument('--upload', metavar='S3_PREFIX', help='after each cell class, `aws s3 sync` to <prefix>/steam_v1_gps.zarr/ (e.g. s3://cn-scms-datastore/csev-steam-1/data/)')
    c.set_defaults(fn=cmd_build)

    s = sub.add_parser('sidecars', help='write tree.nwk, gene_tss.json, genes/ next to the store')
    s.add_argument('--out', required=True, help='the store directory or its parent')
    s.add_argument('--upload', metavar='S3_PREFIX')
    s.set_defaults(fn=cmd_sidecars)

    t = sub.add_parser('status', help='chromosomes done per cell class')
    t.add_argument('--out', required=True)
    t.set_defaults(fn=cmd_status)

    v = sub.add_parser('verify', help='spot-check stored bins against a fresh read of the source')
    v.add_argument('--out', required=True)
    v.add_argument('--n', type=int, default=20)
    v.add_argument('--seed', type=int, default=0)
    v.set_defaults(fn=cmd_verify)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
