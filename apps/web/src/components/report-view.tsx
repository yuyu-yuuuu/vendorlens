"use client";
import { useState } from "react";
import { ChevronDown, FileText } from "lucide-react";
import {
  domainNames,
  statusNames,
  type Assessment,
  type Details,
  type Domain,
  type Evidence,
  type ReportBundle,
  type Vendor,
} from "@/lib/types";
import { Button } from "./ui/button";
function requirement(a: Assessment | undefined) {
  if (!a || a.status === "failed") return "资料缺失";
  const findings = a.requirement_findings;
  if (findings.some((x) => x.status === "not_met")) return "不满足";
  if (findings.some((x) => x.status === "partially_met")) return "部分满足";
  if (findings.some((x) => x.status === "not_stated") || !findings.length)
    return "资料缺失";
  return "满足";
}
function commitment(a: Assessment | undefined) {
  if (!a || a.status === "failed") return "待补充";
  const c = a.commitment_assessment;
  if (
    a.conflicts.length ||
    c?.conflicts.length ||
    c?.findings.some((x) => x.status === "conflict")
  )
    return "存在冲突";
  if (c?.findings.length && c.findings.every((x) => x.status === "verified"))
    return "已明确";
  return "待补充";
}
export function ReportView({
  bundle,
  details,
  vendors,
  demo,
  onEvidence,
}: {
  bundle: ReportBundle;
  details: Details;
  vendors: Vendor[];
  demo: boolean;
  onEvidence: (e: Evidence) => void;
}) {
  const [tab, setTab] = useState<"compare" | "evidence">("compare");
  const [expanded, setExpanded] = useState<string | null>(null);
  const rows = bundle.report.comparison_matrix;
  const displayed = rows.map(
    (row) =>
      vendors.find((v) => v.id === row.vendor_id) ?? {
        id: row.vendor_id,
        name: row.vendor_id,
      },
  );
  const find = (vendorId: string, domain: string) =>
    details.assessments.find(
      (a) => a.vendor_id === vendorId && a.domain === domain,
    );
  const pick = (vendorId: string, domain: string) => {
    const a = find(vendorId, domain);
    const e = details.evidence.find((e) => a?.evidence_ids.includes(e.id));
    if (e) onEvidence({ ...e, domain: domain as Domain });
    else setExpanded(vendorId);
  };
  function conclusion(vendorId: string) {
    const score = rows.find((r) => r.vendor_id === vendorId);
    if (score?.disqualified) return "不予推荐";
    if (score?.eligible)
      return score.conditions.length ? "有条件推荐" : "可推荐";
    return demo && vendorId === "vendor-B" ? "需核实" : "无法判断";
  }
  const tone = (text: string) =>
    ["满足", "已明确", "有条件推荐", "可推荐"].includes(text)
      ? "positive"
      : ["部分满足", "存在冲突", "需核实", "不满足", "不予推荐"].includes(text)
        ? "warning"
        : "muted";
  return (
    <>
      <div className="tabs" role="tablist" aria-label="报告视图">
        <button
          role="tab"
          aria-selected={tab === "compare"}
          onClick={() => setTab("compare")}
        >
          供应商对比
        </button>
        <button
          role="tab"
          aria-selected={tab === "evidence"}
          onClick={() => setTab("evidence")}
        >
          证据核验
        </button>
      </div>
      {!demo && (
        <p className="report-summary">{bundle.report.executive_summary}</p>
      )}
      {tab === "compare" ? (
        <>
          <div className="table-scroll">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>评审维度</th>
                  {displayed.map((v) => (
                    <th key={v.id}>
                      <button
                        className="table-heading"
                        onClick={() =>
                          setExpanded(expanded === v.id ? null : v.id)
                        }
                      >
                        {v.name}
                        <ChevronDown size={14} />
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {["需求符合性", "成本口径", "交付承诺", "评审结论"].map(
                  (label, index) => (
                    <tr key={label}>
                      <th scope="row">{label}</th>
                      {displayed.map((v) => {
                        const value =
                          index === 0
                            ? requirement(find(v.id, "requirement_compliance"))
                            : index === 1
                              ? details.comparison_months
                                ? `${details.comparison_months} 个月`
                                : "未提供"
                              : index === 2
                                ? commitment(
                                    find(v.id, "commitment_verification"),
                                  )
                                : conclusion(v.id);
                        return (
                          <td key={v.id}>
                            <button
                              className={`cell-action ${index === 1 ? "" : tone(value)}`}
                              onClick={() =>
                                index === 1 || index === 3
                                  ? setExpanded(v.id)
                                  : pick(
                                      v.id,
                                      index === 0
                                        ? "requirement_compliance"
                                        : "commitment_verification",
                                    )
                              }
                            >
                              {value}
                            </button>
                          </td>
                        );
                      })}
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
          <section className="review-notes">
            <h2>待核实事项</h2>
            <table className="notes-table">
              <thead>
                <tr>
                  <th>事项</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                {demo ? (
                  <>
                    <tr>
                      <th>成本包含范围</th>
                      <td>
                        需进一步核实报价是否包含实施、数据迁移及后续运维等费用。
                      </td>
                    </tr>
                    <tr>
                      <th>验收前提</th>
                      <td>需明确验收的具体条件、标准及相关依赖事项。</td>
                    </tr>
                  </>
                ) : bundle.report.missing_information.length ? (
                  bundle.report.missing_information.map((item, i) => (
                    <tr key={i}>
                      <th>资料缺口 {i + 1}</th>
                      <td>{replaceIds(item, vendors)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={2}>报告未列出缺失信息。</td>
                  </tr>
                )}
                {!demo &&
                  bundle.report.conflicts.map((c, i) => (
                    <tr key={"c" + i}>
                      <th>冲突</th>
                      <td>
                        {vendors.find((v) => v.id === c.vendor_id)?.name ??
                          c.vendor_id}{" "}
                        · {c.detail}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </section>
          {expanded && (
            <section className="vendor-detail">
              <div className="section-heading">
                <h2>
                  {vendors.find((v) => v.id === expanded)?.name ?? expanded}
                </h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setExpanded(null)}
                >
                  收起
                </Button>
              </div>
              {details.assessments
                .filter((a) => a.vendor_id === expanded)
                .map((a) => (
                  <DomainDetails
                    key={a.domain}
                    assessment={a}
                    evidence={details.evidence}
                    onEvidence={onEvidence}
                  />
                ))}
              {!demo && (
                <div className="recommendation-conditions">
                  <h3>推荐条件与限制</h3>
                  {[
                    ...(rows.find((r) => r.vendor_id === expanded)
                      ?.conditions ?? []),
                    ...(rows.find((r) => r.vendor_id === expanded)
                      ?.disqualification_reasons ?? []),
                  ].map((text, i) => (
                    <p className="warning" key={i}>
                      {replaceIds(text, vendors)}
                    </p>
                  ))}
                </div>
              )}
              {!demo && (
                <p className="muted">
                  后端综合评分：
                  {rows.find((r) => r.vendor_id === expanded)?.total_score ??
                    "未提供"}
                </p>
              )}
            </section>
          )}
        </>
      ) : (
        <section className="evidence-list">
          {details.evidence.length ? (
            details.evidence.map((e) => (
              <button
                key={e.id}
                className="evidence-row"
                onClick={() => onEvidence(e)}
              >
                <FileText size={22} />
                <span>
                  <strong>
                    {e.document_name}
                    {e.version_number ? ` v${e.version_number}` : ""}
                  </strong>
                  <span>{e.quote}</span>
                </span>
                <span className="positive">核验 →</span>
              </button>
            ))
          ) : (
            <p className="empty-state">
              本次报告没有可读取的原文快照。旧任务仍可在资料库查看文档版本。
            </p>
          )}
        </section>
      )}
    </>
  );
}
function replaceIds(value: string, vendors: Vendor[]) {
  let text = value;
  for (const vendor of vendors) text = text.replaceAll(vendor.id, vendor.name);
  for (const [key, label] of Object.entries(domainNames))
    text = text.replaceAll(key, label);
  return text;
}
function DomainDetails({
  assessment: a,
  evidence,
  onEvidence,
}: {
  assessment: Assessment;
  evidence: Evidence[];
  onEvidence: (e: Evidence) => void;
}) {
  return (
    <article className="domain-detail">
      <h3>
        {domainNames[a.domain]}{" "}
        <small>{statusNames[a.status] ?? a.status}</small>
      </h3>
      {a.requirement_findings.map((r) => (
        <p key={r.requirement_id}>
          {statusNames[r.status] ?? r.status} · {r.explanation}
        </p>
      ))}
      {a.cost_assessment && (
        <dl className="cost-breakdown">
          {[
            ["一次性费用", a.cost_assessment.one_time_cost],
            ["年度持续费用", a.cost_assessment.recurring_cost],
            ["基础 TCO", a.cost_assessment.total_cost_of_ownership],
            ["可选费用（单列）", a.cost_assessment.optional_cost],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>
                {new Intl.NumberFormat("zh-CN", {
                  style: "currency",
                  currency: a.cost_assessment!.currency,
                }).format(value as number)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {a.commitment_assessment?.findings.map((f, i) => (
        <p key={i}>
          {statusNames[f.status] ?? f.status} · {f.statement}
        </p>
      ))}
      {[...a.missing_information, ...a.conflicts].map((text, i) => (
        <p className="warning" key={i}>
          {text}
        </p>
      ))}
      <div className="reference-links">
        {a.evidence_ids.map((id) => {
          const e = evidence.find((e) => e.id === id);
          return e ? (
            <button key={id} onClick={() => onEvidence(e)}>
              <FileText size={14} />
              {e.document_name}
            </button>
          ) : (
            <small key={id}>引用 {id}（原文快照不可用）</small>
          );
        })}
      </div>
    </article>
  );
}
