// Imperative core of the widget: owns the view (genomic window + row zoom),
// streams data, runs the compute, draws the figure into a host element, and
// handles the figure's mouse interactions. The control shell (index.ts) renders
// the controls and status tiles around it and talks to it through setParams()
// and two callbacks; the shell never needs to know about pan ticks.

import { DataSource } from "./data";
import { buildTree } from "./tree";
import { callEnhancers, assignSyntenyGroups, coverage, columnTracks } from "./compute";
import { drawFigure, fmtBp, type FigureHandle } from "./draw";
import type { Call, Gene, Params, Slice, Status, StoreMeta, View } from "./types";

export const MIN_SPAN = 2_000, MAX_SPAN = 4_000_000;   // bp; 4 Mb = ~20 chunks, ~10 MB decoded

/** Model keys the figure can change from inside (widget -> host). */
export interface ChangePatch {
  start?: number; end?: number;
  highlight?: string[];
  selected_group?: number | null;
  show_gaps?: boolean;
  row_px?: number | null;
  gene?: string; pos?: number;
}

export interface ControllerCallbacks {
  onStatus(status: Status): void;
  onChange(patch: ChangePatch): void;
  onMeta?(meta: StoreMeta): void;
}

export class Controller {
  p: Params | null = null;
  meta: StoreMeta | null = null;
  tree: string | null = null;
  genes: Gene[] = [];
  slice: Slice | null = null;
  view: View | null = null;
  rowOff = 0;
  figure: FigureHandle | null = null;
  calls: Call[] = [];

  private drag: { x: number; y: number; moved: boolean } | null = null;
  private brush: { x0: number; x1: number } | null = null;
  private panPending: number | null = null;
  private panRaf = 0;
  private redrawRaf = 0;
  private seq = 0;
  private ro: ResizeObserver;
  private readonly _onMove = (ev: MouseEvent) => this.onWinMove(ev);
  private readonly _onUp = () => this.onWinUp();

  constructor(readonly ds: DataSource, readonly host: HTMLElement, readonly cb: ControllerCallbacks) {
    window.addEventListener("mousemove", this._onMove);
    window.addEventListener("mouseup", this._onUp);
    this.ro = new ResizeObserver(() => { if (this.slice) this.scheduleRedraw(); });
    this.ro.observe(host);
  }

  destroy(): void {
    this.ro.disconnect();
    window.removeEventListener("mousemove", this._onMove);
    window.removeEventListener("mouseup", this._onUp);
    cancelAnimationFrame(this.panRaf); cancelAnimationFrame(this.redrawRaf);
    this.host.replaceChildren();
  }

  // --- params -----------------------------------------------------------------
  /** Apply a new parameter set; decides between reload, refetch and redraw. */
  setParams(p: Params): void {
    const prev = this.p; this.p = { ...p };
    if (!prev) { void this.load(p.start != null && p.end != null ? { startBp: +p.start, endBp: +p.end } : null); return; }
    if (prev.chrom !== p.chrom || prev.pos !== p.pos || prev.windowKb !== p.windowKb) { void this.load(null); return; }
    if (prev.cellType !== p.cellType) { void this.setView(this.view ?? this.anchorView()); return; }
    if (p.start != null && p.end != null && (p.start !== prev.start || p.end !== prev.end)
        && this.view && (p.start !== this.view.startBp || p.end !== this.view.endBp)) { void this.setView({ startBp: +p.start, endBp: +p.end }); return; }
    if (prev.rowPx !== p.rowPx && p.rowPx == null) this.rowOff = 0;
    this.scheduleRedraw();
  }

  /** Back to anchor ± window at the fit-all row height. */
  reset(): void {
    this.rowOff = 0;
    this.cb.onChange({ row_px: null });
    void this.setView(this.anchorView());
  }

  anchorView(): View {
    const w = this.p!.windowKb * 1000;
    return { startBp: this.p!.pos - w, endBp: this.p!.pos + w };
  }

  // --- data -------------------------------------------------------------------
  async load(view: View | null): Promise<void> {
    this.cb.onStatus({ loading: true, error: null });
    try {
      if (!this.meta) {
        this.meta = await this.ds.meta();
        this.tree = await this.ds.tree();
        this.cb.onMeta?.(this.meta);
      }
      this.genes = await this.ds.genes(this.p!.chrom);
      await this.setView(view ?? this.anchorView());
    } catch (e) {
      console.error(e);
      this.cb.onStatus({ loading: false, error: (e as Error).message });
    }
  }

  clampView(v: View): View {
    const chromLen = this.meta?.chrom_sizes[this.p!.chrom] ?? Infinity;
    let s = v.startBp, e = v.endBp;
    const span = e - s;
    if (span < MIN_SPAN) { const c = (s + e) / 2; s = c - MIN_SPAN / 2; e = c + MIN_SPAN / 2; }
    if (span > MAX_SPAN) { const c = (s + e) / 2; s = c - MAX_SPAN / 2; e = c + MAX_SPAN / 2; }
    if (s < 0) { e -= s; s = 0; }
    if (e > chromLen) { s = Math.max(0, s - (e - chromLen)); e = chromLen; }
    return { startBp: Math.round(s), endBp: Math.round(e) };
  }

  /** Set the view, stream in the chunks it needs, redraw. `transient` skips the model sync. */
  async setView(v: View, transient = false): Promise<void> {
    this.view = this.clampView(v);
    if (!transient) this.cb.onChange({ start: this.view.startBp, end: this.view.endBp });
    const seq = ++this.seq;
    try {
      const sl = await this.ds.range(this.p!.cellType, this.p!.chrom, this.view.startBp, this.view.endBp);
      if (seq !== this.seq) return;
      this.slice = sl;
      this.redraw();
    } catch (e) {
      console.error(e);
      this.cb.onStatus({ loading: false, error: (e as Error).message });
    }
  }

  async lookupGene(sym: string): Promise<[string, number, string] | null> {
    const tss = await this.ds.geneTss();
    return tss[sym.trim().toUpperCase()] ?? null;
  }

  async geneSymbols(): Promise<string[]> {
    const tss = await this.ds.geneTss();
    const chroms = this.meta ? new Set(Object.keys(this.meta.chrom_offsets)) : null;
    return Object.keys(tss).filter((s) => !chroms || chroms.has(tss[s][0]));
  }

  // --- compute + draw ----------------------------------------------------------
  scheduleRedraw(): void {
    if (!this.redrawRaf) this.redrawRaf = requestAnimationFrame(() => { this.redrawRaf = 0; this.redraw(); });
  }

  redraw(): void {
    if (!this.slice || !this.p) return;
    const p = this.p, { rows, species, startBp, binBp } = this.slice;
    // strand of the anchor gene, if a gene model's TSS sits exactly at the anchor
    const anchorGene = this.genes.find((g) => (g.strand >= 0 ? g.start : g.end) === +p.pos);
    const anchor = { chrom: p.chrom, pos: +p.pos, strand: anchorGene ? (anchorGene.strand >= 0 ? "+" as const : "-" as const) : null };

    const kept: number[] = [];
    rows.forEach((r, i) => { if (coverage(r) >= p.minCov) kept.push(i); });
    const tree = buildTree(this.tree!, kept.map((i) => species[i]));
    const idx = new Map(species.map((s, i) => [s, i]));
    const order = tree.order.map((s) => idx.get(s)!);
    const rowPos = new Map(order.map((ri, r) => [ri, r]));

    const kRows = kept.map((i) => rows[i]), kNames = kept.map((i) => species[i]);
    const calls = callEnhancers(kRows, kNames, { binBp, startBp, anchorPos: anchor.pos, threshold: p.threshold });
    calls.forEach((c) => { c.row = idx.get(c.species)!; });
    const summary = assignSyntenyGroups(calls, p.nMajor);
    const groupSpans = new Map(summary.map((s) => [s.group, s]));
    const selectedGroup = p.selectedGroup != null && groupSpans.has(p.selectedGroup) ? p.selectedGroup : null;
    const tracks = columnTracks(kRows);
    this.calls = calls;

    const inMajor = calls.filter((c) => (c.group ?? 0) > 0).length;
    const sl = this.slice;
    this.cb.onStatus({
      loading: false, error: null,
      species: kept.length, totalSpecies: species.length, enhancers: calls.length,
      pctMajor: calls.length ? Math.round((100 * inMajor) / calls.length) : null,
      perSpecies: kept.length ? calls.length / kept.length : null,
      window: fmtBp(sl.endBp - sl.startBp), bins: rows[0].length,
      fetch: sl.chunksFetched ? `${sl.chunksFetched} chunk${sl.chunksFetched > 1 ? "s" : ""} in ${Math.round(sl.fetchMs)} ms` : "cached",
      calls: calls.map(({ row: _row, ...c }) => c),
    });

    this.figure = drawFigure(this.host, {
      slice: sl, order, rowPos, tree, calls, tracks, anchor, summary, groupSpans, selectedGroup,
      genes: this.genes,
      params: { threshold: p.threshold, nMajor: p.nMajor, minCov: p.minCov, highlight: p.highlight ?? [], showGaps: !!p.showGaps },
      rowPx: p.rowPx ?? null, rowOffset: this.rowOff,
    }, {
      onTipClick: (sp) => {
        const hl = [...(p.highlight ?? [])], i = hl.indexOf(sp);
        if (i >= 0) hl.splice(i, 1); else hl.push(sp);
        this.cb.onChange({ highlight: hl });
      },
      onSelectGroup: (g) => this.cb.onChange({ selected_group: g }),
      onToggleGaps: () => this.cb.onChange({ show_gaps: !p.showGaps }),
      onBrushStart: (clientX) => { this.brush = { x0: clientX, x1: clientX }; },
      onPanStart: (clientX, clientY) => { this.drag = { x: clientX, y: clientY, moved: false }; this.host.classList.add("f6e-panning"); },
      onZoom: (factor, centreBp) => {
        const v = this.view!, s = (v.endBp - v.startBp) * factor, f = (centreBp - v.startBp) / (v.endBp - v.startBp);
        void this.setView({ startBp: centreBp - f * s, endBp: centreBp + (1 - f) * s });
      },
      onRowZoom: (factor, row, cursorY) => {
        const g = this.figure?.geom; if (!g) return;
        // floor at the fit-all height: zooming out never leaves the tree shorter than the figure
        const newPx = Math.min(24, Math.max(g.autoRowH, g.rowH * factor));
        const frac = (cursorY - (row * g.rowH - g.off)) / g.rowH;
        this.rowOff = Math.max(0, Math.min((row + frac) * newPx - cursorY, Math.max(0, newPx * g.nRows - g.heatH)));
        this.cb.onChange({ row_px: Math.abs(newPx - g.autoRowH) < 0.05 ? null : newPx });
      },
      onRowScroll: (dy) => {
        const g = this.figure?.geom; if (!g || g.maxOff <= 0) return false;
        const next = Math.max(0, Math.min(this.rowOff + dy, g.maxOff));
        if (next === this.rowOff) return false;
        this.rowOff = next; this.scheduleRedraw(); return true;
      },
      onReset: () => this.reset(),
      onGeneClick: (g) => this.cb.onChange({ gene: g.name, pos: g.strand >= 0 ? g.start : g.end }),
    });
  }

  // --- window-level drag handling (outlives per-tick redraws) -------------------
  private onWinMove(ev: MouseEvent): void {
    const f = this.figure; if (!f) return;
    if (this.drag) {
      const dx = ev.clientX - this.drag.x, dy = ev.clientY - this.drag.y;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      this.drag.x = ev.clientX; this.drag.y = ev.clientY; this.drag.moved = true;
      if (f.geom.maxOff > 0 && dy) this.rowOff = Math.max(0, Math.min(this.rowOff - dy, f.geom.maxOff));
      if (dx) this.panPending = (this.panPending ?? 0) - (dx / f.geom.heatW) * f.geom.span;
      if (!this.panRaf) this.panRaf = requestAnimationFrame(() => {
        this.panRaf = 0;
        const d = this.panPending; this.panPending = null;
        if (d && this.view) void this.setView({ startBp: this.view.startBp + d, endBp: this.view.endBp + d }, true);
        else this.redraw();
      });
    } else if (this.brush) {
      this.brush.x1 = ev.clientX;
      f.setBrush(this.brush.x0, this.brush.x1);
    }
  }

  private onWinUp(): void {
    const f = this.figure;
    if (this.drag) {
      const moved = this.drag.moved; this.drag = null;
      this.host.classList.remove("f6e-panning");
      if (moved && this.view) void this.setView(this.view);
    } else if (this.brush) {
      const b = this.brush; this.brush = null;
      if (!f) return;
      const [b0, b1, px] = f.setBrush(b.x0, b.x1);
      f.clearBrush();
      if (px >= 4) void this.setView({ startBp: b0, endBp: b1 });
    }
  }
}

export { DataSource };
