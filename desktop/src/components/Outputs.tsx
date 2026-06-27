import { useEffect, useMemo, useState } from "react";
import { fetchOutputs } from "../api";
import { useT } from "../i18n";
import { useExport } from "../lib/useExport";
import { Button } from "./ui/Button";
import { Card, SectionHeader } from "./ui/Card";
import { Message } from "./ui/Message";

export function Outputs({ onReadOutput }: { onReadOutput?: (name: string) => void }) {
  const t = useT();
  const [files, setFiles] = useState<string[]>([]);
  const [selectedForExport, setSelectedForExport] = useState<string[]>([]);
  const [query, setQuery] = useState<string>("");
  const { exporting, message: exportMsg, ok: exportOk, exportFiles: runExport, reportEmpty } = useExport();

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

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return files;
    return files.filter((name) => name.toLowerCase().includes(q));
  }, [files, query]);

  const toggleExport = (name: string): void => {
    setSelectedForExport((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    );
  };

  const exportFiles = async (mode: "selected" | "all"): Promise<void> => {
    const names = mode === "all" ? files : selectedForExport;
    if (names.length === 0) {
      reportEmpty(mode === "all" ? t("fruits.noneToExport") : t("fruits.selectToExport"));
      return;
    }
    await runExport(names);
  };

  return (
    <Card>
      <SectionHeader
        title={
          <>
            {t("fruits.title")}{" "}
            <span className="muted count-note">{t("fruits.count", { n: files.length })}</span>
          </>
        }
        actions={
          <div className="export-actions">
            <Button
              variant="ghost"
              disabled={exporting || selectedForExport.length === 0}
              onClick={() => void exportFiles("selected")}
            >
              {t("fruits.exportSelected")}
            </Button>
            <Button
              disabled={exporting || files.length === 0}
              onClick={() => void exportFiles("all")}
            >
              {t("fruits.exportAll")}
            </Button>
          </div>
        }
      />
      {files.length > 0 && (
        <input
          className="fruit-search"
          type="search"
          placeholder={t("fruits.search")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      {exportMsg && <Message kind={exportOk ? "hint" : "error"}>{exportMsg}</Message>}
      {files.length === 0 ? (
        <p className="muted">{t("fruits.empty")}</p>
      ) : (
        <div className="fruit-grid">
          {filtered.map((name) => {
            const { seq, title } = parseFruit(name);
            const picked = selectedForExport.includes(name);
            return (
              <div className={`fruit-card ${picked ? "picked" : ""}`} key={name}>
                <div
                  className="fruit-open"
                  role="button"
                  tabIndex={0}
                  title={name}
                  onClick={() => onReadOutput?.(name)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onReadOutput?.(name);
                    }
                  }}
                >
                  <span className="fruit-seq">{seq || "·"}</span>
                  <span className="fruit-title">{title}</span>
                </div>
                <label className="fruit-pick" title="export">
                  <input
                    type="checkbox"
                    checked={picked}
                    onChange={() => toggleExport(name)}
                    aria-label={name}
                  />
                </label>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function parseFruit(name: string): { seq: string; title: string } {
  const base = name.replace(/\.md$/i, "");
  const match = base.match(/^(\d+)[.．、]\s*(.*)$/);
  if (match) {
    const title = match[2].replace(/--[a-z0-9]+$/i, "").trim();
    return { seq: match[1], title: title || base };
  }
  return { seq: "", title: base };
}
