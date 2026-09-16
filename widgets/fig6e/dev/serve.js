// Dev server: serves this widget directory at / and ../../data at /data with
// CORS + range support, so dev/index.html and a MyST site on another port can
// both read the zarr store. Usage: node dev/serve.js [port]
import { createServer } from "node:http";
import { stat, readFile } from "node:fs/promises";
import { extname, join, resolve, normalize } from "node:path";

const PORT = Number(process.argv[2] || process.env.PORT || 3000);
const ROOT = resolve(import.meta.dirname, "..");
const DATA = resolve(process.env.DATA_DIR || resolve(ROOT, "../../data"));
const MIME = { ".html": "text/html", ".js": "application/javascript", ".mjs": "application/javascript",
  ".css": "text/css", ".json": "application/json", ".nwk": "text/plain", ".map": "application/json" };

createServer(async (req, res) => {
  const url = decodeURIComponent(req.url.split("?")[0]);
  const path = url === "/" ? join(ROOT, "dev/index.html")
    : url.startsWith("/data/") ? join(DATA, normalize(url.slice(6)))
    : join(ROOT, normalize(url));
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "*");
  res.setHeader("Accept-Ranges", "bytes");
  if (req.method === "OPTIONS") { res.writeHead(204); return res.end(); }
  try {
    const s = await stat(path);
    if (!s.isFile()) throw new Error("dir");
    const buf = await readFile(path);
    const type = MIME[extname(path)] || "application/octet-stream";
    const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range || "");
    if (range) {
      const a = range[1] ? +range[1] : 0, b = range[2] ? Math.min(+range[2], buf.length - 1) : buf.length - 1;
      res.writeHead(206, { "Content-Type": type, "Content-Range": `bytes ${a}-${b}/${buf.length}`, "Content-Length": b - a + 1 });
      return res.end(buf.subarray(a, b + 1));
    }
    res.writeHead(200, { "Content-Type": type, "Content-Length": buf.length });
    res.end(buf);
  } catch {
    res.writeHead(404); res.end("Not found: " + url);
  }
}).listen(PORT, () => console.log(`fig6e dev server: http://localhost:${PORT}  (data at /data)`));
