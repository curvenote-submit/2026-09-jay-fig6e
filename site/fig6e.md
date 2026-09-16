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
  "data_url": "http://localhost:3002/data",
  "gene": "AFP",
  "chrom": "chr4",
  "pos": 73436220,
  "cell_type": "Hepatocytes",
  "window_kb": 100
}
```

:::{note} Phase 0 preview
Only **chr4 × Hepatocytes** is in the store so far, served from a local dev
server. The `GPS ≥`, `Groups` and `Min cov` controls recompute in the browser
without refetching; changing the gene, cell class or window fetches a new slice
(a few hundred KB).

Interactions: **drag** the heatmap to pan along the chromosome (new chunks
stream in as you go), **drag on the coverage/sum tracks** to zoom to a region,
**ctrl + wheel** (or pinch) over the heatmap to zoom at the cursor, **pinch over
the species column** to make rows taller — the figure keeps its height and you
scroll or drag vertically through the species — **double-click** to reset, **click a
species** in the tree column to highlight it, **click a gene** to recentre on
its TSS, and **click a synteny-group swatch** in the legend to isolate that
group.
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
