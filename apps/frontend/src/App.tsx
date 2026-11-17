import { ReadinessStatus } from "./components/ReadinessStatus";

export function App() {
  return (
    <main className="page">
      <header>
        <h1>CloudOps AI</h1>
        <p className="stage">Week 1 foundation</p>
      </header>
      <p>
        This build contains the local application skeleton and a document corpus about the
        OpenTelemetry Demo. Incident investigation, retrieval, and question answering are not
        implemented yet.
      </p>
      <ReadinessStatus />
    </main>
  );
}
