// Data access: the GPS zarr store plus the small sidecars next to it.
//
//   <base>/steam_v1_gps.zarr/hg38/<cell_type>   uint8 (species, genome bins)
//   <base>/steam_v1_gps.zarr/meta.json          chrom_offsets, species, bin_bp ...
//   <base>/tree.nwk, <base>/gene_tss.json, <base>/genes/<chrom>.json
//
// Chunks are read one at a time and kept in a small LRU, so panning along a
// chromosome only fetches the chunks that newly scroll into view.

import * as zarr from "zarrita";
import type { Gene, Slice, StoreMeta } from "./types";

type ZArray = zarr.Array<"uint8", zarr.FetchStore>;
type GeneTss = Record<string, [string, number, string]>;

const LRU_CHUNKS = 48; // × ~500 KB decoded = ~24 MB

/** A data-loading failure with enough context for a useful message. */
export class DataError extends Error {
  constructor(readonly kind: "cors" | "http" | "network" | "format", readonly url: string, readonly status: number | null, message: string) {
    super(message);
    this.name = "DataError";
  }
}

/**
 * fetch() with diagnosis. A bare TypeError from fetch on a cross-origin URL is
 * almost always CORS (the browser hides the real status); same-origin it is a
 * network / server problem.
 */
async function fetchOrExplain(url: string, init?: RequestInit): Promise<Response> {
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch (e) {
    const crossOrigin = (() => { try { return new URL(url).origin !== location.origin; } catch { return false; } })();
    if (crossOrigin) throw new DataError("cors", url, null,
      `The browser refused to read the data store at ${new URL(url).origin}. Most likely the bucket has no CORS policy (it must send Access-Control-Allow-Origin for GET/HEAD).`);
    throw new DataError("network", url, null, `Could not reach the data store (${(e as Error).message}). Is the data server running?`);
  }
  if (!r.ok) {
    const what = r.status === 404 ? "not found" : r.status === 403 ? "forbidden (is the object public?)" : `HTTP ${r.status}`;
    throw new DataError("http", url, r.status, `${url.replace(/^.*\/(steam_v1_gps\.zarr|tree\.nwk|gene_tss\.json|genes)/, "$1")}: ${what}.`);
  }
  return r;
}

export class DataSource {
  base: string;
  private _meta: StoreMeta | null;
  private _arrays: Map<string, ZArray>;
  private _tree: string | null;
  private _tss: GeneTss | null;
  private _genes: Map<string, Gene[]>;
  private _chunks: Map<string, Uint8Array | Promise<Uint8Array>>;
  private _store: zarr.FetchStore;

  constructor(base: string) {
    // FetchStore needs an absolute URL; resolve relative bases against the page.
    this.base = new URL(base.replace(/\/$/, ""), globalThis.document?.baseURI ?? "http://localhost/").href;
    this._meta = null;
    this._arrays = new Map();
    this._tree = null;
    this._tss = null;
    this._genes = new Map();
    this._chunks = new Map();     // "cellType/chunkIndex" -> Uint8Array | Promise
    this._store = new zarr.FetchStore(`${this.base}/steam_v1_gps.zarr`);
  }

  async meta(): Promise<StoreMeta> {
    if (!this._meta) {
      const url = `${this.base}/steam_v1_gps.zarr/meta.json`;
      const r = await fetchOrExplain(url);
      let m: StoreMeta;
      try { m = (await r.json()) as StoreMeta; } catch { throw new DataError("format", url, r.status, "meta.json is not valid JSON — is data_url pointing at the folder that contains steam_v1_gps.zarr/?"); }
      if (!m.chrom_offsets || !m.species) throw new DataError("format", url, r.status, "meta.json is missing chrom_offsets / species — not a STEAM GPS store.");
      this._meta = m;
    }
    return this._meta;
  }

  async tree(): Promise<string> {
    if (!this._tree) this._tree = await (await fetchOrExplain(`${this.base}/tree.nwk`)).text();
    return this._tree;
  }

  /** {SYMBOL: [chrom, tss, strand]} — ~900 KB, loaded on first gene lookup. */
  async geneTss(): Promise<GeneTss> {
    if (!this._tss) this._tss = (await (await fetchOrExplain(`${this.base}/gene_tss.json`)).json()) as GeneTss;
    return this._tss;
  }

  /** Gene models for one chromosome, sorted by start. */
  async genes(chrom: string): Promise<Gene[]> {
    if (!this._genes.has(chrom)) {
      const r = await fetch(`${this.base}/genes/${chrom}.json`);
      const g: Gene[] = r.ok ? await r.json() : [];
      g.sort((a, b) => a.start - b.start);
      this._genes.set(chrom, g);
    }
    return this._genes.get(chrom)!;
  }

  async array(cellType: string): Promise<ZArray> {
    if (!this._arrays.has(cellType)) {
      const arr = await zarr.open.v3(zarr.root(this._store).resolve(`hg38/${cellType}`), { kind: "array" });
      this._arrays.set(cellType, arr as ZArray);
    }
    return this._arrays.get(cellType)!;
  }

  /** One chunk (all species × chunkBins) as a flat Uint8Array, via the LRU. */
  async chunk(cellType: string, ci: number): Promise<Uint8Array> {
    const key = `${cellType}/${ci}`;
    if (this._chunks.has(key)) {
      const v = this._chunks.get(key)!;
      this._chunks.delete(key); this._chunks.set(key, v); // refresh LRU order
      return v;
    }
    const p = (async () => {
      const arr = await this.array(cellType);
      const cb = arr.chunks[1];
      const out = await zarr.get(arr, [null, zarr.slice(ci * cb, Math.min((ci + 1) * cb, arr.shape[1]))]);
      return out.data as Uint8Array;
    })();
    this._chunks.set(key, p);
    try {
      const data = await p;
      this._chunks.set(key, data);
      while (this._chunks.size > LRU_CHUNKS) this._chunks.delete(this._chunks.keys().next().value!);
      return data;
    } catch (e) {
      this._chunks.delete(key);
      throw e;
    }
  }

  /**
   * GPS rows for [startBp, endBp) on a chromosome, assembled from cached chunks.
   * Returns {rows: Uint8Array[], species, startBp, endBp, binBp, fetchMs, chunksFetched}.
   * The range is clipped to the chromosome and snapped to whole bins.
   */
  async range(cellType: string, chrom: string, startBp: number, endBp: number): Promise<Slice> {
    const meta = await this.meta();
    const off = meta.chrom_offsets[chrom];
    if (off === undefined) throw new Error(`unknown chromosome ${chrom}`);
    const binBp = meta.bin_bp;
    const chromBins = Math.floor(meta.chrom_sizes[chrom] / binBp);
    const b0 = Math.max(0, Math.floor(startBp / binBp));
    const b1 = Math.min(chromBins, Math.ceil(endBp / binBp));
    const n = Math.max(0, b1 - b0);
    const arr = await this.array(cellType);
    const cb = arr.chunks[1], nSp = meta.species.length;
    const g0 = off + b0, g1 = off + b1;
    const c0 = Math.floor(g0 / cb), c1 = Math.floor((g1 - 1) / cb);
    const t0 = performance.now();
    const before = this._chunks.size;
    const keys: number[] = [];
    for (let ci = c0; ci <= c1; ci++) keys.push(ci);
    const missing = keys.filter((ci) => !(this._chunks.get(`${cellType}/${ci}`) instanceof Uint8Array)).length;
    const chunks = await Promise.all(keys.map((ci) => this.chunk(cellType, ci)));
    const flat = new Uint8Array(nSp * n);
    chunks.forEach((data, k) => {
      const ci = keys[k];
      const cs = ci * cb, w = Math.min(cb, arr.shape[1] - cs);      // this chunk's global bin range
      const lo = Math.max(g0, cs), hi = Math.min(g1, cs + w);
      if (hi <= lo) return;
      for (let s = 0; s < nSp; s++) {
        flat.set(data.subarray(s * w + (lo - cs), s * w + (hi - cs)), s * n + (lo - g0));
      }
    });
    const rows = meta.species.map((_, i) => flat.subarray(i * n, (i + 1) * n));
    return { rows, species: meta.species, startBp: b0 * binBp, endBp: b1 * binBp, binBp,
             fetchMs: performance.now() - t0, chunksFetched: missing, chunksCached: before };
  }

  /** Legacy helper: anchor ± window. */
  slice(cellType: string, chrom: string, pos: number, windowKb: number): Promise<Slice> {
    const w = windowKb * 1000;
    return this.range(cellType, chrom, pos - w, pos + w);
  }
}
