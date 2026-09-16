# Spike: does MyST render an anywidget with no kernel?

**Yes.** Verified 2026-09-15 with `myst` 1.10.1 via the native `{anywidget}`
directive — no notebook, no Python, no execution step.

```
myst build --html     # or: myst start
```

## What the directive looks like

````markdown
```{anywidget} ./counter.mjs
:css: ./counter.css
{ "count": 0 }
```
````

The ESM must `export default { render }` (anywidget's AFM shape). The build copies
the module and stylesheet to hashed paths under `_build/site/public/` and emits an
AST node `{"type":"anywidget","esm":"/counter-<hash>.mjs","model":{"count":0}}`.

## Findings that shape the real widget

| Finding | Consequence |
|---|---|
| Widget mounts inside a **shadow DOM** | CSS is scoped for free. `document.querySelector` from the page can't reach `el`; tooltips/popovers must be appended inside `el`, not `document.body`. |
| The `:css:` stylesheet is injected as a `<link>` **inside `el`** just before `render()` | Never `el.replaceChildren(...)` / `el.innerHTML = ...`; append to `el`. |
| `model.get`, `model.set`, `model.on("change:key", cb)` work | Self-driving controls inside the widget are fine. |
| **`model.save_changes()` throws** `MystAnyModel.save_changes not implemented yet` | Always `try { model.save_changes() } catch {}`. |
| `esm` is served from the site's own origin | Data must come from a CORS-enabled URL (the zarr bucket) or a path MyST copies. MyST does not copy arbitrary directories; the zarr store is never part of the site. |
| Docs say "Widget support is experimental. The interfaces may change" | Pin the `myst` version in the site's `package.json` / CI. |

This replaces the notebook-based route in the original plan
(`fig6e.ipynb` + `AnyWidget` subclass + `myst build --execute`). The Python class
is still useful for JupyterLab, but the published site doesn't need it.

Refs: [mystmd.org/guide/widgets](https://mystmd.org/guide/widgets),
[jupyter-book/mystmd#2602](https://github.com/jupyter-book/mystmd/pull/2602),
[jupyter-book/myst-theme#795](https://github.com/jupyter-book/myst-theme/pull/795).
