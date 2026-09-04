/* Optional server-side layout catalogue.
 *
 * When the application is served over HTTP(S), this module looks for
 * /list.json, populates the server-layout selector, and forwards a selected
 * entry to the application's internal JSON loader. A missing list is expected
 * and leaves local file import available.
 */
(() => {
  "use strict";

  function listEntries(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && typeof payload === "object" && Array.isArray(payload.files)) return payload.files;
    throw new TypeError("/list.json must be an array or an object with a files array");
  }

  function normalizeList(payload) {
    return listEntries(payload).map((entry, index) => {
      let path;
      let label;
      if (typeof entry === "string") {
        path = entry;
        label = entry;
      } else if (entry && typeof entry === "object" && !Array.isArray(entry)) {
        path = entry.path ?? entry.file;
        label = entry.label ?? entry.name ?? path;
      }
      if (typeof path !== "string" || !path.trim()) {
        throw new TypeError(`/list.json entry ${index + 1} must provide a non-empty path`);
      }
      if (typeof label !== "string" || !label.trim()) {
        throw new TypeError(`/list.json entry ${index + 1} must provide a non-empty label`);
      }
      return { path: path.trim(), label: label.trim() };
    });
  }

  function resolveList(payload, rootHref) {
    const root = new URL("/", rootHref);
    if (!new Set(["http:", "https:"]).has(root.protocol)) {
      throw new TypeError("server layouts require an HTTP(S) page");
    }

    const seen = new Set();
    const resolved = [];
    for (const entry of normalizeList(payload)) {
      const url = new URL(entry.path, root);
      url.hash = "";
      if (url.origin !== root.origin || !new Set(["http:", "https:"]).has(url.protocol)) {
        throw new TypeError(`/list.json entry ${entry.path} must stay on the web server origin`);
      }
      if (seen.has(url.href)) continue;
      seen.add(url.href);
      resolved.push({ label: entry.label, href: url.href });
    }
    return resolved;
  }

  function removeLegacyLayoutParameter(locationObject, historyObject) {
    try {
      const url = new URL(locationObject.href);
      if (!url.searchParams.has("layout")) return false;
      url.searchParams.delete("layout");
      historyObject.replaceState(historyObject.state, "", `${url.pathname}${url.search}${url.hash}`);
      return true;
    } catch {
      return false;
    }
  }

  function makeOption(documentObject, label, value = "") {
    const option = documentObject.createElement("option");
    option.value = value;
    option.textContent = label;
    return option;
  }

  function setSelectMessage(select, documentObject, message) {
    select.replaceChildren(makeOption(documentObject, message));
    select.disabled = true;
    select.title = message;
  }

  async function populateServerLayouts(rootObject) {
    const documentObject = rootObject.document;
    const select = documentObject.getElementById("server-layout-select");
    if (!select) return [];

    setSelectMessage(select, documentObject, "Loading server layouts…");
    if (!new Set(["http:", "https:"]).has(rootObject.location.protocol)) {
      setSelectMessage(select, documentObject, "Server layouts require HTTP");
      return [];
    }

    const rootUrl = new URL("/", rootObject.location.href);
    const listUrl = new URL("list.json", rootUrl);
    let response;
    try {
      response = await rootObject.fetch(listUrl.href, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
    } catch (error) {
      setSelectMessage(select, documentObject, "Server layouts unavailable");
      console.warn(`Could not fetch ${listUrl.href}`, error);
      return [];
    }

    if (response.status === 404) {
      setSelectMessage(select, documentObject, "No /list.json");
      return [];
    }
    if (!response.ok) {
      setSelectMessage(select, documentObject, "Server layouts unavailable");
      console.warn(`Could not fetch ${listUrl.href}: HTTP ${response.status} ${response.statusText}`);
      return [];
    }

    let entries;
    try {
      entries = resolveList(await response.json(), rootUrl.href);
    } catch (error) {
      setSelectMessage(select, documentObject, "Invalid /list.json");
      console.warn(`Could not parse ${listUrl.href}`, error);
      return [];
    }

    if (!entries.length) {
      setSelectMessage(select, documentObject, "No server layouts");
      return [];
    }

    const fragment = documentObject.createDocumentFragment();
    fragment.append(makeOption(documentObject, "Load server layout…"));
    for (const entry of entries) fragment.append(makeOption(documentObject, entry.label, entry.href));
    select.replaceChildren(fragment);
    select.disabled = false;
    select.title = `${entries.length} layout${entries.length === 1 ? "" : "s"} from /list.json`;
    return entries;
  }

  function initialize(rootObject) {
    const documentObject = rootObject.document;
    const select = documentObject.getElementById("server-layout-select");
    const legacyInput = documentObject.getElementById("layout-url");
    const legacyButton = documentObject.getElementById("load-url-button");
    if (!select || !legacyInput || !legacyButton) return;

    select.addEventListener("change", () => {
      if (!select.value) return;
      legacyInput.value = select.value;
      legacyButton.click();
    });
    void populateServerLayouts(rootObject);
  }

  const api = Object.freeze({ normalizeList, resolveList, removeLegacyLayoutParameter });
  globalThis.LayoutStudioServerLayouts = api;

  if (globalThis.document && globalThis.location && globalThis.history) {
    removeLegacyLayoutParameter(globalThis.location, globalThis.history);
    if (globalThis.document.readyState === "loading") {
      globalThis.addEventListener("DOMContentLoaded", () => initialize(globalThis), { once: true });
    } else {
      initialize(globalThis);
    }
  }
})();
