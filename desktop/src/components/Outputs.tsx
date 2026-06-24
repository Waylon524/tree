import { useEffect, useState } from "react";
import {
  chooseExportDirectory,
  exportOutputs,
  fetchOutputHtml,
  fetchOutputs,
  isTauri,
} from "../api";
import { useT } from "../i18n";

export function Outputs({ onReadOutput }: { onReadOutput?: (name: string) => void }) {
  const t = useT();
  const [files, setFiles] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [selectedForExport, setSelectedForExport] = useState<string[]>([]);
  const [html, setHtml] = useState<string>("");
  const [exporting, setExporting] = useState<boolean>(false);
  const [exportMsg, setExportMsg] = useState<string>("");

  useEffect(() => {
    let active = true;
    const load = (): void => {
      fetchOutputs()
        .then((list) => {
          if (!active) return;
          setFiles(list);
          setSelectedForExport((current) => current.filter((name) => list.includes(name)));
        })
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const open = async (name: string): Promise<void> => {
    setSelected(name);
    if (onReadOutput) {
      onReadOutput(name);
      return;
    }
    try {
      setHtml(await fetchOutputHtml(name));
    } catch {
      setHtml("<p>Failed to load.</p>");
    }
  };

  const toggleExport = (name: string): void => {
    setSelectedForExport((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    );
  };

  const chooseDestination = async (): Promise<string | null> => {
    if (isTauri()) return chooseExportDirectory();
    return window.prompt("Export destination folder path")?.trim() || null;
  };

  const exportFiles = async (mode: "selected" | "all"): Promise<void> => {
    const names = mode === "all" ? files : selectedForExport;
    if (names.length === 0) {
      setExportMsg(mode === "all" ? t("fruits.noneToExport") : t("fruits.selectToExport"));
      return;
    }
    setExporting(true);
    setExportMsg("");
    try {
      const destination = await chooseDestination();
      if (!destination) {
        setExportMsg(t("fruits.exportCancelled"));
        return;
      }
      const result = await exportOutputs(destination, names);
      const details = [
        `Exported ${result.exported.length}`,
        result.skipped.length ? `skipped ${result.skipped.length}` : "",
        result.failed.length ? `failed ${result.failed.length}` : "",
      ]
        .filter(Boolean)
        .join(", ");
      setExportMsg(`${details}.`);
    } catch (err) {
      setExportMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="card">
      <div className="section-head">
        <h2>{t("fruits.title")}</h2>
        <div className="export-actions">
          <button
            className="ghost"
            type="button"
            disabled={exporting || selectedForExport.length === 0}
            onClick={() => void exportFiles("selected")}
          >
            {t("fruits.exportSelected")}
          </button>
          <button
            type="button"
            disabled={exporting || files.length === 0}
            onClick={() => void exportFiles("all")}
          >
            {t("fruits.exportAll")}
          </button>
        </div>
      </div>
      {exportMsg && <p className={exportMsg.includes("failed") ? "errors" : "hint"}>{exportMsg}</p>}
      {files.length === 0 ? (
        <p className="muted">{t("fruits.empty")}</p>
      ) : (
        <ul className="outputs file-list">
          {files.map((name) => (
            <li className="file-row" key={name}>
              <input
                type="checkbox"
                checked={selectedForExport.includes(name)}
                onChange={() => toggleExport(name)}
                aria-label={`Select ${name}`}
              />
              <a className={name === selected ? "sel" : ""} onClick={() => void open(name)}>
                {name}
              </a>
            </li>
          ))}
        </ul>
      )}
      {html && <article className="markdown" dangerouslySetInnerHTML={{ __html: html }} />}
    </div>
  );
}
