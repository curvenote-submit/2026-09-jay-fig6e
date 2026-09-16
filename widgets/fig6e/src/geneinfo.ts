// Gene hover cards from MyGene.info (no key, CORS). One query by symbol
// returns the HGNC name, gene type, cytoband, aliases, cross-references and —
// when NCBI has written one — the RefSeq summary paragraph. Cached per symbol
// for the life of the widget; a network failure is not cached so a later
// hover can retry.

export interface GeneInfo {
  symbol: string;
  name: string;
  type: string;            // protein-coding, ncRNA, pseudo, ...
  cytoband: string | null;
  summary: string | null;  // RefSeq summary, may be absent (most non-coding genes)
  aliases: string[];
  entrez: string | null;
  ensembl: string | null;
  hgnc: string | null;
}

const API = "https://mygene.info/v3/query";
const cache = new Map<string, Promise<GeneInfo | null>>();

interface Hit {
  symbol?: string; name?: string; type_of_gene?: string; map_location?: string; summary?: string;
  alias?: string | string[]; entrezgene?: string | number; HGNC?: string;
  ensembl?: { gene: string } | { gene: string }[];
}

export function geneInfo(symbol: string): Promise<GeneInfo | null> {
  const key = symbol.toUpperCase();
  let p = cache.get(key);
  if (!p) {
    p = (async () => {
      const q = new URLSearchParams({
        q: `symbol:${key}`, species: "human", size: "1",
        fields: "symbol,name,summary,alias,entrezgene,ensembl.gene,type_of_gene,map_location,HGNC",
      });
      const r = await fetch(`${API}?${q}`, { headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(`mygene ${r.status}`);
      const d = (await r.json()) as { hits?: Hit[] };
      const h = d.hits?.[0];
      if (!h || !h.symbol) return null;
      const ens = Array.isArray(h.ensembl) ? h.ensembl[0]?.gene : h.ensembl?.gene;
      return {
        symbol: h.symbol,
        name: h.name ?? "",
        type: (h.type_of_gene ?? "").replace(/-/g, "-"),
        cytoband: h.map_location ?? null,
        summary: h.summary ?? null,
        aliases: Array.isArray(h.alias) ? h.alias : h.alias ? [h.alias] : [],
        entrez: h.entrezgene != null ? String(h.entrezgene) : null,
        ensembl: ens ?? null,
        hgnc: h.HGNC ?? null,
      };
    })();
    cache.set(key, p);
    p.catch(() => cache.delete(key));   // do not remember failures
  }
  return p.catch(() => null);
}
