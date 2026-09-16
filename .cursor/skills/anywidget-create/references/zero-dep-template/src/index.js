/**
 * Zero-dependency anywidget front-end module template.
 *
 * Replace "my-widget" with your widget's scoping class name.
 * Replace model keys ("value") with your widget's actual properties.
 */

/** @param {{ model: object, el: HTMLElement }} ctx */
export function render({ model, el }) {
  // Clear previous render (safe for re-renders)
  el.innerHTML = "";
  el.classList.add("my-widget");

  // --- Build DOM ---

  const display = document.createElement("div");
  display.className = "mw-display";
  display.textContent = model.get("value");
  el.appendChild(display);

  // --- Model → DOM sync ---

  function syncValue() {
    display.textContent = model.get("value");
  }
  model.on("value", syncValue);

  // --- DOM → Model sync (for interactive properties) ---
  //
  // display.addEventListener("click", () => {
  //   model.set("selected", true);
  //   model.save_changes();
  // });

  // --- Responsive layout ---

  const resizeObs = new ResizeObserver(() => {
    // Recalculate layout if needed
  });
  resizeObs.observe(el);

  // --- Cleanup ---
  // Return a teardown function. This is called before re-render and on destroy.
  // Remove all window/document-level listeners and disconnect observers here.

  return () => {
    model.off("value", syncValue);
    resizeObs.disconnect();
  };
}
