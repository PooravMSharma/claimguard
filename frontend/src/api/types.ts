export interface ServiceCheck {
  ok: boolean;
  error?: string;
  count?: number;
  backend?: string;
  schema?: string | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  app: string;
  version: string;
  env: string;
  checks: Record<"database" | "queue" | "storage" | "worker", ServiceCheck>;
}

export type JobStatus =
  | "queued"
  | "started"
  | "finished"
  | "failed"
  | "deferred"
  | "scheduled"
  | "stopped"
  | "canceled";

export interface TestJobResponse {
  job_id: string;
  status: JobStatus;
  result?: { message: string; processed_at: string } | null;
}