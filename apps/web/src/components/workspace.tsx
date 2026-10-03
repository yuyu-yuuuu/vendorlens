"use client";
import { useCallback, useEffect, useState } from "react";
import {
  Folder,
  FileText,
  ClipboardList,
  Clock,
  Download,
  ChevronDown,
  Plus,
  Printer,
} from "lucide-react";
import { api, exportJson } from "@/lib/api";
import {
  demoProject,
  demoVendors,
  demoTask,
  demoReport,
  demoDetails,
  demoEvidence,
} from "@/lib/demo";
import {
  taskId,
  type Details,
  type Evidence,
  type Project,
  type ReportBundle,
  type Task,
  type Vendor,
} from "@/lib/types";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
import { ReportView } from "./report-view";
import { EvidencePanel } from "./evidence-panel";
import { DocumentsView } from "./documents-view";
import { TasksView } from "./tasks-view";
import { RuntimeView } from "./runtime-view";
type View = "documents" | "tasks" | "reports" | "runtime";
const nav = [
  { id: "documents" as View, label: "项目资料", icon: Folder },
  { id: "tasks" as View, label: "评审任务", icon: ClipboardList },
  { id: "reports" as View, label: "评审报告", icon: FileText },
  { id: "runtime" as View, label: "运行记录", icon: Clock },
];
export function Workspace() {
  const [demo, setDemo] = useState(true);
  const [view, setView] = useState<View>("reports");
  const [projects, setProjects] = useState<Project[]>([demoProject]);
  const [projectId, setProjectId] = useState(demoProject.id);
  const [vendors, setVendors] = useState<Vendor[]>(demoVendors);
  const [tasks, setTasks] = useState<Task[]>([demoTask]);
  const [selectedId, setSelectedId] = useState("demo-task");
  const [bundle, setBundle] = useState<ReportBundle | null>(demoReport);
  const [details, setDetails] = useState<Details | null>(demoDetails);
  const [evidence, setEvidence] = useState<Evidence | null>(demoEvidence);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [projectDialog, setProjectDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [modeReady, setModeReady] = useState(false);
  useEffect(() => {
    setModeReady(true);
    if (window.matchMedia("(max-width: 900px)").matches) setEvidence(null);
    if (new URLSearchParams(window.location.search).get("mode") === "live")
      void connect();
  }, []);
  async function connect() {
    setLoading(true);
    setDemo(false);
    setError("");
    setProjects([]);
    setProjectId("");
    setVendors([]);
    setTasks([]);
    setSelectedId("");
    setBundle(null);
    setDetails(null);
    setEvidence(null);
    try {
      const list = await api<Project[]>("/projects");
      setProjects(list);
      if (list.length) setProjectId(list[0].id);
      else setView("documents");
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }
  function showDemo() {
    setDemo(true);
    setProjects([demoProject]);
    setProjectId(demoProject.id);
    setVendors(demoVendors);
    setTasks([demoTask]);
    setSelectedId("demo-task");
    setBundle(demoReport);
    setDetails(demoDetails);
    setEvidence(
      window.matchMedia("(max-width: 900px)").matches ? null : demoEvidence,
    );
    setError("");
    setView("reports");
  }
  const refresh = useCallback(async () => {
    if (demo || !projectId) return;
    const list = await api<Task[]>(`/projects/${projectId}/tasks`);
    setTasks(list);
  }, [demo, projectId]);
  const refreshVendors = useCallback(async () => {
    if (!demo && projectId)
      setVendors(await api<Vendor[]>(`/projects/${projectId}/vendors`));
  }, [demo, projectId]);
  useEffect(() => {
    if (demo || !projectId) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    setEvidence(null);
    setBundle(null);
    setDetails(null);
    setVendors([]);
    setTasks([]);
    setSelectedId("");
    Promise.all([
      api<Vendor[]>(`/projects/${projectId}/vendors`),
      api<Task[]>(`/projects/${projectId}/tasks`),
    ])
      .then(([v, t]) => {
        if (cancelled) return;
        setVendors(v);
        setTasks(t);
        setSelectedId(t[0] ? taskId(t[0]) : "");
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [demo, projectId]);
  useEffect(() => {
    if (demo || view !== "reports" || !selectedId) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    setBundle(null);
    setDetails(null);
    setEvidence(null);
    Promise.all([
      api<ReportBundle>(`/tasks/${selectedId}/report`),
      api<Details>(`/tasks/${selectedId}/assessments`),
    ])
      .then(([report, info]) => {
        if (!cancelled) {
          setBundle(report);
          setDetails(info);
          setEvidence(
            window.matchMedia("(max-width: 900px)").matches
              ? null
              : (info.evidence[0] ?? null),
          );
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [demo, selectedId, view]);
  async function createProject(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          description: data.get("description"),
        }),
      });
      setProjects((old) => [...old, project]);
      setProjectId(project.id);
      setProjectDialog(false);
      setView("documents");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const project = projects.find((p) => p.id === projectId);
  const closeEvidence = useCallback(() => setEvidence(null), []);
  return (
    <div className="workspace">
      <aside className="sidebar">
        <a href="/" className="brand">
          VendorLens
        </a>
        <nav aria-label="工作台导航">
          {nav.map((item) => (
            <button
              key={item.id}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => {
                setView(item.id);
                setError("");
              }}
            >
              <item.icon size={24} strokeWidth={1.6} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <label className="project-select-label">
            当前项目
            <select
              aria-label="切换项目"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
            >
              <option value="" disabled>
                选择项目
              </option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          {!demo && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setProjectDialog(true)}
            >
              <Plus size={15} />
              新建项目
            </Button>
          )}
          <button
            className="connection-button"
            disabled={loading || !modeReady}
            onClick={() => (demo ? connect() : showDemo())}
          >
            {demo ? "连接真实项目" : "查看示例资料"}
            <span>↗</span>
          </button>
        </div>
      </aside>
      <main className="main">
        <header className="page-header">
          <div>
            {demo && <p className="eyebrow">示例资料</p>}
            <h1>
              {view === "reports"
                ? "供应商评审"
                : nav.find((n) => n.id === view)?.label}
            </h1>
            <p className="project-name">
              项目：{project?.name ?? "尚未选择项目"}
            </p>
          </div>
          {view === "reports" && bundle && (
            <div className="export-control">
              <Button
                onClick={() => setExportOpen(!exportOpen)}
                aria-expanded={exportOpen}
              >
                <Download size={19} />
                导出报告
              </Button>
              {exportOpen && (
                <div className="export-menu">
                  <button
                    onClick={() => {
                      exportJson(bundle, "VendorLens-评审报告.json");
                      setExportOpen(false);
                    }}
                  >
                    下载 JSON
                  </button>
                  <button
                    onClick={() => {
                      setExportOpen(false);
                      window.print();
                    }}
                  >
                    <Printer size={15} />
                    打印 / 保存 PDF
                  </button>
                </div>
              )}
            </div>
          )}
        </header>
        {!demo && view === "reports" && tasks.length > 0 && (
          <label className="report-picker">
            评审任务
            <select
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
            >
              {tasks.map((t, i) => (
                <option key={taskId(t)} value={taskId(t)}>
                  评审 {tasks.length - i} · {t.status}
                </option>
              ))}
            </select>
            <ChevronDown size={15} />
          </label>
        )}
        {error && !projectDialog && (
          <div className="error-message" role="alert">
            <p>{error}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => (!projectId ? connect() : setView("tasks"))}
            >
              {!projectId ? "重试连接" : "查看任务状态"}
            </Button>
          </div>
        )}
        {loading ? (
          <p className="empty-state">正在读取项目…</p>
        ) : !projectId ? (
          <div className="empty-state">
            <h2>建立评审项目</h2>
            <p>创建项目，上传需求书和供应商方案，开始一次有据可查的评审。</p>
            <Button disabled={demo} onClick={() => setProjectDialog(true)}>
              新建项目
            </Button>
          </div>
        ) : view === "reports" ? (
          bundle && details ? (
            <div
              className={`report-layout ${!evidence ? "without-inspector" : ""}`}
            >
              <div className="report-content">
                <ReportView
                  bundle={bundle}
                  details={details}
                  vendors={vendors}
                  demo={demo}
                  onEvidence={setEvidence}
                />
              </div>
              {evidence && (
                <EvidencePanel
                  evidence={evidence}
                  demo={demo}
                  onClose={closeEvidence}
                />
              )}
            </div>
          ) : (
            !error && (
              <p className="empty-state">选择已完成的评审任务查看报告。</p>
            )
          )
        ) : view === "documents" ? (
          <DocumentsView
            key={`${demo}-${projectId}`}
            projectId={projectId}
            vendors={vendors}
            demo={demo}
            onVendorChange={() =>
              void refreshVendors().catch((e) => setError(String(e)))
            }
          />
        ) : view === "tasks" ? (
          <TasksView
            key={`${demo}-${projectId}`}
            projectId={projectId}
            vendors={vendors}
            tasks={tasks}
            demo={demo}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onRefresh={refresh}
            onReport={(id) => {
              setSelectedId(id);
              setView("reports");
            }}
          />
        ) : (
          <RuntimeView
            key={`${demo}-${projectId}`}
            projectId={projectId}
            taskId={selectedId}
            demo={demo}
          />
        )}
      </main>
      <Dialog
        open={projectDialog}
        onOpenChange={setProjectDialog}
        title="新建评审项目"
      >
        <form className="form-stack" onSubmit={createProject}>
          <label>
            项目名称
            <input name="name" required maxLength={200} />
          </label>
          <label>
            项目说明
            <textarea name="description" maxLength={10000} />
          </label>
          {error && (
            <p className="error-message" role="alert">
              {error}
            </p>
          )}
          <Button type="submit" disabled={busy}>
            {busy ? "正在创建…" : "创建项目"}
          </Button>
        </form>
      </Dialog>
    </div>
  );
}
