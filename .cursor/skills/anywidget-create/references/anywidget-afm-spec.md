# Anywidget Front-end Module (AFM) Specification

The AFM spec defines a standard interface for creating widget front-end code as ECMAScript Modules (ESM). An AFM module exports lifecycle functions that anywidget calls to manage the widget's DOM.

## Module Shape

An AFM module is an ES module that may export the following:

```typescript
export default {
  initialize?: ({ model, el }) => void | (() => void),
  render: ({ model, el }) => void | (() => void),
};
```

Or as named exports:

```typescript
export function initialize({ model, el }): void | (() => void);
export function render({ model, el }): void | (() => void);
```

## Lifecycle Functions

### `initialize({ model, el })`

- **Optional**. Called once when the widget view is first created.
- Use for one-time setup: creating DOM elements, initializing libraries, attaching base structure to `el`.
- May return a **cleanup function** that is called when the view is destroyed.

### `render({ model, el })`

- **Required**. Called after `initialize` and whenever the widget needs to re-render.
- Receives the same `el` element. Responsible for updating the DOM to reflect current model state.
- May return a **cleanup function** that is called before the next render or when the view is destroyed.

## The `model` Object

The `model` object is a proxy for the widget's state (traitlets on the Python side). It exposes:

| Method | Description |
|--------|-------------|
| `model.get(name)` | Get the current value of a trait/attribute |
| `model.set(name, value)` | Set a trait/attribute value |
| `model.save_changes()` | Sync changes back to the kernel/backend |
| `model.on(name, callback)` | Listen for changes to a specific trait |
| `model.off(name, callback)` | Remove a change listener |
| `model.widget_manager` | Access to the widget manager (advanced) |
| `model.send(msg)` | Send a custom message to the backend |

### Listening for Changes

```javascript
function render({ model, el }) {
  const update = () => {
    el.innerText = model.get("value");
  };
  update();
  model.on("value", update);

  // Cleanup: remove listener when destroyed
  return () => model.off("value", update);
}
```

### Sending Updates to Backend

```javascript
model.set("count", model.get("count") + 1);
model.save_changes();
```

## The `el` Element

- `el` is a standard `HTMLElement` attached to the document.
- The widget should render all its content inside `el`.
- Do not replace `el` itself; add children to it.

## CSS / Styles

AFM supports an optional CSS file alongside the ESM module. Anywidget will inject the CSS into the document when the widget is loaded.

In a typical file structure:

```
my-widget/
  index.js    # ESM module with render/initialize
  styles.css  # Optional CSS, auto-injected
```

## Key Constraints

1. The module **must be valid ESM** — use `import`/`export`, no CommonJS (`require`).
2. External dependencies must be ESM-compatible and browser-safe.
3. The `render` function is **required**; `initialize` is optional.
4. Cleanup functions should remove event listeners and free resources.
5. The module runs in the **browser** — no Node.js APIs available.
6. When bundling, the output format must be ESM (`format: "esm"`).
