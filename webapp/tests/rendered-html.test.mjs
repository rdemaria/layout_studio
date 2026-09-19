import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";
import {gunzipSync} from "node:zlib";

test("hosted LHC JSON streams the complete precompressed asset with correct HTTP headers", async () => {
  const {default: worker} = await import("../dist/server/index.js");
  const plain = await readFile(new URL("../public/layouts/LHC--LS3.json", import.meta.url));
  const encoded = await readFile(new URL("../dist/client/layouts/LHC--LS3.json.gz", import.meta.url));
  assert.ok(encoded.length < 25 * 1024 * 1024);
  const env = {ASSETS: {fetch: async request => {
    assert.equal(new URL(request.url).pathname, "/layouts/LHC--LS3.json.gz");
    return new Response(encoded, {headers: {"Content-Type": "application/gzip"}});
  }}};
  const response = await worker.fetch(new Request("http://localhost/layouts/LHC--LS3.json"), env, {});
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Content-Type"), "application/json");
  assert.equal(response.headers.get("Content-Encoding"), "gzip");
  assert.deepEqual(gunzipSync(await response.arrayBuffer()), plain);
  const missing = await worker.fetch(new Request("http://localhost/layouts/LHC--LS3.json"),
    {ASSETS: {fetch: async () => new Response("Not found", {status: 404})}}, {});
  assert.equal(missing.status, 404);
});

const developmentPreviewMeta =
  /<meta(?=[^>]*\bname=["']codex-preview["'])(?=[^>]*\bcontent=["']development["'])[^>]*>/i;

test("renders development preview metadata", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  const response = await worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );

  assert.equal(response.status, 200);
  assert.match(
    response.headers.get("content-type") ?? "",
    /^text\/html\b/i,
  );
  assert.match(await response.text(), developmentPreviewMeta);
});
