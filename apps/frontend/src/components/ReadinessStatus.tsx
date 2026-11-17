import { useCallback, useEffect, useState } from "react";
import { fetchReadiness, type Readiness } from "../services/api";

type State = { kind: "loading" } | Readiness;

const POLL_MS = 10_000;

const LABELS: Record<State["kind"], string> = {
  loading: "Checking…",
  ready: "Ready",
  unavailable: "Unavailable",
  "network-error": "API unreachable",
};

function describe(state: State): string {
  switch (state.kind) {
    case "loading":
      return "Contacting the API.";
    case "ready":
      return "The API is up and the database (with pgvector) is reachable.";
    case "unavailable":
      return "The API is running, but a dependency is not ready.";
    case "network-error":
      return state.message;
  }
}

export function ReadinessStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const check = useCallback((signal?: AbortSignal) => {
    fetchReadiness(signal).then(setState, () => {
      // Aborted on unmount; nothing to update.
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    check(controller.signal);
    const timer = setInterval(() => check(controller.signal), POLL_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [check]);

  const checks = state.kind === "ready" || state.kind === "unavailable" ? state.checks : null;

  return (
    <section className="status" aria-labelledby="readiness-heading" aria-live="polite">
      <h2 id="readiness-heading">Backend readiness</h2>
      <p className={`badge badge-${state.kind}`} data-testid="readiness-label">
        {LABELS[state.kind]}
      </p>
      <p>{describe(state)}</p>
      {checks && (
        <ul className="checks">
          {Object.entries(checks).map(([name, value]) => (
            <li key={name}>
              <code>{name}</code>: {value}
            </li>
          ))}
        </ul>
      )}
      <button type="button" onClick={() => check()}>
        Check again
      </button>
    </section>
  );
}
