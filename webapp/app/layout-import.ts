/** Read JSON or gzip-compressed JSON, including responses already decoded by HTTP. */
export async function readLayoutJson(source: Blob | Response): Promise<unknown> {
  const buffer = await source.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let text: string;

  // Content-Encoding: gzip is decoded by fetch itself. Inspect the payload so
  // raw .gz downloads are decompressed once, regardless of the name or headers.
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("This browser does not support gzip decompression");
    }
    try {
      const stream = new Blob([buffer]).stream().pipeThrough(
        new DecompressionStream("gzip"),
      );
      text = await new Response(stream).text();
    } catch (error) {
      throw new Error("Could not decompress gzip layout", { cause: error });
    }
  } else {
    text = new TextDecoder().decode(buffer);
  }

  return JSON.parse(text);
}

export async function fetchLayoutJson(
  url: string,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return readLayoutJson(response);
}
