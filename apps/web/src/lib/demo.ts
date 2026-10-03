import type {
  Assessment,
  Details,
  Evidence,
  ReportBundle,
  Task,
  Vendor,
  Project,
  Config,
  Document,
  Version,
  Trace,
  Skills,
} from "./types";
export const demoProject: Project = {
  id: "demo-project",
  name: "软件采购项目",
  description: "示例资料，用于浏览评审工作台",
};
export const demoVendors: Vendor[] = ["A", "B", "C"].map((x) => ({
  id: "vendor-" + x,
  name: "供应商 " + x,
}));
export const demoTask: Task = {
  id: "demo-task",
  status: "partially_completed",
  run_id: "demo-run",
  trace_id: "demo-trace",
  attempt: 1,
};
export const demoConfig: Config = {
  requirement_weight: 0.5,
  cost_weight: 0.3,
  commitment_weight: 0.2,
  comparison_months: 36,
  currency: "CNY",
  required_cost_categories: ["software", "implementation", "maintenance"],
  commitment_checklist: [],
};
export const demoEvidence: Evidence = {
  domain: "commitment_verification",
  id: "evidence:demo",
  project_id: "demo-project",
  vendor_id: "vendor-A",
  document_version_id: "demo-v2",
  chunk_id: "demo-chunk",
  quote: "项目上线需完成数据迁移与验收确认。",
  context_text:
    "交付计划\n项目上线需完成数据迁移与验收确认。实施范围与客户配合事项需在项目启动前共同确认。",
  source_text_hash: "示例资料",
  document_name: "实施方案",
  version_number: 2,
  location: { page: 8 },
  source_available: true,
};
const assessments: Assessment[] = demoVendors.flatMap((v, i) => [
  {
    vendor_id: v.id,
    domain: "requirement_compliance",
    status: i === 2 ? "failed" : "completed",
    evidence_ids: i === 0 ? [demoEvidence.id] : [],
    missing_information: i === 2 ? ["需求答复资料缺失"] : [],
    conflicts: [],
    revision_count: 0,
    requirement_findings: [
      {
        requirement_id: "R1",
        status: ["met", "partially_met", "not_stated"][i],
        explanation: "私有化部署需求的示例评审结果。",
        evidence_ids: i === 0 ? [demoEvidence.id] : [],
      },
    ],
    cost_assessment: null,
    commitment_assessment: null,
  },
  {
    vendor_id: v.id,
    domain: "cost_modeling",
    status: "degraded",
    evidence_ids: [],
    missing_information: ["成本包含范围待确认"],
    conflicts: [],
    revision_count: 0,
    requirement_findings: [],
    cost_assessment: null,
    commitment_assessment: null,
  },
  {
    vendor_id: v.id,
    domain: "commitment_verification",
    status: i === 2 ? "failed" : "completed",
    evidence_ids: i === 0 ? [demoEvidence.id] : [],
    missing_information: i === 2 ? ["交付资料待补充"] : [],
    conflicts: i === 1 ? ["交付条件存在冲突"] : [],
    revision_count: 0,
    requirement_findings: [],
    cost_assessment: null,
    commitment_assessment: {
      findings: [
        {
          category: "交付",
          statement:
            i === 0
              ? demoEvidence.quote
              : i === 1
                ? "交付条件需核实。"
                : "尚未提供交付承诺。",
          status: ["verified", "conflict", "not_stated"][i],
          evidence_ids: i === 0 ? [demoEvidence.id] : [],
        },
      ],
      missing_information: [],
      conflicts: [],
    },
  },
]);
export const demoDetails: Details = {
  task_id: "demo-task",
  project_id: "demo-project",
  objective: "比较需求符合性、三年成本与交付条件",
  comparison_months: 36,
  currency: "CNY",
  assessments,
  evidence: [demoEvidence],
};
export const demoReport: ReportBundle = {
  task_id: "demo-task",
  status: "partially_completed",
  report: {
    executive_summary:
      "供应商 A 可有条件推荐；成本包含范围及验收前提仍需核实。",
    comparison_matrix: demoVendors.map((v, i) => ({
      vendor_id: v.id,
      requirement_score: [100, 50, 0][i],
      cost_score: 0,
      commitment_score: [100, 50, 0][i],
      total_score: 0,
      eligible: i === 0,
      disqualified: false,
      conditions: i === 0 ? ["成本包含范围待确认"] : ["需核实"],
      disqualification_reasons: [],
    })),
    risks: [
      {
        vendor_id: "vendor-A",
        conditions: ["成本包含范围", "验收前提"],
        disqualification_reasons: [],
      },
    ],
    conflicts: [
      {
        vendor_id: "vendor-B",
        domain: "commitment_verification",
        detail: "交付条件存在冲突",
      },
    ],
    missing_information: ["成本包含范围", "验收前提"],
    claim_evidence_matrix: assessments.map((a) => ({
      vendor_id: a.vendor_id,
      domain: a.domain,
      status: a.status,
      evidence_ids: a.evidence_ids,
    })),
  },
};
export const demoDocuments: Document[] = [
  {
    id: "demo-doc",
    vendor_id: "vendor-A",
    logical_name: "实施方案.pdf",
    media_type: "application/pdf",
  },
  {
    id: "demo-public",
    vendor_id: null,
    logical_name: "采购需求书.md",
    media_type: "text/markdown",
  },
];
export const demoVersions: Record<string, Version[]> = {
  "demo-doc": [
    {
      id: "demo-v2",
      document_id: "demo-doc",
      version_number: 2,
      status: "indexed",
      created_at: "2026-10-02T10:00:00",
      file_hash: "示例资料",
    },
  ],
  "demo-public": [
    {
      id: "demo-v1",
      document_id: "demo-public",
      version_number: 1,
      status: "indexed",
      created_at: "2026-10-02T09:00:00",
      file_hash: "示例资料",
    },
  ],
};
export const demoTrace: Trace = {
  summary: {
    model_calls: 0,
    tool_calls: 0,
    total_tokens: null,
    usage_complete: false,
    cost_complete: false,
    estimated_costs: {},
  },
  spans: [
    {
      id: "trace-root",
      parent_span_id: null,
      name: "评审工作流（示例）",
      kind: "workflow",
      status: "completed",
      duration_ms: null,
      error: null,
    },
  ],
};
export const demoSkills: Skills = {
  generation: "示例",
  last_error: null,
  skills: [{ id: "general_review", version: "1.0", content_hash: "示例资料" }],
};
