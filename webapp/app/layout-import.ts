/** Read a local JSON file directly as text. */
export async function readLayoutJson(source: Blob): Promise<unknown> {
  return JSON.parse(await source.text());
}

export async function fetchLayoutJson(
  url: string,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
