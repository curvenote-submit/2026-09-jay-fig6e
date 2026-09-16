"""Measure how a STEAM-v1 hg38-projected track compresses once converted to the
proposed zarr representation (100 bp mean -> GPS -> uint8 at 0.25 steps, 255 = NaN).

Pulls one whole chromosome for a handful of species straight from the Shendure
server via pyBigWig range requests, so it needs the steam-fig6e-explorer venv and
network access:

    cd ../steam-fig6e-explorer && source .venv/bin/activate
    python ../data-analysis/measure_track_size.py [chrom] [cell_type]

Numbers from the 2026-09-15 run are in data-sizing.md.
"""
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import pyBigWig

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'steam-fig6e-explorer'))
import fig6e_core as core  # noqa: E402

CHROM = sys.argv[1] if len(sys.argv) > 1 else 'chr21'
CT = sys.argv[2] if len(sys.argv) > 2 else 'Hepatocytes'
SPECIES = ['Canis_lupus_familiaris', 'Bos_taurus', 'Ornithorhynchus_anatinus',
           'Pteropus_vampyrus', 'Homo_sapiens', 'Pan_troglodytes']


def quantise(gps: np.ndarray) -> np.ndarray:
    """GPS float -> uint8, 0.25 resolution, 255 reserved for missing."""
    return np.where(np.isfinite(gps), np.clip(np.round(gps * 4), 0, 254), 255).astype(np.uint8)


def main() -> None:
    levels, qmap = core.load_species_norm_ref()
    species = [s for s in SPECIES if (s, CT) in qmap]
    tot_raw = tot_gz = 0
    for sp in species:
        t = time.time()
        bw = pyBigWig.open(core.HG38_BW_FMT.format(species=sp, cell_type=CT))
        n_bins = bw.chroms()[CHROM] // 100
        vals = bw.stats(CHROM, 0, n_bins * 100, type='mean', nBins=n_bins)
        bw.close()
        raw = np.array([np.nan if v is None else v for v in vals], float)
        q = quantise(core.values_to_phred(raw, qmap[(sp, CT)], levels))
        gz = len(zlib.compress(q.tobytes(), 6))
        tot_raw += q.nbytes
        tot_gz += gz
        print(f'{CHROM} {sp:28s} nan={np.isnan(raw).mean():.2f} '
              f'raw={q.nbytes / 1e6:5.2f}MB gz={gz / 1e6:5.2f}MB '
              f'ratio={q.nbytes / gz:4.1f}x ({time.time() - t:.0f}s)')
    print(f'mean compression ratio: {tot_raw / tot_gz:.1f}x')


if __name__ == '__main__':
    main()
