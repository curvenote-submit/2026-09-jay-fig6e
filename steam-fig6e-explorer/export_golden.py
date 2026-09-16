"""Golden file for the JS parity test: run the Python enhancer caller + synteny
grouping on GPS values read back from a zarr store (so both sides see the same
quantised numbers) and dump the result as JSON.

    python export_golden.py <store> <cell_type> <chrom> <pos> <window_kb> <out.json>
"""
import json, sys
import numpy as np, zarr
sys.path.insert(0, '.')
import synteny as syn

store, ct, chrom, pos, win, out = sys.argv[1:7]
pos, win = int(pos), float(win)
root = zarr.open_group(store, mode='r')
g = root['hg38']; a = g[ct]
meta = dict(g.attrs); bin_bp = meta['bin_bp']
off = meta['chrom_offsets'][chrom]; nchrom = meta['chrom_sizes'][chrom] // bin_bp
w = int(round(win * 1000 / bin_bp)); c = pos // bin_bp
b0, b1 = max(0, c - w), min(nchrom, c + w)
q = a[:, off + b0: off + b1]
gps = np.where(q == 255, np.nan, q / 4.0)
signals = {sp: gps[i] for i, sp in enumerate(meta['species'])}
# window_kb/anchor as the JS sees them: start_bp = b0*bin_bp, so pass anchor such
# that anchor - win*1000 == start_bp
calls = syn.call_enhancers(signals, bin_bp=bin_bp, anchor_pos=b0 * bin_bp + int(win * 1000),
                           window_kb=win, threshold=syn.GPS_THRESHOLD)
calls['dist_to_tss'] = (calls['start'] + calls['end']) // 2 - pos
calls, summary = syn.assign_synteny_groups(calls, n_major=syn.N_MAJOR_GROUPS)
json.dump({'params': dict(cell_type=ct, chrom=chrom, pos=pos, window_kb=win, threshold=syn.GPS_THRESHOLD,
                          n_major=syn.N_MAJOR_GROUPS, start_bp=b0 * bin_bp, end_bp=b1 * bin_bp),
           'calls': calls.to_dict(orient='records'),
           'summary': summary.to_dict(orient='records')}, open(out, 'w'), indent=1)
print(f'{len(calls)} calls, {len(summary)} major groups ->', out)
