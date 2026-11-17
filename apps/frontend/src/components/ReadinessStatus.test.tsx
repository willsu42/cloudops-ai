import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReadinessStatus } from "./ReadinessStatus";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const label = () => screen.getByTestId("readiness-label");

describe("ReadinessStatus", () => {
  it("shows loading until the API answers", async () => {
    let resolve: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => (resolve = r))));
    render(<ReadinessStatus />);
    expect(label()).toHaveTextContent("Checking");
    resolve(jsonResponse(200, { status: "ready", checks: { database: "ok" } }));
    expect(await screen.findByText("Ready")).toBeInTheDocument();
  });

  it("shows ready with the individual checks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(200, { status: "ready", checks: { database: "ok", vector_extension: "ok" } }),
      ),
    );
    render(<ReadinessStatus />);
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("vector_extension")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith("/health/ready", expect.anything());
  });

  it("shows unavailable when the API reports 503", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(503, {
          status: "unavailable",
          checks: { database: "unavailable", vector_extension: "skipped" },
        }),
      ),
    );
    render(<ReadinessStatus />);
    expect(await screen.findByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByText(/a dependency is not ready/)).toBeInTheDocument();
  });

  it("shows a network error when the request fails, and recovers on retry", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonResponse(200, { status: "ready", checks: { database: "ok" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ReadinessStatus />);
    expect(await screen.findByText("API unreachable")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByText("Ready")).toBeInTheDocument();
  });

  it("treats a proxy error (API down) as unreachable, not as unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 502 })));
    render(<ReadinessStatus />);
    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
    expect(screen.getByText(/HTTP 502/)).toBeInTheDocument();
  });
});
