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
  sla_due_at: string | null;
  suggested_payable: string | null;
  assigned_to_id: string | null;
  assignee: string | null;
  latest_recommendation: Recommendation | null;
}

export type RiskBand = "green" | "amber" | "red";
export type Recommendation = "approve" | "approve_with_deductions" | "query_hospital" | "escalate" | "refer_siu";

export const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  approve: "Approve",
  approve_with_deductions: "Approve with deductions",
  query_hospital: "Query hospital",
  escalate: "Escalate to senior reviewer",
  refer_siu: "Refer to investigation (SIU)",
};

/** What a recommendation that keeps the claim open means in the queue. */
export const WAITING_LABELS: Partial<Record<Recommendation, string>> = {
  query_hospital: "Query sent",
  escalate: "Escalated",
  refer_siu: "With SIU",
};

export interface ScorePart {
  rule_id: string;
  name: string;
  severity: Severity;
  weight: number;
  medical: boolean;
  findings: number;
}

export interface Person {
  id: string;
  name: string;
  role: RoleName;
}

export interface DeductionLine {
  description: string;
  amount: string;
  finding_id: string | null;
}

export interface DecisionRow {
  id: string;
  recommendation: Recommendation;
  approved_amount: string | null;
  deductions: DeductionLine[];
  reason: string;
  decided_by: string | null;
  created_at: string;
}

export interface ReviewSummary {
  claim_id: string;
  claim_number: string;
  status: ClaimStatus;
  stage: ClaimStage;
  source: "upload" | "synthetic";
  patient_name: string | null;
  age: number | null;
  gender: string | null;
  policy_number: string | null;
  sum_insured: string | null;
  hospital_name: string | null;
  rohini_id: string | null;
  doctor: string | null;
  diagnosis: string | null;
  icd_code: string | null;
  procedures: string[];
  admission_at: string | null;
  discharge_at: string | null;
  stay_nights: number | null;
  claimed_amount: string | null;
  amount_at_stake: string;
  suggested_deduction: string;
  suggested_payable: string | null;
  score: number | null;
  band: RiskBand | null;
  band_reason: string;
  suggested_action: string;
  score_parts: ScorePart[];
  dismissed_findings: number;
  open_findings: number;
  open_blocking: number;
  sla_due_at: string | null;
  assignee: Person | null;
  escalated: boolean;
  decisions: DecisionRow[];
  approve_blockers: string[];
  can_decide: boolean;
}

export interface QueryLetter {
  subject: string;
  body: string;
  documents: string[];
  clarifications: string[];
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
  text_source: "text_layer" | "ocr" | "none" | null;
  ocr_confidence: number | null;
  quality_flag: QualityFlag | null;
  rotation: number;
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
  pages_needing_review: number;
  page_types: Record<string, number>;
  files: ClaimFile[];
}

// ---------- Step 7: page reading ----------

export type QualityFlag = "good" | "fair" | "poor" | "unreadable" | "blank";

export const DOC_TYPE_LABELS: Record<string, string> = {
  claim_form: "Claim form",
  preauth_form: "Pre-auth form",
  preauth_approval: "Pre-auth approval",
  discharge_summary: "Discharge summary",
  final_bill: "Final bill",
  bill_breakup: "Bill breakup",
  pharmacy_bill: "Pharmacy bill",
  lab_report: "Lab report",
  imaging_report: "Imaging report",
  ot_notes: "OT notes",
  anaesthesia_notes: "Anaesthesia notes",
  implant_invoice: "Implant invoice",
  implant_sticker: "Implant sticker",
  case_sheet: "Case sheet",
  nursing_chart: "Nursing chart",
  policy_copy: "Policy / e-card",
  id_proof: "ID proof",
  mlc_fir: "MLC / FIR",
  other: "Other",
  unknown: "Unknown",
};

export const QUALITY_LABELS: Record<QualityFlag, string> = {
  good: "Clear",
  fair: "Readable",
  poor: "Poor scan",
  unreadable: "Unreadable",
  blank: "Blank",
};

export interface PageText {
  id: string;
  page_number: number;
  doc_type: string;
  text_source: string | null;
  ocr_confidence: number | null;
  quality_flag: QualityFlag | null;
  text: string;
  words: [string, number, number, number, number, number][];
}

export interface ClaimList {
  items: ClaimRow[];
  total: number;
  limit: number;
  offset: number;
  counts: Partial<Record<ClaimStatus, number>>;
  band_counts: Partial<Record<RiskBand | "none", number>>;
}

export interface UploadResult {
  claim: ClaimDetail;
  warnings: string[];
}

// ---------- Step 8: claim record ----------

export interface Bbox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface RecordField {
  id: string;
  name: string;
  value: string | null;
  normalized: unknown;
  corrected_value: string | null;
  method: "rule" | "ai" | "reviewer";
  confidence: number | null;
  needs_review: boolean;
  page_id: string | null;
  bbox: Bbox | null;
}

export interface RecordDocument {
  key: string;
  doc_type: string;
  page_ids: string[];
  quality: string | null;
  ai_used: boolean;
  missing: string[];
  fields: RecordField[];
  tables: Record<string, Record<string, { text: string; page_id: string | null; bbox: number[] | null; method: string }>[]>;
}

interface Located {
  page_id: string | null;
  bbox: Bbox | null;
}

export interface RecordBillLine extends Located {
  line_no: number | null;
  service_date: string | null;
  description: string;
  category: string;
  category_text: string | null;
  quantity: string | null;
  unit_rate: string | null;
  amount: string;
  method: string;
}

export interface RecordMedication extends Located {
  drug_name: string;
  quantity: string | null;
  batch_no: string | null;
  expiry: string | null;
  mrp: string | null;
  unit_rate: string | null;
  amount: string | null;
  in_medication_chart: boolean | null;
}

export interface RecordInvestigation extends Located {
  test_name: string;
  kind: string | null;
  performed_at: string | null;
  billed: boolean;
  has_report: boolean;
  results: { parameter?: string; value?: string; unit?: string; reference?: string }[] | null;
}

export interface RecordItem extends Located {
  description: string;
  source_document: string | null;
  code: string | null;
  performed_at: string | null;
}

export interface ClaimRecord {
  summary: Record<string, string | number | null>;
  documents: RecordDocument[];
  bill_lines: RecordBillLine[];
  medications: RecordMedication[];
  investigations: RecordInvestigation[];
  diagnoses: RecordItem[];
  procedures: RecordItem[];
  ai: { calls: number; failed: number; input_tokens: number; output_tokens: number; cost_usd: string; models: string[] };
  fields_needing_review: number;
}

export interface Focus {
  pageId: string;
  bbox: Bbox | null;
  stamp: number;
}

export const FIELD_LABELS: Record<string, string> = {
  patient_name: "Patient", uhid: "UHID", age: "Age", gender: "Sex", policy_no: "Policy no.", member_id: "Member ID",
  hospital_name: "Hospital", rohini_id: "Rohini ID", hfr_id: "HFR ID", doctor_name: "Doctor",
  doctor_qualification: "Qualification", doctor_reg_no: "Doctor reg. no.", admission: "Admitted",
  discharge: "Discharged", diagnosis: "Diagnosis", icd_code: "ICD-10", procedures: "Procedures",
  claimed_amount: "Amount claimed", bill_total: "Bill total", bill_no: "Bill no.", bill_date: "Bill date",
  submitted: "Submitted", claim_type: "Claim type", preauth_no: "Pre-auth no.", preauth_diagnosis: "Pre-auth diagnosis",
  proposed_treatment: "Proposed treatment", estimate: "Estimate", approved: "Approved", requested_on: "Requested",
  history: "Past history", complaints: "Complaints", course: "Course in hospital", advice: "Advice",
  examination: "Examination", investigations_text: "Investigations", signed: "Signed", ward_type: "Ward",
  room_category: "Room category", pharmacy_total: "Pharmacy total", test: "Test", report_date: "Report date",
  impression: "Impression", procedure_date: "Operated", surgeon: "Surgeon", anaesthetist: "Anaesthetist",
  anaesthesia: "Anaesthesia", findings: "Findings", invoice_no: "Invoice no.", model: "Model", brand: "Brand",
  serial: "Serial / lot", mlc_no: "MLC no.", mlc_date: "MLC date", police_station: "Police station",
  product: "Product", product_code: "Product code", sum_insured: "Sum insured", policy_start: "Policy start",
  policy_end: "Policy end", inception: "First inception", room_eligibility: "Room eligibility",
  sub_limits: "Sub-limits", used_si: "Claims paid this year", balance_si: "Balance sum insured",
  id_last4: "ID (last 4)", birth_year: "Year of birth", ip_no: "IP no.",
};

// ---------- Step 9: checks and findings ----------

export type Severity = "red" | "amber" | "info";

export interface Evidence {
  document: string | null;
  page_id: string | null;
  bbox: Bbox | null;
  quote: string;
}

export interface FindingReview {
  id: string;
  action: "confirmed" | "dismissed" | "noted";
  reason: string | null;
  reviewer: string | null;
  created_at: string;
}

export interface Finding {
  id: string;
  rule_id: string;
  rule_name: string;
  layer: string;
  severity: Severity;
  title: string;
  explanation: string;
  evidence: Evidence[];
  amount_at_stake: string | null;
  suggested_deduction: string | null;
  confidence: number | null;
  needs_medical_review: boolean;
  model_version: string | null;
  is_current: boolean;
  status: "open" | "confirmed" | "dismissed";
  created_at: string;
  reviews: FindingReview[];
}

export interface FindingsResponse {
  claim_id: string;
  summary: { red: number; amber: number; info: number; open: number; amount_at_stake: string; suggested_deduction: string };
  findings: Finding[];
}

export type CheckStatus =
  | "passed"
  | "flagged"
  | "not_applicable"
  | "not_evaluable"
  | "pending_ai"
  | "disabled"
  | "error"
  | "not_run";

export interface CheckRunRow {
  rule_id: string;
  name: string;
  layer: string;
  method: string;
  max_severity: Severity;
  status: CheckStatus;
  detail: string | null;
  finding_count: number;
}

export interface ChecksResponse {
  claim_id: string;
  ran_at: string | null;
  counts: Partial<Record<CheckStatus, number>>;
  checks: CheckRunRow[];
}

export const LAYER_LABELS: Record<string, string> = {
  completeness: "Completeness",
  forensics: "Document forensics",
  consistency: "Consistency across documents",
  medical: "Medical necessity",
  billing: "Billing",
  policy: "Policy",
  registry: "Registries",
};

export const CHECK_STATUS_LABELS: Record<CheckStatus, string> = {
  passed: "Passed",
  flagged: "Flagged",
  not_applicable: "Not applicable",
  not_evaluable: "Could not check",
  pending_ai: "AI check (Step 10)",
  disabled: "Disabled",
  error: "Error",
  not_run: "Not run",
};
