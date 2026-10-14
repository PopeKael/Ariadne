/* Local interactive checks inside the opaque-origin coding preview sandbox. */
(() => {
  const config = window.__ariadneCodingPreview;
  delete window.__ariadneCodingPreview;
  if (!config || !config.token) return;
  const errors = [];
  const send = (kind, payload = {}) => parent.postMessage({
    type: "ariadne-coding-preview", run_id: config.run_id, token: config.token, kind, payload,
  }, "*");
  const recordError = message => {
    if (errors.length < 100) errors.push(String(message).slice(0, 2000));
    send("runtime_error", {message: String(message).slice(0, 2000)});
  };
  addEventListener("error", event => recordError(`${event.message} (${event.filename || "preview"}:${event.lineno || 0}:${event.colno || 0})`));
  addEventListener("unhandledrejection", event => recordError(String(event.reason)));
  addEventListener("securitypolicyviolation", event => send("blocked_resource", {
    directive: event.violatedDirective, resource: event.blockedURI,
  }));
  const visible = element => {
    const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const snapshot = () => ({
    text: document.body?.innerText.slice(0, 4000) || "",
    controls: [...document.querySelectorAll("button,input,select,textarea,[role=button]")]
      .filter(visible).slice(0, 40).map(element => ({
        tag: element.tagName, id: element.id, label: (element.textContent || element.value || "").trim().slice(0, 140),
        disabled: Boolean(element.disabled), value: element.value || "",
      })),
  });
  const changed = (before, after) => before.text !== after.text || JSON.stringify(before.controls) !== JSON.stringify(after.controls);
  addEventListener("DOMContentLoaded", () => setTimeout(() => {
    const initial = snapshot();
    const usable = Boolean(document.body && [...document.querySelectorAll("body *")].some(element =>
      visible(element) && (element.textContent.trim() || ["CANVAS", "SVG", "INPUT"].includes(element.tagName))));
    send("loaded", {title: document.title, usable, initial, errors: [...errors]});
  }, 150));
  addEventListener("message", async event => {
    if (event.source !== parent || event.data?.type !== "ariadne-coding-verify" || event.data.token !== config.token) return;
    const initial = snapshot(), actions = [], warnings = [];
    try {
      for (const select of [...document.querySelectorAll("select")].filter(visible).slice(0, 10)) {
        if (select.options.length > 1) {
          const before = snapshot();
          select.selectedIndex = 1;
          select.dispatchEvent(new Event("input", {bubbles: true}));
          select.dispatchEvent(new Event("change", {bubbles: true}));
          await new Promise(resolve => setTimeout(resolve, 100));
          actions.push({kind: "select", label: select.id || select.name || "select", changed: changed(before, snapshot())});
        }
      }
      for (const control of [...document.querySelectorAll("button,input[type=button],input[type=submit],[role=button]")]
        .filter(element => visible(element) && !element.disabled).slice(0, 25)) {
        if (!control.isConnected || !visible(control) || control.disabled) continue;
        const before = snapshot();
        const label = (control.textContent || control.value || control.getAttribute("aria-label") || "button").trim().slice(0, 140);
        control.click();
        await new Promise(resolve => setTimeout(resolve, 150));
        const didChange = changed(before, snapshot());
        actions.push({kind: "click", label, changed: didChange});
        if (!didChange) warnings.push(`No visible state change after clicking: ${label}`);
      }
      const final = snapshot();
      const usable = initial.text.length > 0 || initial.controls.length > 0
        || [...document.querySelectorAll("canvas,svg,input")].some(visible);
      send("verification", {passed: usable && errors.length === 0, usable,
        initial, final, controls: initial.controls, actions, warnings, errors: [...errors]});
    } catch (error) {
      recordError(error.message || error);
      send("verification", {passed: false, usable: initial.text.length > 0 || initial.controls.length > 0, initial, controls: initial.controls,
        actions, warnings, errors: [...errors]});
    }
  });
})();
