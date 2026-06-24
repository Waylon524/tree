import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import {
  chooseExportDirectory,
  exportOutputs,
  fetchOutputRaw,
  isTauri,
  markLearningNodeRead,
  openLearningNode,
  submitLearningFeedback,
} from "../api";
import type { RawOutput } from "../api";

export interface ReaderTarget {
  name: string;
  from: "dag" | "outputs";
  nodeId?: string;
}

interface ReaderProps {
  target: ReaderTarget;
  onBackToDag: () => void;
  onBackToOutputs: () => void;
}

export function Reader({ target, onBackToDag, onBackToOutputs }: ReaderProps) {
  const [output, setOutput] = useState<RawOutput | null>(null);
  const [error, setError] = useState<string>("");
  const [exporting, setExporting] = useState<boolean>(false);
  const [exportMsg, setExportMsg] = useState<string>("");
  const [learningMsg, setLearningMsg] = useState<string>("");
  const [feedback, setFeedback] = useState<string>("");
  const [feedbackBusy, setFeedbackBusy] = useState<boolean>(false);
  const [readBusy, setReadBusy] = useState<boolean>(false);

  useEffect(() => {
    let active = true;
    setOutput(null);
    setError("");
    setLearningMsg("");
    setFeedback("");
    fetchOutputRaw(target.name)
      .then((data) => {
        if (active) setOutput(data);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      active = false;
    };
  }, [target.name]);

  useEffect(() => {
    if (!target.nodeId || target.from !== "dag") return;
    let active = true;
    openLearningNode(target.nodeId)
      .then(() => {
        if (active) setLearningMsg("Reading progress saved.");
      })
      .catch((err: unknown) => {
        if (active) setLearningMsg(err instanceof Error ? err.message : String(err));
      });
    return () => {
      active = false;
    };
  }, [target.from, target.nodeId]);

  const chooseDestination = async (): Promise<string | null> => {
    if (isTauri()) return chooseExportDirectory();
    return window.prompt("Export destination folder path")?.trim() || null;
  };

  const exportCurrent = async (): Promise<void> => {
    setExporting(true);
    setExportMsg("");
    try {
      const destination = await chooseDestination();
      if (!destination) {
        setExportMsg("Export cancelled.");
        return;
      }
      const result = await exportOutputs(destination, [target.name]);
      const failed = result.failed.length ? `, failed ${result.failed.length}` : "";
      const skipped = result.skipped.length ? `, skipped ${result.skipped.length}` : "";
      setExportMsg(`Exported ${result.exported.length}${skipped}${failed}.`);
    } catch (err) {
      setExportMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(false);
    }
  };

  const markRead = async (): Promise<void> => {
    if (!target.nodeId) return;
    setReadBusy(true);
    setLearningMsg("");
    try {
      await markLearningNodeRead(target.nodeId, true);
      setLearningMsg("Marked as read.");
    } catch (err) {
      setLearningMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setReadBusy(false);
    }
  };

  const submitFeedback = async (): Promise<void> => {
    if (!target.nodeId || !feedback.trim()) return;
    setFeedbackBusy(true);
    setLearningMsg("");
    try {
      await submitLearningFeedback(target.nodeId, feedback.trim());
      const updated = await fetchOutputRaw(target.name);
      setOutput(updated);
      setFeedback("");
      setLearningMsg("Feedback applied. Please reread this node.");
    } catch (err) {
      setLearningMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setFeedbackBusy(false);
    }
  };

  return (
    <section className="reader-shell" aria-label="Output reader">
      <header className="reader-toolbar">
        <div>
          <h1>{target.name}</h1>
          {output && (
            <p className="muted">
              {formatBytes(output.size_bytes)} · Updated {formatDate(output.updated_at)}
            </p>
          )}
        </div>
        <div className="reader-actions">
          <button className="ghost" type="button" onClick={target.from === "dag" ? onBackToDag : onBackToOutputs}>
            Back
          </button>
          <button className="ghost" type="button" onClick={onBackToDag}>
            Back to DAG
          </button>
          <button className="ghost" type="button" onClick={onBackToOutputs}>
            Back to Generated Files
          </button>
          <button type="button" onClick={() => void exportCurrent()} disabled={exporting}>
            {exporting ? "Exporting..." : "Export this file"}
          </button>
          {target.nodeId && (
            <button type="button" onClick={() => void markRead()} disabled={readBusy}>
              {readBusy ? "Saving..." : "完成阅读"}
            </button>
          )}
        </div>
      </header>

      {exportMsg && <p className={exportMsg.includes("failed") ? "errors" : "ok"}>{exportMsg}</p>}
      {learningMsg && (
        <p className={learningMsg.includes("failed") || learningMsg.includes("Error") ? "errors" : "ok"}>
          {learningMsg}
        </p>
      )}
      {error && <p className="errors">{error}</p>}
      {!output && !error && <p className="muted">Loading output...</p>}
      {target.nodeId && output && (
        <section className="reader-feedback" aria-label="Learning feedback">
          <h2>反馈微调</h2>
          <textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="例如：这里没有讲清楚公式里的符号含义，或者缺少一个推导步骤。"
            rows={4}
          />
          <div className="reader-feedback-actions">
            <button
              type="button"
              onClick={() => void submitFeedback()}
              disabled={feedbackBusy || !feedback.trim()}
            >
              {feedbackBusy ? "Revising..." : "提交反馈"}
            </button>
          </div>
        </section>
      )}
      {output && (
        <article className="reader-markdown">
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={[rehypeKatex]}
          >
            {output.markdown}
          </ReactMarkdown>
        </article>
      )}
    </section>
  );
}

function formatBytes(value: number): string {
  if (!value) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
