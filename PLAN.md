# Plan: Fig 6e explorer → interactive anywidget in a MyST site

Goal: take `steam-fig6e-explorer/` (Streamlit + Python backend) and turn it into a
self-contained, interactive anywidget that renders on a static MyST site with no
kernel — any gene, any cell class — so the figure can sit inline next to the paper.

## What the Streamlit app does today

```
gene symbol ──► hg38 TSS ──► fetch 239 remote bigwigs (pyBigWig, range requests)
                                     │
                              raw → genome-wide Phred (GPS) via norm_ref_species.npz
                                     │
              ┌──────────────────────┼─────────────────────┐
     coverage filter        call enhancers (GPS ≥ 24.5)    tree prune + ladderize
     (min_cov slider)       synteny groups (merge overlaps,  (Biopython, zoonomia_241.nwk)
                            rank, top-11 coloured)
              └──────────────────────┼─────────────────────┘
                                     ▼
                     matplotlib: tree | calls | heatmap (+ cov / sum / gene tracks)
                     PNG / PDF / SVG / TSV downloads
```

Everything between the fetch and the plot is small, pure array logic that ports to
JS in an afternoon. The two things that don't port are the **bigwig fetch** and
**matplotlib** — and both are replaced, not ported.

## Data: re-host as Zarr, pre-normalised to GPS

### Why the current source doesn't work from a browser

`shendure-web.gs.washington.edu` supports range requests (`206`, `Accept-Ranges`)
but sends **no `Access-Control-Allow-Origin`**, so a browser can't read the bigwigs
at all. Even with CORS, a locus fetch is 239 bigwigs × (header + R-tree index + data
blocks) ≈ 700–1000 HTTP round trips, and the client would also need the 25 MB
`norm_ref_species.npz` to turn raw values into GPS.

### The Zarr layout

One array per cell class (or one array with a cell-class axis — same thing on disk):

```
steam_v1_gps.zarr/
  hg38/
    Hepatocytes/            # uint8 GPS, shape (241 species, 30_956_000 bins @ 100 bp)
    CNS_neurons/            #   value = round(GPS * 4), 255 = no alignment (NaN)
    ...
  species.json              # row order (Zoonomia tree leaf order)
  chrom_offsets.json        # chrom -> first bin index
  tree.nwk, genes.json.gz   # or served alongside
```

Design choices and why:

| Choice | Reason |
|---|---|
| **Pre-apply the GPS normalisation** | Client never needs `norm_ref_species.npz`. Enhancer calling is only meaningful on this scale anyway. |
| **`uint8` at 0.25 GPS resolution, 255 = missing** | 8× smaller than float64; GPS tops out ~60 in practice; the heatmap colour map can't resolve finer than 0.25. |
| **100 bp bins** (what the app already draws) | Genome = 31 M bins → 31 MB/track uncompressed, ~239 GB for all 7 712 tracks before compression. 45 % of cells are NaN runs, signal is sparse → measured **4.1× compression** on chr21 → **~60 GB total, ~1.8 GB per cell class.** |
| **Chunk `(241, 2048)`** — all species, 205 kb of genome | A ±250 kb window = 3 chunks ≈ 3 requests, ~150 KB each compressed. **One locus loads in ~1 s** instead of ~7 s / ~1 000 requests. |
| **Zarr v3 + sharding** (e.g. 64 chunks per shard) | Keeps file count sane on object storage (15 k chunks per track → ~240 shards). |
| **Codec: zstd, gzip fallback** | `zarrita` 0.7.5 (npm) reads both; gzip is native in the browser via `DecompressionStream`. Confirm zstd works in spike #2. |
| **Host on Cloudflare R2 or S3** with `Access-Control-Allow-Origin: *` | R2 has no egress fees; 60 GB ≈ $0.90/month. Any bucket with CORS works. |

Reading in the widget is then `zarrita`'s `get(arr, [null, slice(start_bin, end_bin)])`
against a `FetchStore` — ~10 lines, and it works identically in JupyterLab.

### Conversion job (one-off, Python)

- Per track: `pyBigWig.stats(chrom, 0, len, type='mean', nBins=len//100)` per chrom
  (uses bigwig zoom levels, so this is fast) → `values_to_phred` with the existing
  norm curve → quantise → write.
- The one bigwig I checked is 415–450 MB; ×7 712 tracks ≈ 3.5 TB of reads. **Run this
  where the data lives** (Shendure lab / UW cluster), or get them to hand over the
  files. Don't pull 3.5 TB over the public web.
- Do **Hepatocytes first** (the paper's Fig 6e cell class) — ~1.8 GB, an hour or two near the data —
  and build the whole widget against it. Other cell classes are a loop.
- Do we also want the *native-coordinate* tracks (the paper's actual Fig 6e
  approach)? Different data, different chunking (per species genome). Not in scope
  unless someone asks; the hg38-projected view is what the app draws today.

Full measurements (source sizes, compression ratio, per-view transfer, conversion
time) are in [data-analysis/data-sizing.md](data-analysis/data-sizing.md).

### Data budget for the static site

Nothing heavy ships with the site itself — just `species.json` (4 KB),
`tree.nwk` (11 KB), and the gene index. Gene symbol → TSS lookup: ship
`gene_models_hg38.json.gz` (2.3 MB, lazy-loaded on first keystroke) or hit Ensembl
REST, which *does* send CORS headers. Prefer the local file — no third-party
dependency at read time.

## The widget: interactive, not a static picture

Replacing matplotlib with canvas + SVG is what makes interaction possible. The
figure keeps the paper's three-panel layout; these interactions come on top:

| Interaction | Where |
|---|---|
| **Hover** a heatmap cell → species, hg38 position, GPS value | heatmap |
| **Hover** an enhancer call → group id, coords, peak/mean GPS, distance to TSS | call panel |
| **Hover** a tree tip → highlight that row across all three panels | tree |
| **Click** a tree tip → pin/unpin as highlighted species (replaces the text box) | tree |
| **Click** a legend swatch → isolate that synteny group; click again to clear | legend |
| **Drag (brush) on the coverage/sum tracks** → zoom the x-range; double-click to reset | tracks |
| **Shift-drag / wheel** → pan the window along the chromosome, streaming new chunks | heatmap |
| **Click a gene** in the gene track → re-centre on its TSS | gene track |
| **Sliders** (threshold, groups, coverage) recompute in-place, no refetch | controls |
| **Gene search** with prefix matching, cell-class dropdown, window select | controls |

Rendering stack:

- Heatmap → `<canvas>` (viridis LUT over the `uint8` slice). Cheap enough to redraw on
  every slider tick.
- Tree, calls, tracks, gene models, legend, axes → one `<svg>` sharing the x-scale.
- **D3 modules via a bundle** (`d3-scale`, `d3-axis`, `d3-zoom`, `d3-brush`,
  `d3-selection`) — brush/zoom done right is worth the ~40 KB. This means the widget
  is the *bundled* variant from the anywidget skill (esbuild, `dist/index.js`), not
  the zero-dep one. `zarrita` is the other bundled dependency.
- Exports: SVG (serialise; embed the canvas as a data-URL `<image>`), PNG (render SVG
  to an offscreen canvas at 2×), TSV of calls, UCSC link. **PDF dropped** — SVG
  covers print.
- Status tiles across the top (species kept, enhancers, % in major groups,
  per-species) as in the Streamlit app; a collapsible "how this compares to the
  published panel" note.

**Model keys** (JSON-serialisable; the widget drives itself in static mode and can be
driven from Python in JupyterLab):

| key | type | default | notes |
|---|---|---|---|
| `zarr_url` | string | R2/S3 URL | base of the store |
| `gene` | string \| null | `"AFP"` | resolved to `chrom`/`pos` client-side |
| `chrom`, `pos` | string, int | AFP TSS | coordinate mode |
| `cell_type` | string | `"Hepatocytes"` | |
| `window_kb` | number | 100 | 25–500 |
| `threshold` | number | 24.5 | |
| `n_major` | int | 11 | |
| `min_cov` | number | 0.5 | |
| `highlight` | string[] | dog, cat | synced from tree clicks |
| `selected_group` | int \| null | null | synced from legend clicks |
| `calls` | object[] | `[]` | **widget → Python**: current enhancer table, so a notebook can `pd.DataFrame(w.calls)` |

## Repo layout

```
2026-09-interactive/
├── steam-fig6e-explorer/       # existing app — keep as reference + parity oracle
│   └── build_zarr.py           # NEW: bigwig → GPS uint8 zarr (phase 0: one chrom from laptop)
├── widgets/fig6e/              # anywidget AFM (bundled variant)
│   ├── src/{index.js, data.js, compute.js, tree.js, draw.js, styles.css}
│   ├── test/compute.test.js    # golden-file parity vs Python
│   ├── dev/{index.html, serve.js}
│   ├── package.json            # esbuild; deps: zarrita, d3-*
│   └── README.md
├── site/                       # MyST project
│   ├── myst.yml
│   ├── index.md
│   ├── fig6e.ipynb             # one cell: Fig6eWidget(gene="AFP")
│   ├── fig6e_widget.py         # AnyWidget subclass, _esm -> ../widgets/fig6e/dist/index.js
│   └── public/{species.json, tree.nwk, gene_models_hg38.json.gz}
└── PLAN.md
```

## Phases — vertical slice first, then widen one axis at a time

Organised so that the whole stack works end-to-end on a tiny dataset before any
dimension is scaled, and so that nothing waits on the Shendure lab until phase 2.

### Phase 0 — vertical slice on one chromosome ✅ done 2026-09-15

| Layer | Delivered |
|---|---|
| **Data** | `data/steam_v1_gps.zarr`: chr4 × Hepatocytes × 241 species, **121 MB**, zarr v3, chunks `(241, 2048)`, **gzip** (zarrita decodes it natively; zstd/blosc would drag 1.3 MB of WASM into the widget). Built from the remote bigwigs in 46 min with 16 workers; `build_zarr.py` is resumable (per-species row cache). Values match the Streamlit app's own normalised fetch to the 0.125 quantisation half-step. |
| **Sidecars** | `tree.nwk`, `gene_tss.json` (28 278 symbols), `genes/<chrom>.json` via `export_sidecars.py`. |
| **Widget** | `widgets/fig6e/` — 52 KB bundle. Gene box, cell class, window, GPS threshold, group count, min coverage; canvas heatmap + SVG tree/calls/tracks/axis; hover tooltips; status tiles; legend. Slider → recompute + redraw in 24–54 ms. |
| **Parity** | `bun run test`: JS calls + synteny groups == Python (`synteny.py`) for AFP ±100 kb (1 829 calls) and ±500 kb (2 487 calls), every field. |
| **MyST** | `site/` renders the widget through the native **`{anywidget}` directive** — no notebook, no kernel, no Python. |

What the spikes taught us (details in `spikes/myst-anywidget/README.md`):

- MyST mounts the widget in a **shadow root**, injects the `:css:` `<link>` *inside `el`* before `render()`, and **throws on `save_changes()`** — so append to `el`, never replace, and wrap `save_changes` in try/catch.
- The store is pre-binned on fixed 100 bp genome tiles; the Streamlit app bins relative to the TSS. Counts therefore differ slightly (AFP: 1 490 vs 1 541 enhancers; bin phase −63, quantisation +13 — see site/fig6e.md). Any two loci are directly comparable in the store, which the app never offered.
- Human and mouse have no hg38-projected norm curve, so their rows are all-missing; the widget drops them via the coverage filter.

### Phase 1 — real interactivity, still chr4 (≈1–2 weeks) ← next

- [x] Port the rest of the compute (synteny groups, coverage filter, ladderized
      tree) with a golden-file parity test — done in phase 0.
- [x] All sliders, status tiles, hover tooltips — done in phase 0.
- [x] Widget ported to **TypeScript**; figure logic split into a `Controller` class behind
      a thin vanilla control shell. (A React + shadcn/Radix + Tailwind shell was tried and
      dropped: 430 KB vs 72 KB, plus shadow-DOM portal workarounds, for no functional gain.)
- [x] Gene track from `genes/<chrom>.json` — exons, introns, strand chevrons; click recentres on the TSS.
- [x] Tree-tip click → highlight; legend click → isolate a synteny group (others dimmed, span banded).
- [x] Brush-to-zoom on the tracks, ctrl+wheel zoom, ± / reset buttons; drag-to-pan with
      chunk streaming through a 48-chunk LRU in `data.js`. The view (`start`/`end`) is the
      analysis window and is synced to the model. Span clamped to 2 kb – 4 Mb until the
      phase-3 pyramid.
- [ ] SVG / PNG / TSV export, UCSC link.
- [ ] Store to R2/S3 with `Access-Control-Allow-Origin: *` (**needs a bucket**);
      until then the widget dev server (`bun run dev`) serves `data/` with CORS.
- This is the version to show Jay.

### Phase 2 — whole genome, one cell class (≈1 day near the data, or ~14 h/cell class from a laptop)

- Same `build_zarr.py`, all chromosomes, run where the bigwigs live. ~1.8 GB.
- Add sharding (64 chunks/shard) now that there are ~15 k chunks per track.
- Widget unchanged except the chromosome list stops being a stub.
- Deploy the MyST site to GitHub Pages.

### Phase 3 — scale the axes the widget will be asked to scale on

| Dimension | What changes |
|---|---|
| **Genomic extent** — zoom out past ±500 kb | Resolution pyramid: 100 bp / 1 kb / 10 kb arrays (like bigwig zoom levels). Widget picks the level by pixels-per-bin. +11 % storage. |
| **Cell classes** — 1 → 32 | Loop the build job (~60 GB). One array per cell class, so adding one never rewrites the others. Widget dropdown reads the store's group listing. |
| **Rows** — 241 species, fixed | Nothing on disk. Tree-collapse-by-clade (click an internal node → aggregate its tips) keeps the panel readable on small screens. |
| **Loci per page** | Widget is stateless per instance; add a shared chunk cache across instances on one page. |
| **Users** | Static site + object storage: nothing to do. |
| **Datasets** — mouse native, future model versions | Namespace the store (`hg38/`, `mm10/`, `steam_v2/`); `genome` key on the model. |

### Phase 4 — hardening

Golden test in CI, `build_zarr.py` as a resumable batch job with a manifest,
Cache-Control/ETag on the bucket, `myst build` check in CI, READMEs.

## Decisions still open

1. **Bucket.** R2 or S3, and whose account. Needed by end of phase 1.
2. **Who runs phase 2's conversion** next to the bigwigs (Shendure lab / UW).
3. Cell-class order after Hepatocytes.
4. Native-coordinate mode (the paper's real Fig 6e approach) — out of scope unless wanted.
