import { useEffect, useRef, useState } from "react";
import { fetchImportedFiles, listMaterials, uploadMaterials } from "../api";
import type { ImportedFile } from "../api";
import { useT } from "../i18n";
import { FruitTreeMark } from "./illustrations";

export function Materials() {
  const t = useT();
  const [items, setItems] = useState<ImportedFile[]>([]);
  const [msg, setMsg] = useState<string>("");
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = (): void => {
    fetchImportedFiles()
      .then(setItems)
      .catch(() => {
        listMaterials()
          .then((names) => setItems(names.map(importedFileFromLegacyName)))
          .catch(() => undefined);
      });
  };

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const onUpload = async (): Promise<void> => {
    const files = fileRef.current?.files;
    if (!files || files.length === 0) return;
    try {
      const res = await uploadMaterials("default", files);
      const skipped = res.skipped.length ? t("seeds.skipped", { n: res.skipped.length }) : "";
      setMsg(t("seeds.imported", { n: res.saved.length, skipped }));
      if (fileRef.current) fileRef.current.value = "";
      setSelectedNames([]);
      refresh();
    } catch (err) {
      setMsg(String(err));
    }
  };

  return (
    <div className="card">
      <div className="section-head seeds-head">
        <h2>{t("seeds.title")}</h2>
        <FruitTreeMark fruits={0} size={40} />
      </div>
      <div className="controls">
        <input
          ref={fileRef}
          className="visually-hidden"
          type="file"
          multiple
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            setSelectedNames(files.map((file) => file.name));
          }}
        />
        <button className="ghost" type="button" onClick={() => fileRef.current?.click()}>
          {t("seeds.choose")}
        </button>
        <button onClick={() => void onUpload()} disabled={selectedNames.length === 0}>
          {t("seeds.import")}
        </button>
        {msg && <span className="hint">{msg}</span>}
      </div>
      {selectedNames.length > 0 && (
        <p className="hint selected-files">
          {t("seeds.selected", { n: selectedNames.length })}: {selectedNames.slice(0, 3).join(", ")}
          {selectedNames.length > 3 ? "..." : ""}
        </p>
      )}
      {items.length === 0 ? (
        <p className="muted">{t("seeds.empty")}</p>
      ) : (
        <ul className="imported-list">
          {items.map((item) => (
            <li className="imported-item" key={item.id}>
              <div className="imported-title">
                <b>{item.original_name}</b>
                <span className={`pill import-${item.status}`}>
                  {item.status === "missing" ? t("seeds.missing") : t("seeds.active")}
                </span>
              </div>
              <div className="imported-meta">
                {item.collection !== "default" && (
                  <span>
                    {t("seeds.sourceGroup")}: {item.collection}
                  </span>
                )}
                <span>{item.size_bytes ? formatBytes(item.size_bytes) : t("seeds.sizeUnknown")}</span>
                <span>{item.imported_at ? formatImportedAt(item.imported_at) : t("seeds.legacy")}</span>
              </div>
              {item.original_name !== item.stored_name && (
                <p className="hint">{t("seeds.storedAs", { name: item.stored_name })}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function importedFileFromLegacyName(name: string): ImportedFile {
  const parts = name.split("/");
  const storedName = parts[parts.length - 1] || name;
  return {
    id: `legacy:${name}`,
    original_name: storedName,
    stored_name: storedName,
    relative_path: name,
    collection: parts.length > 1 ? parts[0] : "default",
    size_bytes: 0,
    sha256: "",
    imported_at: "",
    status: "active",
  };
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatImportedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}
