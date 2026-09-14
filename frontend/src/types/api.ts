/**
 * Response types mirroring the backend Pydantic schemas.
 * Kept in sync with backend/app/schemas.py by hand - see coding-standards.md.
 */

/**
 * Three roles. `platform_admin` is the service provider operating the platform;
 * `admin` administers one company; `user` works inside one company.
 *
 * The UI hides what a role cannot do, but that is convenience, not security -
 * every one of these boundaries is enforced again in the backend.
 */
export type Role = "user" | "admin" | "platform_admin";

/** The roles a company admin is allowed to hand out. Never widen this. */
export type TenantAssignableRole = "user" | "admin";

export const ROLE_LABELS: Record<Role, string> = {
  platform_admin: "Platform admin",
  admin: "Company admin",
  user: "Member",
};

export type Department =
  | "hr"
  | "finance"
  | "legal"
  | "sales"
  | "marketing"
  | "operations"
  | "technical";

export const DEPARTMENTS: Department[] = [
  "hr",
  "finance",
  "legal",
  "sales",
  "marketing",
  "operations",
  "technical",
];

export type DocumentStatus = "pending" | "processing" | "ready" | "failed" | "deleted";
export type Modality = "text" | "table" | "image" | "audio" | "video";
export type JobStatus =
  | "queued"
  | "extracting"
  | "transcribing"
  | "chunking"
  | "embedding"
  | "completed"
  | "failed";

export interface Me {
  user_id: string;
  tenant_id: string;
  role: Role;
  email: string | null;
  tenant_name?: string | null;
}

// ---------------------------------------------------------------------------
// authentication
// ---------------------------------------------------------------------------

/** Cognito's name for "you were invited and must choose a real password". */
export const NEW_PASSWORD_REQUIRED = "NEW_PASSWORD_REQUIRED";

export interface SessionResponse {
  token: string | null;
  expires_in: number | null;
  refresh_token: string | null;
  challenge: string | null;
  challenge_session: string | null;
  user: Me | null;
}

export interface Acknowledged {
  status: string;
  message: string;
}

// ---------------------------------------------------------------------------
// tenants and users
// ---------------------------------------------------------------------------
export interface TenantOut {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  contact_email: string | null;
  created_by: string | null;
  created_at: string;
  user_count: number;
  active_user_count: number;
  admin_count: number;
}

export interface TenantList {
  items: TenantOut[];
  total: number;
}

export interface UserOut {
  id: string;
  email: string;
  display_name: string | null;
  role: Role;
  department: Department | null;
  is_active: boolean;
  tenant_id: string;
  invited_by: string | null;
  last_login_at: string | null;
  created_at: string;
}

export interface UserList {
  items: UserOut[];
  total: number;
}

export interface InviteResult {
  user: UserOut;
  invitation_sent: boolean;
  message: string;
}

export interface DocumentOut {
  id: string;
  name: string;
  original_filename: string;
  content_type: string;
  modality: Modality;
  department: Department | null;
  status: DocumentStatus;
  size_bytes: number;
  chunk_count: number;
  page_count: number | null;
  version: number;
  owner_id: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentList {
  items: DocumentOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface IngestionJob {
  id: string;
  document_id: string;
  status: JobStatus;
  progress: number;
  chunks_written: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Citation {
  source_number: number;
  document_id: string;
  document_name: string;
  chunk_id: string;
  page_number: number | null;
  section: string | null;
  score: number;
}

export interface RetrievedChunk {
  chunk_id: string | null;
  document_id: string | null;
  document_name: string | null;
  page_number: number | null;
  section: string | null;
  modality: string | null;
  score: number;
  preview: string;
}

/** PROJECT.md section 7 - the full response envelope. */
export interface ChatResponse {
  answer: string;
  citations: Citation[];
  retrieved_chunks: RetrievedChunk[];
  model_used: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  latency_ms: number;
  cache_hit: boolean;
  tenant_id: string;
  confidence: number;
  correlation_id: string;
  conversation_id: string | null;
}

export type WorkflowId =
  | "policy_comparison"
  | "summarization"
  | "cross_document_analysis"
  | "knowledge_extraction"
  | "report_generation";

export interface WorkflowInfo {
  id: WorkflowId;
  name: string;
  description: string;
}

export interface AgentStep {
  node: string;
  [key: string]: unknown;
}

export interface AgentResponse {
  answer: string;
  citations: Citation[];
  retrieved_chunks: RetrievedChunk[];
  steps: AgentStep[];
  workflow: string;
  model_used: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  latency_ms: number;
  tenant_id: string;
  confidence: number;
  partial: boolean;
  error: string | null;
  correlation_id: string;
}

export interface UsageSummary {
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_estimated_cost: number;
  avg_latency_ms: number;
  cache_hit_rate: number;
}

export interface AdminMetrics {
  tenant_id: string;
  usage: UsageSummary;
  documents: number;
  chunks: number;
  ingestion_failures: number;
  security_events: number;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  severity: string;
  resource_type: string | null;
  resource_id: string | null;
  reason: string | null;
  user_id: string | null;
  correlation_id: string | null;
  created_at: string;
  details: Record<string, unknown>;
}

export interface ApiError {
  error: { code: string; message: string };
  correlation_id: string;
}
