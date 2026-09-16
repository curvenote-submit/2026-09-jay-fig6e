# Zero-Dependency Widget Template

A starter project structure for anywidget front-end modules that have **no JavaScript dependencies** and require **no bundling**. Use this as the starting point whenever a widget can be built with plain browser APIs alone.

## Structure

```
<widget-name>/
├── src/
│   ├── index.js      # AFM module — exports render (and optionally initialize)
│   └── styles.css    # Scoped CSS — all selectors nested under a root class
├── dev/
│   ├── index.html    # Browser dev harness with mock model
│   └── serve.js      # Zero-dep Node.js static file server
├── package.json      # Metadata + "dev" script only — no build, no deps
└── README.md         # Overview, model table, dev instructions, Python example
```

## Key characteristics

- **No `node_modules/`, no `dist/`, no bundler config.** The source files *are* the distribution files.
- `package.json` has only a `"dev"` script (`node dev/serve.js`). No `"build"` or `"watch"`.
- `src/index.js` is plain ES module JavaScript. It imports nothing — all code is inline.
- `src/styles.css` scopes every rule under a class added to `el` in `render()`.
- `dev/index.html` uses a `createMockModel()` helper that implements the full anywidget model interface (`get`, `set`, `save_changes`, `on`, `off`, `send`), plus a `_fire` helper for console testing. The model is exposed as `globalThis.model`.
- `dev/serve.js` is a ~30-line Node.js static server. No framework, no deps.

## Template files

The complete template files are in [`zero-dep-template/`](zero-dep-template/):

| File | Purpose |
|------|---------|
| [`src/index.js`](zero-dep-template/src/index.js) | AFM module with render function, model sync, ResizeObserver, and cleanup |
| [`src/styles.css`](zero-dep-template/src/styles.css) | Scoped CSS starter |
| [`dev/index.html`](zero-dep-template/dev/index.html) | Dev harness with mock model |
| [`dev/serve.js`](zero-dep-template/dev/serve.js) | Static file server |
| [`package.json`](zero-dep-template/package.json) | Minimal package metadata |

## How to use this template

1. Copy the `zero-dep-template/` directory and rename it to your widget name.
2. In `src/index.js`: replace `"my-widget"` with your scoping class, replace `"value"` with your model keys, and build your DOM.
3. In `src/styles.css`: replace `.my-widget` selectors with your class name.
4. In `dev/index.html`: update the `createMockModel()` initial state to match your model keys.
5. In `package.json`: update `name` and `description`.
6. Run `npm run dev` and open `http://localhost:3000` to develop in the browser.

## When NOT to use this template

Use the bundled project structure instead when:

- The widget imports from npm packages (e.g., D3, Three.js, Leaflet).
- TypeScript is used.
- Multiple source files need to be combined into a single ESM output.
