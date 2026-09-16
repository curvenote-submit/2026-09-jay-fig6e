// Rendering: tree (SVG) | enhancer calls (SVG) | heatmap (canvas) with the
// coverage / summed-strength tracks, a gene-model track and a coordinate axis.
// Replaces fig6e_core.plot_fig6e + fig6e_panel.plot_fig6e_paper, plus the
// interactions: hover, tip click, legend isolate, brush-zoom, drag-pan.

import { interpolateViridis } from "d3-scale-chromatic";
import { MISSING, groupColor } from "./compute";
import { abbreviate } from "./tree";
import type { TreeLayout } from "./tree";
import type { Call, Gene, GroupSummary, Slice } from "./types";

export interface FigureParams { threshold: number; nMajor: number; minCov: number; highlight: string[]; showGaps: boolean }
export interface FigureView {
  slice: Slice;
  order: number[];                     // row -> index into slice.species
  rowPos: Map<number, number>;         // species index -> row
  tree: TreeLayout;
  calls: Call[];
  params: FigureParams;
  anchor: { chrom: string; pos: number };
  tracks: { cov: Float32Array; sum: Float32Array };
  genes?: Gene[];
  summary?: GroupSummary[];
  groupSpans?: Map<number, GroupSummary>;
  selectedGroup?: number | null;
  rowPx?: number | null;
  rowOffset?: number;
}
export interface FigureCallbacks {
  onHover?(info: { species: string; bp: number; gps: number | null } | null): void;
  onTipClick?(species: string): void;
  onSelectGroup?(group: number | null): void;
  onToggleGaps?(): void;
  onPanStart?(clientX: number, clientY: number): void;
  onBrushStart?(clientX: number): void;
  onZoom?(factor: number, centreBp: number): void;
  onRowZoom?(factor: number, row: number, cursorY: number): void;
  onRowScroll?(dy: number): boolean;
  onReset?(): void;
  onGeneClick?(gene: Gene): void;
}
export interface FigureGeom { heatW: number; span: number; rowH: number; autoRowH: number; heatH: number; contentH: number; maxOff: number; off: number; nRows: number }
export interface FigureHandle {
  canvas: HTMLCanvasElement;
  svg: SVGSVGElement;
  setHoverRow(r: number | null): void;
  geom: FigureGeom;
  setBrush(c0: number, c1: number): [number, number, number];
  clearBrush(): void;
}

type Attrs = Record<string, string | number>;
// elements carry their datum for event delegation
type HitRect = SVGRectElement & { _row?: number; _species?: string; _call?: Call };
type GeneGroup = SVGGElement & { _gene?: Gene };

export const FG = "#334155", DIM = "#94a3b8", FAINT = "#e2e8f0", HL = "#dc2626", ACCENT = "#0d9488";
const SVG = "http://www.w3.org/2000/svg";
const VMAX = 30; // GPS colour ceiling, as in the paper figure
const GENE_ROW_H = 13, MAX_GENE_ROWS = 6;

// 256-entry RGB lookup for the canvas hot loop, built once from d3's viridis.
export const VIRIDIS = (() => {
  const lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const hex = parseInt(interpolateViridis(i / 255).slice(1), 16);   // "#rrggbb"
    lut[i * 3] = hex >> 16; lut[i * 3 + 1] = (hex >> 8) & 255; lut[i * 3 + 2] = hex & 255;
  }
  return lut;
})();
export const VIRIDIS_FLOOR = interpolateViridis(0);

export function svgEl<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Attrs = {}, parent?: Element): SVGElementTagNameMap[K] {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  if (parent) parent.appendChild(e);
  return e;
}

/** Nice tick positions in bp for a [start, end] range, ~target ticks. */
export function genomicTicks(start: number, end: number, target = 6): number[] {
  const span = end - start;
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const ticks: number[] = [];
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) ticks.push(t);
  return ticks;
}

export function fmtBp(bp: number): string {
  return bp >= 1e6 && bp % 1e5 === 0 ? `${(bp / 1e6).toFixed(1)} Mb`
    : `${(bp / 1000).toLocaleString(undefined, { maximumFractionDigits: bp % 1000 ? 1 : 0 })} kb`;
}

/** fig6e_core._pack_genes: greedy UCSC-style packing into rows. Returns [{gene,row}], nRows. */
export function packGenes(genes: Gene[], startBp: number, endBp: number): { packed: { gene: Gene; row: number }[]; nRows: number } {
  const pad = Math.max((endBp - startBp) * 0.025, 500);
  const rowsEnd: number[] = [], out: { gene: Gene; row: number }[] = [];
  for (const g of genes) {
    if (g.end < startBp || g.start > endBp) continue;
    let row = rowsEnd.findIndex((e) => g.start > e + pad);
    if (row < 0) { rowsEnd.push(g.end); row = rowsEnd.length - 1; } else rowsEnd[row] = g.end;
    out.push({ gene: g, row });
  }
  return { packed: out, nRows: Math.min(MAX_GENE_ROWS, Math.max(rowsEnd.length, out.length ? 1 : 0)) };
}

/**
 * Paint a uint8 GPS matrix into an offscreen canvas at natural size.
 * Missing cells (no alignment) are the viridis floor by default — the paper's
 * encoding (fig6e_core: cmap.set_bad(VIRIDIS_FLOOR)) — or white with showGaps.
 */
export function paintHeatmap(rows: Uint8Array[], order: number[], showGaps = false): HTMLCanvasElement {
  const n = rows[0].length, m = order.length;
  const off = document.createElement("canvas");
  off.width = n; off.height = m;
  const ctx = off.getContext("2d")!;
  const img = ctx.createImageData(n, m);
  const d = img.data;
  const scale = 255 / (VMAX * 4);
  for (let r = 0; r < m; r++) {
    const row = rows[order[r]];
    let p = r * n * 4;
    for (let b = 0; b < n; b++, p += 4) {
      const v = row[b];
      if (v === MISSING) {
        if (showGaps) { d[p] = d[p + 1] = d[p + 2] = 255; } else { d[p] = VIRIDIS[0]; d[p + 1] = VIRIDIS[1]; d[p + 2] = VIRIDIS[2]; }
        d[p + 3] = 255; continue;
      }
      const i = Math.min(255, Math.round(v * scale)) * 3;
      d[p] = VIRIDIS[i]; d[p + 1] = VIRIDIS[i + 1]; d[p + 2] = VIRIDIS[i + 2]; d[p + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return off;
}

/**
 * Draw the whole figure into `root` (emptied first).
 * @param {HTMLElement} root
 * @param {object} v  {slice, order, rowPos, tree, calls, params, anchor, tracks, genes, selectedGroup, groupSpans}
 * @param {object} cb {onHover, onTipClick, onPanStart(clientX, clientY), onBrushStart(clientX), onZoom(factor, bp),
 *                     onRowZoom(factor, rowUnderCursor, cursorYInViewport), onRowScroll(dyPx) -> bool, onReset, onGeneClick, onToggleGaps}
 *
 * Rows: the figure's height is fixed (`vpH`, from the auto row height); `v.rowPx`
 * overrides the row height and `v.rowOffset` (px) scrolls the rows behind a clip.
 */
export function drawFigure(root: HTMLElement, v: FigureView, cb: FigureCallbacks = {}): FigureHandle | null {
  root.replaceChildren();
  const { slice, order, tree, calls, params, anchor, tracks, genes = [], selectedGroup = null } = v;
  const nRows = order.length;
  if (nRows === 0) {
    const d = document.createElement("div");
    d.className = "f6e-empty";
    d.textContent = "No species pass the coverage filter in this window (or no data here yet).";
    root.appendChild(d);
    return null;
  }
  const width = Math.max(640, root.clientWidth || 900);
  const autoRowH = Math.max(2.5, Math.min(12, 720 / Math.max(nRows, 1)));
  const rowH = v.rowPx ?? autoRowH;
  const heatH = Math.round(autoRowH * nRows);          // viewport height: fixed regardless of row zoom
  const contentH = rowH * nRows;
  const maxOff = Math.max(0, contentH - heatH);
  const off = Math.min(Math.max(0, v.rowOffset ?? 0), maxOff);
  const showLabels = rowH >= 7;
  const treeW = 110, labelW = showLabels ? 120 : 10, callsW = Math.round(width * 0.22);
  const trackH = 44, axisH = 26, gap = 6;
  const heatX = treeW + labelW + callsW + gap * 2;
  const heatW = width - heatX;
  const { packed, nRows: geneRows } = packGenes(genes, slice.startBp, slice.endBp);
  const geneH = geneRows ? geneRows * GENE_ROW_H + 6 : 0;
  // colourbar + synteny legend live top-left; the legend wraps 6 per line
  const CB_X = 8, CB_Y = 12, CB_H = 9, LEG_Y0 = CB_Y + CB_H + 26, LEG_ROW = 15, LEG_PER_ROW = 6;
  const legendItems = (v.summary ?? []).length + 1;                       // + "minor"
  const legendH = LEG_Y0 + Math.ceil(legendItems / LEG_PER_ROW) * LEG_ROW;
  const top = Math.max(trackH * 2 + geneH, legendH) + gap;
  const totalH = top + heatH + axisH;
  const span = slice.endBp - slice.startBp;
  const n0 = slice.rows[0].length;

  const x = (bp: number) => heatX + ((bp - slice.startBp) / span) * heatW;
  const bpAt = (px: number) => slice.startBp + ((px - heatX) / heatW) * span;
  const xCalls = (bp: number) => treeW + labelW + gap + ((bp - slice.startBp) / span) * callsW;
  const y = (r: number) => top + r * rowH - off;
  const rowAt = (yPx: number) => Math.floor((yPx - top + off) / rowH);   // figure px -> row index

  const fig = document.createElement("div");
  fig.className = "f6e-fig";
  fig.style.height = `${totalH}px`;
  root.appendChild(fig);

  // --- heatmap canvas ------------------------------------------------------
  const canvas = document.createElement("canvas");
  canvas.className = "f6e-heat";
  canvas.width = heatW * devicePixelRatio; canvas.height = heatH * devicePixelRatio;
  canvas.style.cssText = `left:${heatX}px;top:${top}px;width:${heatW}px;height:${heatH}px`;
  const ctx = canvas.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  // draw only the visible band of rows: source y in row units, fractional is fine
  ctx.drawImage(paintHeatmap(slice.rows, order, params.showGaps), 0, off / rowH, n0, heatH / rowH, 0, 0, canvas.width, canvas.height);
  fig.appendChild(canvas);

  // --- svg overlay (everything else) --------------------------------------
  const svg = svgEl("svg", { width, height: totalH, class: "f6e-svg" }, fig);
  const clipId = `f6e-rows-${Math.random().toString(36).slice(2, 8)}`;
  svgEl("rect", { x: 0, y: top, width, height: heatH }, svgEl("clipPath", { id: clipId }, svgEl("defs", {}, svg)));
  const rowsG = svgEl("g", { "clip-path": `url(#${clipId})` }, svg);   // everything laid out per row
  // catch wheel / pinch anywhere in the species and calls columns, not just on drawn rows
  svgEl("rect", { x: 0, y: top, width: heatX - gap, height: heatH, fill: "transparent", class: "f6e-rowsbg" }, rowsG);

  // isolated synteny group: band across the heatmap
  const selSpan = selectedGroup ? v.groupSpans?.get(selectedGroup) : undefined;
  if (selectedGroup && selSpan) {
    const s = selSpan;
    svgEl("rect", { x: x(s.start), y: top, width: Math.max(2, x(s.end) - x(s.start)), height: heatH,
      fill: groupColor(selectedGroup, params.nMajor), opacity: 0.18, "pointer-events": "none" }, svg);
  }

  // tree
  if (tree.edges.length) {
    const sx = (d: number) => 6 + (d / tree.depth) * (treeW - 12);
    const g = svgEl("g", { stroke: FG, "stroke-width": 0.8, fill: "none" }, rowsG);
    for (const e of tree.edges) svgEl("line", { x1: sx(e.x0), y1: y(e.y0 + 0.5), x2: sx(e.x1), y2: y(e.y1 + 0.5) }, g);
  }

  // row highlight (moved on hover)
  const hlRow = svgEl("rect", { x: 0, y: 0, width, height: rowH, fill: FG, opacity: 0, "pointer-events": "none" }, rowsG);
  const setHoverRow = (r: number | null) => {
    if (r === null) { hlRow.setAttribute("opacity", "0"); return; }
    hlRow.setAttribute("y", String(y(r))); hlRow.setAttribute("opacity", "0.12");
  };

  // tip labels + click targets (invisible full-row rects across tree+label columns)
  const tips = svgEl("g", { "font-size": Math.min(10, rowH - 1), fill: FG }, rowsG);
  order.forEach((ri, r) => {
    if (y(r) + rowH < top || y(r) > top + heatH) return;          // culled: outside the viewport
    const sp = slice.species[ri];
    const hl = params.highlight.includes(sp);
    if (showLabels) {
      svgEl("text", { x: treeW + 4, y: y(r) + rowH * 0.5, "dominant-baseline": "middle", fill: hl ? HL : FG,
        "font-weight": hl ? 600 : 400, "pointer-events": "none" }, tips).textContent = abbreviate(sp);
    } else if (hl) {
      svgEl("rect", { x: treeW, y: y(r), width: labelW, height: rowH, fill: HL, "pointer-events": "none" }, tips);
    }
    const hit = svgEl("rect", { x: 0, y: y(r), width: treeW + labelW, height: rowH, fill: "transparent", class: "f6e-tiphit" }, tips) as HitRect;
    hit._row = r; hit._species = sp;
  });

  // calls panel
  svgEl("rect", { x: treeW + labelW + gap, y: top, width: callsW, height: heatH, fill: "none", stroke: FAINT, "pointer-events": "none" }, svg);
  const cg = svgEl("g", {}, rowsG);
  for (const c of calls) {
    const r = v.rowPos.get(c.row);
    if (r === undefined || y(r) + rowH < top || y(r) > top + heatH) continue;
    const dim = selectedGroup !== null && c.group !== selectedGroup;
    const rect = svgEl("rect", {
      x: xCalls(c.start), y: y(r), width: Math.max(1, xCalls(c.end) - xCalls(c.start)), height: Math.max(1, rowH - 0.5),
      fill: groupColor(c.group ?? 0, params.nMajor), opacity: dim ? 0.12 : 1, class: "f6e-call",
    }, cg) as HitRect;
    rect._call = c;
  }
  // anchor marker on calls + heatmap
  if (anchor.pos >= slice.startBp && anchor.pos <= slice.endBp) {
    for (const xa of [xCalls(anchor.pos), x(anchor.pos)]) {
      svgEl("line", { x1: xa, y1: top - geneH - 4, x2: xa, y2: top + heatH, stroke: HL, "stroke-width": 1, "stroke-dasharray": "2 3", opacity: 0.8, "pointer-events": "none" }, svg);
    }
  }

  // colourbar (top-left, where the matplotlib figure keeps its legend)
  {
    const cbX = CB_X, cbY = CB_Y, cbW = Math.min(180, treeW + labelW + callsW - 60), cbH = CB_H;
    const defs = svgEl("defs", {}, svg);
    const gradId = `f6e-viridis-${Math.random().toString(36).slice(2, 8)}`;
    const grad = svgEl("linearGradient", { id: gradId, x1: 0, x2: 1, y1: 0, y2: 0 }, defs);
    for (let i = 0; i <= 16; i++) svgEl("stop", { offset: `${(i / 16) * 100}%`, "stop-color": interpolateViridis(i / 16) }, grad);
    const g = svgEl("g", { "font-size": 9, fill: FG }, svg);
    svgEl("text", { x: cbX, y: cbY - 3 }, g).textContent = "STEAM-v1 GPS (genome-wide Phred)";
    svgEl("rect", { x: cbX, y: cbY, width: cbW, height: cbH, fill: `url(#${gradId})`, stroke: FAINT }, g);
    for (const t of [0, 10, 20, 30]) {
      const tx = cbX + (t / VMAX) * cbW;
      svgEl("line", { x1: tx, y1: cbY + cbH, x2: tx, y2: cbY + cbH + 3, stroke: FG }, g);
      svgEl("text", { x: tx, y: cbY + cbH + 12, "text-anchor": t === 0 ? "start" : t === VMAX ? "end" : "middle" }, g).textContent = t === VMAX ? `≥${t}` : String(t);
    }
    // "no alignment" swatch: click to toggle between the colour-map floor (the
    // paper's encoding) and white, so gaps can be told apart from closed chromatin
    const mx = cbX + cbW + 14;
    const sw = svgEl("g", { class: "f6e-gapswatch" }, g);
    svgEl("title", {}, sw).textContent = params.showGaps
      ? "Unaligned bins shown in white. Click to use the colour-map floor (the paper's encoding)."
      : "Unaligned bins take the colour-map floor, as in the paper. Click to show them in white.";
    svgEl("rect", { x: mx, y: cbY, width: 14, height: cbH, fill: params.showGaps ? "#ffffff" : VIRIDIS_FLOOR, stroke: DIM, "stroke-dasharray": params.showGaps ? "2 1" : "none" }, sw);
    svgEl("text", { x: mx + 18, y: cbY + cbH - 1, "text-decoration": "underline dotted" }, sw).textContent = "no alignment";
    svgEl("rect", { x: mx - 2, y: cbY - 3, width: 90, height: cbH + 6, fill: "transparent" }, sw);   // hit area
    sw.addEventListener("click", () => cb.onToggleGaps?.());

    // synteny-group legend, 6 per line; click a swatch to isolate that group
    const lg = svgEl("g", { "font-size": 9, fill: FG, class: "f6e-leg" }, svg);
    svgEl("text", { x: cbX, y: LEG_Y0 - 4, fill: DIM }, lg).textContent = "Synteny Groups";
    const items: { g: number; label: string; s?: GroupSummary }[] = [...(v.summary ?? []).map((s) => ({ g: s.group, label: String(s.group), s })), { g: 0, label: "minor" }];
    const colW = Math.max(26, Math.floor((heatX - 56 - cbX) / LEG_PER_ROW));   // stop short of the track axes
    items.forEach((it, i) => {
      const lx = cbX + (i % LEG_PER_ROW) * colW, ly = LEG_Y0 + Math.floor(i / LEG_PER_ROW) * LEG_ROW;
      const isSel = selectedGroup === it.g, dimmed = selectedGroup !== null && !isSel;
      const item = svgEl("g", { class: it.g ? "f6e-legitem" : "", opacity: dimmed ? 0.4 : 1 }, lg);
      if (it.s) svgEl("title", {}, item).textContent = `group ${it.g}: ${it.s.n_enhancers} enhancers in ${it.s.n_species} species · ${(it.s.mean_dist_to_tss / 1000).toFixed(1)} kb from TSS · click to ${isSel ? "clear" : "isolate"}`;
      if (isSel) svgEl("rect", { x: lx - 2, y: ly - 1, width: colW - 4, height: LEG_ROW - 1, fill: "#e2e8f0", rx: 2 }, item);
      svgEl("rect", { x: lx, y: ly, width: 12, height: 12, rx: 2, fill: groupColor(it.g, params.nMajor) }, item);
      svgEl("text", { x: lx + 15, y: ly + 9.5, "font-weight": isSel ? 600 : 400 }, item).textContent = it.label;
      svgEl("rect", { x: lx - 2, y: ly - 1, width: colW - 4, height: LEG_ROW - 1, fill: "transparent" }, item);  // hit area
      if (it.g) item.addEventListener("click", () => cb.onSelectGroup?.(isSel ? null : it.g));
    });
  }

  // top tracks: coverage, summed strength
  const n = tracks.cov.length;
  const tx = (b: number) => heatX + (b / n) * heatW;
  const trackPath = (arr: Float32Array, max: number, y0: number) => {
    let d = `M${tx(0)},${y0 + trackH - 2}`;
    for (let b = 0; b < n; b++) d += `L${tx(b)},${y0 + trackH - 2 - (arr[b] / max) * (trackH - 8)}`;
    return d + `L${tx(n)},${y0 + trackH - 2}Z`;
  };
  const sumMax = Math.max(1e-9, ...tracks.sum);
  svgEl("path", { d: trackPath(tracks.cov, 1, 0), fill: DIM, opacity: 0.8, "pointer-events": "none" }, svg);
  svgEl("path", { d: trackPath(tracks.sum, sumMax, trackH), fill: ACCENT, opacity: 0.85, "pointer-events": "none" }, svg);
  // y axes for the two tracks: baseline at y0 + trackH - 2, full scale at y0 + 6
  const fmtSum = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : String(Math.round(v));
  const trackAxis = (y0: number, title: string, fmt: (f: number) => string) => {   // ticks at 0, ½ and full scale
    const g = svgEl("g", { "font-size": 8, fill: FG, "text-anchor": "end", "pointer-events": "none" }, svg);
    const yBase = y0 + trackH - 2, yTop = y0 + 6;
    svgEl("line", { x1: heatX - 0.5, y1: yTop, x2: heatX - 0.5, y2: yBase, stroke: FG, "stroke-width": 0.8 }, g);
    for (const f of [0, 0.5, 1]) {
      const yy = yBase - f * (yBase - yTop);
      svgEl("line", { x1: heatX - 3, y1: yy, x2: heatX, y2: yy, stroke: FG, "stroke-width": 0.8 }, g);
      svgEl("text", { x: heatX - 5, y: yy, "dominant-baseline": f === 0 ? "auto" : f === 1 ? "hanging" : "middle" }, g).textContent = fmt(f);
    }
    svgEl("text", { x: heatX - 34, y: (yBase + yTop) / 2, "dominant-baseline": "middle", "font-size": 9, fill: DIM }, g).textContent = title;
  };
  trackAxis(0, "Coverage", (f) => `${Math.round(f * 100)}%`);   // share of kept species aligned
  trackAxis(trackH, "Σ GPS", (f) => fmtSum(f * sumMax));   // summed GPS across kept species, absolute

  // gene track
  const gg = svgEl("g", { "font-size": 9, fill: FG }, svg);
  const gy0 = trackH * 2 + 3;
  if (geneRows) svgEl("text", { x: heatX - 4, y: gy0 + 10, "text-anchor": "end" }, gg).textContent = "genes";
  for (const { gene: g, row } of packed) {
    if (row >= MAX_GENE_ROWS) continue;
    const yy = gy0 + row * GENE_ROW_H + GENE_ROW_H / 2;
    const gs = Math.max(x(g.start), heatX), ge = Math.min(x(g.end), heatX + heatW);
    const grp = svgEl("g", { class: "f6e-gene" }, gg) as GeneGroup;
    grp._gene = g;
    svgEl("rect", { x: gs, y: yy - 6, width: Math.max(1, ge - gs), height: 12, fill: "transparent" }, grp); // hit area
    svgEl("line", { x1: gs, y1: yy, x2: ge, y2: yy, stroke: FG, "stroke-width": 1 }, grp);
    for (const [a, b] of g.exons) {
      const ea = Math.max(x(a), heatX), eb = Math.min(x(b), heatX + heatW);
      if (eb > ea) svgEl("rect", { x: ea, y: yy - 4, width: Math.max(1, eb - ea), height: 8, fill: FG }, grp);
    }
    // strand chevrons every ~28 px along introns
    const step = 28, chev = g.strand >= 0 ? "M-2,-2 L1,0 L-2,2" : "M2,-2 L-1,0 L2,2";
    for (let px = gs + step / 2; px < ge - 4; px += step) {
      svgEl("path", { d: chev, transform: `translate(${px},${yy})`, stroke: FG, fill: "none", "stroke-width": 0.9 }, grp);
    }
    if (ge - gs > 30 || g.end - g.start < span * 0.02) {
      const lx = Math.min(Math.max(gs, heatX + 2), heatX + heatW - 40);
      svgEl("text", { x: lx, y: yy - 6, "font-style": "italic" }, grp).textContent = g.name;
    }
  }

  // x axis
  const ax = svgEl("g", { "font-size": 9, fill: FG, stroke: "none" }, svg);
  svgEl("line", { x1: heatX, y1: top + heatH + 0.5, x2: heatX + heatW, y2: top + heatH + 0.5, stroke: FG }, ax);
  for (const t of genomicTicks(slice.startBp, slice.endBp)) {
    svgEl("line", { x1: x(t), y1: top + heatH, x2: x(t), y2: top + heatH + 4, stroke: FG }, ax);
    svgEl("text", { x: x(t), y: top + heatH + 15, "text-anchor": "middle" }, ax).textContent = fmtBp(t);
  }
  svgEl("text", { x: heatX + heatW, y: totalH - 2, "text-anchor": "end", "font-size": 9, fill: DIM }, ax)
    .textContent = `${anchor.chrom}:${slice.startBp.toLocaleString()}–${slice.endBp.toLocaleString()} (${fmtBp(span)}), hg38`;

  // row scrollbar (only when rows overflow the viewport)
  if (maxOff > 0) {
    const sbX = heatX + heatW - 5, thumbH = Math.max(12, (heatH / contentH) * heatH);
    svgEl("rect", { x: sbX, y: top, width: 4, height: heatH, fill: FAINT, rx: 2, "pointer-events": "none" }, svg);
    svgEl("rect", { x: sbX, y: top + (off / contentH) * heatH, width: 4, height: thumbH, fill: DIM, rx: 2, "pointer-events": "none" }, svg);
    svgEl("text", { x: sbX - 4, y: top + 10, "text-anchor": "end", "font-size": 9, fill: DIM, "pointer-events": "none" }, svg)
      .textContent = `rows ${rowAt(top) + 1}–${Math.min(nRows, rowAt(top + heatH - 1) + 1)} of ${nRows}`;
  }

  // --- zoom buttons --------------------------------------------------------
  const btns = document.createElement("div");
  btns.className = "f6e-zoom";
  btns.style.cssText = `left:${heatX + heatW - 74}px;top:2px`;
  const zoomBtns: [string, string, () => void][] = [
    ["−", "zoom out (ctrl+wheel)", () => cb.onZoom?.(2, (slice.startBp + slice.endBp) / 2)],
    ["+", "zoom in (ctrl+wheel, or drag on the tracks)", () => cb.onZoom?.(0.5, (slice.startBp + slice.endBp) / 2)],
    ["⟲", "reset window and row height (double-click)", () => cb.onReset?.()],
  ];
  for (const [label, title, fn] of zoomBtns) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = label; b.title = title;
    b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
    btns.appendChild(b);
  }
  fig.appendChild(btns);

  // --- tooltip ---------------------------------------------------------------
  const tip = document.createElement("div");
  tip.className = "f6e-tip"; tip.hidden = true;
  fig.appendChild(tip);
  const showTip = (ev: MouseEvent, html: string) => {
    const b = fig.getBoundingClientRect();
    tip.innerHTML = html; tip.hidden = false;
    tip.style.left = `${Math.min(ev.clientX - b.left + 12, width - 230)}px`;
    tip.style.top = `${ev.clientY - b.top + 12}px`;
  };
  const hideTip = () => { tip.hidden = true; setHoverRow(null); cb.onHover?.(null); };

  // --- pan by drag: the drag itself is tracked by index.js (it outlives redraws)
  canvas.addEventListener("mousedown", (ev) => { cb.onPanStart?.(ev.clientX, ev.clientY); ev.preventDefault(); });
  canvas.addEventListener("mousemove", (ev) => {
    if (ev.buttons) return;
    const r = rowAt(top + ev.offsetY), b = Math.floor((ev.offsetX / heatW) * n);
    if (r < 0 || r >= nRows || b < 0 || b >= n) return;
    const ri = order[r], val = slice.rows[ri][b];
    const bp = slice.startBp + b * slice.binBp;
    setHoverRow(r);
    showTip(ev, `<b>${abbreviate(slice.species[ri])}</b><br>${anchor.chrom}:${bp.toLocaleString()}<br>GPS ${val === MISSING ? "—" : (val / 4).toFixed(2)}`);
    cb.onHover?.({ species: slice.species[ri], bp, gps: val === MISSING ? null : val / 4 });
  });
  // wheel over the heatmap: pinch / ctrl+wheel zooms the genomic axis; plain
  // wheel scrolls rows when they overflow (and lets the page scroll at the ends)
  const zoomFactor = (ev: WheelEvent) => Math.exp(Math.max(-60, Math.min(60, ev.deltaY)) * 0.006);
  canvas.addEventListener("wheel", (ev) => {
    if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); cb.onZoom?.(zoomFactor(ev), bpAt(heatX + ev.offsetX)); return; }
    if (maxOff > 0 && cb.onRowScroll?.(ev.deltaY)) ev.preventDefault();
  }, { passive: false });
  // wheel over the species / calls columns: pinch changes the row height
  // around the row under the cursor; plain wheel scrolls rows
  const rowWheel = (ev: WheelEvent) => {
    const yPx = ev.clientY - svg.getBoundingClientRect().top;
    if (ev.ctrlKey || ev.metaKey) { ev.preventDefault(); cb.onRowZoom?.(1 / zoomFactor(ev), rowAt(yPx), yPx - top); return; }
    if (maxOff > 0 && cb.onRowScroll?.(ev.deltaY)) ev.preventDefault();
  };
  rowsG.addEventListener("wheel", rowWheel, { passive: false });

  // calls
  cg.addEventListener("mousemove", (ev) => {
    const c = (ev.target as HitRect)._call;
    if (!c) { hideTip(); return; }
    setHoverRow(v.rowPos.get(c.row) ?? null);
    showTip(ev, `<b>${abbreviate(c.species)}</b> · group ${c.group || "minor"}<br>${anchor.chrom}:${c.start.toLocaleString()}–${c.end.toLocaleString()}<br>peak GPS ${c.gps_max.toFixed(1)}, mean ${c.gps_mean.toFixed(1)}<br>${(c.dist_to_tss / 1000).toFixed(1)} kb from TSS`);
  });

  // tips: hover row, click toggles highlight
  tips.addEventListener("mousemove", (ev) => {
    const t = ev.target as HitRect;
    if (t._species === undefined) return;
    setHoverRow(t._row ?? null);
    showTip(ev, `<b>${t._species.replaceAll("_", " ")}</b><br><span class="f6e-hint">click to ${params.highlight.includes(t._species) ? "un-highlight" : "highlight"}</span>`);
  });
  tips.addEventListener("click", (ev) => { const sp = (ev.target as HitRect)._species; if (sp) cb.onTipClick?.(sp); });

  // genes: hover + click recentres
  const geneOf = (ev: Event) => ((ev.target as Element).closest?.(".f6e-gene") as GeneGroup | null)?._gene;
  gg.addEventListener("mousemove", (ev) => {
    const g = geneOf(ev);
    if (!g) { hideTip(); return; }
    showTip(ev, `<i>${g.name}</i> (${g.strand >= 0 ? "+" : "−"})<br>${anchor.chrom}:${g.start.toLocaleString()}–${g.end.toLocaleString()}<br><span class="f6e-hint">click to centre on its TSS</span>`);
  });
  gg.addEventListener("click", (ev) => {
    const g = geneOf(ev);
    if (g) cb.onGeneClick?.(g);
  });

  // brush on the tracks area (drag tracked by index.js; setBrush/clearBrush draw it)
  const brushArea = svgEl("rect", { x: heatX, y: 0, width: heatW, height: trackH * 2, fill: "transparent", class: "f6e-brusharea" }, svg);
  const brushRect = svgEl("rect", { x: 0, y: 0, width: 0, height: totalH - axisH, fill: ACCENT, opacity: 0, "pointer-events": "none" }, svg);
  brushArea.addEventListener("mousedown", (ev) => { cb.onBrushStart?.(ev.clientX); ev.preventDefault(); });
  brushArea.addEventListener("mousemove", (ev) => {
    if (ev.buttons) return;
    const bp = bpAt(ev.clientX - svg.getBoundingClientRect().left), bi = Math.floor((bp - slice.startBp) / slice.binBp);
    if (bi < 0 || bi >= n) return;
    showTip(ev, `${anchor.chrom}:${Math.round(bp).toLocaleString()}<br>coverage ${(tracks.cov[bi] * 100).toFixed(0)}% · summed GPS ${tracks.sum[bi].toFixed(0)}<br><span class="f6e-hint">drag to zoom</span>`);
  });

  fig.addEventListener("dblclick", () => cb.onReset?.());
  for (const e of [canvas, cg, tips, gg, brushArea]) e.addEventListener("mouseleave", hideTip);

  return {
    canvas, svg, setHoverRow,
    /** geometry for index.js's drag handling: px width / bp span, and the row viewport */
    geom: { heatW, span, rowH, autoRowH, heatH, contentH, maxOff, off, nRows },
    setBrush(c0, c1) {
      const left = svg.getBoundingClientRect().left;
      const lo = Math.min(Math.max(Math.min(c0, c1) - left, heatX), heatX + heatW), hi = Math.min(Math.max(Math.max(c0, c1) - left, heatX), heatX + heatW);
      brushRect.setAttribute("x", String(lo)); brushRect.setAttribute("width", String(hi - lo)); brushRect.setAttribute("opacity", "0.2");
      return [bpAt(lo), bpAt(hi), hi - lo];
    },
    clearBrush() { brushRect.setAttribute("opacity", "0"); },
  };
}
