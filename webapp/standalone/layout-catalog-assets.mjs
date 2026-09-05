import { copyFile, mkdir, readFile } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";

const CATALOG_BASE = new URL(
  "https://layout-studio.invalid/standalone/list.json",
);
const CATALOG_DIRECTORY = "/standalone/";
const MAX_CATALOG_ASSETS = 500;

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function catalogEntries(value) {
  if (Array.isArray(value)) return value;
  if (isRecord(value) && Array.isArray(value.layouts)) return value.layouts;
  if (isRecord(value) && Array.isArray(value.files)) return value.files;
  throw new TypeError(
    "list.json must be an array or an object with a layouts/files array",
  );
}

function catalogEntryPath(value) {
  if (typeof value === "string") return value.trim();
  if (!isRecord(value) || typeof value.path !== "string") return "";
  return value.path.trim();
}

/**
 * Return the portable, local JSON assets referenced by a layout catalog.
 *
 * Standalone catalogs deliberately accept relative paths only. That keeps the
 * catalog working when the build directory is mounted below an arbitrary URL
 * (for example the tokenized path used by the Python viewer).
 */
export function localLayoutCatalogAssetPaths(value) {
  const paths = [];
  const seen = new Set();

  for (const rawEntry of catalogEntries(value)) {
    const entryPath = catalogEntryPath(rawEntry);
    if (
      !entryPath ||
      entryPath.startsWith("/") ||
      entryPath.startsWith("\\") ||
      entryPath.includes("\\") ||
      /^[a-z][a-z\d+.-]*:/i.test(entryPath) ||
      /%(?:00|2f|5c)/i.test(entryPath)
    ) {
      continue;
    }

    let resolved;
    try {
      resolved = new URL(entryPath, CATALOG_BASE);
    } catch {
      continue;
    }
    if (
      resolved.origin !== CATALOG_BASE.origin ||
      !resolved.pathname.startsWith(CATALOG_DIRECTORY)
    ) {
      continue;
    }

    let relativePath;
    try {
      relativePath = decodeURIComponent(
        resolved.pathname.slice(CATALOG_DIRECTORY.length),
      );
    } catch {
      continue;
    }
    const lowerPath = relativePath.toLowerCase();
    if (
      !relativePath ||
      (!lowerPath.endsWith(".json") && !lowerPath.endsWith(".json.gz")) ||
      seen.has(relativePath)
    ) {
      continue;
    }

    seen.add(relativePath);
    paths.push(relativePath);
    if (paths.length >= MAX_CATALOG_ASSETS) break;
  }

  return paths;
}

export async function copyLayoutCatalogAssets(publicRoot, buildRoot) {
  const catalogSource = resolve(publicRoot, "list.json");
  let catalogText;
  try {
    catalogText = await readFile(catalogSource, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }

  const assetPaths = localLayoutCatalogAssetPaths(JSON.parse(catalogText));
  await mkdir(buildRoot, { recursive: true });
  await copyFile(catalogSource, resolve(buildRoot, "list.json"));

  for (const assetPath of assetPaths) {
    const source = resolveInside(publicRoot, assetPath);
    const target = resolveInside(buildRoot, assetPath);
    await mkdir(dirname(target), { recursive: true });
    await copyFile(source, target);
  }

  return ["list.json", ...assetPaths];
}

function resolveInside(root, relativePath) {
  const target = resolve(root, relativePath);
  const relation = relative(resolve(root), target);
  if (
    relation === ".." ||
    relation.startsWith(`..${sep}`) ||
    isAbsolute(relation)
  ) {
    throw new TypeError(`Catalog asset escapes its root: ${relativePath}`);
  }
  return target;
}
