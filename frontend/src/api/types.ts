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

export type RoleName = "adjudicator" | "senior_reviewer" | "fraud_investigator" | "team_lead" | "admin";

export const ROLE_LABELS: Record<RoleName, string> = {
  adjudicator: "Adjudicator",
  senior_reviewer: "Senior reviewer",
  fraud_investigator: "Fraud investigator",
  team_lead: "Team lead",
  admin: "Admin",
};

export interface Me {
  id: string;
  email: string;
  full_name: string;
  role: RoleName;
  permissions: string[];
}

export interface UserRow {
  id: string;
  email: string;
  full_name: string;
  role: RoleName;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface AuditEntry {
  id: number;
  at: string;
  user_id: string | null;
  user_email: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  details: Record<string, unknown>;
  ip_address: string | null;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  limit: number;
  offset: number;
}