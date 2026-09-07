export type LayoutUrlSuggestion = {
  href: string;
  label?: string;
  path: string;
};

const MAX_URL_SUGGESTIONS = 500;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function catalogEntries(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (isRecord(value) && Array.isArray(value.layouts)) return value.layouts;
  if (isRecord(value) && Array.isArray(value.files)) return value.files;
  throw new TypeError(
    "list.json must be an array or an object with a layouts/files array",
  );
}

function catalogEntry(
  value: unknown,
): { label?: string; path: string } | null {
  if (typeof value === "string") {
    const path = value.trim();
    return path ? { path } : null;
  }
  if (!isRecord(value) || typeof value.path !== "string") return null;

  const path = value.path.trim();
  if (!path) return null;
  const label =
    typeof value.label === "string" && value.label.trim()
      ? value.label.trim()
      : undefined;
  return { path, ...(label ? { label } : {}) };
}

export function parseLayoutUrlList(
  value: unknown,
  catalogUrl: string | URL,
): LayoutUrlSuggestion[] {
  const base = new URL(catalogUrl);
  if (base.protocol !== "http:" && base.protocol !== "https:") {
    throw new TypeError("list.json must use HTTP or HTTPS");
  }

  const seen = new Set<string>();
  const suggestions: LayoutUrlSuggestion[] = [];

  for (const rawEntry of catalogEntries(value)) {
    const entry = catalogEntry(rawEntry);
    if (!entry) continue;

    let resolved: URL;
    try {
      resolved = new URL(entry.path, base);
    } catch {
      continue;
    }
    if (
      (resolved.protocol !== "http:" && resolved.protocol !== "https:") ||
      resolved.origin !== base.origin ||
      resolved.username ||
      resolved.password
    ) {
      continue;
    }

    resolved.hash = "";
    if (seen.has(resolved.href)) continue;
    seen.add(resolved.href);
    suggestions.push({
      href: resolved.href,
      path: entry.path,
      ...(entry.label ? { label: entry.label } : {}),
    });
    if (suggestions.length >= MAX_URL_SUGGESTIONS) break;
  }

  return suggestions;
}

export function layoutCatalogUrl(documentUrl: string | URL): URL {
  return new URL("list.json", documentUrl);
}

export function resolveLayoutUrl(
  value: string,
  catalogUrl: string | URL,
): string {
  const input = value.trim();
  if (!input) throw new TypeError("Layout URL is empty");
  return new URL(input, catalogUrl).href;
}
