import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { copyLayoutCatalogAssets } from "./layout-catalog-assets.mjs";

const standaloneRoot = dirname(fileURLToPath(import.meta.url));
const outputRoot = resolve(standaloneRoot, "dist");
const publicRoot = resolve(standaloneRoot, "../public");
const buildRoot = resolve(standaloneRoot, "../build");
const target = resolve(buildRoot, "index.html");
let html = await readFile(resolve(outputRoot, "index.html"), "utf8");

html = await replaceAsync(
  html,
  /<link\b(?=[^>]*\brel="stylesheet")(?=[^>]*\bhref="([^"]+)")[^>]*>/g,
  async (_match, href) => {
    const css = await readFile(resolve(outputRoot, href), "utf8");
    return `<style>\n${css.replace(/<\/style/gi, "<\\/style")}\n</style>`;
  },
);

html = await replaceAsync(
  html,
  /<script\b(?=[^>]*\btype="module")(?=[^>]*\bsrc="([^"]+)")[^>]*><\/script>/g,
  async (_match, src) => {
    const javascript = await readFile(resolve(outputRoot, src), "utf8");
    return `<script type="module">\n${javascript.replace(/<\/script/gi, "<\\/script")}\n</script>`;
  },
);

html = html.replace(/<link\b[^>]*\brel="modulepreload"[^>]*>/g, "");

if (/\b(?:src|href)="\.?\/assets\//.test(html)) {
  throw new Error("The generated page still refers to external build assets");
}

await writeFile(target, html);
await copyLayoutCatalogAssets(publicRoot, buildRoot);

async function replaceAsync(input, expression, replacer) {
  const matches = [...input.matchAll(expression)];
  const replacements = await Promise.all(matches.map((match) => replacer(...match)));
  let cursor = 0;
  let result = "";
  for (let index = 0; index < matches.length; index += 1) {
    const match = matches[index];
    result += input.slice(cursor, match.index) + replacements[index];
    cursor = match.index + match[0].length;
  }
  return result + input.slice(cursor);
}
