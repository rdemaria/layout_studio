/** Decode JSON without silently replacing an earlier definition of a name. */
export function parseLayoutJson(text: string): unknown {
  const value: unknown = JSON.parse(text);
  // The native parser checks grammar. This iterative scan only checks decoded
  // member names; it neither reimplements number parsing nor recurses on depth.
  const stack: {keys?: Set<string>; expectsKey: boolean}[] = [];
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char === '"') {
      const start = index++;
      while (index < text.length && text[index] !== '"') {
        if (text[index] === "\\") index++;
        index++;
      }
      const parent = stack.at(-1);
      if (parent?.keys && parent.expectsKey) {
        const key: string = JSON.parse(text.slice(start, index + 1));
        if (parent.keys.has(key)) throw new Error(`Duplicate JSON member ${JSON.stringify(key)}`);
        parent.keys.add(key);
        parent.expectsKey = false;
      }
    } else if (char === "{") stack.push({keys: new Set(), expectsKey: true});
    else if (char === "[") stack.push({expectsKey: false});
    else if (char === "}" || char === "]") stack.pop();
    else if (char === "," && stack.at(-1)?.keys) stack.at(-1)!.expectsKey = true;
  }
  return value;
}
