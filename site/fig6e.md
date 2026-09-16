---
title: "Figure 6e: cross-species enhancer view"
---

Pick a gene and a cell class. The widget streams STEAM-v1 predicted-accessibility
tracks for 241 Zoonomia mammals from a Zarr store, orders them by the Zoonomia
phylogeny, calls enhancers at the paper's GPS threshold (24.5), groups them by
synteny, and draws the three-panel figure: **tree | called enhancers coloured by
synteny group | accessibility heatmap**.

```{anywidget} ../widgets/fig6e/dist/index.js
:css: ../widgets/fig6e/dist/styles.css
{
  "data_url": "https://cn-scms-datastore.s3.us-east-1.amazonaws.com/csev-steam-1/data",
  "gene": "AFP",
  "chrom": "chr4",
  "pos": 73436220,
  "cell_type": "Hepatocytes",
  "window_kb": 100
}
```

:::{note} Phase 0 preview
Only **chr4 × Hepatocytes** is in the store so far. The store is the
`data_url` in the directive above (an S3 bucket; a local dev server such as
`http://localhost:3002/data` works too — any CORS-enabled URL with the same
layout). The `GPS ≥`, `Groups` and `Min cov` controls recompute in the browser
without refetching; changing the gene, cell class or window fetches a new slice
(a few hundred KB).

Interactions: **drag or scroll sideways** on the heatmap to pan along the
chromosome (new chunks stream in as you go), **drag on the coverage/sum tracks**
or **pinch the heatmap** to zoom, **pinch the species column** to make rows
taller and scroll through the species, **hover** a species, gene, enhancer or
synteny group for details (species and gene cards come from Wikipedia and
MyGene.info), **click a species** to highlight it, **click a gene** to recentre
on its TSS, **click a synteny group** (in the enhancer panel or the Synteny
Groups track) to isolate it, and use the buttons in the controls bar to open
the window in UCSC, download the calls or group summary as TSV, or reset.
:::

## How this compares to the published panel

This view is drawn in **hg38 coordinates** — every species projected onto one
shared human axis — whereas the published Fig 6e works in each species' own
coordinates. Positions line up directly across rows here, which the published
panel does not give you, but synteny-group numbering will not match the paper's
clusters 1–11.

### Why the enhancer count differs from the Streamlit explorer

At the default locus (*AFP*, Hepatocytes, ±100 kb, GPS ≥ 24.5) this widget
reports **1 490** enhancers across 185 species; the Streamlit explorer reports
**1 541**. Same predictions, same species, same calling code — the difference
is where the 100 bp bins start:

| Binning | Species | Enhancers |
|---|---|---|
| Bins anchored at the TSS (the explorer: `start = TSS − 100 000`) | 185 | 1 541 |
| Bins on the fixed 100 bp genome grid (this store) | 187 | 1 478 |
| … plus the store's 0.25-GPS quantisation | 187 | 1 491 |
| This widget's window, read from the store | 185 | 1 490 |

A 20 bp shift in bin edges changes which bins clear the threshold at peak
edges and can split or merge adjacent runs (about −4 % here); storing GPS in
0.25 steps lets a few bins at 24.4 round up past 24.5 (under +1 %). Neither
grid is more correct. The fixed grid has the advantage that any two loci, or
the same locus in two cell classes, are binned identically and so are directly
comparable — which TSS-relative binning cannot offer.
