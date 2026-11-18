# ADR 002: Use a single bounded agent

- **Status:** Accepted (planned for Week 4; nothing implemented yet)
- **Date:** 2026-11-17

## Context

Investigations, starting with OpenTelemetry Demo checkout failures, need
several steps: retrieve documentation, query telemetry and deployment
evidence, compare it, and report with citations. This could be built as one
agent or as several cooperating agents. The project must stay explainable,
testable, and cheap to run, and it must never take unapproved actions.

## Decision

Use **one** LangGraph workflow with explicit state and bounded control flow:

- Nodes: plan, gather evidence (retrieval plus allowlisted read-only tools),
  analyze, an optional single query refinement, and report.
- Hard limits, starting from the PROJECT_PLAN §3 values: model calls, tool
  calls, tokens, per-tool timeouts, and total active time. On a limit or
  missing evidence the result is a partial report or an abstention, never
  invented tool output.
- Persisted jobs and graph checkpoints, so work survives restarts.
- Any state-changing action is simulated only and requires persisted human
  approval (Week 7).

## Alternatives considered

- **Multiple specialized agents** (for example a log agent, a retrieval agent,
  and a planner). They can split responsibilities, but they add coordination
  logic and more permission boundaries, make evaluation harder (which agent
  caused a failure?), and multiply model cost.
- **A fixed, non-agentic pipeline.** Simplest and deterministic, but it cannot
  adapt which evidence to gather. It is kept as an experimental baseline
  (PROJECT_PLAN §8, experiment 2).

## Consequences

- Behavior is easier to trace, bound, and test, including restart, timeout,
  and loop-limit tests.
- A single agent may handle very different incident types less well than
  specialists would. Measure this before considering a split.
- Revisit only if evaluation shows a single workflow cannot reach the quality
  targets within budget.
