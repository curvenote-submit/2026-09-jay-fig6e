function render({ model, el }) {
  const btn = document.createElement("button");
  const show = () => (btn.textContent = `count is ${model.get("count")}`);
  btn.addEventListener("click", () => {
    model.set("count", model.get("count") + 1);
    model.save_changes();
  });
  model.on("change:count", show);
  show();
  el.appendChild(btn);
}
export default { render };
