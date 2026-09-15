"""Enhancer calling and synteny grouping for the Fig 6d/6e middle panel.

Fig 6d in the paper builds its synteny groups by lifting called enhancers over
*between* species and connecting overlapping pairs. Every species here is already
projected into hg38, so that liftover has effectively been applied: "overlaps after
liftover" becomes "overlaps in hg38", and the connected components of the overlap
graph are exactly the merged intervals of the pooled call set.

Enhancer calls are re-derived from the genome-wide Phred-normalised tracks at the
paper's GPS threshold, so calls and heatmap always come from the same numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Paper's GPS enhancer-calling threshold (calibrated on the mouse genome-wide scale).
GPS_THRESHOLD = 24.5
# Paper's Fig 6e species filter: >= 50 kb contiguous recoverable sequence on each side.
MIN_FLANK_KB = 50.0
# Fig 6d colours clusters 1-11 and greys out the remainder.
N_MAJOR_GROUPS = 11

GREY = '#c2c9d2'   # pale grey — minor groups recede on a white ground


def _runs_above(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True in ``mask`` as half-open [start, end) bin indices."""
    if not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist()))


def call_enhancers(signals: dict[str, np.ndarray], *, bin_bp: float,
                   anchor_pos: int, window_kb: float,
                   threshold: float = GPS_THRESHOLD,
                   min_bins: int = 1) -> pd.DataFrame:
    """Call enhancers as runs of bins at or above ``threshold`` on the Phred scale.

    ``signals`` must already be genome-wide Phred-normalised (GPS); calling on raw
    prediction scores would not be comparable to the paper's threshold.

    Returns one row per called enhancer with hg38 coordinates, bin extent, peak and
    mean GPS, and signed distance from the anchor TSS.
    """
    win_bp = int(window_kb * 1000)
    start_bp = int(anchor_pos) - win_bp
    rows = []
    for sp, arr in signals.items():
        mask = np.isfinite(arr) & (arr >= threshold)
        for b0, b1 in _runs_above(mask):
            if b1 - b0 < min_bins:
                continue
            seg = arr[b0:b1]
            s = start_bp + int(round(b0 * bin_bp))
            e = start_bp + int(round(b1 * bin_bp))
            centre = (s + e) // 2
            rows.append({
                'species': sp,
                'start': s, 'end': e,
                'bin_start': b0, 'bin_end': b1,
                'gps_max': float(seg.max()), 'gps_mean': float(seg.mean()),
                'dist_to_tss': centre - int(anchor_pos),
            })
    cols = ['species', 'start', 'end', 'bin_start', 'bin_end',
            'gps_max', 'gps_mean', 'dist_to_tss']
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)[cols].sort_values(['start', 'species'], ignore_index=True)


def assign_synteny_groups(calls: pd.DataFrame, *,
                          n_major: int = N_MAJOR_GROUPS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Connect overlapping calls across species into synteny groups.

    Single-linkage on interval overlap, so a connected component is just a merged
    interval over the pooled calls. Groups are ranked by enhancer count; the top
    ``n_major`` become clusters 1..n_major and the rest are grouped as 0 (grey),
    matching Fig 6d's treatment of smaller groups and singletons.

    Returns ``(calls_with_group, group_summary)``. The summary carries the four
    per-group statistics Fig 6d reports.
    """
    calls = calls.copy()
    if calls.empty:
        calls['group'] = pd.Series(dtype=int)
        return calls, pd.DataFrame(columns=[
            'group', 'n_enhancers', 'n_species', 'mean_dist_to_tss', 'mean_gps',
            'start', 'end'])

    order = calls.sort_values(['bin_start', 'bin_end']).index
    comp = {}
    cur_id, cur_end = -1, None
    for idx in order:
        b0, b1 = calls.at[idx, 'bin_start'], calls.at[idx, 'bin_end']
        if cur_end is None or b0 >= cur_end:      # no overlap -> new component
            cur_id += 1
            cur_end = b1
        else:
            cur_end = max(cur_end, b1)
        comp[idx] = cur_id
    calls['_comp'] = pd.Series(comp)

    sizes = calls['_comp'].value_counts()
    # rank by enhancer count, break ties by position so numbering is deterministic
    first_bin = calls.groupby('_comp')['bin_start'].min()
    ranked = sorted(sizes.index, key=lambda c: (-sizes[c], first_bin[c]))
    remap = {c: (i + 1 if i < n_major else 0) for i, c in enumerate(ranked)}
    calls['group'] = calls['_comp'].map(remap).astype(int)

    summary = (calls[calls['group'] > 0]
               .groupby('group')
               .agg(n_enhancers=('species', 'size'),
                    n_species=('species', 'nunique'),
                    mean_dist_to_tss=('dist_to_tss', 'mean'),
                    mean_gps=('gps_max', 'mean'),
                    start=('start', 'min'), end=('end', 'max'))
               .reset_index()
               .sort_values('group', ignore_index=True))
    return calls.drop(columns='_comp'), summary


# Eleven saturated hues that stay legible on white. tab20's pale tints (light
# blue, pink, lavender) disappear against a light background, so they are not used.
GROUP_PALETTE = [
    '#2563eb',  # blue
    '#ea580c',  # orange
    '#16a34a',  # green
    '#9333ea',  # purple
    '#0891b2',  # cyan
    '#ca8a04',  # amber
    '#be123c',  # rose
    '#4d7c0f',  # olive
    '#7c3aed',  # violet
    '#0f766e',  # teal
    '#b45309',  # brown
]


def group_colors(n_major: int = N_MAJOR_GROUPS) -> dict[int, str]:
    """Colour per synteny group; group 0 (minor groups + singletons) is grey."""
    colors = {0: GREY}
    for g in range(1, n_major + 1):
        colors[g] = GROUP_PALETTE[(g - 1) % len(GROUP_PALETTE)]
    return colors


def flank_spans_kb(arr: np.ndarray, bin_bp: float,
                   max_gap_kb: float = 10.0) -> tuple[float, float]:
    """Longest contiguous covered span (kb) in the left and right half of a track.

    Contiguity tolerates gaps up to ``max_gap_kb``, matching ``compute_synteny``'s
    treatment of short projection dropouts.
    """
    from fig6e_core import contiguous_span_bins
    max_gap_bins = int(round(max_gap_kb * 1000 / bin_bp))
    mid = len(arr) // 2
    left = contiguous_span_bins(arr[:mid], max_gap_bins) * bin_bp / 1000
    right = contiguous_span_bins(arr[mid:], max_gap_bins) * bin_bp / 1000
    return left, right


def paper_species_filter(signals: dict[str, np.ndarray], *, bin_bp: float,
                         min_flank_kb: float = MIN_FLANK_KB,
                         max_gap_kb: float = 10.0) -> tuple[list[str], pd.DataFrame]:
    """Fig 6e's species filter: keep species with >= ``min_flank_kb`` contiguous
    recoverable sequence on *each* side of the anchor.

    Returns ``(kept_species, per_species_table)``.
    """
    rows = []
    for sp, arr in signals.items():
        left, right = flank_spans_kb(arr, bin_bp, max_gap_kb)
        rows.append({'species': sp, 'left_span_kb': left, 'right_span_kb': right,
                     'keep': left >= min_flank_kb and right >= min_flank_kb})
    tab = pd.DataFrame(rows).sort_values('species', ignore_index=True)
    return tab.loc[tab['keep'], 'species'].tolist(), tab
