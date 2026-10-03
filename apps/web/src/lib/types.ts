export type Project = { id: string; name: string; description: string };
export type Vendor = { id: string; name: string };
export type Document = {
  id: string;
  vendor_id: string | null;
  logical_name: string;
  media_type: string;
};
export type Version = {
  id: string;
  document_id: string;
  version_number: number;
  status: string;
  created_at: string;
  file_hash: string;
};
export type Chunk = {
  id: string;
  text: string;
  location: Record<string, unknown>;
  chunk_type: string;
};
export type Requirement = {
  id: string;
  code: string;
  text: string;
  mandatory: boolean;
  source_chunk_id: string;
  weight: number;
};
export type Task = {
  id?: string;
  task_id?: string;
  status: string;
  run_id: string;
  trace_id: string;
  attempt: number;
  created_at?: string;
};
export type Domain =
  "requirement_compliance" | "cost_modeling" | "commitment_verification";
export type Evidence = {
  domain?: Domain;
  id: string;
  vendor_id: string | null;
  project_id: string;
  document_version_id: string;
  chunk_id: string;
  quote: string;
  context_text?: string | null;
  source_text_hash: string;
  document_name: string;
  version_number: number | null;
  location: Record<string, unknown>;
  source_available: boolean;
};
export type Assessment = {
  vendor_id: string;
  domain: Domain;
  status: string;
  evidence_ids: string[];
  missing_information: string[];
  conflicts: string[];
  revision_count: number;
  requirement_findings: {
    requirement_id: string;
    status: string;
    explanation: string;
    evidence_ids: string[];
  }[];
  cost_assessment: {
    currency: string;
    comparison_months: number;
    one_time_cost: number;
    recurring_cost: number;
    optional_cost: number;
    total_cost_of_ownership: number;
    payment_terms: string[];
    missing_costs: string[];
    conflicts: string[];
    evidence_ids: string[];
  } | null;
  commitment_assessment: {
    findings: {
      category: string;
      statement: string;
      status: string;
      evidence_ids: string[];
    }[];
    missing_information: string[];
    conflicts: string[];
  } | null;
};
export type Score = {
  vendor_id: string;
  requirement_score: number;
  cost_score: number;
  commitment_score: number;
  total_score: number;
  eligible: boolean;
  disqualified: boolean;
  conditions: string[];
  disqualification_reasons: string[];
};
export type Report = {
  executive_summary: string;
  comparison_matrix: Score[];
  risks: {
    vendor_id: string;
    conditions: string[];
    disqualification_reasons: string[];
  }[];
  conflicts: { vendor_id: string; domain: string; detail: string }[];
  missing_information: string[];
  claim_evidence_matrix: {
    vendor_id: string;
    domain: Domain;
    status: string;
    evidence_ids: string[];
  }[];
};
export type ReportBundle = { task_id: string; report: Report; status: string };
export type Details = {
  task_id: string;
  project_id: string;
  objective: string;
  comparison_months: number | null;
  currency: string | null;
  assessments: Assessment[];
  evidence: Evidence[];
};
export type Config = {
  requirement_weight: number;
  cost_weight: number;
  commitment_weight: number;
  comparison_months: number;
  currency: string;
  required_cost_categories: string[];
  commitment_checklist: string[];
};
export type Preferences = {
  comparison_months?: number;
  currency?: string;
  report_style?: "concise" | "detailed";
  focus_areas?: string[];
};
export type Trace = {
  summary: {
    model_calls: number;
    tool_calls: number;
    total_tokens: number | null;
    usage_complete: boolean;
    cost_complete: boolean;
    estimated_costs: Record<string, number>;
  };
  spans: {
    id: string;
    parent_span_id: string | null;
    name: string;
    kind: string;
    status: string;
    duration_ms: number | null;
    error: string | null;
  }[];
};
export type RouteAttempt = {
  id: string;
  vendor_id: string;
  endpoint_id: string;
  status: string;
  latency_ms: number | null;
  quality_failure_rate: number | null;
  error_type: string | null;
  decision: Record<string, unknown>;
};
export type Skills = {
  generation: string | null;
  last_error: string | null;
  skills: { id: string; version: string; content_hash: string }[];
};
export type Health = {
  configured?: boolean;
  endpoints: Record<string, unknown>[];
};
export type RunEvent = {
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
};
export const taskId = (task: Task) => task.task_id ?? task.id ?? "";
export const domainNames: Record<Domain, string> = {
  requirement_compliance: "需求符合性",
  cost_modeling: "成本建模",
  commitment_verification: "交付承诺",
};
export const statusNames: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  completed: "已完成",
  partially_completed: "部分完成",
  failed: "失败",
  enqueue_failed: "入队失败",
  degraded: "已降级",
  indexed: "已索引",
  index_failed: "索引失败",
  ocr_required: "需要 OCR",
  parsed: "已解析",
  met: "满足",
  partially_met: "部分满足",
  not_met: "不满足",
  not_stated: "资料缺失",
  verified: "已明确",
  partial: "部分明确",
  conflict: "存在冲突",
};
export function locationText(location: Record<string, unknown>) {
  if (
    location.page != null &&
    Object.values(location).filter(
      (v) => v != null && (!Array.isArray(v) || v.length),
    ).length === 1
  )
    return `第 ${location.page} 页`;
  const values = Object.entries(location)
    .filter(
      ([, value]) =>
        value != null &&
        value !== "" &&
        (!Array.isArray(value) || value.length > 0),
    )
    .map(
      ([key, value]) =>
        (({
          page: "页码",
          page_number: "页码",
          sheet: "工作表",
          sheet_name: "工作表",
          cell_range: "单元格",
          paragraph: "段落",
          paragraph_index: "段落",
          line_start: "起始行",
          line_end: "结束行",
          heading: "标题",
          heading_path: "章节",
        })[key] ?? key) +
        " " +
        (Array.isArray(value) ? value.join("–") : String(value)),
    );
  return values.join(" · ") || "未提供定位";
}
