# MyST Markdown Widget Integration

MyST Markdown supports interactive widgets via anywidget. Widgets can be embedded in MyST documents and rendered in MyST-based sites.

## How Widgets Work in MyST

MyST documents can include Jupyter notebooks with widget outputs. When a notebook cell produces a widget, MyST renders it using the widget's front-end module.

### Embedding an Anywidget in MyST

In a Jupyter notebook used with MyST:

```python
import anywidget
import traitlets

class CounterWidget(anywidget.AnyWidget):
    _esm = "path/to/index.js"    # Path to the ESM module
    _css = "path/to/styles.css"   # Optional CSS

    value = traitlets.Int(0).tag(sync=True)
```

The `_esm` field points to the AFM module. The `_css` field optionally points to a stylesheet.

### Using a Published Widget

If the widget is published as a Python package with bundled front-end code:

```python
from my_widget_package import MyWidget
widget = MyWidget(value=42)
widget
```

### Static Rendering

For static MyST sites (no live kernel), widgets render using their front-end module with the initial state serialized from the notebook output. The widget is interactive on the client side but cannot communicate back to a Python kernel.

## MyST Frontmatter for Widget Support

MyST documents that use widgets should ensure proper configuration. In the document or project `myst.yml`:

```yaml
project:
  jupyter: true
```

## Key Considerations for MyST Compatibility

1. **ESM module must be self-contained or use CDN imports** — MyST static sites serve the widget JS directly; relative imports to `node_modules` won't work in production.
2. **Use dynamic `import()` for large dependencies** — keeps initial load fast.
3. **Initial state matters** — in static rendering, the widget gets the state captured when the notebook was last executed. Design widgets that render meaningfully from initial state alone.
4. **CSS should be scoped** — avoid global styles that could conflict with MyST's own styling. Prefer class-based selectors scoped to your widget's container.
5. **Responsive design** — MyST documents may be viewed at various widths; widgets should handle resizing gracefully.
6. **No Python runtime assumed** — the front-end module should work fully in the browser. Use `model.on()` / `model.set()` / `model.save_changes()` for interactivity. In static contexts, `save_changes()` is a no-op but should not error.

## File Referencing

When developing locally, `_esm` and `_css` can be:

- **Relative file paths** — resolved relative to the Python file or notebook: `_esm = "./src/index.js"`
- **Inline strings** — small widgets can embed JS directly: `_esm = "export function render({ model, el }) { ... }"`
- **Pathlib paths** — `_esm = pathlib.Path(__file__).parent / "static" / "widget.js"`

For distribution, bundle the front-end assets into the Python package so they are available at install time.
