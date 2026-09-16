// Shared types for the fig6e widget.

/** meta.json written next to the zarr store by build_zarr.py */
export interface StoreMeta {
  genome: string;
  bin_bp: number;
  missing: number;
  scale: number;
  chrom_sizes: Record<string, number>;
  chrom_offsets: Record<string, number>;
  species: string[];
  cell_types: string[];
}

/** One gene model from genes/<chrom>.json */
export interface Gene {
  name: string;
  start: number;
  end: number;
  strand: number;          // +1 / -1
  exons: [number, number][];
}

/** A window of GPS rows, one Uint8Array per species (255 = missing). */
export interface Slice {
  rows: Uint8Array[];
  species: string[];
  startBp: number;
  endBp: number;
  binBp: number;
  fetchMs: number;
  chunksFetched: number;
  chunksCached: number;
}

/** One called enhancer (synteny.call_enhancers row). */
export interface Call {
  species: string;
  row: number;             // index into Slice.species
  start: number;
  end: number;
  bin_start: number;
  bin_end: number;
  gps_max: number;
  gps_mean: number;
  dist_to_tss: number;
  group?: number;          // 1..nMajor, 0 = minor; set by assignSyntenyGroups
}

/** Per-group summary (synteny.assign_synteny_groups). */
export interface GroupSummary {
  group: number;
  n_enhancers: number;
  n_species: number;
  mean_dist_to_tss: number;
  mean_gps: number;
  start: number;
  end: number;
}

export interface View { startBp: number; endBp: number }

/** Everything the figure needs that the user can change. */
export interface Params {
  chrom: string;
  pos: number;
  cellType: string;
  windowKb: number;
  threshold: number;
  nMajor: number;
  minCov: number;
  highlight: string[];
  selectedGroup: number | null;
  showGaps: boolean;
  rowPx: number | null;
  start?: number | null;
  end?: number | null;
}

/** Status pushed from the controller to the React shell. */
export interface Status {
  loading: boolean;
  error: string | null;
  species?: number;
  totalSpecies?: number;
  enhancers?: number;
  pctMajor?: number | null;
  perSpecies?: number | null;
  window?: string;
  bins?: number;
  fetch?: string;
  calls?: Omit<Call, "row">[];
}

/** Minimal anywidget model surface the widget relies on. */
export interface AnyModel {
  get(key: string): unknown;
  set(key: string, value: unknown): void;
  save_changes(): void;
  on(event: string, cb: () => void): void;
  off?(event: string, cb: () => void): void;
}
