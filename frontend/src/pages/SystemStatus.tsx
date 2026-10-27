import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { HealthResponse, TestJobResponse } from "../api/types";

const SERVICES: { key: keyof HealthResponse["checks"]; label: string }[] = [
  { key: "database", label: "Database (PostgreSQL)" },
  { key: "queue", label: "Job queue (Redis)" },
  { key: "storage", label: "File storage" },
  { key: "worker", label: "Background worker" },
];

const REFRESH_MS = 10_000;

export default function SystemStatus() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [job, setJob] = useState<TestJobResponse | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const loadHealth = useCallback(async () => {
    try {
      setHealth(await api.get<HealthResponse>("/health"));
      setApiError(null);
    } catch (err) {
      setHealth(null);
      setApiError(err instanceof Error ? err.message : "API unreachable");
    }
  }, []);

  useEffect(() => {
    loadHealth();
    const timer = window.setInterval(loadHealth, REFRESH_MS);
    return () => {
      window.clearInterval(timer);
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, [loadHealth]);

  const pollJob = useCallback((jobId: string, attempt = 0) => {
    pollRef.current = window.setTimeout(async () => {
      try {
        const latest = await api.get<TestJobResponse>(`/health/test-job/${jobId}`);
        setJob(latest);
        const done = ["finished", "failed", "stopped", "canceled"].includes(latest.status);
        if (!done && attempt < 20) pollJob(jobId, attempt + 1);
        if (!done && attempt >= 20) setJobError("Job did not finish in 10 seconds. Is the worker running?");
      } catch (err) {
        setJobError(err instanceof Error ? err.message : "Could not read job status");
      }
    }, 500);
  }, []);

  const runTestJob = async () => {
    setJob(null);
    setJobError(null);
    try {
      const started = await api.post<TestJobResponse>("/health/test-job");
      setJob(started);
      pollJob(started.job_id);
    } catch (err) {
      setJobError(err instanceof Error ? err.message : "Could not start job");
    }
  };

  const jobRunning = job !== null && !["finished", "failed", "stopped", "canceled"].includes(job.status);

  return (
    <section>
      <h1 className="page-title">System status</h1>

      <div className="card">
        <div className="card-head">
          <h2>Services</h2>
          {health && (
            <span className={`badge ${health.status === "ok" ? "badge-ok" : "badge-bad"}`}>
              {health.status === "ok" ? "All systems running" : "Some services down"}
            </span>
          )}
        </div>

        {apiError && (
          <p className="error-text">
            API not connected: {apiError}. Check that the backend container is running.
          </p>
        )}

        {health && (
          <>
            <ul className="status-list">
              {SERVICES.map(({ key, label }) => {
                const check = health.checks[key];
                return (
                  <li key={key} className="status-row">
                    <span className={`dot ${check.ok ? "dot-ok" : "dot-bad"}`} aria-hidden="true" />
                    <span className="status-label">{label}</span>
                    <span className={check.ok ? "status-ok" : "status-bad"}>
                      {check.ok ? "Running" : "Down"}
                      {key === "worker" && check.count !== undefined ? ` (${check.count})` : ""}
                      {key === "database" && check.schema ? ` · schema ${check.schema}` : ""}
                    </span>
                    {check.error && <span className="status-error">{check.error}</span>}
                  </li>
                );
              })}
            </ul>
            <p className="meta">
              API connected · {health.app} v{health.version} · {health.env} · refreshes every 10 s
            </p>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Worker test</h2>
          <button className="button" onClick={runTestJob} disabled={jobRunning}>
            {jobRunning ? "Running…" : "Run test job"}
          </button>
        </div>
        <p className="muted">
          Sends a job through the queue to the background worker and waits for its answer.
        </p>
        {job && (
          <p>
            Job <code>{job.job_id.slice(0, 8)}</code>: <strong>{job.status}</strong>
            {job.result && <> · worker replied “{job.result.message}”</>}
          </p>
        )}
        {jobError && <p className="error-text">{jobError}</p>}
      </div>
    </section>
  );
}
