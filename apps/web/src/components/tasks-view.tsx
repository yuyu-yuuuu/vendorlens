"use client";
import { useEffect, useRef, useState } from "react";
import { Plus, RefreshCw, ArrowRight } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { demoConfig } from "@/lib/demo";
import { readEvents } from "@/lib/events";
import {
  taskId,
  statusNames,
  type Config,
  type RunEvent,
  type Task,
  type Vendor,
} from "@/lib/types";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
export function TasksView({
  projectId,
  vendors,
  tasks,
  demo,
  onRefresh,
  onReport,
  onSelect,
  selectedId,
}: {
  projectId: string;
  vendors: Vendor[];
  tasks: Task[];
  demo: boolean;
  onRefresh: () => Promise<void>;
  onReport: (id: string) => void;
  onSelect: (id: string) => void;
  selectedId: string;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [config, setConfig] = useState<Config>(demoConfig);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [streamError, setStreamError] = useState("");
  const [reconnect, setReconnect] = useState(0);
  const sequence = useRef(0);
  const [sessionId, setSessionId] = useState("");
  const [context, setContext] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let cancelled = false;
    setSessionId("");
    setContext(null);
    setError("");
    if (demo) {
      setConfig(demoConfig);
      return;
    }
    api<Config>(`/projects/${projectId}/review-config`)
      .then((c) => {
        if (!cancelled) setConfig(c);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, demo]);
  useEffect(() => {
    setEvents([]);
    sequence.current = 0;
  }, [selectedId]);
  useEffect(() => {
    if (demo || !selectedId) return;
    const controller = new AbortController();
    let stopped = false;
    setStreamError("");
    async function follow() {
      while (!stopped) {
        try {
          let terminal = false;
          await readEvents(
            selectedId,
            sequence.current,
            controller.signal,
            (event) => {
              sequence.current = event.sequence;
              setEvents((old) =>
                old.some((e) => e.sequence === event.sequence)
                  ? old
                  : [...old, event],
              );
              if (["run.finished", "run.failed"].includes(event.type))
                terminal = true;
            },
          );
          if (stopped) return;
          await onRefresh();
          if (terminal) return;
          const task = await api<Task>(`/tasks/${selectedId}`);
          if (!["queued", "running"].includes(task.status)) return;
        } catch (e) {
          if (controller.signal.aborted) return;
          setStreamError(String(e));
          return;
        }
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, 1500);
          controller.signal.addEventListener(
            "abort",
            () => {
              clearTimeout(timer);
              resolve();
            },
            { once: true },
          );
        });
      }
    }
    void follow();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [demo, selectedId, reconnect]);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    if (data.getAll("vendor").length < 1 || data.getAll("vendor").length > 20) {
      setError("请选择 1–20 家供应商");
      setBusy(false);
      return;
    }
    try {
      const result = await api<Task>(`/projects/${projectId}/tasks`, {
        method: "POST",
        body: JSON.stringify({
          objective: data.get("objective"),
          vendor_ids: data.getAll("vendor"),
          industry: data.get("industry"),
          comparison_months: Number(data.get("months")),
          currency: data.get("currency"),
          ...(sessionId ? { session_id: sessionId, user_id: "local" } : {}),
        }),
      });
      setOpen(false);
      onSelect(taskId(result));
      await onRefresh();
    } catch (e) {
      setError(String(e));
      if (
        e instanceof ApiError &&
        e.status === 503 &&
        typeof e.detail === "object" &&
        e.detail !== null &&
        "task_id" in e.detail
      ) {
        onSelect(String(e.detail.task_id));
        await onRefresh();
      }
    } finally {
      setBusy(false);
    }
  }
  async function retry(id: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/tasks/${id}/retry`, { method: "POST" });
      onSelect(id);
      setReconnect((x) => x + 1);
      await onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function saveMessage() {
    setBusy(true);
    setError("");
    try {
      let id = sessionId;
      if (!id) {
        const result = await api<{ session_id: string }>(
          `/projects/${projectId}/sessions`,
          { method: "POST", body: JSON.stringify({ user_id: "local" }) },
        );
        id = result.session_id;
        setSessionId(id);
      }
      await api(`/projects/${projectId}/sessions/${id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: message }),
      });
      setContext(await api(`/projects/${projectId}/sessions/${id}/context`));
      setMessage("");
      setNotice("补充意见已保存，新评审将关联本次会话。");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="section-toolbar">
        <p className="muted">评审目标、执行状态与节点进度。</p>
        <Button
          disabled={demo || !vendors.length}
          onClick={() => {
            setError("");
            setOpen(true);
          }}
        >
          <Plus size={17} />
          新建评审
        </Button>
      </div>
      {error && !open && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      {demo && (
        <p className="subtle-note">
          示例任务用于浏览。连接真实项目后可提交评审。
        </p>
      )}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>评审任务</th>
              <th>状态</th>
              <th>执行次数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task, i) => (
              <tr
                className={selectedId === taskId(task) ? "selected-row" : ""}
                key={taskId(task)}
              >
                <td>
                  <button
                    className="text-button"
                    onClick={() => onSelect(taskId(task))}
                  >
                    {demo ? "供应商方案评审" : `评审 ${tasks.length - i}`}
                    <small className="muted mono">
                      {taskId(task).slice(0, 8)}
                    </small>
                  </button>
                </td>
                <td>{statusNames[task.status] ?? task.status}</td>
                <td>{task.attempt}</td>
                <td>
                  {["completed", "partially_completed"].includes(
                    task.status,
                  ) && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onReport(taskId(task))}
                    >
                      查看报告
                      <ArrowRight size={15} />
                    </Button>
                  )}
                  {!demo &&
                    ["failed", "enqueue_failed", "running"].includes(
                      task.status,
                    ) && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => retry(taskId(task))}
                      >
                        <RefreshCw size={14} />
                        重试 / 恢复
                      </Button>
                    )}
                </td>
              </tr>
            ))}
            {!tasks.length && (
              <tr>
                <td colSpan={4}>尚无评审任务。请先上传资料并建立需求目录。</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {selectedId && (
        <section className="review-notes">
          <h2>执行进度</h2>
          {streamError && (
            <p role="alert" className="warning">
              {streamError}{" "}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setReconnect((x) => x + 1)}
              >
                重新连接
              </Button>
            </p>
          )}
          <ol className="event-list">
            {(demo
              ? [
                  { sequence: 1, type: "run.queued", payload: {} },
                  {
                    sequence: 2,
                    type: "node.completed",
                    payload: { node: "compose_report" },
                  },
                  {
                    sequence: 3,
                    type: "run.finished",
                    payload: { status: "partially_completed" },
                  },
                ]
              : events
            ).map((event) => (
              <li key={event.sequence}>
                <span className="event-number">{event.sequence}</span>
                <span>
                  {event.type === "run.finished"
                    ? "评审结束"
                    : event.type === "run.queued"
                      ? "任务已排队"
                      : event.type === "node.completed"
                        ? "节点完成"
                        : event.type}
                  <small>
                    {String(
                      event.payload.node ??
                        statusNames[String(event.payload.status)] ??
                        "",
                    )}
                  </small>
                </span>
              </li>
            ))}
          </ol>
          {!demo && !events.length && !streamError && (
            <p className="muted">等待任务事件…</p>
          )}
        </section>
      )}
      {!demo && (
        <section className="review-notes">
          <h2>评审讨论</h2>
          <label>
            补充意见
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              maxLength={8000}
              placeholder="例如：重点核实数据迁移的费用范围。"
            />
          </label>
          <Button
            variant="outline"
            disabled={busy || !message.trim()}
            onClick={saveMessage}
          >
            保存补充意见
          </Button>
          {notice && (
            <p role="status" className="positive">
              {notice}
            </p>
          )}
          {context !== null && (
            <details>
              <summary>查看本次会话上下文</summary>
              <pre>{JSON.stringify(context, null, 2)}</pre>
            </details>
          )}
        </section>
      )}
      <Dialog open={open} onOpenChange={setOpen} title="新建供应商评审">
        <form onSubmit={submit} className="form-stack">
          <label>
            评审目标
            <textarea
              name="objective"
              required
              maxLength={4000}
              placeholder="比较方案、三年总拥有成本与交付条件"
            />
          </label>
          <fieldset>
            <legend>参与供应商（最多 20 家）</legend>
            {vendors.map((v) => (
              <label className="checkbox-label" key={v.id}>
                <input
                  type="checkbox"
                  name="vendor"
                  value={v.id}
                  defaultChecked
                />
                {v.name}
              </label>
            ))}
          </fieldset>
          <div className="form-pair">
            <label>
              行业
              <select name="industry">
                <option value="general">通用评审</option>
                <option value="software">软件采购</option>
              </select>
            </label>
            <label>
              比较周期（月）
              <input
                name="months"
                type="number"
                min={1}
                max={1200}
                required
                defaultValue={config.comparison_months}
              />
            </label>
          </div>
          <label>
            币种
            <select name="currency" defaultValue={config.currency}>
              <option>CNY</option>
              <option>USD</option>
              <option>EUR</option>
            </select>
          </label>
          <p className="muted">
            权重：需求 {config.requirement_weight * 100}% · 成本{" "}
            {config.cost_weight * 100}% · 承诺 {config.commitment_weight * 100}%
          </p>
          {sessionId && (
            <p className="positive">本次评审将关联已保存的会话。</p>
          )}
          {error && (
            <p role="alert" className="error-message">
              {error}
            </p>
          )}
          <Button type="submit" disabled={busy}>
            {busy ? "正在提交…" : "提交评审"}
          </Button>
          <small className="muted">
            提交后由 Worker 执行，可能产生所配置模型的调用费用。
          </small>
        </form>
      </Dialog>
    </>
  );
}
