export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type StageStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";
export type StageName = "analyze" | "architect" | "implement" | "devops" | "review";
export type Severity = "error" | "warning" | "info";

export interface Stage {
  name: StageName;
  label: string;
  status: StageStatus;
  summary: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number | null;
  input_tokens: number;
  output_tokens: number;
}

export interface JobSummary {
  id: string;
  title: string;
  status: JobStatus;
  provider: string;
  model: string | null;
  current_stage: StageName | null;
  project_name: string | null;
  progress: number;
  created_at: string;
  finished_at: string | null;
}

export interface JobDetail extends JobSummary {
  spec: string;
  error: string | null;
  started_at: string | null;
  stages: Stage[];
  input_tokens: number;
  output_tokens: number;
  cancel_requested: boolean;
  validation: { passed: boolean; errors: number; warnings: number; files_checked: number } | null;
  review_score: number | null;
}

export interface JobPage {
  items: JobSummary[];
  next_cursor: string | null;
}

export interface FileEntry {
  path: string;
  size: number;
  stage: string;
  description: string;
  language: string;
}

export interface FileContent extends FileEntry {
  content: string;
}

export interface ProviderInfo {
  name: string;
  model: string;
  default: boolean;
}

export interface ServerInfo {
  version: string;
  auth_required: boolean;
  providers: ProviderInfo[];
  max_spec_chars: number;
  min_spec_chars: number;
}

export interface EntityField {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export interface Entity {
  name: string;
  description: string;
  fields: EntityField[];
}

export interface Requirements {
  project_name: string;
  summary: string;
  user_stories: string[];
  functional_requirements: string[];
  non_functional_requirements: string[];
  entities: Entity[];
  assumptions: string[];
  out_of_scope: string[];
}

export interface Architecture {
  style: string;
  overview: string;
  components: { name: string; responsibility: string; technology: string; depends_on: string[] }[];
  entities: Entity[];
  endpoints: { method: string; path: string; summary: string; entity: string | null }[];
  tech_stack: { layer: string; choice: string; rationale: string }[];
  deployment: {
    container_port: number;
    replicas: number;
    database: string;
    cloud_target: string;
    environment_variables: string[];
  };
  decisions: { title: string; decision: string; rationale: string; alternatives: string[] }[];
}

export interface ValidationIssue {
  severity: Severity;
  rule: string;
  message: string;
  file: string | null;
  line: number | null;
}

export interface Review {
  summary: string;
  score: number;
  findings: { severity: Severity; file: string | null; message: string; suggestion: string }[];
  validation: { files_checked: number; issues: ValidationIssue[] };
}

export interface JobEvent {
  id: number | null;
  event: string;
  at?: string;
  stage?: StageName;
  label?: string;
  message?: string;
  summary?: string;
  error?: string;
  duration_s?: number;
  status?: string;
  [key: string]: unknown;
}

export const TERMINAL: ReadonlySet<JobStatus> = new Set(["succeeded", "failed", "cancelled"]);
