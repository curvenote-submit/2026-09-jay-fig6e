// Inline SVG icons for the widget's buttons (lucide-style strokes, currentColor).
const wrap = (body: string, size = 14) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICON = {
  plus: wrap('<path d="M5 12h14"/><path d="M12 5v14"/>'),
  minus: wrap('<path d="M5 12h14"/>'),
  reset: wrap('<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'),
  download: wrap('<path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/>'),
  table: wrap('<path d="M12 3v18"/><rect width="18" height="18" x="3" y="3" rx="0"/><path d="M3 9h18"/><path d="M3 15h18"/>'),
  external: wrap('<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>', 11),
};
