import {readFile, writeFile, rm} from "node:fs/promises";
import {gzipSync} from "node:zlib";

// Keep the source and standalone catalog as plain JSON. HTTP compression keeps
// the hosted LHC asset below the 25 MiB per-file limit without changing its URL.
const root = new URL("../", import.meta.url);
const path = "layouts/LHC--LS3.json";
const plain = await readFile(new URL(`public/${path}`, root));
const encoded = gzipSync(plain, {level: 9});
if (encoded.byteLength > 25 * 1024 * 1024) throw new Error("Compressed LHC layout exceeds the static asset limit");
await writeFile(new URL(`dist/client/${path}.gz`, root), encoded);
await rm(new URL(`dist/client/${path}`, root), {force: true});
console.log(`Prepared ${path}: ${encoded.byteLength} bytes over HTTP`);
