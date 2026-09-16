# fig6e — STEAM-v1 cross-species enhancer view (anywidget)

Interactive, kernel-free version of Fig 6e from Qiu, Daza, Welsh *et al.*: pick a
gene and a cell class; the widget slices a Zarr store of STEAM-v1 predicted
accessibility (genome-wide Phred, "GPS") for 241 Zoonomia mammals, orders the
species by the Zoonomia phylogeny, calls enhancers at a GPS threshold, groups them
by synteny, and draws **tree | called enhancers by synteny group | heatmap** with
coverage and summed-strength tracks.

Everything downstream of the fetch runs in the browser: the threshold, group-count
and coverage sliders recompute in ~25–50 ms without touching the network.

Interactions: drag the heatmap to **pan** (chunks stream in through an LRU),
drag on the coverage/sum tracks to **zoom** to a region, pinch / ctrl+wheel
over the heatmap to zoom at the cursor, ± / ⟲ buttons, double-click to reset,
**pinch over the species column** to change the row height (the figure keeps
its height; scroll or drag vertically to move through the rows), **click a species** to toggle
its highlight, **click a gene** to recentre on its TSS, **click a legend swatch**
to isolate a synteny group. Hover anything for details.

## Hover cards (third-party lookups)

Two hovers call public APIs at view time; nothing else leaves the page:

- **Species** (tree/label column): Wikipedia's REST summary endpoint
  (`en.wikipedia.org/api/rest_v1/page/summary/<Latin_name>`) — common name,
  one-line description, first paragraph, thumbnail. Wikipedia's own redirects
  map the Zoonomia binomials to the common-name articles.
- **Genes** (gene track): MyGene.info (`mygene.info/v3/query?q=symbol:X&species=human`)
  — HGNC name, gene type, cytoband, aliases, NCBI/Ensembl/HGNC IDs and the
  RefSeq summary paragraph where one exists.

Both are cached per name for the widget's lifetime (`wiki.ts`, `geneinfo.ts`);
network failures are not cached, so a later hover retries.

## Model

| key | type | default | direction |
|---|---|---|---|
| `data_url` | string | `"./data"` | in — base URL holding `steam_v1_gps.zarr/`, `tree.nwk`, `gene_tss.json`, `genes/` |
| `gene` | string | `"AFP"` | both — resolved to `chrom`/`pos` via `gene_tss.json` |
| `chrom`, `pos` | string, int | `"chr4"`, `73436220` | both — hg38 anchor (TSS) |
| `cell_type` | string | `"Hepatocytes"` | both — must be an array in the store |
| `window_kb` | number | `100` | both — one of 25/50/100/250/500 |
| `threshold` | number | `24.5` | both — GPS enhancer-calling cutoff |
| `n_major` | int | `11` | both — synteny groups to colour |
| `min_cov` | number | `0.5` | both — drop species with less hg38 coverage |
| `highlight` | string[] | dog, cat | both — species drawn in red; tree-tip clicks toggle |
| `selected_group` | int \| null | `null` | both — isolated synteny group (legend click) |
| `show_gaps` | bool | `false` | both — unaligned bins white instead of the viridis floor (the paper's encoding) |
| `row_px` | number \| null | `null` | both — row height in px; `null` fits every species into the figure. Pinching the species column sets it. |
| `start`, `end` | int | anchor ± window | both — the current view in bp; pan/zoom write it, a host may set it |
| `calls` | object[] | — | **out** — current enhancer table (species, start, end, gps_max, gps_mean, dist_to_tss, group) |

All values are JSON. `save_changes()` is wrapped in try/catch because MyST's
static host does not implement it.

## Data layout

```
<data_url>/
  steam_v1_gps.zarr/
    meta.json                 # species (row order), chrom_offsets, chrom_sizes, bin_bp, cell_types
    hg38/<cell_type>          # zarr v3, uint8 (241, 30 882 687), chunks (241, 2048), gzip
  tree.nwk                    # Zoonomia 241-way tree
  gene_tss.json               # {SYMBOL: [chrom, tss, strand]}
  genes/<chrom>.json          # gene models for the gene track
```

Values are `round(GPS × 4)`, 0–254; `255` = no alignment. Built by
`../../steam-fig6e-explorer/build_zarr.py` and `export_sidecars.py`.

## Stack

TypeScript throughout, no framework: native `<select>` / `<input type=range>`
controls, and an imperative canvas + SVG figure (`draw.ts`) driven by a small
`Controller` class (`controller.ts`) that owns the view, streaming, compute and
mouse interactions. `index.ts` is the thin control shell that binds model keys
to the controls and the controller. Runtime deps are just `zarrita` and
`d3-scale-chromatic`; the bundle is ~72 KB minified.

## Development

```bash
bun install            # deps
bun run build          # esbuild (TS -> dist/index.js) + copies styles.css
bun run typecheck      # tsc --noEmit
bun run dev            # http://localhost:3000 — dev/index.html with a mock model;
                       #   serves ../../data at /data with CORS + range headers
bun run test           # JS enhancer calls / synteny groups == Python golden files
```

Node 24 runs the `.ts` sources directly, so the tests import them without a build.

`DATA_DIR=/elsewhere bun run dev` points the dev server at another data folder.
The golden files in `test/` come from `../../steam-fig6e-explorer/export_golden.py`.

## Using it

**MyST** (no Python, no kernel) — see `../../site/fig6e.md`:

````markdown
```{anywidget} ../widgets/fig6e/dist/index.js
:css: ../widgets/fig6e/dist/styles.css
{ "data_url": "https://<bucket>/steam", "gene": "AFP", "cell_type": "Hepatocytes" }
```
````

**JupyterLab** (consumer-side Python, not part of this package):

```python
import anywidget, traitlets, pathlib

class Fig6e(anywidget.AnyWidget):
    _esm = pathlib.Path("widgets/fig6e/dist/index.js")
    _css = pathlib.Path("widgets/fig6e/dist/styles.css")
    data_url = traitlets.Unicode("https://<bucket>/steam").tag(sync=True)
    gene = traitlets.Unicode("AFP").tag(sync=True)
    chrom = traitlets.Unicode("chr4").tag(sync=True)
    pos = traitlets.Int(73436220).tag(sync=True)
    cell_type = traitlets.Unicode("Hepatocytes").tag(sync=True)
    window_kb = traitlets.Float(100).tag(sync=True)
    threshold = traitlets.Float(24.5).tag(sync=True)
    n_major = traitlets.Int(11).tag(sync=True)
    min_cov = traitlets.Float(0.5).tag(sync=True)
    highlight = traitlets.List(traitlets.Unicode()).tag(sync=True)
    calls = traitlets.List().tag(sync=True)

w = Fig6e(); w            # then: pd.DataFrame(w.calls)
```

## Source map

| file | role |
|---|---|
| `src/index.ts` | AFM entry: `render({model, el})` — controls, status tiles, model ↔ controls, owns the `Controller` |
| `src/controller.ts` | view (window + row zoom), fetch, compute, draw, mouse interactions |
| `src/data.ts` | zarrita `FetchStore` + chunk LRU + sidecars; `range(cellType, chrom, start, end)` |
| `src/compute.ts` | ports of `synteny.py`: `callEnhancers`, `assignSyntenyGroups`, coverage, tracks |
| `src/tree.ts` | Newick parse / prune / ladderize / layout (matches Biopython's order) |
| `src/draw.ts` | canvas heatmap (d3 viridis, vmax 30) + SVG tree, calls, tracks, genes, legend, colourbar, hover |
| `src/types.ts` | shared types (`Slice`, `Call`, `Params`, `Status`, `AnyModel`, …) |
| `src/styles.css` | all widget CSS, scoped to `.f6e` |
