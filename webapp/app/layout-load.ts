import LayoutLoadWorker from "./layout-load.worker?worker&inline";
import {parseLayout, type LayoutData} from "./layout-data";
import {parseLayoutJson} from "./layout-json";
import {endLayoutProfile, beginLayoutProfile} from "./layout-performance";

export type LayoutSource = {url: string} | {file: Blob} | {value: unknown};

/** Parsing and graph validation run away from the UI. Workers are bundled into standalone HTML too. */
export async function loadLayoutAsync(source: LayoutSource, signal?: AbortSignal): Promise<LayoutData> {
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  if (typeof Worker === "undefined") {
    let value: unknown;
    if ("url" in source) {
      const response = await fetch(source.url, {signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      value = parseLayoutJson(await response.text());
    } else value = "file" in source ? parseLayoutJson(await source.file.text()) : source.value;
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    return parseLayout(value);
  }
  const started = beginLayoutProfile();
  return new Promise((resolve, reject) => {
    const worker = new LayoutLoadWorker();
    const close = () => {worker.terminate(); signal?.removeEventListener("abort", abort);};
    const abort = () => {close(); reject(new DOMException("Aborted", "AbortError"));};
    signal?.addEventListener("abort", abort, {once: true});
    worker.onerror = (event) => {close(); reject(new Error(event.message || "Could not load layout worker"));};
    worker.onmessage = (event) => {
      close();
      if (event.data.error) reject(new Error(event.data.error));
      else {
        endLayoutProfile("loadWorker", started, event.data.timings);
        resolve(event.data.layout as LayoutData);
      }
    };
    try { worker.postMessage(source); }
    catch (error) { close(); reject(error); }
  });
}
