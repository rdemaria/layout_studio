/** Opt-in diagnostics for large layouts: open the app with ?profile=1. */
export function beginLayoutProfile(): number | undefined {
  if (typeof window === "undefined" || !new URLSearchParams(window.location.search).has("profile")) return;
  return performance.now();
}

export function endLayoutProfile(stage: string, started: number | undefined, details: Record<string, unknown> = {}): void {
  if (started === undefined) return;
  console.info(`[layout-profile] ${JSON.stringify({stage, milliseconds: performance.now() - started, ...details})}`);
}
