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

  useEffect(() => {
    let active = true;
    setOutput(null);
    setError("");
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
        </div>
      </header>

      {exportMsg && <p className={exportMsg.includes("failed") ? "errors" : "ok"}>{exportMsg}</p>}
      {error && <p className="errors">{error}</p>}
      {!output && !error && <p className="muted">Loading output...</p>}
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
