# steam-zarr — build the STEAM-v1 GPS store

Converts the Shendure lab's hg38-projected STEAM-v1 bigwigs (241 Zoonomia
species × 32 cell classes) into the single Zarr store the Fig 6e widget reads.
One CLI, five commands, resumable, safe to run on a spot instance.

For **what to ask AWS infra for** (instance, IAM policy, cost, time) see
[INFRA.md](INFRA.md). This file is how to install and run it.

## What it produces

```
<out>/                                  e.g. /data
  steam_v1_gps.zarr/
    zarr.json
    meta.json                           species (row order), chrom_offsets, chrom_sizes, bin_bp, cell_types
    hg38/<cell_type>/                   zarr v3 array, uint8 (241, 30 882 687), chunks (241, 2048), gzip
  tree.nwk                              Zoonomia 241-way tree
  gene_tss.json                         {SYMBOL: [chrom, tss, strand]}
  genes/<chrom>.json                    gene models for the gene track
```

Values are `round(GPS × 4)`, 0–254, with `255` = no alignment. GPS is the
paper's genome-wide Phred score, applied per (species, cell class) from
`norm_ref_species.npz`. Rows are in ladderized Zoonomia tree order. ~1.8 GB per
cell class, ~60 GB for all 32.

## Install

Fresh Amazon Linux 2023 / Ubuntu 24.04 box:

```bash
git clone <this repo> && cd 2026-09-interactive/pipeline
./setup.sh                    # system packages, venv, `pip install -e .`, checks the AWS CLI
source .venv/bin/activate
steam-zarr --help
```

Anywhere else with Python ≥ 3.10: `pip install -e pipeline/`. The reference
files it needs (`norm_ref_species.npz`, `zoonomia_241.nwk`,
`gene_models_hg38.json.gz`) are in `steam-fig6e-explorer/data/` in this repo
and are found automatically; point elsewhere with `--ref-dir` or
`$STEAM_REF_DIR`.

Requirements: `numpy`, `pyBigWig`, `zarr ≥ 3`, `biopython`; the `aws` CLI for
`--upload`. Outbound HTTPS to `shendure-web.gs.washington.edu`.

## Run

### 1. Benchmark (5 minutes) — pick the worker count

```bash
steam-zarr bench --workers 48
```

Reads chr21 for 48 species at once and prints the aggregate rate, the
per-connection rate, and the projected hours for the whole job at that worker
count. One connection alone does ~10–15 Mb of genome per second; if the
per-connection median has collapsed well below that, the server is throttling
and you should use fewer workers. Measured from a c6i.4xlarge in us-east-1
(2026-09-18): 16 → 113 Mb/s, 48 → 408 Mb/s, 96 → 780 Mb/s aggregate with
~10 Mb/s per connection throughout, i.e. **≈ 8.5 h for the whole job at
`--workers 96`**.

### 2. Build

```bash
mkdir -p /data
nohup steam-zarr build --all --workers 96 \
      --out /data/steam_v1_gps.zarr \
      --upload s3://cn-scms-datastore/csev-steam-1/data/ \
      > /data/build.log 2>&1 &
```

- Works cell class by cell class, chromosome by chromosome. After each cell
  class completes it `aws s3 sync`s the store to the bucket, so finished cell
  classes are usable by the widget while the rest run.
- **Resumable.** Progress is recorded per chromosome in the array attributes and
  per species in a small on-disk cache; re-running the same command after a
  crash or spot interruption picks up where it stopped. Re-running on a finished
  cell class is a no-op.
- `--cell-types Hepatocytes CNS_neurons` instead of `--all` to do a subset (or
  to split the job across several instances — each instance writes its own
  cell classes; syncs into the same bucket prefix do not conflict because each
  cell class is its own array directory).
- `--chroms chr21 chr22` to build only some chromosomes (testing).
- `--limit-species 6` for a quick smoke test (the other rows stay missing).
- `--workers` is the number of parallel HTTPS connections; the work is
  I/O-bound, so 3–4× the vCPU count is fine. Memory is ~125 MB per worker.
- `--retries 3` (default) retries a failed species read with backoff; species
  that still fail are recorded in the array's `failures` attribute and shown by
  `status`; rerunning the chromosome (delete it from `chroms_done`, or just
  rerun `build` after fixing the cause) fills them in.

Log lines look like:

```
06:10:43   Hepatocytes chr1: 240/241 species   612s
06:10:49 Hepatocytes chr1: wrote 2,489,564 bins, 52% non-missing, 618s · 970 Mb/s · ETA for this cell class 38 min
```

### 3. Sidecars (once)

```bash
steam-zarr sidecars --out /data/steam_v1_gps.zarr --upload s3://cn-scms-datastore/csev-steam-1/data/
```

Writes `tree.nwk`, `gene_tss.json` and `genes/` next to the store and uploads
them. Independent of the build; takes seconds.

### 4. Watch

Run the build inside `tmux` so it survives a dropped SSH connection and you
can reattach to the live log (`nohup … &` alone also survives the drop, but
tmux lets you look):

```bash
tmux new -s steam            # then run the build command; Ctrl-b d to detach
tmux attach -t steam         # from any later ssh session
```

Three ways to see progress, from most to least access required:

```bash
steam-zarr status --out /data/steam_v1_gps.zarr     # on the instance: table per cell class
tail -f /data/build.log                             # on the instance: live log
curl -s https://cn-scms-datastore.s3.us-east-1.amazonaws.com/csev-steam-1/data/status.json   # anywhere, no login
```

With `--upload`, the build writes `status.json` next to the store and copies it
to the bucket **after every chromosome**: cell classes complete, chromosomes
done per cell class, failures, the current cell class and chromosome, the
current rate and the ETA for the cell class in progress. It is public like the
rest of the data, so anyone can check it in a browser.

### 5. Verify, then finish

```bash
steam-zarr verify --out /data/steam_v1_gps.zarr --n 40
```

Re-reads 40 random 2 Mb windows (random cell class, species, chromosome) from
the source and compares them bin-for-bin with the store. Expect
`40/40 checked windows identical`. Then make sure the last upload finished
(`aws s3 ls s3://cn-scms-datastore/csev-steam-1/data/steam_v1_gps.zarr/hg38/`
should list 32 directories), and terminate the instance.

## How it reads a track (and why it is fast)

`pyBigWig.values(chrom, start, end, numpy=True)` over 20 Mb windows, then
`reshape(-1, 100).nanmean(axis=1)` — identical to the `bw.stats(nBins=…)` call
the Streamlit explorer uses (verified bit-for-bit against the chr4 store built
that way), but 8× faster and using 1/50 of the CPU, because it lets numpy do
the binning instead of pyBigWig's per-bin interval scan. That is what turns a
CPU-bound job into a network-bound one and makes a 16-vCPU instance enough.

## Files

| file | role |
|---|---|
| `steam_zarr/core.py` | source URLs, cell classes, store constants, reference loading, the per-track read, the process-pool task |
| `steam_zarr/cli.py` | `bench`, `build`, `sidecars`, `status`, `verify` |
| `setup.sh` | one-shot install on a fresh box |
| `INFRA.md` | instance sizing, IAM policy, cost and time, text for the infra request |

The older `steam-fig6e-explorer/build_zarr.py` did the same job with the slow
read path and a laptop-sized cache; it is superseded by this package.
