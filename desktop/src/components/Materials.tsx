import { useEffect, useRef, useState } from "react";
import { listMaterials, uploadMaterials } from "../api";

export function Materials() {
  const [items, setItems] = useState<string[]>([]);
  const [collection, setCollection] = useState<string>("default");
  const [msg, setMsg] = useState<string>("");
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = (): void => {
    listMaterials()
      .then(setItems)
      .catch(() => undefined);
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
      const res = await uploadMaterials(collection, files);
      const skipped = res.skipped.length ? `, skipped ${res.skipped.length}` : "";
      setMsg(`Added ${res.saved.length}${skipped}.`);
      if (fileRef.current) fileRef.current.value = "";
      refresh();
    } catch (err) {
      setMsg(String(err));
    }
  };

  return (
    <div className="card">
      <h2>Materials</h2>
      <div className="controls">
        <input
          value={collection}
          onChange={(event) => setCollection(event.target.value)}
          placeholder="collection"
        />
        <input ref={fileRef} type="file" multiple />
        <button onClick={() => void onUpload()}>Add</button>
        {msg && <span className="hint">{msg}</span>}
      </div>
      {items.length === 0 ? (
        <p className="muted">No materials yet — add PDFs / images / docs above.</p>
      ) : (
        <ul className="outputs">
          {items.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
