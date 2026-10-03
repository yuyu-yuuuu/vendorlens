"use client";
import { FileText, ExternalLink, X, Download } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";
import { api, downloadSource } from "@/lib/api";
import {
  type Chunk,
  type Evidence,
  locationText,
  domainNames,
} from "@/lib/types";
export function EvidencePanel({
  evidence,
  demo,
  onClose,
}: {
  evidence: Evidence;
  demo: boolean;
  onClose: () => void;
}) {
  const [open, setOpen] = useState<"source" | "version" | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const openRef = useRef(open);
  openRef.current = open;
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !openRef.current && !e.defaultPrevented) {
        onClose();
        previous?.focus();
      }
    };
    const isMobile = window.matchMedia("(max-width: 900px)").matches;
    setMobile(isMobile);
    window.addEventListener("keydown", handler);
    if (window.matchMedia("(max-width: 900px)").matches)
      closeRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", handler);
      previous?.focus();
    };
  }, [onClose, evidence.id]);
  async function source() {
    setOpen("source");
    setError("");
    setBusy(true);
    try {
      setChunks(
        demo
          ? [
              {
                id: evidence.chunk_id,
                text: evidence.context_text ?? evidence.quote,
                location: evidence.location,
                chunk_type: "parent",
              },
            ]
          : await api<Chunk[]>(
              `/projects/${evidence.project_id}/versions/${evidence.document_version_id}/chunks`,
            ),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <aside
      ref={panelRef}
      className="evidence-panel"
      aria-label="证据核验"
      role={mobile ? "dialog" : undefined}
      aria-modal={mobile ? true : undefined}
      onKeyDown={(event) => {
        if (!mobile || event.key !== "Tab") return;
        const nodes = panelRef.current?.querySelectorAll<HTMLButtonElement>(
          "button:not([disabled])",
        );
        if (!nodes?.length) return;
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
    >
      <div className="inspector-title">
        <h2>证据核验</h2>
        <button
          ref={closeRef}
          className="icon-button mobile-close"
          aria-label="关闭证据"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <section>
        <p className="field-caption">核验维度</p>
        <h3 className="sans">
          {evidence.domain ? domainNames[evidence.domain] : "原文来源"}
        </h3>
      </section>
      <section>
        <p className="field-caption">资料来源</p>
        <div className="source-line">
          <FileText size={25} />
          <span>
            {evidence.document_name}
            {evidence.version_number !== null
              ? ` v${evidence.version_number}`
              : ""}{" "}
            · {locationText(evidence.location)}
          </span>
        </div>
      </section>
      <section>
        <p className="field-caption">相关内容摘录</p>
        <blockquote>“{evidence.quote}”</blockquote>
      </section>
      <section className="source-actions">
        <p className="field-caption">操作</p>
        <button onClick={source}>
          <ExternalLink size={20} />
          查看原文
        </button>
        <button onClick={() => setOpen("version")}>
          <FileText size={21} />
          查看版本
        </button>
      </section>
      <Dialog
        open={open !== null}
        onOpenChange={(value) => {
          if (!value) setOpen(null);
        }}
        title={open === "version" ? "引用版本" : "来源原文"}
      >
        <p className="muted">
          {evidence.document_name} · {locationText(evidence.location)}
        </p>
        {open === "version" ? (
          <dl className="definition-list">
            <dt>文档版本</dt>
            <dd>{evidence.version_number ?? "未提供"}</dd>
            <dt>版本 ID</dt>
            <dd>{evidence.document_version_id}</dd>
            <dt>原文哈希</dt>
            <dd className="mono">{evidence.source_text_hash}</dd>
            <dt>来源文件</dt>
            <dd>{evidence.source_available ? "可回查" : "当前不可用"}</dd>
          </dl>
        ) : (
          <>
            {busy ? (
              <p>正在读取原文…</p>
            ) : error ? (
              <p role="alert">{error}</p>
            ) : (
              <div className="source-content">
                {(chunks.filter((c) => c.id === evidence.chunk_id).length
                  ? chunks.filter((c) => c.id === evidence.chunk_id)
                  : []
                ).map((c) => (
                  <p key={c.id}>{c.text}</p>
                ))}
                {!chunks.some((c) => c.id === evidence.chunk_id) && (
                  <p>
                    当前未能回查来源块；评审快照：
                    <br />
                    {evidence.context_text ?? evidence.quote}
                  </p>
                )}
              </div>
            )}
          </>
        )}
        {!demo && evidence.source_available && (
          <Button
            variant="outline"
            onClick={() =>
              downloadSource(
                evidence.project_id,
                evidence.document_version_id,
                evidence.document_name,
              ).catch((e) => setError(String(e)))
            }
          >
            <Download size={17} />
            下载原文件
          </Button>
        )}
        {open === "version" && error && <p role="alert">{error}</p>}
      </Dialog>
    </aside>
  );
}
