"""STEAM-v1 Fig 6e explorer.

Two views over the same renderer:

  * **Fig 6e (as published)** — the paper's panel: AFP / Hepatocytes / +-100 kb,
    enhancers called at the paper's GPS threshold and coloured by synteny group.
  * **Explore any locus** — the same three panels for any hg38 locus and any of
    the 32 cell classes.

Run locally:
    streamlit run app.py
"""
from __future__ import annotations

import io
import pathlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import streamlit as st

import fig6e_core as core
import fig6e_panel as panel
import diskcache as dc
import synteny as syn

# --- the published Fig 6e parameters ----------------------------------------
PAPER = dict(
    gene='AFP',
    chrom='chr4',
    tss=73_436_221,          # hg38 MANE TSS of AFP (ENST00000395792.7)
    strand='+',
    cell_type='Hepatocytes',
    window_kb=100.0,         # paper: +-100 kb
)
PAPER_URL = ('https://shendure.curve.space/articles/'
             'evolutionary-transfer-learning')

st.set_page_config(page_title='STEAM-v1 Fig 6e explorer', page_icon='🧬', layout='wide')

st.markdown(
    """
    <style>
    :root {
        --ink:#0f172a; --body:#334155; --muted:#64748b;
        --line:#e2e8f0; --surface:#ffffff; --surface-2:#f8fafc;
        --accent:#0d9488; --accent-dark:#0f766e;
    }
    html, body, .stApp, button, input, select, textarea {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                     "Helvetica Neue", Arial, sans-serif;
    }
    /* Streamlit draws chevrons and toggles as ligature glyphs in a Material icon
       font; the rule above catches them (they live inside buttons) and renders
       the ligature name as literal text. */
    [data-testid="stIconMaterial"], .material-icons, .material-icons-outlined,
    span[class*="material-symbols"], [data-testid="stExpanderToggleIcon"] {
        font-family: 'Material Symbols Rounded', 'Material Icons' !important;
    }
    .stApp { background: var(--surface-2); color: var(--body); }
    .block-container { padding-top: 2.5rem; max-width: 1180px; }

    h1 { font-size: 1.6rem !important; font-weight: 650 !important;
         color: var(--ink) !important; letter-spacing: -0.01em; }
    h2 { font-size: 1.15rem !important; font-weight: 620 !important;
         color: var(--ink) !important; }
    h3 { font-size: 1rem !important; font-weight: 600 !important;
         color: var(--ink) !important; }
    p, label, li { color: var(--body); }

    section[data-testid="stSidebar"] {
        background: var(--surface); border-right: 1px solid var(--line);
    }
    section[data-testid="stSidebar"] .block-container { padding-top: 1.5rem; }

    /* buttons: quiet by default, solid for the primary action */
    .stButton > button, .stDownloadButton > button, .stLinkButton > a {
        border: 1px solid var(--line) !important; border-radius: 8px !important;
        background: var(--surface) !important; color: var(--body) !important;
        font-weight: 500 !important; box-shadow: 0 1px 2px rgba(15,23,42,.04) !important;
        transition: background .12s ease, border-color .12s ease;
    }
    .stButton > button:hover, .stDownloadButton > button:hover,
    .stLinkButton > a:hover {
        background: var(--surface-2) !important; border-color: #cbd5e1 !important;
        color: var(--ink) !important;
    }
    .stButton > button[kind="primary"] {
        background: var(--accent) !important; border-color: var(--accent) !important;
        color: #fff !important;
    }
    .stButton > button[kind="primary"]:hover {
        background: var(--accent-dark) !important; border-color: var(--accent-dark) !important;
    }

    /* inputs */
    input, textarea, [data-baseweb="select"] > div, [data-baseweb="base-input"] {
        background: var(--surface) !important; color: var(--ink) !important;
        border-radius: 8px !important;
    }

    /* the figure and the table read as cards */
    [data-testid="stImage"] {
        background: var(--surface); border: 1px solid var(--line);
        border-radius: 12px; padding: 12px;
        box-shadow: 0 1px 3px rgba(15,23,42,.05);
    }
    [data-testid="stImage"] img { border-radius: 6px; }
    [data-testid="stDataFrame"] {
        border: 1px solid var(--line); border-radius: 10px; overflow: auto;
    }
    [data-testid="stMetric"] {
        background: var(--surface); border: 1px solid var(--line);
        border-radius: 10px; padding: 10px 14px;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.45rem !important; color: var(--ink) !important;
        font-variant-numeric: tabular-nums;
    }
    [data-testid="stMetricLabel"] { color: var(--muted) !important; }

    a, a:visited { color: var(--accent-dark); }
    hr { border-color: var(--line) !important; }
    code { background: var(--surface-2) !important; color: var(--ink) !important;
           border: 1px solid var(--line); border-radius: 4px; padding: 1px 4px; }

    /* --- responsive ------------------------------------------------------
       Streamlit lays columns out as a flex row that does not wrap, so on a
       phone the metric row and the download row squeeze to unreadable slivers.
       Let them wrap, and give each child a sensible floor. */
    @media (max-width: 760px) {
        .block-container { padding: 1.25rem 0.9rem 3rem; }
        h1 { font-size: 1.3rem !important; }
        [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: .6rem; }
        [data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
            flex: 1 1 calc(50% - .6rem) !important;
            min-width: calc(50% - .6rem) !important;
        }
        [data-testid="stMetricValue"] { font-size: 1.15rem !important; }
        [data-testid="stImage"] { padding: 6px; border-radius: 8px; }
        /* a 185-row figure is unreadable at phone width — let it scroll wide */
        [data-testid="stImage"] > div { overflow-x: auto; }
    }
    @media (max-width: 460px) {
        [data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
            flex: 1 1 100% !important; min-width: 100% !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

CHROMS = [f'chr{i}' for i in list(range(1, 23)) + ['X', 'Y']]

gene_sym = None  # set only in Explore + gene-symbol mode


def show_fig(fig, target_px: int = 1600):
    """Render the figure as a PNG sized to the column.

    An SVG in a components.html iframe needs its height declared server-side, but
    the column width is only known in the browser — when the column is narrower
    than the declared width the image shrinks and leaves a tall band of dead space
    that pushes everything below it off-screen. st.image flows with the column, so
    vector output is offered through the SVG/PDF download buttons instead.
    """
    fw, fh = fig.get_size_inches()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight',
                dpi=max(100, min(220, target_px / fw)))
    st.image(buf.getvalue(), use_container_width=True)


def fig_bytes(fig, fmt: str) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, bbox_inches='tight', dpi=200)
    return buf.getvalue()


@st.cache_data(show_spinner=False)
def gene_symbol_list() -> list[str]:
    """hg38 gene symbols (UCSC refGene, main chromosomes) for the picker's
    type-ahead. Falls back to an empty list, which turns the picker into a
    plain free-text box."""
    f = pathlib.Path('data/gene_symbols.txt')
    return f.read_text().split() if f.exists() else []


@st.cache_data(max_entries=200, show_spinner=False, ttl=24 * 3600)
def cached_gene_lookup(symbol: str):
    """Ensembl symbol -> hg38 TSS. Disk-cached: the call costs ~1 s, and Ensembl
    is slow enough to time out under load."""
    key = dict(kind='tss', sym=symbol)
    hit = dc.load_json(**key)
    if hit is not None:
        return hit
    out = core.local_gene_tss(symbol)
    if out is None:                   # not in the bundled table — ask Ensembl
        out = core.lookup_gene_tss(symbol)
    dc.save_json(out, **key)
    return out


@st.cache_data(max_entries=64, show_spinner=False, ttl=24 * 3600)
def cached_gene_models(chrom: str, pos: int, window_kb: float):
    """Ensembl gene models for the window. This was the single slowest stage of a
    cold query (~6 s, against ~4 s for all 241 bigwigs), so it is disk-cached and
    started concurrently with the fetch."""
    key = dict(kind='genes', chrom=chrom, pos=pos, win=window_kb)
    hit = dc.load_json(**key)
    if hit is not None:
        return hit
    lo_bp, hi_bp = max(1, int(pos - window_kb * 1000)), int(pos + window_kb * 1000)
    out = core.local_gene_models(chrom, lo_bp, hi_bp)
    if not out:                       # bundled table missed it — try Ensembl
        out = core.fetch_gene_models(chrom, lo_bp, hi_bp)
    dc.save_json(out, **key)
    return out


@st.cache_data(max_entries=24, show_spinner=False, ttl=6 * 3600)
def cached_signals(chrom: str, pos: int, cell_type: str, window_kb: float,
                   n_species, normalize: bool):
    """hg38-projected tracks, normalised to genome-wide Phred (GPS) — enhancer
    calling is only meaningful on that scale. Backed by an on-disk cache so a
    server restart does not re-pull hundreds of bigwigs."""
    key = dict(space='hg38', chrom=chrom, pos=pos, ct=cell_type,
               win=window_kb, n=n_species, norm=normalize)
    hit = dc.load_signals(**key)
    if hit is not None:
        return hit
    pool = core.list_species()
    if n_species != 'All':
        pool = core.subsample_evenly(pool, int(n_species))
    out = core.fetch_signals(chrom, pos, cell_type, window_kb=window_kb,
                             bin_kb=0.1, species_list=pool,
                             max_workers=32, normalize=normalize)
    dc.save_signals(out, **key)
    return out


# --- sidebar -----------------------------------------------------------------
with st.sidebar:
    st.header('Query')
    mode = st.radio('Look up by:', ['Gene symbol', 'Coordinate'], horizontal=True)
    if mode == 'Gene symbol':
        syms = gene_symbol_list()
        if syms:
            gene_sym = st.selectbox(
                'Gene symbol', syms,
                index=syms.index(PAPER['gene']) if PAPER['gene'] in syms else 0,
                accept_new_options=True,
                # default is fuzzy subsequence matching, which for gene symbols
                # surfaces odd hits ("GATA" -> "ARHGAP12"); prefix is what people
                # expect from a symbol box
                filter_mode='prefix',
                placeholder='Type to search 28,000+ hg38 genes…',
                help='Start typing to filter; any symbol Ensembl knows also works '
                     'even if it is not in the list.',
            )
            gene_sym = (gene_sym or '').strip().upper()
        else:
            gene_sym = st.text_input('Gene symbol (HGNC)', value=PAPER['gene'],
                                     max_chars=40).strip().upper()
        chrom = pos = None
        gene_label, strand = gene_sym, None
    else:
        chrom = st.selectbox('Chromosome (hg38)', CHROMS,
                             index=CHROMS.index(PAPER['chrom']))
        pos = int(st.number_input('Position (hg38, 1-based)', min_value=1,
                                  value=PAPER['tss'], step=1, format='%d'))
        gene_label, strand, gene_sym = f'{chrom}:{pos:,}', None, None

    cell_type = st.selectbox('Cell class', core.CELL_TYPES,
                             index=core.CELL_TYPES.index(PAPER['cell_type']))
    window_kb = float(st.slider('Window (± kb)', 25, 500,
                                int(PAPER['window_kb']), step=25))
    n_species = st.select_slider(
        'Species to fetch', options=[32, 64, 128, 'All'], value='All',
        help='Subsampled evenly along the phylogeny. Fewer is faster, though '
             'all 241 take only a few seconds.')

    if (gene_label == PAPER['gene'] and cell_type == PAPER['cell_type']
            and window_kb == PAPER['window_kb']):
        st.success("The paper's Fig 6e locus, cell class and window.")

    st.divider()
    st.header('Enhancer calling')
    threshold = st.slider(
        'GPS threshold', 10.0, 50.0, syn.GPS_THRESHOLD, step=0.5,
        help='The paper calls enhancers at GPS > 24.5, calibrated on the mouse '
             'genome-wide scale. A referee asked whether conclusions are robust '
             'to this threshold — this slider is that check.',
    )
    n_major = int(st.number_input('Synteny groups to colour', 1, 20,
                                  syn.N_MAJOR_GROUPS, step=1,
                                  help='Fig 6d colours 11; the rest are grey.'))
    min_cov = st.slider(
        'Min hg38 coverage per species', 0.0, 0.9, 0.5, step=0.05,
        help="Fig 6e keeps species with >=50 kb contiguous recoverable sequence "
             'on each flank — a native-coordinate test. hg38 projection is '
             'gappier (median ~55% coverage), so this coverage fraction stands '
             'in for it.')
    highlight_str = st.text_input('Highlight species (red tips)',
                                  value='Canis_lupus_familiaris, Felis_catus')
    show_genes = st.toggle('Gene models (Ensembl)', value=True)

    run = st.button('Generate figure', type='primary', use_container_width=True)


st.title('STEAM-v1 cross-species enhancer view')
st.caption(
    f'Fig 6e from Qiu et al., [*evolutionary transfer learning*]({PAPER_URL}). '
    'Left: Zoonomia phylogeny. Middle: called enhancers coloured by synteny '
    'group (Fig 6d). Right: predicted accessibility per species × 100 bp bin.'
)

if not core.norm_ref_available():
    st.error('`data/norm_ref_species.npz` is missing — enhancer calls need the '
             'genome-wide Phred reference. Copy it in before generating.')
    st.stop()

if not run:
    st.info('Set the query in the sidebar, then **Generate figure**. '
            'The first fetch for a fresh locus pulls ~239 remote bigwigs '
            '(~60–120 s); repeats are instant from cache.')
    st.stop()

# --- resolve the anchor ------------------------------------------------------
if gene_sym:
    try:
        info = cached_gene_lookup(gene_sym)
        chrom, pos, strand = info['chrom'], info['tss'], info['strand']
        gene_label = info.get('symbol', gene_sym)
    except Exception as e:
        st.error(f'Could not resolve `{gene_sym}` via Ensembl: {e}')
        st.stop()

# Ensembl gene models were the slowest stage of a cold query (~6 s, vs ~4 s for
# all 241 bigwigs), so start that request first and let it run while the bigwig
# fetch happens in its worker subprocess.
_genes_future = None
if show_genes:
    _genes_pool = ThreadPoolExecutor(max_workers=1)
    _genes_future = _genes_pool.submit(cached_gene_models, chrom, pos, window_kb)

with st.spinner(f'Fetching STEAM-v1 {cell_type} tracks around '
                f'{gene_label} ({chrom}:{pos:,})…'):
    signals = cached_signals(chrom, pos, cell_type, window_kb, n_species, True)

if not signals:
    st.error('No species returned data at this locus.')
    st.stop()

# --- species filter, calls, synteny groups -----------------------------------
keep = [s for s, a in signals.items() if np.isfinite(a).mean() >= min_cov]
if not keep:
    st.error('No species pass the current filter. Loosen it in the sidebar.')
    st.stop()
sub = {s: signals[s] for s in keep}

calls = syn.call_enhancers(sub, bin_bp=100, anchor_pos=pos,
                           window_kb=window_kb, threshold=threshold)
calls, groups = syn.assign_synteny_groups(calls, n_major=n_major)

order, tree = core.load_pruned_tree(keep)
order = [s for s in order if s in sub]
highlight = [h.strip() for h in highlight_str.split(',') if h.strip()]
genes = None
if _genes_future is not None:
    try:
        genes = _genes_future.result(timeout=25)
    except Exception:
        genes = None          # Ensembl is flaky; the figure is fine without it
    finally:
        _genes_pool.shutdown(wait=False)

# --- headline numbers, against the paper's -----------------------------------
n_maj = int((calls['group'] > 0).sum()) if not calls.empty else 0
per_sp = calls.groupby('species').size().mean() if not calls.empty else 0.0
c1, c2, c3, c4 = st.columns(4)
c1.metric('Species', len(order), help='Paper Fig 6e retains 136.')
c2.metric(f'{cell_type} enhancers', len(calls), help='Paper: 621 at AFP.')
c3.metric('In major groups',
          f'{100 * n_maj / len(calls):.0f}%' if len(calls) else '—',
          help='Paper: 591/621 = 95%.')
c4.metric('Per species', f'{per_sp:.1f}', help='Paper: 4.6 ± 1.7.')

fig = panel.plot_fig6e_paper(
    order, sub, tree, calls,
    anchor_label=gene_label, anchor_chrom=chrom, anchor_pos=pos,
    cell_type=cell_type, window_kb=window_kb, anchor_strand=strand,
    genes=genes, highlight_species=highlight, n_major=n_major,
)
show_fig(fig)

d1, d2, d3, d4 = st.columns(4)
d1.download_button('Download PNG', fig_bytes(fig, 'png'),
                   f'fig6e_{gene_label}_{cell_type}.png', 'image/png',
                   use_container_width=True)
d2.download_button('Download PDF', fig_bytes(fig, 'pdf'),
                   f'fig6e_{gene_label}_{cell_type}.pdf', 'application/pdf',
                   use_container_width=True)
d3.download_button('Download SVG', fig_bytes(fig, 'svg'),
                   f'fig6e_{gene_label}_{cell_type}.svg', 'image/svg+xml',
                   use_container_width=True)
d4.link_button('View in UCSC Browser',
               f'https://genome.ucsc.edu/cgi-bin/hgTracks?db=hg38&position='
               f'{chrom}:{int(pos - window_kb * 1000)}-{int(pos + window_kb * 1000)}',
               use_container_width=True)

# --- Fig 6d group table ------------------------------------------------------
st.subheader('Synteny groups (Fig 6d statistics)')
if groups.empty:
    st.caption('No synteny groups at this threshold.')
else:
    show = groups.copy()
    show['mean_dist_to_tss'] = show['mean_dist_to_tss'].round(0).astype(int)
    show['mean_gps'] = show['mean_gps'].round(2)
    show['span'] = show.apply(
        lambda r: f"{chrom}:{int(r['start']):,}-{int(r['end']):,}", axis=1)
    st.dataframe(
        show[['group', 'n_enhancers', 'n_species', 'mean_dist_to_tss',
              'mean_gps', 'span']],
        hide_index=True, use_container_width=True,
    )
    st.download_button('Download calls (TSV)',
                       calls.to_csv(sep='\t', index=False).encode(),
                       f'enhancer_calls_{gene_label}_{cell_type}.tsv', 'text/tab-separated-values')

with st.expander('How this compares to the published panel'):
    st.markdown(f"""
- **Coordinate space.** Fig 6e works in each species' own coordinates, lifting
  the anchor into every genome. This view puts all species on one shared hg38
  axis instead, so positions line up directly across rows.
- **Species filter.** The paper's ≥50 kb-per-flank rule is a native-coordinate
  test. hg38 projection is gappier (median ~55% coverage), so the coverage
  fraction stands in for it.
- **Synteny groups.** Fig 6d connects enhancers by lifting them over pairwise
  between species. Because every species here is already projected into hg38,
  "overlaps after liftover" becomes "overlaps in hg38" — a close proxy, but the
  group numbering will not match the paper's clusters 1–11.
- **Calling.** Enhancers are runs of 100 bp bins at GPS ≥ {threshold:g}. The
  paper adds tiling, tandem-repeat mitigation and model-based trimming, so its
  per-species counts run lower ({per_sp:.1f} here vs. 4.6 published).
""")
