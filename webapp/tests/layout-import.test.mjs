import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer as createHttpServer } from "node:http";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom",
  configFile: false,
  root,
  server: { middlewareMode: true },
});
const { readLayoutJson, fetchLayoutJson } = await vite.ssrLoadModule(
  "/app/layout-import.ts",
);
const { layoutUrlFromQuery } = await vite.ssrLoadModule(
  "/app/layout-url-catalog.ts",
);
const { parseLayout } = await vite.ssrLoadModule("/app/layout-data.ts");
const sampleBytes = await readFile(new URL("../public/layouts/sample-layout.json", import.meta.url));
const sample = JSON.parse(sampleBytes.toString("utf8"));
const compressed = gzipSync(sampleBytes);

const server = createHttpServer((request, response) => {
  switch (request.url) {
    case "/raw.json.gz":
      response.setHeader("Content-Type", "application/gzip");
      response.end(compressed);
      break;
    case "/encoded.json.gz":
      response.setHeader("Content-Type", "application/json");
      response.setHeader("Content-Encoding", "gzip");
      response.end(compressed);
      break;
    case "/plain.json":
      response.setHeader("Content-Type", "application/json");
      response.end(sampleBytes);
      break;
    default:
      response.writeHead(404);
      response.end("Not found");
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const baseUrl = `http://127.0.0.1:${server.address().port}/`;

after(async () => {
  server.closeAllConnections();
  await Promise.all([
    vite.close(),
    new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  ]);
});

test("imports JSON and gzip files based on bytes rather than name or MIME type", async () => {
  for (const [name, bytes, type] of [
    ["layout.json", sampleBytes, "application/json"],
    ["layout.json.gz", compressed, "application/gzip"],
    ["layout.gz", compressed, ""],
    ["layout.json", compressed, "application/json"],
    ["layout.json.gz", sampleBytes, "application/gzip"],
    ["bom.json", Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), sampleBytes]), ""],
  ]) {
    const file = new File([bytes], name, { type });
    assert.deepEqual(await readLayoutJson(file), sample, name);
  }
  const unicode = { name: "SPS — entrée" };
  assert.deepEqual(
    await readLayoutJson(new File([gzipSync(JSON.stringify(unicode))], "unicode.gz")),
    unicode,
  );
});

test("loads plain, raw gzip, and HTTP-decoded gzip URL responses", async () => {
  for (const path of ["plain.json", "raw.json.gz", "encoded.json.gz"]) {
    assert.deepEqual(await fetchLayoutJson(new URL(path, baseUrl).href), sample, path);
  }
});

test("reports failed URL requests and supports cancellation", async () => {
  await assert.rejects(fetchLayoutJson(new URL("missing.json.gz", baseUrl).href), /HTTP 404/);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(fetchLayoutJson(baseUrl, controller.signal), { name: "AbortError" });
});

test("rejects corrupt gzip and invalid JSON", async () => {
  await assert.rejects(
    readLayoutJson(new File([compressed.subarray(0, 12)], "broken.gz")),
    /Could not decompress gzip layout/,
  );
  for (const bytes of ["{", gzipSync("{"), ""]) {
    await assert.rejects(readLayoutJson(new File([bytes], "invalid.json")), SyntaxError);
  }
});

test("reads the shipped SPS and M2 gzip conversions as valid editor layouts", async () => {
  for (const [machine, count] of [["SPS", 12339], ["M2", 428]]) {
    const name = `${machine}--LS3.json.gz`;
    const bytes = await readFile(new URL(`../public/layouts/${name}`, import.meta.url));
    const layout = parseLayout(await readLayoutJson(new File([bytes], name)));
    assert.equal(Object.keys(layout.objects).length, count, machine);
  }
});

test("debug URL query accepts relative, external, quoted, and encoded values", () => {
  const page = new URL("https://layout.example/studio/index.html");
  for (const value of ["", "  ", '""', "''"]) {
    page.searchParams.set("url", value);
    assert.equal(layoutUrlFromQuery(page), null);
  }
  page.search = "";
  assert.equal(layoutUrlFromQuery(page), null);

  const external = "https://other.example/SPS.json.gz?download=1&version=LS3#layout";
  for (const value of [external, `"${external}"`, `'${external}'`]) {
    page.searchParams.set("url", value);
    assert.equal(layoutUrlFromQuery(page), external);
  }
  assert.equal(
    layoutUrlFromQuery('https://layout.example/studio/?url="layouts/SPS--LS3.json.gz"'),
    "https://layout.example/studio/layouts/SPS--LS3.json.gz",
  );
  page.searchParams.set("url", " ../M2.json.gz ");
  assert.equal(layoutUrlFromQuery(page), "https://layout.example/M2.json.gz");
  page.searchParams.set("url", "data:application/json,{}");
  assert.throws(() => layoutUrlFromQuery(page), /must use HTTP or HTTPS/);
  page.searchParams.set("url", "https://[");
  assert.throws(() => layoutUrlFromQuery(page), TypeError);
});
