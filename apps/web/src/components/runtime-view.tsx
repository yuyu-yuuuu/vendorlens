"use client";
import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { demoTrace, demoSkills } from "@/lib/demo";
import {
  statusNames,
  type Health,
  type Preferences,
  type RouteAttempt,
  type Skills,
  type Trace,
} from "@/lib/types";
import { Button } from "./ui/button";
export function RuntimeView({
  projectId,
  taskId,
  demo,
}: {
  projectId: string;
  taskId: string;
  demo: boolean;
}) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [routes, setRoutes] = useState<RouteAttempt[]>([]);
  const [health, setHealth] = useState<Health>({ endpoints: [] });
  const [skills, setSkills] = useState<Skills | null>(null);
  const [preferences, setPreferences] = useState<Preferences>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  async function load() {
    setBusy(true);
    setError("");
    if (demo) {
      setTrace(demoTrace);
      setSkills(demoSkills);
      setBusy(false);
      return;
    }
    const results = await Promise.allSettled([
      taskId ? api<Trace>(`/tasks/${taskId}/trace`) : Promise.resolve(null),
      taskId
        ? api<{ attempts: RouteAttempt[] }>(`/tasks/${taskId}/routing`)
        : Promise.resolve({ attempts: [] }),
      api<Health>("/routing/health"),
      api<Skills>("/skills"),
      api<Preferences>(`/projects/${projectId}/preferences`),
    ]);
    const [t, r, h, s, p] = results;
    if (t.status === "fulfilled") setTrace(t.value);
    if (r.status === "fulfilled") setRoutes(r.value.attempts);
    if (h.status === "fulfilled") setHealth(h.value);
    if (s.status === "fulfilled") setSkills(s.value);
    if (p.status === "fulfilled") setPreferences(p.value);
    setError(
      results
        .filter((x) => x.status === "rejected")
        .map((x) => String((x as PromiseRejectedResult).reason))
        .join("；"),
    );
    setBusy(false);
  }
  useEffect(() => {
    void load();
  }, [projectId, taskId, demo]);
  async function reload() {
    setBusy(true);
    setError("");
    try {
      await api("/skills/reload", { method: "POST" });
      await load();
      setNotice("规则已重载，后续任务使用新版本。");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      setPreferences(
        await api<Preferences>(`/projects/${projectId}/preferences`, {
          method: "PUT",
          body: JSON.stringify({
            comparison_months: Number(data.get("months")),
            currency: data.get("currency"),
            report_style: data.get("style"),
            focus_areas: String(data.get("focus") ?? "")
              .split(/[，,\n]/)
              .map((x) => x.trim())
              .filter(Boolean),
          }),
        }),
      );
      setNotice("偏好已保存。任务显式口径与项目配置优先。");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const depth = (id: string): number => {
    let result = 0,
      current = trace?.spans.find((s) => s.id === id);
    const visited = new Set<string>();
    while (current?.parent_span_id && !visited.has(current.parent_span_id)) {
      visited.add(current.parent_span_id);
      current = trace?.spans.find((s) => s.id === current!.parent_span_id);
      result++;
    }
    return Math.min(result, 6);
  };
  return (
    <div className="runtime-view">
      <div className="section-toolbar">
        <p className="muted">任务追踪、模型路由与运行设置。</p>
        <Button variant="outline" disabled={busy} onClick={load}>
          <RefreshCw size={16} />
          刷新
        </Button>
      </div>
      {error && (
        <p role="alert" className="error-message">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="positive">
          {notice}
        </p>
      )}
      <section>
        <h2>执行追踪</h2>
        {trace ? (
          <>
            <div className="runtime-summary">
              <span>
                模型请求 <strong>{trace.summary.model_calls}</strong>
              </span>
              <span>
                工具调用 <strong>{trace.summary.tool_calls}</strong>
              </span>
              <span>
                Token <strong>{trace.summary.total_tokens ?? "未提供"}</strong>
                {!trace.summary.usage_complete &&
                trace.summary.total_tokens !== null
                  ? "（部分）"
                  : ""}
              </span>
              <span>
                估算费用{" "}
                <strong>
                  {Object.entries(trace.summary.estimated_costs)
                    .map(
                      ([currency, value]) => `${currency} ${value.toFixed(4)}`,
                    )
                    .join(" / ") || "未提供"}
                </strong>
                {!trace.summary.cost_complete &&
                Object.keys(trace.summary.estimated_costs).length
                  ? "（部分）"
                  : ""}
              </span>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>操作</th>
                  <th>状态</th>
                  <th>耗时</th>
                </tr>
              </thead>
              <tbody>
                {trace.spans.map((s) => (
                  <tr key={s.id}>
                    <td style={{ paddingLeft: 16 + depth(s.id) * 18 }}>
                      <span className="trace-node">
                        {s.parent_span_id ? "↳ " : ""}
                        {s.name}
                      </span>
                      <small>
                        {s.kind}
                        {s.error ? ` · ${s.error}` : ""}
                      </small>
                    </td>
                    <td>{statusNames[s.status] ?? s.status}</td>
                    <td>
                      {s.duration_ms !== null
                        ? `${s.duration_ms} ms`
                        : "未提供"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <p className="empty-state">选择任务后查看执行追踪。</p>
        )}
      </section>
      <section className="review-notes">
        <h2>模型路由</h2>
        {routes.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>端点</th>
                <th>状态</th>
                <th>耗时</th>
                <th>详情</th>
              </tr>
            </thead>
            <tbody>
              {routes.map((r) => (
                <tr key={r.id}>
                  <td>{r.endpoint_id}</td>
                  <td>{statusNames[r.status] ?? r.status}</td>
                  <td>
                    {r.latency_ms !== null ? `${r.latency_ms} ms` : "未提供"}
                  </td>
                  <td>
                    <details>
                      <summary>查看选择依据</summary>
                      <pre>{JSON.stringify(r.decision, null, 2)}</pre>
                      <p>{r.error_type ?? "未记录异常"}</p>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">
            {demo ? "示例任务没有调用模型。" : "当前任务尚无路由记录。"}
          </p>
        )}
        {health.endpoints.length > 0 && (
          <details>
            <summary>端点健康与冷却状态</summary>
            <pre>{JSON.stringify(health.endpoints, null, 2)}</pre>
          </details>
        )}
      </section>
      <section className="review-notes">
        <div className="section-heading">
          <h2>评审规则</h2>
          <Button variant="outline" disabled={demo || busy} onClick={reload}>
            重载 Skills
          </Button>
        </div>
        {skills?.last_error && (
          <p className="warning">最近一次重载失败，保留上一版有效规则。</p>
        )}
        <table className="data-table">
          <thead>
            <tr>
              <th>规则</th>
              <th>版本</th>
              <th>内容哈希</th>
            </tr>
          </thead>
          <tbody>
            {skills?.skills.map((s) => (
              <tr key={s.id}>
                <td>{s.id}</td>
                <td>{s.version}</td>
                <td className="mono" title={s.content_hash}>
                  {s.content_hash.slice(0, 16)}
                </td>
              </tr>
            ))}
            {!skills?.skills.length && (
              <tr>
                <td colSpan={3}>没有已加载规则。</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
      <section className="review-notes">
        <h2>评审偏好</h2>
        <form
          key={JSON.stringify(preferences)}
          onSubmit={save}
          className="form-stack preference-form"
        >
          <div className="form-pair">
            <label>
              比较周期（月）
              <input
                name="months"
                type="number"
                min={1}
                max={1200}
                defaultValue={preferences.comparison_months ?? 36}
                required
              />
            </label>
            <label>
              币种
              <select
                name="currency"
                defaultValue={preferences.currency ?? "CNY"}
              >
                <option>CNY</option>
                <option>USD</option>
                <option>EUR</option>
              </select>
            </label>
          </div>
          <label>
            报告详略
            <select
              name="style"
              defaultValue={preferences.report_style ?? "detailed"}
            >
              <option value="detailed">详细</option>
              <option value="concise">简洁</option>
            </select>
          </label>
          <label>
            关注领域
            <input
              name="focus"
              defaultValue={preferences.focus_areas?.join("，")}
              placeholder="以逗号分隔，例如：迁移范围，验收条件"
            />
          </label>
          <Button type="submit" disabled={demo || busy}>
            保存偏好
          </Button>
        </form>
      </section>
    </div>
  );
}
