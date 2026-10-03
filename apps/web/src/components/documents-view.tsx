"use client";
import { useEffect, useState } from "react";
import { FileText, Upload, Plus, Download, RefreshCw } from "lucide-react";
import { api, downloadSource } from "@/lib/api";
import { demoDocuments, demoVersions, demoEvidence } from "@/lib/demo";
import {
  locationText,
  statusNames,
  type Chunk,
  type Document,
  type Requirement,
  type Vendor,
  type Version,
} from "@/lib/types";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
export function DocumentsView({
  projectId,
  vendors,
  demo,
  onVendorChange,
}: {
  projectId: string;
  vendors: Vendor[];
  demo: boolean;
  onVendorChange: () => void;
}) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [versions, setVersions] = useState<Record<string, Version[]>>({});
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [dialog, setDialog] = useState<
    "upload" | "vendor" | "source" | "requirement" | null
  >(null);
  const [activeDoc, setActiveDoc] = useState<Document | null>(null);
  const [versionId, setVersionId] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [selectedChunk, setSelectedChunk] = useState<Chunk | null>(null);
  const [notice, setNotice] = useState("");
  async function load() {
    setLoading(true);
    setError("");
    try {
      if (demo) {
        setDocuments(demoDocuments);
        setVersions(demoVersions);
        setRequirements([
          {
            id: "R1",
            code: "R1",
            text: "支持私有化部署",
            mandatory: true,
            source_chunk_id: "demo",
            weight: 1,
          },
        ]);
        return;
      }
      const [docs, reqs] = await Promise.all([
        api<Document[]>(`/projects/${projectId}/documents`),
        api<Requirement[]>(`/projects/${projectId}/requirements`),
      ]);
      const results = await Promise.all(
        docs.map((d) =>
          api<Version[]>(`/projects/${projectId}/documents/${d.id}/versions`),
        ),
      );
      setDocuments(docs);
      setRequirements(reqs);
      setVersions(Object.fromEntries(docs.map((d, i) => [d.id, results[i]])));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [projectId, demo]);
  async function readVersion(id: string) {
    setVersionId(id);
    setChunks([]);
    setError("");
    setBusy(true);
    try {
      setChunks(
        demo
          ? [
              {
                id: "demo",
                text: demoEvidence.context_text!,
                chunk_type: "parent",
                location: demoEvidence.location,
              },
            ]
          : await api<Chunk[]>(`/projects/${projectId}/versions/${id}/chunks`),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      if (dialog === "vendor") {
        await api(`/projects/${projectId}/vendors`, {
          method: "POST",
          body: JSON.stringify({ name: data.get("name") }),
        });
        onVendorChange();
      } else if (dialog === "upload") {
        if (!data.get("vendor_id")) data.delete("vendor_id");
        await api(`/projects/${projectId}/documents`, {
          method: "POST",
          body: data,
        });
      } else if (dialog === "requirement" && selectedChunk) {
        await api(`/projects/${projectId}/requirements`, {
          method: "POST",
          body: JSON.stringify({
            code: data.get("code"),
            text: data.get("text"),
            mandatory: data.has("mandatory"),
            weight: 1,
            source_chunk_id: selectedChunk.id,
          }),
        });
      }
      setDialog(null);
      setNotice("已保存");
      await load();
    } catch (e) {
      if (dialog === "upload") await load();
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function reindex(id: string) {
    setBusy(true);
    setError("");
    try {
      await api(`/projects/${projectId}/versions/${id}/index`, {
        method: "POST",
      });
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="section-toolbar">
        <p className="muted">公共资料与供应商方案，按版本保留。</p>
        <div>
          <Button
            variant="outline"
            disabled={demo}
            onClick={() => {
              setError("");
              setDialog("vendor");
            }}
          >
            <Plus size={16} />
            新增供应商
          </Button>
          <Button
            disabled={demo}
            onClick={() => {
              setError("");
              setDialog("upload");
            }}
          >
            <Upload size={16} />
            上传资料
          </Button>
        </div>
      </div>
      {demo && (
        <p className="subtle-note">
          示例资料可查看版本；连接项目后可上传和编辑。
        </p>
      )}
      {error && !dialog && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="positive">
          {notice}
        </p>
      )}
      {loading ? (
        <p className="empty-state">正在读取资料…</p>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>文件名称</th>
                <th>归属</th>
                <th>版本</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((d) => {
                const latest = versions[d.id]?.at(-1);
                return (
                  <tr key={d.id}>
                    <td>
                      <span className="inline-icon">
                        <FileText size={18} />
                        {d.logical_name}
                      </span>
                    </td>
                    <td>
                      {vendors.find((v) => v.id === d.vendor_id)?.name ??
                        "项目公共资料"}
                    </td>
                    <td>{latest ? `v${latest.version_number}` : "—"}</td>
                    <td>
                      {latest
                        ? (statusNames[latest.status] ?? latest.status)
                        : "未提供"}
                    </td>
                    <td>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={!latest}
                        onClick={() => {
                          setActiveDoc(d);
                          setDialog("source");
                          void readVersion(latest!.id);
                        }}
                      >
                        查看版本
                      </Button>
                      {latest?.status === "index_failed" && !demo && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() => reindex(latest.id)}
                        >
                          <RefreshCw size={14} />
                          重试索引
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {!documents.length && (
                <tr>
                  <td colSpan={5}>
                    还没有资料，请先上传项目需求书及供应商方案。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      <section className="review-notes">
        <h2>需求目录</h2>
        <p className="muted">从公共资料原文中登记需求，作为评审依据。</p>
        <table className="notes-table">
          <thead>
            <tr>
              <th>编号</th>
              <th>需求</th>
              <th>约束</th>
            </tr>
          </thead>
          <tbody>
            {requirements.map((r) => (
              <tr key={r.id}>
                <th>{r.code}</th>
                <td>{r.text}</td>
                <td>{r.mandatory ? "必选" : "一般"}</td>
              </tr>
            ))}
            {!requirements.length && (
              <tr>
                <td colSpan={3}>查看公共资料版本，选取来源段落并添加需求。</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
      <Dialog
        open={dialog !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDialog(null);
            setError("");
          }
        }}
        title={
          dialog === "upload"
            ? "上传资料"
            : dialog === "vendor"
              ? "新增供应商"
              : dialog === "requirement"
                ? "登记需求"
                : "资料版本"
        }
      >
        {dialog === "source" && activeDoc ? (
          <>
            <p>{activeDoc.logical_name}</p>
            <label>
              版本
              <select
                value={versionId}
                onChange={(e) => readVersion(e.target.value)}
              >
                {versions[activeDoc.id]?.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_number} · {statusNames[v.status] ?? v.status}
                  </option>
                ))}
              </select>
            </label>
            {busy ? (
              <p>正在读取原文…</p>
            ) : (
              <div className="chunk-list">
                {chunks.map((c) => (
                  <article key={c.id}>
                    <p className="field-caption">{locationText(c.location)}</p>
                    <p>{c.text}</p>
                    {!demo &&
                      !activeDoc.vendor_id &&
                      c.chunk_type === "parent" && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setSelectedChunk(c);
                            setDialog("requirement");
                          }}
                        >
                          从此段添加需求
                        </Button>
                      )}
                  </article>
                ))}
                {!chunks.length && <p>此版本没有解析出的文本块。</p>}
              </div>
            )}
            {!demo && (
              <Button
                variant="outline"
                onClick={() =>
                  downloadSource(
                    projectId,
                    versionId,
                    activeDoc.logical_name,
                  ).catch((e) => setError(String(e)))
                }
              >
                <Download size={16} />
                下载原文件
              </Button>
            )}
          </>
        ) : (
          <form onSubmit={submit} className="form-stack">
            {dialog === "vendor" ? (
              <label>
                供应商名称
                <input name="name" required maxLength={200} />
              </label>
            ) : dialog === "upload" ? (
              <>
                <label>
                  资料归属
                  <select name="vendor_id">
                    <option value="">项目公共资料</option>
                    {vendors.map((v) => (
                      <option value={v.id} key={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  选择文件
                  <input
                    name="file"
                    type="file"
                    required
                    accept=".pdf,.docx,.xlsx,.md,.txt"
                  />
                </label>
                <small className="muted">
                  支持文字 PDF、DOCX、XLSX、Markdown 与 TXT。
                </small>
              </>
            ) : (
              <>
                <p className="source-content">{selectedChunk?.text}</p>
                <label>
                  需求编号
                  <input name="code" required maxLength={80} placeholder="R1" />
                </label>
                <label>
                  需求描述
                  <textarea
                    name="text"
                    defaultValue={selectedChunk?.text}
                    required
                    maxLength={10000}
                  />
                </label>
                <label className="checkbox-label">
                  <input type="checkbox" name="mandatory" />
                  必选需求
                </label>
              </>
            )}
            <Button type="submit" disabled={busy}>
              {busy ? "正在保存…" : "保存"}
            </Button>
          </form>
        )}
        {error && (
          <p className="error-message" role="alert">
            {error}
          </p>
        )}
      </Dialog>
    </>
  );
}
