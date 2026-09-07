import {parseLayout} from "./layout-data";
import {parseLayoutJson} from "./layout-json";

self.onmessage = async (event: MessageEvent) => {
  try {
    const {url, file, value} = event.data;
    let input = value;
    const started = performance.now();
    if (url) {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      input = parseLayoutJson(await response.text());
    } else if (file) input = parseLayoutJson(await (file as Blob).text());
    const decoded = performance.now();
    const layout = parseLayout(input);
    self.postMessage({layout, timings: {decodeMs: decoded - started, validateMs: performance.now() - decoded}});
  } catch (error) {
    self.postMessage({error: error instanceof Error ? error.message : "Invalid layout"});
  }
};
