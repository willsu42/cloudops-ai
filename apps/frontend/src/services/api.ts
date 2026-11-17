// Typed client for the CloudOps AI API. Requests use relative URLs; in
// development Vite proxies them to the backend (see vite.config.ts).

export type CheckState = "ok" | "unavailable" | "missing" | "skipped";

export interface ReadyResponse {
  status: "ready" | "unavailable";
  checks: Record<string, CheckState>;
}

export type Readiness =
  | { kind: "ready"; checks: Record<string, CheckState> }
  | { kind: "unavailable"; checks: Record<string, CheckState> }
  | { kind: "network-error"; message: string };

function isReadyResponse(value: unknown): value is ReadyResponse {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    (v.status === "ready" || v.status === "unavailable") &&
    typeof v.checks === "object" &&
    v.checks !== null
  );
}

/**
 * Fetch /health/ready. 200 and 503 are both valid API answers ("ready" and
 * "unavailable"). Anything else, including a failed request or an error from
 * the dev proxy when the API is down, means the API itself was not reached.
 */
export async function fetchReadiness(signal?: AbortSignal): Promise<Readiness> {
  let response: Response;
  try {
    response = await fetch("/health/ready", { signal, headers: { Accept: "application/json" } });
  } catch (error) {
    if (signal?.aborted) throw error;
    return { kind: "network-error", message: "Could not reach the API." };
  }
  if (response.status !== 200 && response.status !== 503) {
    return { kind: "network-error", message: `The API did not respond (HTTP ${response.status}).` };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { kind: "network-error", message: "The API returned an unexpected response." };
  }
  if (!isReadyResponse(body)) {
    return { kind: "network-error", message: "The API returned an unexpected response." };
  }
  return body.status === "ready" && response.status === 200
    ? { kind: "ready", checks: body.checks }
    : { kind: "unavailable", checks: body.checks };
}
