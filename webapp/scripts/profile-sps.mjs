import { fileURLToPath } from "node:url";

// Preserve the existing SPS profiling entrypoint.
process.argv.splice(2, 0, fileURLToPath(new URL("../public/layouts/SPS--LS3.json", import.meta.url)));
await import("./profile-layout.mjs");
