// Pure ports of steam-fig6e-explorer/synteny.py. Inputs are the uint8 GPS
// matrix straight from the zarr slice (value/4 = GPS, 255 = missing).

import type { Call, GroupSummary } from "./types";

export const MISSING = 255;
export const GPS_THRESHOLD = 24.5;
export const N_MAJOR_GROUPS = 11;
export const GREY = "#c2c9d2";
export const GROUP_PALETTE = [
  "#2563eb", "#ea580c", "#16a34a", "#9333ea", "#0891b2", "#ca8a04",
  "#be123c", "#4d7c0f", "#7c3aed", "#0f766e", "#b45309",
];

export function groupColor(g: number, nMajor = N_MAJOR_GROUPS): string {
  return g > 0 && g <= nMajor ? GROUP_PALETTE[(g - 1) % GROUP_PALETTE.length] : GREY;
}

/** Fraction of non-missing bins in a row. */
export function coverage(row: Uint8Array): number {
  let n = 0;
  for (let i = 0; i < row.length; i++) if (row[i] !== MISSING) n++;
  return n / row.length;
}

export interface CallOptions { binBp: number; startBp: number; anchorPos: number; threshold: number; minBins?: number }

/** synteny.call_enhancers: runs of bins with GPS >= threshold, per species. */
export function callEnhancers(rows: Uint8Array[], species: string[], o: CallOptions): Call[] {
  const q = o.threshold * 4;           // compare on the quantised scale
  const minBins = o.minBins ?? 1;
  const calls: Call[] = [];
  rows.forEach((row, r) => {
    let b0 = -1;
    for (let b = 0; b <= row.length; b++) {
      const on = b < row.length && row[b] !== MISSING && row[b] >= q;
      if (on && b0 < 0) b0 = b;
      if (!on && b0 >= 0) {
        if (b - b0 >= minBins) {
          let mx = 0, sum = 0;
          for (let k = b0; k < b; k++) { if (row[k] > mx) mx = row[k]; sum += row[k]; }
          const s = o.startBp + Math.round(b0 * o.binBp);
          const e = o.startBp + Math.round(b * o.binBp);
          calls.push({
            species: species[r], row: r, start: s, end: e, bin_start: b0, bin_end: b,
            gps_max: mx / 4, gps_mean: sum / (b - b0) / 4,
            dist_to_tss: Math.floor((s + e) / 2) - o.anchorPos,
          });
        }
        b0 = -1;
      }
    }
  });
  calls.sort((a, b) => a.start - b.start || (a.species < b.species ? -1 : a.species > b.species ? 1 : 0));
  return calls;
}

/**
 * synteny.assign_synteny_groups: single-linkage on interval overlap, ranked by
 * count, top nMajor -> 1..nMajor, rest -> 0. Mutates calls (adds .group), returns summary.
 */
export function assignSyntenyGroups(calls: Call[], nMajor = N_MAJOR_GROUPS): GroupSummary[] {
  if (calls.length === 0) return [];
  const order = calls.map((c, i) => i).sort((i, j) =>
    calls[i].bin_start - calls[j].bin_start || calls[i].bin_end - calls[j].bin_end);
  const comp = new Int32Array(calls.length);
  let cur = -1, curEnd = null;
  for (const i of order) {
    const c = calls[i];
    if (curEnd === null || c.bin_start >= curEnd) { cur++; curEnd = c.bin_end; }
    else curEnd = Math.max(curEnd, c.bin_end);
    comp[i] = cur;
  }
  const size = new Map<number, number>(), firstBin = new Map<number, number>();
  calls.forEach((c, i) => {
    size.set(comp[i], (size.get(comp[i]) ?? 0) + 1);
    firstBin.set(comp[i], Math.min(firstBin.get(comp[i]) ?? Infinity, c.bin_start));
  });
  const ranked = [...size.keys()].sort((a, b) => size.get(b)! - size.get(a)! || firstBin.get(a)! - firstBin.get(b)!);
  const remap = new Map(ranked.map((c, i) => [c, i < nMajor ? i + 1 : 0]));
  calls.forEach((c, i) => { c.group = remap.get(comp[i]); });

  const summary: GroupSummary[] = [];
  for (let g = 1; g <= Math.min(nMajor, ranked.length); g++) {
    const members = calls.filter((c) => c.group === g);
    summary.push({
      group: g,
      n_enhancers: members.length,
      n_species: new Set(members.map((c) => c.species)).size,
      mean_dist_to_tss: members.reduce((a, c) => a + c.dist_to_tss, 0) / members.length,
      mean_gps: members.reduce((a, c) => a + c.gps_max, 0) / members.length,
      start: Math.min(...members.map((c) => c.start)),
      end: Math.max(...members.map((c) => c.end)),
    });
  }
  return summary;
}

/** Column-wise coverage fraction and summed GPS across species (the two top tracks). */
export function columnTracks(rows: Uint8Array[]): { cov: Float32Array; sum: Float32Array } {
  const n = rows[0]?.length ?? 0;
  const cov = new Float32Array(n), sum = new Float32Array(n);
  for (const row of rows) for (let b = 0; b < n; b++) if (row[b] !== MISSING) { cov[b]++; sum[b] += row[b] / 4; }
  for (let b = 0; b < n; b++) cov[b] /= rows.length || 1;
  return { cov, sum };
}
