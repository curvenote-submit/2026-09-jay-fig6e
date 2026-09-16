// Parity: JS enhancer calls + synteny groups == Python (synteny.py) on the same
// zarr slice. Golden files are produced by steam-fig6e-explorer/export_golden.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import * as zarr from "zarrita";
import { FileSystemStore } from "@zarrita/storage";
import { callEnhancers, assignSyntenyGroups } from "../src/compute.ts";

const here = dirname(fileURLToPath(import.meta.url));
const STORE = process.env.STORE || join(process.env.DATA_DIR || join(here, "../../../data"), "steam_v1_gps.zarr");

for (const f of readdirSync(here).filter((f) => f.startsWith("golden_") && f.endsWith(".json"))) {
  test(f, async () => {
    const g = JSON.parse(readFileSync(join(here, f), "utf8"));
    const p = g.params;
    const meta = JSON.parse(readFileSync(join(STORE, "meta.json"), "utf8"));
    const store = new FileSystemStore(STORE);
    const arr = await zarr.open.v3(zarr.root(store).resolve(`hg38/${p.cell_type}`), { kind: "array" });
    const off = meta.chrom_offsets[p.chrom], binBp = meta.bin_bp;
    const b0 = p.start_bp / binBp, b1 = p.end_bp / binBp, n = b1 - b0;
    const chunk = await zarr.get(arr, [null, zarr.slice(off + b0, off + b1)]);
    const rows = meta.species.map((_, i) => chunk.data.subarray(i * n, (i + 1) * n));

    const calls = callEnhancers(rows, meta.species, { binBp, startBp: p.start_bp, anchorPos: p.pos, threshold: p.threshold });
    const summary = assignSyntenyGroups(calls, p.n_major);

    assert.equal(calls.length, g.calls.length, "number of calls");
    calls.forEach((c, i) => {
      const e = g.calls[i];
      for (const k of ["species", "start", "end", "bin_start", "bin_end", "dist_to_tss", "group"]) assert.equal(c[k], e[k], `${k} of call ${i}`);
      assert.ok(Math.abs(c.gps_max - e.gps_max) < 1e-9, `gps_max of call ${i}`);
      assert.ok(Math.abs(c.gps_mean - e.gps_mean) < 1e-9, `gps_mean of call ${i}`);
    });
    assert.equal(summary.length, g.summary.length, "number of major groups");
    summary.forEach((s, i) => {
      const e = g.summary[i];
      for (const k of ["group", "n_enhancers", "n_species", "start", "end"]) assert.equal(s[k], e[k], `${k} of group ${i}`);
      assert.ok(Math.abs(s.mean_dist_to_tss - e.mean_dist_to_tss) < 1e-6, `mean_dist of group ${i}`);
      assert.ok(Math.abs(s.mean_gps - e.mean_gps) < 1e-9, `mean_gps of group ${i}`);
    });
  });
}
