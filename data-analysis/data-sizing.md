# STEAM-v1 data sizing

Measured 2026-09-15 against `shendure-web.gs.washington.edu`, for the plan in
[../PLAN.md](../PLAN.md) to re-host the hg38-projected tracks as Zarr. Script:
[measure_track_size.py](measure_track_size.py).

## Source: what exists today

hg38-projected bigwigs at
`.../jax_atac_augmented_241_mammals_hg38/hg38/{species}/{species}.{cell_type}.bw`.

| | Size |
|---|---|
| One bigwig (species × cell class) | 415–450 MB (two checked: dog/Hepatocytes 450 MB, mouse/CNS_neurons 415 MB) |
| One cell class, all 241 species | ~105 GB |
| **All 241 species × 32 cell classes = 7 712 files** | **~3.4 TB** |

Server sends `Accept-Ranges: bytes` / `206` but **no `Access-Control-Allow-Origin`**,
so browsers cannot read these directly.

## Target: 100 bp GPS `uint8` Zarr

Representation: `bw.stats(type='mean', nBins=len//100)` per chromosome → genome-wide
Phred (GPS) via `norm_ref_species.npz` → `round(GPS * 4)` clipped to 0–254, with
255 = no alignment (NaN). hg38 ≈ 3.1 Gb → ~31 M bins per track.

### Measured compression (chr21, Hepatocytes, gzip level 6)

| Species | NaN fraction | raw | gz | ratio |
|---|---|---|---|---|
| Canis_lupus_familiaris | 0.60 | 0.47 MB | 0.11 MB | 4.1× |
| Bos_taurus | 0.63 | 0.47 MB | 0.11 MB | 4.1× |
| Pteropus_vampyrus | 0.66 | 0.47 MB | 0.10 MB | 4.9× |
| Pan_troglodytes | 0.31 | 0.47 MB | 0.13 MB | 3.6× |
| **mean** | | | | **4.1×** |

Sanity check on a gene-dense locus (cached AFP ±225 kb, 241 species × 4500 bins):
1.08 MB raw → 337 KB gz = 3.2×. Whole-genome is a bit better because intergenic
NaN runs dominate. zstd should improve on gzip modestly; not measured.

`Homo_sapiens` and `Ornithorhynchus_anatinus` have no Hepatocytes norm curve in
`norm_ref_species.npz` (239 of 241 species do) and were skipped.

### Resulting sizes (at 4×)

| | Uncompressed | Compressed |
|---|---|---|
| One track (one species, one cell class, whole genome) | 31 MB | ~7.5 MB |
| **One cell class, 241 species** (Hepatocytes = paper's Fig 6e) | 7.5 GB | **~1.8 GB** |
| All 32 cell classes | 239 GB | **~60 GB** |

Hosting on Cloudflare R2 (no egress fees): ~$0.03/month for one cell class,
~$0.90/month for all 32.

## Per-view transfer in the browser

Chunking `(241 species, 2048 bins)` = all species × 205 kb of genome per chunk.

| | Requests | Transfer |
|---|---|---|
| One chunk | 1 | ~500 KB raw → ~120 KB compressed |
| ±100 kb window (paper default) | 1–2 | ~150–250 KB |
| ±250 kb | 3 | ~350 KB |
| ±500 kb (slider max) | 5–6 | ~700 KB |
| One-time per page: species list + tree | 2 | 4 KB + 11 KB |
| Gene index (`gene_models_hg38.json.gz`, lazy on first search) | 1 | 2.3 MB |

For comparison, the Streamlit app moves a similar number of bytes per locus but
across ~700–1 000 range requests to 239 files (~7 s); here it is 1–6 requests.

## Conversion cost

- One whole-chromosome `bw.stats` call over the public web from Vancouver: ~18 s
  for chr21 → roughly 5 min per track for all of hg38, ~20 h per cell class from
  a laptop. Bigwig zoom levels make the stats calls cheap; the time is network.
- Next to the data (local disk, modest process pool): minutes per track, an hour
  or two per cell class, a weekend for all 32.
- Do Hepatocytes first; the widget can be built end-to-end against that one
  cell class.
