import { useEffect, useState } from "react";
import { fetchOutputHtml, fetchOutputs } from "../api";

export function Outputs() {
  const [files, setFiles] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [html, setHtml] = useState<string>("");

  useEffect(() => {
    let active = true;
    const load = (): void => {
      fetchOutputs()
        .then((list) => {
          if (active) setFiles(list);
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
    try {
      setHtml(await fetchOutputHtml(name));
    } catch {
      setHtml("<p>Failed to load.</p>");
    }
  };

  return (
    <div className="card">
      <h2>Outputs</h2>
      {files.length === 0 ? (
        <p className="muted">No outputs yet — they appear as nodes PASS.</p>
      ) : (
        <ul className="outputs">
          {files.map((name) => (
            <li key={name}>
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
