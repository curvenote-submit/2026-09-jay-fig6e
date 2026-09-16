// Rendering: tree (SVG) | enhancer calls (SVG) | heatmap (canvas) with the
// coverage / summed-strength tracks, a gene-model track and a coordinate axis.
// Replaces fig6e_core.plot_fig6e + fig6e_panel.plot_fig6e_paper, plus the
// interactions: hover, tip click, legend isolate, brush-zoom, drag-pan.

import { interpolateViridis } from "d3-scale-chromatic";
import { MISSING, groupColor } from "./compute";
import { abbreviate } from "./tree";
import type { TreeLayout } from "./tree";
import type { Call, Gene, GroupSummary, Slice } from "./types";
import { wikiSummary, shortExtract } from "./wiki";
import { geneInfo } from "./geneinfo";
import { ICON } from "./icons";

export interface FigureParams { threshold: number; nMajor: number; minCov: number; highlight: string[]; showGaps: boolean }
export interface FigureView {
  slice: Slice;
  order: number[];                     // row -> index into slice.species
  rowPos: Map<number, number>;         // species index -> row
  tree: TreeLayout;
  calls: Call[];
  params: FigureParams;
  anchor: { chrom: string; pos: number; strand?: "+" | "-" | null };
  tracks: { cov: Float32Array; sum: Float32Array };
  genes?: Gene[];
  summary?: GroupSummary[];
  groupSpans?: Map<number, GroupSummary>;
  selectedGroup?: number | null;
  rowPx?: number | null;
  rowOffset?: number;
  /** zoom limits, so the buttons can disable at the ends */
  limits?: { minSpan: number; maxSpan: number; chromLen: number; rowPxMax: number };
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

/** Always kb — the axis must not switch units as you zoom out. */
export function fmtKb(bp: number): string {
  return `${(bp / 1000).toLocaleString(undefined, { maximumFractionDigits: bp % 1000 ? 1 : 0 })} kb`;
}
/** Span label for the status tile (kb, Mb when large). */
export function fmtBp(bp: number): string {
  return bp >= 1e6 && bp % 1e5 === 0 ? `${(bp / 1e6).toFixed(1)} Mb` : fmtKb(bp);
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
  // Measure first, then build the new figure detached and swap it in at the
  // end: emptying the container before layout would shrink the page for a
  // frame and make the browser clamp the scroll position on every pan tick.
  const width = Math.max(640, root.clientWidth || 900);
  const { slice, order, tree, calls, params, anchor, tracks, genes = [], selectedGroup = null } = v;
  const nRows = order.length;
  if (nRows === 0) {
    const d = document.createElement("div");
    d.className = "f6e-empty";
    d.textContent = "No species pass the coverage filter in this window (or no data here yet).";
    root.style.minHeight = "";
    root.replaceChildren(d);
    return null;
  }
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
  const vGap = 20;   // room between the gene track and the heatmap for the TSS tick + arrow
  const top = Math.max(trackH * 2 + geneH, legendH) + vGap;
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
  root.style.minHeight = `${totalH}px`;   // keeps the page height stable during the swap

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

  // cross-panel position guides: hovering the heatmap marks the same bp on the
  // calls panel (grey), hovering the calls panel marks it on the heatmap (white)
  const vCalls = svgEl("line", { y1: top, y2: top + heatH, stroke: DIM, "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0, "pointer-events": "none" }, svg);
  const vHeat = svgEl("line", { y1: top, y2: top + heatH, stroke: "#fff", "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0, "pointer-events": "none" }, svg);
  const setGuides = (bp: number | null, from: "heat" | "calls") => {
    if (bp === null) { vCalls.setAttribute("opacity", "0"); vHeat.setAttribute("opacity", "0"); return; }
    const l = from === "heat" ? vCalls : vHeat, xx = from === "heat" ? xCalls(bp) : x(bp);
    l.setAttribute("x1", String(xx)); l.setAttribute("x2", String(xx)); l.setAttribute("opacity", "0.9");
    (from === "heat" ? vHeat : vCalls).setAttribute("opacity", "0");
  };

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
  const callsX0 = treeW + labelW + gap;
  svgEl("rect", { x: callsX0, y: top, width: callsW, height: heatH, fill: "none", stroke: FAINT, "pointer-events": "none" }, svg);
  const cg = svgEl("g", {}, rowsG);
  svgEl("rect", { x: callsX0, y: top, width: callsW, height: heatH, fill: "transparent", class: "f6e-callsbg" }, cg);   // catches hover between calls
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
  // anchor (TSS) marker: dotted line inside both plot boxes, plus the paper's
  // tick + strand arrow just above the heatmap (arrow = direction of transcription)
  if (anchor.pos >= slice.startBp && anchor.pos <= slice.endBp) {
    for (const xa of [xCalls(anchor.pos), x(anchor.pos)]) {
      svgEl("line", { x1: xa, y1: top, x2: xa, y2: top + heatH, stroke: HL, "stroke-width": 1, "stroke-dasharray": "2 3", opacity: 0.8, "pointer-events": "none" }, svg);
    }
    const xa = x(anchor.pos), ya = top - vGap / 2;   // vertically centred in the gap
    const tss = svgEl("g", { stroke: HL, fill: HL, "stroke-width": 1.2, class: "f6e-tss" }, svg);
    svgEl("line", { x1: xa, y1: ya - 4, x2: xa, y2: ya + 3 }, tss);
    let tipText = `Anchor: ${anchor.chrom}:${anchor.pos.toLocaleString()} (dotted line). Enhancer distances are measured from here.`;
    if (anchor.strand) {
      // a fixed 22 px arrow: it shows direction only, not extent
      const dir = anchor.strand === "+" ? 1 : -1;
      const xe = Math.max(heatX, Math.min(heatX + heatW, xa + dir * 22));
      svgEl("line", { x1: xa, y1: ya, x2: xe, y2: ya }, tss);
      svgEl("path", { d: `M${xe},${ya} l${-dir * 5},-2.5 l0,5 Z`, stroke: "none" }, tss);
      tipText = `Transcription start site of the anchor gene, on the ${anchor.strand} strand. The arrow shows the direction the gene is transcribed (${anchor.strand === "+" ? "left to right" : "right to left"}); the dotted line marks the same position in both panels and is the zero point for distance-to-TSS.`;
    }
    // hit area so the tooltip is easy to reach
    svgEl("rect", { x: Math.min(xa, xa + (anchor.strand === "-" ? -26 : 0)) - 4, y: ya - 6, width: 34, height: 12, fill: "transparent", stroke: "none" }, tss);
    tss.addEventListener("mousemove", (ev) => showTip(ev, tipText));
    tss.addEventListener("mouseleave", () => { tip.hidden = true; });
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
    // threshold marker on the colourbar. It is rebuilt with the figure, so to
    // glide with the slider the new marker starts where the old one was and
    // transitions to its new position.
    {
      const tx = cbX + (Math.min(params.threshold, VMAX) / VMAX) * cbW;
      const prev = (root as HTMLElement & { _f6eThrX?: number })._f6eThrX ?? tx;
      const m = svgEl("g", { class: "f6e-thr", "pointer-events": "none" }, g);
      svgEl("line", { x1: 0, y1: cbY - 1, x2: 0, y2: cbY + cbH + 1, stroke: "#fff", "stroke-width": 3 }, m);
      svgEl("line", { x1: 0, y1: cbY - 1, x2: 0, y2: cbY + cbH + 1, stroke: FG, "stroke-width": 1.2 }, m);
      svgEl("path", { d: `M-3.5,${cbY + cbH + 5} L3.5,${cbY + cbH + 5} L0,${cbY + cbH + 1} Z`, fill: FG }, m);
      m.style.transform = `translateX(${prev}px)`;
      requestAnimationFrame(() => { m.style.transform = `translateX(${tx}px)`; });
      (root as HTMLElement & { _f6eThrX?: number })._f6eThrX = tx;
      svgEl("title", {}, m).textContent = `enhancer-calling cutoff: GPS ≥ ${params.threshold}`;
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
      if (isSel) svgEl("rect", { x: lx - 2, y: ly - 1, width: colW - 4, height: LEG_ROW - 1, fill: "#e2e8f0" }, item);
      svgEl("rect", { x: lx, y: ly, width: 12, height: 12, fill: groupColor(it.g, params.nMajor) }, item);
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
  // Each axis also has a hover readout: while the pointer is over the figure the
  // static tick labels give way to the value at the hovered bin, drawn at its height.
  interface TrackAxis { show(b: number): void; hide(): void }
  const trackAxis = (y0: number, title: string, arr: Float32Array, max: number, fmt: (f: number) => string): TrackAxis => {
    const g = svgEl("g", { "font-size": 8, fill: FG, "text-anchor": "end", "pointer-events": "none" }, svg);
    const yBase = y0 + trackH - 2, yTop = y0 + 6;
    const yOf = (f: number) => yBase - f * (yBase - yTop);
    svgEl("line", { x1: heatX - 0.5, y1: yTop, x2: heatX - 0.5, y2: yBase, stroke: FG, "stroke-width": 0.8 }, g);
    const staticG = svgEl("g", {}, g);
    for (const f of [0, 0.5, 1]) {
      svgEl("line", { x1: heatX - 3, y1: yOf(f), x2: heatX, y2: yOf(f), stroke: FG, "stroke-width": 0.8 }, staticG);
      svgEl("text", { x: heatX - 5, y: yOf(f), "dominant-baseline": f === 0 ? "auto" : f === 1 ? "hanging" : "middle" }, staticG).textContent = fmt(f);
    }
    svgEl("text", { x: heatX - 34, y: (yBase + yTop) / 2, "dominant-baseline": "middle", "font-size": 9, fill: DIM }, g).textContent = title;
    // hover readout: tick + value on the axis, dot on the curve, guide down to the baseline
    const hov = svgEl("g", { visibility: "hidden" }, g);
    const hTick = svgEl("line", { x1: heatX - 4, x2: heatX, stroke: FG, "stroke-width": 1 }, hov);
    const hText = svgEl("text", { x: heatX - 5, "dominant-baseline": "middle", "font-weight": 600 }, hov);
    const hGuide = svgEl("line", { stroke: FG, "stroke-width": 0.6, opacity: 0.5 }, hov);
    const hDot = svgEl("circle", { r: 2.2, fill: FG, stroke: "#fff", "stroke-width": 1 }, hov);
    return {
      show(b: number) {
        const f = Math.max(0, Math.min(1, arr[b] / max)), yy = yOf(f), xx = tx(b) + (heatW / n) / 2;
        for (const el of [hTick, hGuide]) { el.setAttribute("y1", String(yy)); }
        hTick.setAttribute("y2", String(yy));
        hGuide.setAttribute("x1", String(xx)); hGuide.setAttribute("x2", String(xx)); hGuide.setAttribute("y2", String(yBase));
        hDot.setAttribute("cx", String(xx)); hDot.setAttribute("cy", String(yy));
        // keep the label inside the track band
        hText.setAttribute("y", String(Math.max(yTop + 3, Math.min(yBase - 3, yy))));
        hText.textContent = fmt(f);
        staticG.setAttribute("visibility", "hidden"); hov.setAttribute("visibility", "visible");
      },
      hide() { staticG.setAttribute("visibility", "visible"); hov.setAttribute("visibility", "hidden"); },
    };
  };
  const covAxis = trackAxis(0, "Coverage", tracks.cov, 1, (f) => `${Math.round(f * 100)}%`);   // share of kept species aligned
  const sumAxis = trackAxis(trackH, "Σ GPS", tracks.sum, sumMax, (f) => fmtSum(f * sumMax));    // summed GPS across kept species, absolute
  const showTrackValues = (b: number) => { covAxis.show(b); sumAxis.show(b); };
  const hideTrackValues = () => { covAxis.hide(); sumAxis.hide(); };

  // gene track
  const gg = svgEl("g", { "font-size": 9, fill: FG }, svg);
  const gy0 = trackH * 2 + 3;
  // title aligned with the Coverage / Σ GPS axis titles (same x, colour and size)
  if (geneRows) svgEl("text", { x: heatX - 34, y: gy0 + geneH / 2 - 3, "text-anchor": "end", "dominant-baseline": "middle", "font-size": 9, fill: DIM }, gg).textContent = "Genes";
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
    svgEl("text", { x: x(t), y: top + heatH + 15, "text-anchor": "middle" }, ax).textContent = fmtKb(t);
  }
  // x-axis title for the calls panel
  svgEl("text", { x: treeW + labelW + gap + callsW / 2, y: top + heatH + 15, "text-anchor": "middle", "font-size": 9, fill: FG }, ax)
    .textContent = "Enhancers (Synteny Group)";
  svgEl("text", { x: heatX + heatW, y: totalH - 2, "text-anchor": "end", "font-size": 9, fill: DIM }, ax)
    .textContent = `${anchor.chrom}:${slice.startBp.toLocaleString()}–${slice.endBp.toLocaleString()} (${fmtKb(span)}), hg38`;

  // row scrollbar (only when rows overflow the viewport)
  if (maxOff > 0) {
    const sbX = heatX + heatW - 5, thumbH = Math.max(12, (heatH / contentH) * heatH);
    svgEl("rect", { x: sbX, y: top, width: 4, height: heatH, fill: FAINT, rx: 2, "pointer-events": "none" }, svg);
    svgEl("rect", { x: sbX, y: top + (off / contentH) * heatH, width: 4, height: thumbH, fill: DIM, rx: 2, "pointer-events": "none" }, svg);
    svgEl("text", { x: heatX + 6, y: top + 11, "text-anchor": "start", "font-size": 9, fill: "#fff", opacity: 0.85, "pointer-events": "none" }, svg)
      .textContent = `rows ${rowAt(top) + 1}–${Math.min(nRows, rowAt(top + heatH - 1) + 1)} of ${nRows}`;
  }

  // --- hover-only button groups: x-zoom inside the heatmap's top-right corner,
  //     row expand/contract inside the calls panel's. Each group appears while
  //     the pointer is over its panel (or over the buttons themselves).
  const buttonGroup = (left: number, items: [string, string, () => void, boolean][]) => {
    const div = document.createElement("div");
    div.className = "f6e-zoom";
    div.style.cssText = `left:${left}px;top:${top + 4}px`;
    for (const [icon, title, fn, enabled] of items) {
      const b = document.createElement("button");
      b.type = "button"; b.innerHTML = icon; b.title = title; b.disabled = !enabled;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      b.addEventListener("dblclick", (e) => e.stopPropagation());
      div.appendChild(b);
    }
    fig.appendChild(div);
    return div;
  };
  // Which panel the pointer is over is remembered on the root, because every
  // pan tick rebuilds the figure: a rebuilt group starts in the same shown/hidden
  // state (without the fade), so the buttons do not flicker while dragging.
  const hoverState = root as HTMLElement & { _f6eHover?: Record<string, boolean> };
  hoverState._f6eHover ??= {};
  const hoverReveal = (key: string, group: HTMLElement, ...areas: Element[]) => {
    let timer = 0;
    if (hoverState._f6eHover![key]) { group.classList.add("f6e-zoom-now", "f6e-zoom-on"); }
    const show = () => { clearTimeout(timer); hoverState._f6eHover![key] = true; group.classList.remove("f6e-zoom-now"); group.classList.add("f6e-zoom-on"); };
    const hide = () => {
      clearTimeout(timer);
      timer = window.setTimeout(() => { hoverState._f6eHover![key] = false; group.classList.remove("f6e-zoom-on", "f6e-zoom-now"); }, 150);
    };
    for (const a of [group, ...areas]) { a.addEventListener("mouseenter", show); a.addEventListener("mouseleave", hide); }
  };
  const lim = v.limits ?? { minSpan: 0, maxSpan: Infinity, chromLen: Infinity, rowPxMax: Infinity };
  // the slice is snapped to whole bins, so compare with a one-bin tolerance
  const canZoomIn = span > lim.minSpan + slice.binBp, canZoomOut = span < Math.min(lim.maxSpan, lim.chromLen) - slice.binBp;
  const xZoom = buttonGroup(heatX + heatW - 54, [
    [ICON.minus, "zoom out (ctrl+wheel)", () => cb.onZoom?.(2, (slice.startBp + slice.endBp) / 2), canZoomOut],
    [ICON.plus, "zoom in (ctrl+wheel, or drag on the tracks)", () => cb.onZoom?.(0.5, (slice.startBp + slice.endBp) / 2), canZoomIn],
  ]);
  hoverReveal("heat", xZoom, canvas);
  const midRow = rowAt(top + heatH / 2);
  const canShrink = rowH > autoRowH + 0.05, canGrow = rowH < lim.rowPxMax - 0.05;
  const rowZoom = buttonGroup(callsX0 + callsW - 54, [
    [ICON.minus, "shorter rows (pinch the species column)", () => cb.onRowZoom?.(1 / 1.5, midRow, heatH / 2), canShrink],
    [ICON.plus, "taller rows (pinch the species column)", () => cb.onRowZoom?.(1.5, midRow, heatH / 2), canGrow],
  ]);
  hoverReveal("calls", rowZoom, cg);

  // --- tooltip ---------------------------------------------------------------
  const tip = document.createElement("div");
  tip.className = "f6e-tip"; tip.hidden = true;
  fig.appendChild(tip);
  const showTip = (ev: MouseEvent, html: string, card = false) => {
    const b = fig.getBoundingClientRect();
    tip.innerHTML = html; tip.hidden = false;
    tip.classList.toggle("f6e-tip-card", card);
    placeTip(ev, card);
  };
  /** Below-right of the pointer; flips above when it would run off the figure's bottom. */
  const placeTip = (ev: MouseEvent, card = false) => {
    const b = fig.getBoundingClientRect();
    const w = card ? 270 : 230, hgt = tip.offsetHeight;
    let y = ev.clientY - b.top + 12;
    if (y + hgt > totalH) y = Math.max(0, ev.clientY - b.top - 12 - hgt);
    tip.style.left = `${Math.min(ev.clientX - b.left + 12, width - w)}px`;
    tip.style.top = `${y}px`;
  };
  const esc = (t: string) => t.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
  const hideTip = () => { tip.hidden = true; setHoverRow(null); hideTrackValues(); setGuides(null, "heat"); cb.onHover?.(null); };

  // --- pan by drag: the drag itself is tracked by index.js (it outlives redraws)
  canvas.addEventListener("mousedown", (ev) => { cb.onPanStart?.(ev.clientX, ev.clientY); ev.preventDefault(); });
  canvas.addEventListener("mousemove", (ev) => {
    if (ev.buttons) return;
    const r = rowAt(top + ev.offsetY), b = Math.floor((ev.offsetX / heatW) * n);
    if (r < 0 || r >= nRows || b < 0 || b >= n) return;
    const ri = order[r], val = slice.rows[ri][b];
    const bp = slice.startBp + b * slice.binBp;
    setHoverRow(r);
    showTrackValues(b);
    setGuides(bp, "heat");
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

  // calls: structured card with the group swatch and a key/value grid
  const callCard = (c: Call) => {
    const grp = c.group ? `Group ${c.group}` : "Minor group";
    const g = v.groupSpans?.get(c.group ?? 0);
    const d = c.dist_to_tss;
    const side = anchor.strand
      ? ((d < 0) === (anchor.strand === "+") ? "upstream" : "downstream")   // relative to transcription
      : (d < 0 ? "left" : "right");
    const dist = Math.abs(d) < 1000 ? `${Math.abs(d)} bp` : `${(Math.abs(d) / 1000).toFixed(1)} kb`;
    const row = (k: string, val: string) => `<div class="f6e-kv-k">${k}</div><div class="f6e-kv-v">${val}</div>`;
    return `<div class="f6e-tip-title"><span class="f6e-tip-sw" style="background:${groupColor(c.group ?? 0, params.nMajor)}"></span>` +
      `${esc(abbreviate(c.species))}<span class="f6e-tip-dim"> · ${grp}${g ? ` (${g.n_species} species)` : ""}</span></div>` +
      `<div class="f6e-kv">` +
      row("Position", `${esc(anchor.chrom)}:${c.start.toLocaleString()}–${c.end.toLocaleString()}`) +
      row("Length", `${(c.end - c.start).toLocaleString()} bp`) +
      row("GPS", `peak <b>${c.gps_max.toFixed(1)}</b> · mean ${c.gps_mean.toFixed(1)}`) +
      row("From TSS", `${dist} ${side}`) +
      `</div>`;
  };
  cg.addEventListener("mousemove", (ev) => {
    const px = ev.clientX - svg.getBoundingClientRect().left;
    const bp = slice.startBp + ((px - callsX0) / callsW) * span;
    setGuides(bp, "calls");
    showTrackValues(Math.max(0, Math.min(n - 1, Math.floor((bp - slice.startBp) / slice.binBp))));
    const c = (ev.target as HitRect)._call;
    if (!c) { tip.hidden = true; setHoverRow(null); return; }
    setHoverRow(v.rowPos.get(c.row) ?? null);
    showTip(ev, callCard(c), true);
  });

  // tips: hover row + Wikipedia card, click toggles highlight
  let hoverSpecies: string | null = null;
  const speciesCard = (sp: string, w: Awaited<ReturnType<typeof wikiSummary>>) => {
    const hint = `<span class="f6e-hint">click to ${params.highlight.includes(sp) ? "un-highlight" : "highlight"}</span>`;
    const latin = `<i>${esc(sp.replaceAll("_", " "))}</i>`;
    if (!w) return `<b>${latin}</b><br>${hint}`;
    return `${w.thumb ? `<img class="f6e-tip-img" src="${w.thumb}" alt="">` : ""}` +
      `<div class="f6e-tip-title">${esc(w.title)}</div><div>${latin}${w.description ? ` · ${esc(w.description)}` : ""}</div>` +
      `${w.extract ? `<div class="f6e-tip-extract">${esc(shortExtract(w.extract))}</div>` : ""}` +
      `<div class="f6e-tip-foot">Wikipedia · ${hint}</div>`;
  };
  tips.addEventListener("mousemove", (ev) => {
    const t = ev.target as HitRect;
    if (t._species === undefined) return;
    const sp = t._species;
    setHoverRow(t._row ?? null);
    const same = hoverSpecies === sp;
    hoverSpecies = sp;
    if (!same) {
      showTip(ev, speciesCard(sp, null), true);   // name at once, card when the summary lands
      void wikiSummary(sp).then((w) => {
        if (hoverSpecies !== sp || tip.hidden) return;
        showTip(ev, speciesCard(sp, w), true);
        tip.querySelector("img")?.addEventListener("load", () => { if (hoverSpecies === sp) placeTip(ev, true); });
      });
    } else {
      placeTip(ev, true);   // keep following the pointer without re-rendering the card
    }
  });
  tips.addEventListener("mouseleave", () => { hoverSpecies = null; });
  tips.addEventListener("click", (ev) => { const sp = (ev.target as HitRect)._species; if (sp) cb.onTipClick?.(sp); });

  // genes: hover shows a MyGene.info card, click recentres
  const geneOf = (ev: Event) => ((ev.target as Element).closest?.(".f6e-gene") as GeneGroup | null)?._gene;
  let hoverGene: string | null = null;
  const geneCard = (g: Gene, info: Awaited<ReturnType<typeof geneInfo>>) => {
    const head = `<div class="f6e-tip-title"><i>${esc(g.name)}</i>${info?.name ? ` · ${esc(info.name)}` : ""}</div>`;
    const where = `${anchor.chrom}:${g.start.toLocaleString()}–${g.end.toLocaleString()} (${g.strand >= 0 ? "+" : "−"})` +
      `${info?.cytoband ? ` · ${esc(info.cytoband)}` : ""}${info?.type ? ` · ${esc(info.type)}` : ""}`;
    const summary = info?.summary ? `<div class="f6e-tip-extract">${esc(shortExtract(info.summary, 260))}</div>` : "";
    const aliases = info?.aliases.length ? `<div class="f6e-tip-extract">also: ${esc(info.aliases.slice(0, 6).join(", "))}</div>` : "";
    const ids = info ? [info.entrez && `NCBI Gene ${info.entrez}`, info.ensembl, info.hgnc && `HGNC:${info.hgnc}`].filter(Boolean).join(" · ") : "";
    const foot = `<div class="f6e-tip-foot">${ids ? `${esc(ids)} · MyGene.info · ` : ""}<span class="f6e-hint">click to centre on its TSS</span></div>`;
    return `${head}<div>${where}</div>${summary}${aliases}${foot}`;
  };
  gg.addEventListener("mousemove", (ev) => {
    const g = geneOf(ev);
    if (!g) { hideTip(); hoverGene = null; return; }
    const same = hoverGene === g.name;
    hoverGene = g.name;
    if (same) { placeTip(ev, true); return; }
    showTip(ev, geneCard(g, null), true);
    void geneInfo(g.name).then((info) => { if (hoverGene === g.name && !tip.hidden) showTip(ev, geneCard(g, info), true); });
  });
  gg.addEventListener("mouseleave", () => { hoverGene = null; });
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
    showTrackValues(bi);
    showTip(ev, `${anchor.chrom}:${Math.round(bp).toLocaleString()}<br>coverage ${(tracks.cov[bi] * 100).toFixed(0)}% · summed GPS ${tracks.sum[bi].toFixed(0)}<br><span class="f6e-hint">drag to zoom</span>`);
  });

  fig.addEventListener("dblclick", () => cb.onReset?.());
  for (const e of [canvas, cg, tips, gg, brushArea]) e.addEventListener("mouseleave", hideTip);

  root.replaceChildren(fig);

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
