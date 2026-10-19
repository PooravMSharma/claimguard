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

// ---------- Step 4: reference data ----------

export interface ReferenceImport {
  id: number;
  dataset: string;
  version: string;
  source_name: string;
  source_url: string | null;
  effective_from: string | null;
  file_name: string | null;
  file_sha256: string | null;
  row_count: number;
  notes: string | null;
  imported_at: string;
}

export interface DatasetSummary {
  dataset: string;
  label: string;
  rows: number;
  latest_import: ReferenceImport | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PackageRow {
  id: number;
  code: string;
  version: string;
  name: string;
  specialty: string | null;
  rate: string | null;
  expected_los_days: number | null;
  is_day_care: boolean | null;
  mandatory_documents: string[];
  inclusions: string[];
  effective_from: string | null;
}

export interface TariffRow {
  id: number;
  source: string;
  code: string;
  name: string;
  category: string | null;
  specialty: string | null;
  city_tier: string;
  nabh_accredited: boolean | null;
  super_speciality: boolean;
  ward_type: string;
  ward_uniform: boolean;
  rate: string;
  effective_from: string;
  effective_to: string | null;
}

export interface PriceResult {
  code: string;
  name: string;
  category: string | null;
  city_tier: string;
  grade: string;
  ward_type: string;
  ward_uniform: boolean;
  base_rate: string;
  base_grade: string;
  base_city_tier: string;
  rate: string;
  effective_from: string;
  adjustments: { step: string; factor: string; note: string }[];
  source_name: string | null;
  source_url: string | null;
}

export interface NonPayable {
  id: number;
  list_number: number;
  item_name: string;
  synonyms: string[];
  notes: string | null;
}

export interface NonPayableMatch {
  line: string;
  matched: boolean;
  list_number: number | null;
  item_name: string | null;
  matched_term: string | null;
  method: string | null;
  score: number | null;
  notes: string | null;
}

export interface IcdCode {
  code: string;
  description: string;
  chapter: string | null;
  category: string | null;
  is_billable: boolean;
}

export interface PolicyTermRow {
  product_code: string;
  key: string;
  value: Record<string, unknown>;
  description: string | null;
  source: string | null;
}

// ---------- Step 6: claims ----------

export type ClaimStatus = "received" | "processing" | "ready_for_review" | "in_review" | "decided" | "failed";
export type ClaimStage = "preauth" | "final_cashless" | "reimbursement";

export const STATUS_LABELS: Record<ClaimStatus, string> = {
  received: "Received",
  processing: "Processing",
  ready_for_review: "Ready for review",
  in_review: "In review",
  decided: "Decided",
  failed: "Failed",
};

export const STAGE_LABELS: Record<ClaimStage, string> = {
  preauth: "Pre-authorisation",
  final_cashless: "Final cashless",
  reimbursement: "Reimbursement",
};

export interface ClaimRow {
  id: string;
  claim_number: string;
  stage: ClaimStage;
  status: ClaimStatus;
  source: "upload" | "synthetic";
  policy_number: string | null;
  member_name: string | null;
  hospital_name: string | null;
  claimed_amount: string | null;
  risk_band: "green" | "amber" | "red" | null;
  risk_score: number | null;
  documents: number;
  pages: number;
  created_at: string;
  processing_error: string | null;
}

export interface ClaimPageItem {
  id: string;
  page_number: number;
  doc_type: string;
  doc_type_confidence: number | null;
  width: number | null;
  height: number | null;
  has_text_layer: boolean;
  image_hash: string | null;
}

export interface ClaimFile {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  page_count: number | null;
  uploaded_at: string;
  duplicate_of_id: string | null;
  pages: ClaimPageItem[];
}

export interface ClaimDetail extends ClaimRow {
  created_by: string | null;
  processing_started_at: string | null;
  processing_finished_at: string | null;
  files: ClaimFile[];
}

export interface ClaimList {
  items: ClaimRow[];
  total: number;
  limit: number;
  offset: number;
  counts: Partial<Record<ClaimStatus, number>>;
}

export interface UploadResult {
  claim: ClaimDetail;
  warnings: string[];
}