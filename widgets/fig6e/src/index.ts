// anywidget front-end module (AFM) entry: vanilla-DOM control shell around the
// imperative figure in controller.ts.
//
// Model keys (all JSON): data_url, gene, chrom, pos, cell_type, window_kb,
// threshold, n_major, min_cov, highlight, selected_group, show_gaps, row_px,
// start / end (the current view), calls (widget -> host).
// Works with no kernel: every control lives inside the widget and
// save_changes() is best-effort (MyST's static host throws on it).

import { Controller, DataSource, type ChangePatch } from "./controller";
import { GPS_THRESHOLD, N_MAJOR_GROUPS } from "./compute";
import type { AnyModel, Params, Status, StoreMeta } from "./types";
import { ICON } from "./icons";

interface ModelKeys {
  data_url: string;
  gene: string; chrom: string; pos: number;
  cell_type: string; window_kb: number;
  threshold: number; n_major: number; min_cov: number;
  highlight: string[];
  selected_group: number | null;
  show_gaps: boolean;
  row_px: number | null;
  start: number | null; end: number | null;
}
const DEFAULTS: ModelKeys = {
  data_url: "./data",
  gene: "AFP", chrom: "chr4", pos: 73436220,
  cell_type: "Hepatocytes", window_kb: 100,
  threshold: GPS_THRESHOLD, n_major: N_MAJOR_GROUPS, min_cov: 0.5,
  highlight: ["Canis_lupus_familiaris", "Felis_catus"],
  selected_group: null,
  show_gaps: false,
  row_px: null,
  start: null, end: null,
};
type Key = keyof ModelKeys;

const WINDOWS = [25, 50, 100, 250, 500];

/** Small hyperscript helper. `on*` keys become listeners, `class` sets className. */
function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, unknown> = {}, ...kids: (Node | string)[]): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null) continue;
    if (k === "class") e.className = String(v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v as EventListener);
    else e.setAttribute(k, String(v));
  }
  e.append(...kids);
  return e;
}

export function render({ model, el }: { model: AnyModel; el: HTMLElement }): () => void {
  el.classList.add("f6e");

  // --- model access -----------------------------------------------------------
  const get = <K extends Key>(k: K): ModelKeys[K] => {
    const v = model.get(k);
    return (v === undefined || v === null ? DEFAULTS[k] : v) as ModelKeys[K];
  };
  // Writes from inside the widget are flagged so our own model.on listeners
  // (meant for host-driven changes) do not re-run the work a second time.
  let selfWrite = false;
  const set = (patch: Partial<ModelKeys>) => {
    selfWrite = true;
    try {
      for (const [k, v] of Object.entries(patch)) model.set(k, v);
      try { model.save_changes(); } catch { /* MyST's static host has no kernel */ }
    } finally { selfWrite = false; }
  };

  // --- DOM ----------------------------------------------------------------------
  const geneIn = h("input", { class: "f6e-geneinput", value: get("gene"), placeholder: "gene symbol", list: "f6e-genes", spellcheck: "false" });
  const geneList = h("datalist", { id: "f6e-genes" });
  const ctSel = h("select", { class: "f6e-select" });
  const winSel = h("select", { class: "f6e-select" }, ...WINDOWS.map((w) => h("option", { value: w }, `± ${w} kb`)));
  const thr = h("input", { type: "range", min: 10, max: 50, step: 0.5, value: get("threshold") });
  const thrV = h("span", { class: "f6e-val" }, get("threshold").toFixed(1));
  const nMaj = h("input", { type: "number", min: 1, max: 20, step: 1, value: get("n_major"), class: "f6e-num" });
  const cov = h("input", { type: "range", min: 0, max: 0.9, step: 0.05, value: get("min_cov") });
  const covV = h("span", { class: "f6e-val" }, get("min_cov").toFixed(2));
  const status = h("div", { class: "f6e-status" });
  const msg = h("div", { class: "f6e-msg" });
  const figure = h("div", { class: "f6e-figure" });
  const resetBtn = h("button", { type: "button", class: "f6e-reset", title: "Back to the anchor ± window at the default row height (or double-click the figure)" });
  resetBtn.innerHTML = ICON.reset;
  const ucsc = h("a", { class: "f6e-ucsc", target: "_blank", rel: "noopener", title: "Open the current window in the UCSC Genome Browser (hg38)" }, "UCSC ");
  ucsc.insertAdjacentHTML("beforeend", ICON.external);
  const updateUcsc = () => {
    const v = ctl.view;
    if (!v) return;
    ucsc.href = `https://genome.ucsc.edu/cgi-bin/hgTracks?db=hg38&position=${get("chrom")}:${v.startBp + 1}-${v.endBp}`;
  };

  // MyST's host puts its <link rel=stylesheet> inside `el` before render(), so append rather than replace.
  el.append(
    h("div", { class: "f6e-head" },
      h("div", { class: "f6e-title" }, "STEAM-v1 cross-species enhancer view"),
      h("div", { class: "f6e-sub" }, "Fig 6e: Zoonomia phylogeny · called enhancers by synteny group · predicted accessibility (GPS) per species × 100 bp bin")),
    h("div", { class: "f6e-controls" },
      h("label", {}, "Gene ", geneIn, geneList),
      h("label", {}, "Cell class ", ctSel),
      h("label", {}, "Window ", winSel),
      h("label", {}, "GPS ≥ ", thr, thrV),
      h("label", {}, "Groups ", nMaj),
      h("label", {}, "Min cov ", cov, covV),
      h("span", { class: "f6e-right" }, ucsc, resetBtn)),
    status, msg, figure,
    h("div", { class: "f6e-hint-bar" },
      "drag or scroll sideways on the heatmap to pan · drag on the tracks or pinch the heatmap to zoom · pinch the species column to resize rows, then scroll or drag vertically · Reset (or double-click) to go back · click a species, a gene, a synteny group, or the “no alignment” swatch"),
  );
  winSel.value = String(get("window_kb"));

  const tile = (value: string | number, label: string, sub: string) =>
    h("div", { class: "f6e-tile" }, h("div", { class: "f6e-tv" }, String(value)), h("div", { class: "f6e-tl" }, label), h("div", { class: "f6e-ts" }, sub));

  // --- controller ----------------------------------------------------------------
  let meta: StoreMeta | null = null;
  const ctl = new Controller(new DataSource(get("data_url")), figure, {
    onMeta: (m) => {
      meta = m;
      ctSel.replaceChildren(...m.cell_types.map((c) => h("option", { value: c }, c.replaceAll("_", " "))));
      ctSel.value = get("cell_type");
    },
    onStatus: (s: Status) => {
      msg.textContent = s.loading ? "loading…" : s.error ? `error: ${s.error}` : "";
      msg.className = s.error ? "f6e-msg f6e-err" : "f6e-msg";
      if (s.species === undefined) return;
      updateUcsc();
      status.replaceChildren(
        tile(s.species, "Species", `of ${s.totalSpecies} with coverage ≥ ${get("min_cov").toFixed(2)}`),
        tile(s.enhancers ?? 0, "Enhancers", `GPS ≥ ${get("threshold").toFixed(1)}`),
        tile(s.pctMajor != null ? `${s.pctMajor}%` : "—", "In major groups", `top ${get("n_major")} synteny groups`),
        tile(s.perSpecies != null ? s.perSpecies.toFixed(1) : "—", "Per species", "mean enhancers"),
        tile(s.window ?? "—", "Window", `${(s.bins ?? 0).toLocaleString()} bins · ${s.fetch ?? ""}`),
      );
      if (s.calls) { selfWrite = true; try { model.set("calls", s.calls); try { model.save_changes(); } catch { /* static host */ } } finally { selfWrite = false; } }
    },
    onChange: (p: ChangePatch) => {
      // the figure changed something: persist it and re-push params
      const patch: Partial<ModelKeys> = {};
      if (p.start !== undefined) patch.start = p.start;
      if (p.end !== undefined) patch.end = p.end;
      if (p.highlight) patch.highlight = p.highlight;
      if ("selected_group" in p) patch.selected_group = p.selected_group ?? null;
      if (p.show_gaps !== undefined) patch.show_gaps = p.show_gaps;
      if ("row_px" in p) patch.row_px = p.row_px ?? null;
      if (p.gene !== undefined) { patch.gene = p.gene; geneIn.value = p.gene; }
      if (p.pos !== undefined) { patch.pos = p.pos; patch.start = null; patch.end = null; }
      set(patch);
      push();
    },
  });

  const params = (): Params => ({
    chrom: get("chrom"), pos: +get("pos"), cellType: get("cell_type"), windowKb: +get("window_kb"),
    threshold: +get("threshold"), nMajor: +get("n_major"), minCov: +get("min_cov"),
    highlight: get("highlight"), selectedGroup: get("selected_group"), showGaps: get("show_gaps"), rowPx: get("row_px"),
    start: get("start"), end: get("end"),
  });
  const push = () => ctl.setParams(params());

  // --- controls ------------------------------------------------------------------
  const lookupGene = async () => {
    const sym = geneIn.value.trim().toUpperCase();
    if (!sym || sym === get("gene")) return;
    const hit = await ctl.lookupGene(sym);
    if (!hit) { msg.textContent = `unknown gene symbol: ${sym}`; msg.className = "f6e-msg f6e-err"; return; }
    if (meta && !(hit[0] in meta.chrom_offsets)) { msg.textContent = `${sym} is on ${hit[0]}, which is not in the store yet`; msg.className = "f6e-msg f6e-err"; return; }
    set({ gene: sym, chrom: hit[0], pos: hit[1], start: null, end: null });
    push();
  };
  geneIn.addEventListener("change", () => void lookupGene());
  geneIn.addEventListener("focus", () => {
    if (geneList.childElementCount) return;
    void ctl.geneSymbols().then((syms) => geneList.append(...syms.map((s) => h("option", { value: s }))));
  });
  resetBtn.addEventListener("click", () => ctl.reset());
  ctSel.addEventListener("change", () => { set({ cell_type: ctSel.value }); push(); });
  winSel.addEventListener("change", () => { set({ window_kb: +winSel.value, start: null, end: null }); push(); });
  thr.addEventListener("input", () => { thrV.textContent = (+thr.value).toFixed(1); set({ threshold: +thr.value }); push(); });
  nMaj.addEventListener("change", () => { const v = Math.max(1, Math.min(20, +nMaj.value || 1)); nMaj.value = String(v); set({ n_major: v }); push(); });
  cov.addEventListener("input", () => { covV.textContent = (+cov.value).toFixed(2); set({ min_cov: +cov.value }); push(); });

  // host-driven changes (JupyterLab): sync the controls, then re-push
  const HOST_KEYS: Key[] = ["gene", "chrom", "pos", "cell_type", "window_kb", "threshold", "n_major", "min_cov", "highlight", "selected_group", "show_gaps", "row_px", "start", "end"];
  const onHostChange = () => {
    if (selfWrite) return;
    geneIn.value = get("gene"); ctSel.value = get("cell_type"); winSel.value = String(get("window_kb"));
    thr.value = String(get("threshold")); thrV.textContent = get("threshold").toFixed(1);
    nMaj.value = String(get("n_major"));
    cov.value = String(get("min_cov")); covV.textContent = get("min_cov").toFixed(2);
    push();
  };
  for (const k of HOST_KEYS) model.on(`change:${k}`, onHostChange);

  push();

  return () => {
    ctl.destroy();
    for (const k of HOST_KEYS) model.off?.(`change:${k}`, onHostChange);
  };
}

export default { render };
