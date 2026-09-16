---
name: anywidget-create-vanilla
description: >
  Create an anywidget front-end module (AFM) compatible with anywidget hosts. Generates ESM JavaScript/TypeScript widget code with initialize and
  render lifecycle functions, accompanying CSS, a local development environment, and
  build tooling. Use when asked to create an interactive visualisation, custom widget,
  or front-end component for use in a MyST document, Jupyter notebook, or any system
  that supports anywidgets.
keywords:
  - anywidget
  - widget
  - interactive
  - visualisation
  - visualization
  - esm
  - javascript
  - typescript
  - myst
  - mystmd
  - front-end
  - afm
  - browser
  - component
allowed-tools: read_file, write_file, edit_file, glob, grep, shell, web_fetch, ask_user
---

## Required Inputs

| Input                  | Required | Description                                                                 |
|------------------------|----------|-----------------------------------------------------------------------------|
| Widget purpose         | Yes      | What the widget should do — its behaviour and visual output                 |
| Data model             | Optional | Description of the data/state the widget needs (traits synced via `model`)  |
| Dependencies           | Optional | Any specific JS libraries to use (e.g., D3, Three.js, Leaflet)             |
| Target environment     | Optional | Where the widget will run — MyST site, JupyterLab, Colab, **pixel-artist-be session harness**, etc.             |

When used standalone, these inputs come from the user or the agent's prompt. When used within a workflow, the workflow's stage prompt will specify how to obtain them.

## Primary Output

**This skill produces a JavaScript (or TypeScript) front-end module — not a Python file.**

The deliverable is a widget directory containing:
- `src/index.js` (or `.ts`) — the AFM ESM module exporting `render` (and optionally `initialize`)
- `src/styles.css` — scoped CSS
- `dev/index.html` + `dev/serve.js` — local dev harness for browser testing without Python
- `package.json` — project metadata and scripts
- `model.json` — initial model state when used inside pixel-artist-be `iter-N/` (see `data_urls` convention below)
- `README.md` — usage docs (including example Python integration snippets)

Do **not** create a `.py` file as the primary artifact. Python widget classes that reference these JS/CSS files are the *consumer's* responsibility and belong in a separate package or notebook — not in this widget directory. The README may include Python usage examples for reference.

## Steps

### 1. Analyse Requirements

- Clarify what the widget should render and how users interact with it.
- Identify the data shape — what `model.get()` / `model.set()` keys the JS front end needs. These correspond to traits on a Python host class, but this skill only produces the JS side.
- Determine whether third-party JS libraries are needed.
- If requirements are ambiguous, use `ask_user` to clarify before proceeding.

### 2. Understand the AFM Lifecycle

Before writing code, review the anywidget front-end module contract. See [references/anywidget-afm-spec.md](references/anywidget-afm-spec.md) for the full spec. Key points:

- **`initialize({ model, el })`** — optional, called once. Use for one-time DOM setup.
- **`render({ model, el })`** — required, called after initialize and on re-renders. Update the DOM from model state here.
- Both may return a **cleanup function** for teardown.
- Use `model.get(name)`, `model.set(name, value)`, `model.save_changes()`, `model.on(name, cb)`.
- All rendering goes inside `el` — never replace `el` itself.

### 3. Design the Model Data

Define the model keys (properties) the JavaScript front end will read and write via `model.get(name)` and `model.set(name, value)`. These keys correspond to traitlets on the Python side, but this skill only produces the JS consumer.

- List every model key the widget needs, with its JS type and default value.
- Distinguish between:
  - **Display properties** — read on the front end to render (e.g., `data`, `title`, `color_scale`).
  - **Interaction properties** — written on the front end and synced back via `model.set()` + `model.save_changes()` (e.g., `selected_index`, `zoom_level`).
- All values must be JSON-serializable (strings, numbers, booleans, arrays, plain objects).

### 4. Evaluate Dependencies

- Prefer plain JavaScript/DOM APIs when the widget is simple enough.
- When a library is needed, confirm it provides an **ESM build** and runs in **all modern browsers**.
- Good sources for ESM-compatible packages: esm.sh, skypack, unpkg (with `?module`), or npm packages with `"type": "module"`.
- If a dependency is CommonJS-only, bundling is required (see step 6).

### 5. Choose the Project Structure

Create a directory for the widget. The layout depends on whether bundling is needed.

**Zero-dependency widget (no bundling):**

```
<widget-name>/
├── src/
│   ├── index.js          # AFM module — exports initialize/render
│   └── styles.css        # Widget styles
├── dev/
│   ├── index.html        # Local dev harness page
│   └── serve.js          # Lightweight dev server script
├── package.json          # Project metadata and "dev" script only
└── README.md             # Usage and development docs
```

**Widget with dependencies or TypeScript (bundling required):**

```
<widget-name>/
├── src/
│   ├── index.js          # (or index.ts) AFM module — exports initialize/render
│   └── styles.css        # Widget styles
├── dev/
│   ├── index.html        # Local dev harness page
│   └── serve.js          # Lightweight dev server script
├── package.json          # Dependencies, build + dev scripts
├── tsconfig.json         # Only if using TypeScript
├── README.md             # Usage and development docs
└── dist/                 # Build output (gitignored)
    ├── index.js
    └── styles.css
```

### 6. Decide on Bundling

Bundling is **required** when:
- The widget imports from `node_modules` (third-party libraries).
- TypeScript is used.
- Multiple source files need to be combined into a single ESM output.

Bundling is **not required** when:
- The widget uses only browser APIs and inline code.
- External dependencies are loaded via CDN `import` from esm.sh or similar.

When bundling is required, choose a bundler:

| Bundler | When to use |
|---------|-------------|
| **Bun** | **Preferred in pixel-artist-be sessions** — `bun install`, `bun run build`; fast, minimal config |
| **esbuild** | Default choice elsewhere — fast, minimal config, great for straightforward bundles |
| **tsup** | TypeScript projects — wraps esbuild with sensible TS defaults |
| **vite** | Complex projects needing HMR, dev server, plugin ecosystem |

In **pixel-artist-be** session workspaces, prefer Bun over npm when Bun is available (`command -v bun`). Fall back to `npm install` / `npm run build` if Bun is missing.

Configure the bundler to output **ESM format** with the entry point as `src/index.js` (or `.ts`) and output to `dist/`.

Example `tsup.config.ts`:
```typescript
import { defineConfig } from "tsup";
export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  outDir: "dist",
  clean: true,
  minify: true,
});
```

Example `esbuild` in `package.json` scripts:
```json
{
  "scripts": {
    "build": "esbuild src/index.js --bundle --format=esm --outfile=dist/index.js --minify"
  }
}
```

### 7. Set Up the Development Environment

For **pixel-artist-be** sessions, `publish-widget` writes the Phase 1 AFM harness to `iter-N/index.html` at publish time — local `dev/index.html` is optional for pre-publish testing only.

1. Create `package.json` directly (or use `bun init` / `npm init -y`).
2. Always add a `"dev"` script that starts the local dev server: `"dev": "node dev/serve.js"`.
3. **Only if bundling is needed:** install dev dependencies (prefer **Bun**: `bun add -d esbuild`; npm fallback), and add `"build"` and `"watch"` scripts. Do not add these for zero-dependency widgets.
4. If TypeScript, create a minimal `tsconfig.json`:
   ```json
   {
     "compilerOptions": {
       "target": "ES2020",
       "module": "ESNext",
       "moduleResolution": "bundler",
       "strict": true,
       "esModuleInterop": true,
       "outDir": "dist",
       "declaration": true
     },
     "include": ["src"]
   }
   ```

### 8. Write the Widget Code

Write `src/index.js` (or `.ts`) following the AFM spec:

```javascript
/** @param {{ model: object, el: HTMLElement }} context */
export function initialize({ model, el }) {
  // One-time DOM setup
  el.classList.add("my-widget");
}

/** @param {{ model: object, el: HTMLElement }} context */
export function render({ model, el }) {
  // Read state and update DOM
  const value = model.get("value");
  el.innerHTML = `<div class="display">${value}</div>`;

  // Listen for model changes
  const onChange = () => {
    el.querySelector(".display").textContent = model.get("value");
  };
  model.on("value", onChange);

  // Cleanup
  return () => {
    model.off("value", onChange);
  };
}
```

Write `src/styles.css` with **scoped styles** — always scope to a class on `el` to avoid polluting the host page:

```css
.my-widget {
  font-family: system-ui, sans-serif;
  padding: 1rem;
}
.my-widget .display {
  font-size: 1.5rem;
}
```

### 9. Create the Local Development Harness

Create `dev/index.html` — a standalone HTML page that loads the widget with a mock `model` object, allowing testing without Python/Jupyter:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Widget Dev</title>
  <link rel="stylesheet" href="../src/styles.css" />
</head>
<body>
  <div id="widget-container"></div>
  <script type="module">
    import * as widget from "../src/index.js";

    // Mock model that mimics the anywidget model interface
    function createMockModel(initialState) {
      const state = { ...initialState };
      const listeners = {};
      return {
        get(key) { return state[key]; },
        set(key, value) { state[key] = value; },
        save_changes() { console.log("save_changes:", { ...state }); },
        on(key, cb) {
          (listeners[key] ||= []).push(cb);
        },
        off(key, cb) {
          listeners[key] = (listeners[key] || []).filter(fn => fn !== cb);
        },
        _fire(key) {
          (listeners[key] || []).forEach(cb => cb());
        },
        send(msg) { console.log("send:", msg); },
      };
    }

    const model = createMockModel({ value: "Hello, Widget!" });
    const el = document.getElementById("widget-container");

    if (widget.initialize) widget.initialize({ model, el });
    widget.render({ model, el });
  </script>
</body>
</html>
```

Create `dev/serve.js` — a lightweight Node.js static file server:

```javascript
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, resolve } from "node:path";

const PORT = 3000;
const ROOT = resolve(join(import.meta.dirname, ".."));

const MIME = {
  ".html": "text/html",
  ".js":   "application/javascript",
  ".mjs":  "application/javascript",
  ".css":  "text/css",
  ".json": "application/json",
  ".svg":  "image/svg+xml",
  ".png":  "image/png",
};

createServer(async (req, res) => {
  const url = req.url === "/" ? "/dev/index.html" : req.url;
  const filePath = join(ROOT, url);
  try {
    const data = await readFile(filePath);
    res.writeHead(200, { "Content-Type": MIME[extname(filePath)] || "application/octet-stream" });
    res.end(data);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
}).listen(PORT, () => console.log(`Dev server: http://localhost:${PORT}`));
```

### 10. Build and Verify

**If the widget has no dependencies and no TypeScript** (no bundling needed):
1. Verify `src/index.js` is valid ESM by importing it in Node: `node -e "import('./src/index.js').then(m => console.log(Object.keys(m)))"`.
2. Start the dev server (`npm run dev`) and open the browser to verify the widget renders.
3. Test interactivity by calling `model.set()` / `model._fire()` in the browser console.

**If bundling is configured:**
1. Run `npm install` to install dependencies.
2. Run the build script and confirm `dist/` output is valid ESM.
3. Start the dev server and open the browser to verify the widget renders.
4. Test interactivity by calling `model.set()` / `model._fire()` in the browser console.

### 11. Write the README

Create a `README.md` covering:

1. **Overview** — what the widget does, with a description of the visual output.
2. **Model** — table of model keys the widget reads/writes, their types, and defaults.
3. **Development** — how to run the dev server (`npm run dev`) and build (if bundling is used).
4. **Python integration** — example showing how a Python class would reference these files:
   ```python
   import anywidget
   import traitlets

   class MyWidget(anywidget.AnyWidget):
       _esm = "path/to/src/index.js"   # or dist/index.js if bundled
       _css = "path/to/src/styles.css"  # or dist/styles.css if bundled
       value = traitlets.Unicode("Hello").tag(sync=True)
   ```
   This is a consumer example — the Python class is **not** part of this widget's deliverables.
5. **MyST Integration** — note that the widget is compatible with MyST Markdown documents.

## MyST Compatibility Checklist

Before considering the widget complete, verify against [references/myst-widgets-guide.md](references/myst-widgets-guide.md):

- [ ] Output is a valid self-contained ESM module (no bare `node_modules` imports in final bundle).
- [ ] CSS is scoped to the widget — no global style leakage.
- [ ] Widget renders meaningfully from initial state alone (static MyST sites have no live kernel).
- [ ] `save_changes()` calls do not throw when no kernel is available.
- [ ] Widget handles container resizing gracefully (responsive).
- [ ] No Node.js-only APIs used in widget code (it runs in the browser).

## Edge Cases

- **No bundler needed** — if the widget is simple with no external deps, skip bundling entirely. Serve `src/index.js` directly. This is the preferred path for zero-dependency widgets — do not add build tooling that isn't needed. The `dist/` directory, bundler config, and `npm install` step should all be omitted.
- **Large dependencies** — use dynamic `import()` for heavy libraries to avoid blocking initial page load. Consider loading from esm.sh CDN.
- **TypeScript without bundler** — not recommended. TypeScript always needs a compile step; use tsup or esbuild.
- **Multiple widgets in one package** — export each widget from a separate entry point or use a single module with multiple named exports.
- **HMR / live reload** — if using vite, HMR works out of the box. For esbuild/tsup, pair with a simple watch + livereload setup.
- **Browser compatibility** — target ES2020+ which covers all modern browsers. Avoid top-level await if supporting older Safari versions.
- **CSS loading order** — in some hosts, CSS may load after first render. Ensure the widget doesn't flash unstyled content or handles it gracefully.
