import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import {
  exportOutputs,
  fetchOutputRaw,
  markLearningNodeRead,
  openLearningNode,
  submitLearningFeedback,
} from "../api";
import type { RawOutput } from "../api";
import { useT } from "../i18n";
import { formatBytes, formatDateTime } from "../lib/format";
import { chooseExportDestination } from "../lib/export";

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
  const t = useT();
  const [output, setOutput] = useState<RawOutput | null>(null);
  const [error, setError] = useState<string>("");
  const [exporting, setExporting] = useState<boolean>(false);
  const [exportMsg, setExportMsg] = useState<string>("");
  const [exportOk, setExportOk] = useState<boolean>(true);
  const [learningMsg, setLearningMsg] = useState<string>("");
  const [learningOk, setLearningOk] = useState<boolean>(true);
  const [feedback, setFeedback] = useState<string>("");
  const [feedbackBusy, setFeedbackBusy] = useState<boolean>(false);
  const [feedbackOpen, setFeedbackOpen] = useState<boolean>(false);
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
        if (active) {
          setLearningOk(true);
          setLearningMsg(t("reader.progressSaved"));
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setLearningOk(false);
          setLearningMsg(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      active = false;
    };
  }, [target.from, target.nodeId, t]);

  const exportCurrent = async (): Promise<void> => {
    setExporting(true);
    setExportMsg("");
    try {
      const destination = await chooseExportDestination();
      if (!destination) {
        setExportOk(false);
        setExportMsg(t("fruits.exportCancelled"));
        return;
      }
      const result = await exportOutputs(destination, [target.name]);
      const failed = result.failed.length ? `, failed ${result.failed.length}` : "";
      const skipped = result.skipped.length ? `, skipped ${result.skipped.length}` : "";
      setExportOk(result.failed.length === 0);
      setExportMsg(`Exported ${result.exported.length}${skipped}${failed}.`);
    } catch (err) {
      setExportOk(false);
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
      setLearningOk(true);
      setLearningMsg(t("reader.marked"));
    } catch (err) {
      setLearningOk(false);
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
      setFeedbackOpen(false);
      setLearningOk(true);
      setLearningMsg(t("reader.feedbackApplied"));
    } catch (err) {
      setLearningOk(false);
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
              {formatBytes(output.size_bytes)} · {t("common.updated")} {formatDateTime(output.updated_at)}
            </p>
          )}
        </div>
        <div className="reader-actions">
          <button
            className="ghost"
            type="button"
            onClick={target.from === "dag" ? onBackToDag : onBackToOutputs}
          >
            {target.from === "dag" ? t("reader.backToHarvest") : t("reader.backToFruits")}
          </button>
          <button type="button" onClick={() => void exportCurrent()} disabled={exporting}>
            {exporting ? t("reader.exporting") : t("reader.export")}
          </button>
          {target.nodeId && (
            <button
              className={feedbackOpen ? "" : "ghost"}
              type="button"
              onClick={() => setFeedbackOpen((open) => !open)}
            >
              {t("reader.feedback")}
            </button>
          )}
          {target.nodeId && (
            <button type="button" onClick={() => void markRead()} disabled={readBusy}>
              {readBusy ? t("reader.marking") : t("reader.markRead")}
            </button>
          )}
        </div>
      </header>

      {exportMsg && <p className={exportOk ? "ok" : "errors"}>{exportMsg}</p>}
      {learningMsg && <p className={learningOk ? "ok" : "errors"}>{learningMsg}</p>}
      {error && <p className="errors">{error}</p>}
      {!output && !error && <p className="muted">{t("common.loading")}</p>}
      {target.nodeId && output && feedbackOpen && (
        <section className="reader-feedback" aria-label="Learning feedback">
          <h2>{t("reader.feedback")}</h2>
          <textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder={t("reader.feedbackPlaceholder")}
            rows={4}
          />
          <div className="reader-feedback-actions">
            <button
              type="button"
              onClick={() => void submitFeedback()}
              disabled={feedbackBusy || !feedback.trim()}
            >
              {feedbackBusy ? t("reader.revising") : t("reader.submit")}
            </button>
          </div>
        </section>
      )}
      {output && (
        <article className="reader-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
            {output.markdown}
          </ReactMarkdown>
        </article>
      )}
    </section>
  );
}
