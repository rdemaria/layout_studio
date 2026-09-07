import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";

import { localLayoutCatalogAssetPaths } from "../standalone/layout-catalog-assets.mjs";

const webappRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("selects only portable local JSON assets from a catalog", () => {
  assert.deepEqual(
    localLayoutCatalogAssetPaths({
      files: [
        "layouts/one.json",
        { path: "./layouts/two.json.gz?download=1#latest" },
        "layouts/one.json?duplicate=1",
        "../outside.json",
        "/absolute.json",
        "https://example.test/remote.json",
        "layouts/not-json.txt",
        "layouts/%2Fencoded-slash.json",
        { label: "Missing path" },
      ],
    }),
    ["layouts/one.json", "layouts/two.json.gz"],
  );
});

test("standalone build contains list.json and every local catalog asset", async () => {
  const publicCatalogText = await readFile(
    resolve(webappRoot, "public/list.json"),
    "utf8",
  );
  const builtCatalogText = await readFile(
    resolve(webappRoot, "build/list.json"),
    "utf8",
  );
  assert.equal(builtCatalogText, publicCatalogText);

  const assetPaths = localLayoutCatalogAssetPaths(JSON.parse(publicCatalogText));
  assert.ok(assetPaths.length > 0, "the checked-in catalog should not be empty");

  for (const assetPath of assetPaths) {
    const content = await readFile(resolve(webappRoot, "build", assetPath));
    const json = assetPath.toLowerCase().endsWith(".gz")
      ? gunzipSync(content).toString("utf8")
      : content.toString("utf8");
    assert.doesNotThrow(() => JSON.parse(json), `${assetPath} must contain JSON`);
  }
});
