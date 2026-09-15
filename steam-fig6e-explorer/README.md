# STEAM-v1 Fig 6e explorer

An interactive version of **Figure 6e** from Qiu, Daza, Welsh *et al.*,
*Evolutionary transfer learning enables organism-wide inference of mammalian
enhancer landscapes*.

Pick a locus and a cell class; it pulls STEAM-v1 predicted-accessibility tracks
for 241 Zoonomia mammals, orders them by the Zoonomia phylogeny, calls enhancers
at the paper's GPS threshold, groups them by synteny, and draws the three-panel
figure: **tree | called enhancers coloured by synteny group | accessibility
heatmap**.

---

## Running it

**macOS or Linux**, Python 3.10 or newer. In Terminal:

```bash
cd steam-fig6e-explorer
./run.sh
```

That makes a private virtualenv inside the folder, installs what it needs
(~1–2 minutes the first time), and opens the app in your browser. Re-running is
instant. Press **Ctrl+C** in the terminal to stop it.

If `./run.sh` says "permission denied", run `chmod +x run.sh` first.
If you keep Python somewhere unusual: `PYTHON=/path/to/python3 ./run.sh`.

**Windows** is not supported — `pyBigWig`, which reads the remote data files,
has no Windows build. WSL works.

---

## Using it

1. Type a gene symbol in the sidebar (it searches ~28,000 hg38 genes), or switch
   to **Coordinate** and enter an hg38 position.
2. Choose a cell class and a window size.
3. Click **Generate figure**.

It opens on *AFP* / Hepatocytes / ±100 kb — the paper's Fig 6e locus.

A new locus takes **~7 seconds**; anything you have looked at before comes back
in about 2 seconds, and that cache survives restarts. The status numbers across
the top compare what you are looking at against the published panel.

You can download the figure as PNG, PDF or SVG, download the enhancer calls as
a TSV, and open the same window in the UCSC browser.

**You need to be online** — the per-species tracks stream from
`shendure-web.gs.washington.edu` on demand. Nothing else is downloaded.

---

## What the controls do

| Control | What it does |
|---|---|
| **GPS threshold** | Enhancer-calling cutoff. The paper uses 24.5, calibrated on the mouse genome-wide scale. Slide it to see how sensitive the picture is. |
| **Synteny groups to colour** | Fig 6d colours the 11 largest groups; the rest are grey. |
| **Min hg38 coverage per species** | Drops species with patchy alignment at this locus. |
| **Species to fetch** | Fewer is faster, but all 241 only takes a few seconds. |
| **Highlight species** | Underscored names, comma-separated, e.g. `Canis_lupus_familiaris`. Shown in red. |

---

## A caveat worth knowing

This is drawn in **hg38 coordinates** — every species projected onto one shared
human axis — whereas the published Fig 6e works in each species' own
coordinates. Positions therefore line up directly across rows here, which the
published panel does not give you, but the synteny group numbering will not
match the paper's clusters 1–11. The in-app "How this compares to the published
panel" section spells out the differences.

---

## If something goes wrong

- **"could not create a virtualenv"** — on Debian/Ubuntu: `sudo apt install python3-venv`.
- **pyBigWig fails to install** — you need a compiler and zlib headers.
  macOS: `xcode-select --install`. Ubuntu: `sudo apt install build-essential zlib1g-dev libcurl4-openssl-dev`.
- **Everything is slow or empty** — check you can reach
  <https://shendure-web.gs.washington.edu/content/members/cxqiu/public/nobackup/>
  in a browser. On a VPN or a locked-down network this may be blocked.
- **Want to reclaim disk** — delete the `cache/` folder; it only holds fetched
  results and will refill itself.
