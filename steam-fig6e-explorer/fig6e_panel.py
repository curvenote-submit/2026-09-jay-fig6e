"""The Fig 6e three-panel renderer: tree | synteny-grouped enhancer calls | heatmap.

Panel order and content follow the published legend:

    Left:   phylogenetic tree for the retained species
    Middle: called enhancers, coloured by synteny group (as in Fig 6d)
    Right:  predicted chromatin accessibility

with the coverage / summed-strength tracks and an optional gene model track above
the heatmap. Aesthetics, tree drawing and the coordinate ruler are reused from
``fig6e_core`` so both views in the app render identically.
"""
from __future__ import annotations

from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.transforms import blended_transform_factory

import fig6e_core as core
import synteny as syn

FS = core.FS


def _draw_call_panel(ax, calls: pd.DataFrame, species_order: list[str],
                     window_kb: float, anchor_pos: int, colors: dict[int, str]):
    """Middle panel: one horizontal bar per called enhancer, coloured by synteny group."""
    yidx = {s: i for i, s in enumerate(species_order)}
    n = len(species_order)
    ax.set_facecolor(core.FIG_BG)
    ax.set_xlim(-window_kb, window_kb)
    ax.set_ylim(n - 0.5, -0.5)

    if not calls.empty:
        d = calls[calls['species'].isin(yidx)]
        # draw grey (minor/singleton) groups first so coloured groups sit on top
        for grp, sub in sorted(d.groupby('group'), key=lambda kv: kv[0] != 0):
            x0 = (sub['start'] - anchor_pos) / 1000.0
            w = (sub['end'] - sub['start']) / 1000.0
            y = sub['species'].map(yidx).to_numpy(float)
            # widen to a visible minimum so single-bin calls remain legible
            w = np.maximum(w.to_numpy(float), window_kb * 0.012)
            ax.barh(y, w, left=x0.to_numpy(float), height=0.85,
                    color=colors.get(int(grp), syn.GREY),
                    edgecolor='none', linewidth=0)

    ax.axvline(0, color=core.FIG_HL, lw=0.7, ls=':', alpha=0.8)
    ax.set_yticks([])
    ax.set_xticks([])
    for s in ax.spines.values():
        s.set_color(core.FIG_DIM)
        s.set_linewidth(0.5)


def plot_fig6e_paper(species_order: list[str],
                     signals: dict[str, np.ndarray],
                     tree_obj,
                     calls: pd.DataFrame,
                     *,
                     anchor_label: str,
                     anchor_chrom: str,
                     anchor_pos: int,
                     cell_type: str,
                     window_kb: float,
                     score_vmax: float = 30.0,
                     highlight_species=(),
                     anchor_strand: Optional[str] = None,
                     genes: Optional[list[dict]] = None,
                     score_label: str = 'STEAM-v1 GPS (genome-wide Phred)',
                     coord_mode: str = 'absolute',
                     n_major: int = syn.N_MAJOR_GROUPS):
    n = len(species_order)
    if n == 0:
        fig = plt.figure(figsize=(6, 2))
        fig.text(0.5, 0.5, 'No species to plot.', ha='center', va='center')
        return fig

    have_tree = tree_obj is not None
    colors = syn.group_colors(n_major)
    fontsize = float(np.clip(560 / max(n, 1), 3.0, FS))

    mat = np.vstack([signals[s] for s in species_order])
    coverage = np.isfinite(mat).mean(axis=0)
    strength = np.nansum(mat, axis=0)
    strength = strength / strength.max() if strength.max() > 0 else strength
    xgrid = np.linspace(-window_kb, window_kb, mat.shape[1])

    have_genes = bool(genes)
    gene_rows = core._pack_genes(genes, anchor_pos, window_kb)[1] if have_genes else 0
    gene_h = (0.28 * gene_rows + 0.25) if have_genes else 0.0

    base_h = max(6.0, n * 0.085)
    track_h = 0.75
    # tree | tip labels | enhancer calls | accessibility heatmap
    col_w = ([1.0, 0.45] if have_tree else []) + [1.15, 3.0]
    fig_w = sum(col_w) * 1.7

    row_h = [track_h, track_h] + ([gene_h] if have_genes else []) + [base_h]
    fig_h = sum(row_h) + 1.0
    fig = plt.figure(figsize=(fig_w, fig_h))
    # Pin the grid just below the title rather than letting the default margins
    # scale with figure height — at 180+ species that leaves inches of dead space.
    gs = fig.add_gridspec(len(row_h), len(col_w), width_ratios=col_w,
                          height_ratios=row_h, wspace=0.02, hspace=0.05,
                          top=1.0 - 0.34 / fig_h, bottom=0.55 / fig_h,
                          left=0.015, right=0.995)
    heat_col = len(col_w) - 1
    call_col = heat_col - 1
    heat_row = len(row_h) - 1

    grid_axes: list = []

    # --- coverage track ---
    ax_cov = fig.add_subplot(gs[0, heat_col])
    ax_cov.fill_between(xgrid, coverage, color=core.FIG_DIM, lw=0)
    ax_cov.set_xlim(-window_kb, window_kb)
    ax_cov.set_ylim(0, 1)
    ax_cov.set_ylabel('cov.', fontsize=FS, rotation=0, ha='right', va='center')
    ax_cov.set_xticks([])
    ax_cov.tick_params(labelsize=FS)
    grid_axes.append(ax_cov)

    # --- summed strength track ---
    ax_str = fig.add_subplot(gs[1, heat_col], sharex=ax_cov)
    ax_str.fill_between(xgrid, strength, color=core.FIG_ACCENT, lw=0)
    ax_str.set_ylim(0, 1)
    ax_str.set_ylabel('sum', fontsize=FS, rotation=0, ha='right', va='center')
    ax_str.set_xticks([])
    ax_str.tick_params(labelsize=FS)
    grid_axes.append(ax_str)

    # --- gene models ---
    if have_genes:
        gene_row = 2
        ax_gene = fig.add_subplot(gs[gene_row, heat_col], sharex=ax_cov)
        core._draw_gene_track(ax_gene, genes, anchor_pos, window_kb, fontsize=FS)
        for c in range(heat_col):
            fig.add_subplot(gs[gene_row, c]).axis('off')

    # --- tree + tip labels ---
    if have_tree:
        ax_tree = fig.add_subplot(gs[heat_row, 0])
        ax_lab = fig.add_subplot(gs[heat_row, 1])
        core._draw_rect_tree(ax_tree, ax_lab, tree_obj, species_order, fontsize,
                             highlight=highlight_species)

    # --- middle panel: enhancer calls by synteny group ---
    ax_call = fig.add_subplot(gs[heat_row, call_col])
    _draw_call_panel(ax_call, calls, species_order, window_kb, anchor_pos, colors)
    ax_call.set_xlabel('enhancers\n(synteny group)', fontsize=FS)

    # --- right panel: accessibility heatmap ---
    ax = fig.add_subplot(gs[heat_row, heat_col], sharex=ax_cov)
    masked = np.ma.masked_invalid(mat)
    cmap = plt.cm.viridis.copy()
    cmap.set_bad(core.VIRIDIS_FLOOR)
    vmax = float(score_vmax)
    ax.set_facecolor(core.VIRIDIS_FLOOR)
    im = ax.imshow(masked, aspect='auto', cmap=cmap, vmin=0, vmax=vmax,
                   extent=[-window_kb, window_kb, n - 0.5, -0.5],
                   interpolation='nearest')

    ax.axvline(0, color=core.FIG_HL, lw=0.7, ls=':', alpha=0.8)
    trans = blended_transform_factory(ax.transData, ax.transAxes)
    arrow_len_kb = max(8.0, window_kb * 0.12)
    if anchor_strand in ('+', '-'):
        dx = arrow_len_kb if anchor_strand == '+' else -arrow_len_kb
        ax.annotate('', xy=(dx, 1.015), xytext=(0, 1.015), xycoords=trans,
                    arrowprops=dict(arrowstyle='-|>', color=core.FIG_HL,
                                    lw=0.9, mutation_scale=8), annotation_clip=False)
    ax.scatter([0], [1.015], transform=trans, marker='|', s=30, color=core.FIG_HL,
               linewidths=1.2, clip_on=False, zorder=5)
    core._draw_scale_bar(ax, window_kb)
    ax.set_yticks([])
    if coord_mode == 'relative':
        # Native mode: every row is in its own genome, so the shared axis can only
        # be distance from that species' own anchor.
        step = core._nice_round(window_kb / 2.5)
        ticks = np.arange(-window_kb, window_kb + step / 2, step)
        ax.set_xticks(ticks)
        ax.set_xticklabels([f'{t:+.0f}' if t else '0' for t in ticks], fontsize=FS)
        ax.tick_params(axis='x', which='major', length=4, width=0.6, labelsize=FS)
        for gax in grid_axes:
            gax.set_xticks(ticks)
            for gx in ticks:
                gax.axvline(gx, color=core.FIG_DIM, lw=0.3, zorder=0)
        ax.set_xlabel(f'kb from {anchor_label} TSS in each species\'s own genome',
                      fontsize=FS)
    else:
        core._apply_coord_ruler(ax, anchor_pos, window_kb, fontsize=FS,
                                grid_axes=grid_axes)
        ax.set_xlabel(f'{anchor_chrom} (hg38) — dotted line = {anchor_label} TSS',
                      fontsize=FS)

    # --- colorbar + synteny-group key ---
    # Both live in the empty block above the tree, stacked so neither clips the
    # other: colorbar on the track row, group swatches on the row beneath.
    if have_tree:
        cb_holder = fig.add_subplot(gs[0, 0:2])
        cb_holder.axis('off')
        cax = cb_holder.inset_axes([0.06, 0.30, 0.88, 0.16])
    else:
        cax = ax.inset_axes([0.0, 1.04, 0.32, 0.02])
    cb = fig.colorbar(im, cax=cax, orientation='horizontal')
    cb.set_label(score_label, fontsize=FS - 1, labelpad=2, color=core.FIG_FG)
    cb.set_ticks([t for t in (0, 10, 20, 30, 40, 50) if t <= vmax + 1e-9])
    cb.ax.tick_params(labelsize=FS - 1, length=2, colors=core.FIG_FG)
    cb.outline.set_edgecolor(core.FIG_FG)

    if have_tree and not calls.empty:
        key = fig.add_subplot(gs[1, 0:2])
        key.axis('off')
        key.set_xlim(0, 1); key.set_ylim(0, 1)
        present = sorted({int(g) for g in calls['group'] if g > 0})[:n_major]
        key.text(0.06, 0.92, 'synteny group', fontsize=FS - 1,
                 color=core.FIG_FG, va='top')
        # lay the swatches out in two rows so they fit the narrow block
        per_row = int(np.ceil(len(present) / 2)) or 1
        for i, g in enumerate(present):
            r, c = divmod(i, per_row)
            x = 0.06 + c * (0.88 / per_row)
            y = 0.52 - r * 0.30
            key.add_patch(plt.Rectangle((x, y), 0.055, 0.20,
                                        color=colors[g], clip_on=False))
            key.text(x + 0.068, y + 0.10, str(g), fontsize=FS - 2,
                     color=core.FIG_FG, va='center')

    fig.suptitle(
        f'{anchor_label} — {cell_type} — STEAM-v1 across {n} mammals',
        fontsize=FS + 2, color=core.FIG_FG, y=1.0, va='top')
    return fig
