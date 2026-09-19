import {readFile, writeFile} from "node:fs/promises";
import {gunzipSync} from "node:zlib";

// Store the large snapshot losslessly compressed in Git, and expose plain JSON
// to development and standalone viewers without changing their import format.
const path = new URL("../public/layouts/LHC--LS3.json", import.meta.url);
await writeFile(path, gunzipSync(await readFile(new URL(`${path.href}.gz`))));
