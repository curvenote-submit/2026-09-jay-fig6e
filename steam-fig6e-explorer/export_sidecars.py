"""Write the small static files the widget loads next to the zarr store:

  data/tree.nwk          Zoonomia 241-way tree (verbatim)
  data/gene_tss.json     {SYMBOL: [chrom, tss, strand]} — one entry per symbol,
                         longest transcript, same rule as core.local_gene_tss
  data/genes/<chrom>.json  gene models per chromosome for the gene track
"""
import gzip, json, shutil, sys
from collections import defaultdict
from pathlib import Path

here = Path(__file__).resolve().parent
out = Path(sys.argv[1] if len(sys.argv) > 1 else here.parent / 'data')
out.mkdir(parents=True, exist_ok=True)

shutil.copy(here / 'data' / 'zoonomia_241.nwk', out / 'tree.nwk')

with gzip.open(here / 'data' / 'gene_models_hg38.json.gz', 'rt') as fh:
    genes = json.load(fh)

tss, by_chrom = {}, defaultdict(list)
for g in genes:
    sym = g['name'].upper()
    if sym not in tss or (g['end'] - g['start']) > tss[sym][3]:
        tss[sym] = [g['chrom'], g['start'] if g['strand'] >= 0 else g['end'],
                    '+' if g['strand'] >= 0 else '-', g['end'] - g['start']]
    by_chrom[g['chrom']].append({k: g[k] for k in ('name', 'start', 'end', 'strand', 'exons')})
(out / 'gene_tss.json').write_text(json.dumps({k: v[:3] for k, v in sorted(tss.items())}, separators=(',', ':')))
(out / 'genes').mkdir(exist_ok=True)
for c, gs in by_chrom.items():
    (out / 'genes' / f'{c}.json').write_text(json.dumps(gs, separators=(',', ':')))
print(f'{len(tss)} symbols, {len(by_chrom)} chromosomes ->', out)
