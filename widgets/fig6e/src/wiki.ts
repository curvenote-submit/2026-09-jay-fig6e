// Species hover cards from the Wikipedia REST summary API. The Zoonomia names
// are Latin binomials (Canis_lupus_familiaris); Wikipedia redirects those to
// the common-name article (Dog) and the summary endpoint follows the redirect.
// Successful lookups (including genuine misses) are cached per species for the
// life of the widget; network failures are not, so a later hover retries.

export interface WikiSummary {
  title: string;           // common name, e.g. "Giant panda"
  description: string;     // short description line
  extract: string;         // first paragraph
  thumb: string | null;    // thumbnail URL (~320 px wide)
  url: string;             // article URL
}

const API = "https://en.wikipedia.org/api/rest_v1/page/summary/";
const cache = new Map<string, Promise<WikiSummary | null>>();

interface RestSummary {
  type?: string; title?: string; description?: string; extract?: string;
  thumbnail?: { source: string; width: number; height: number };
  content_urls?: { desktop?: { page?: string } };
}

export function wikiSummary(species: string): Promise<WikiSummary | null> {
  let p = cache.get(species);
  if (!p) {
    p = (async () => {
        const r = await fetch(API + encodeURIComponent(species), { headers: { Accept: "application/json" } });
        if (!r.ok) return null;
        const d = (await r.json()) as RestSummary;
        if (d.type === "disambiguation" || !d.title) return null;
        return {
          title: d.title,
          description: d.description ?? "",
          extract: d.extract ?? "",
          thumb: d.thumbnail?.source ?? null,
          url: d.content_urls?.desktop?.page ?? `https://en.wikipedia.org/wiki/${encodeURIComponent(species)}`,
        };
    })();
    cache.set(species, p);
    p.catch(() => cache.delete(species));   // network failure: let a later hover retry
  }
  return p.catch(() => null);
}

/** First ~n characters of the extract, cut at a sentence boundary when possible. */
export function shortExtract(text: string, n = 220): string {
  if (text.length <= n) return text;
  const cut = text.slice(0, n);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("; "));
  return (end > n * 0.5 ? cut.slice(0, end + 1) : cut.replace(/\s+\S*$/, "") + "…");
}
