import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { fetchSettings, saveSettings } from "../api";
import type { RoleKey, RoleModels, SettingsData, SettingsSave } from "../api";

const ROLES: Array<{ key: RoleKey; label: string }> = [
  { key: "examiner", label: "Examiner" },
  { key: "student", label: "Student" },
  { key: "writer", label: "Writer" },
  { key: "archivist", label: "Archivist" },
  { key: "dagger", label: "Dagger" },
];

const EMPTY_ROLE_MODELS: RoleModels = {
  examiner: "",
  student: "",
  writer: "",
  archivist: "",
  dagger: "",
};

const EMPTY_FIELDS: SettingsSave = {
  llm_api_key: "",
  llm_base_url: "",
  llm_model: "",
  role_models: EMPTY_ROLE_MODELS,
  paddleocr_api_token: "",
};

const MASKED_SECRET = "***";

function fieldsFromSettings(settings: SettingsData): SettingsSave {
  return {
    llm_api_key: settings.llm_api_key_configured ? MASKED_SECRET : "",
    llm_base_url: settings.llm_base_url,
    llm_model: settings.llm_model,
    role_models: settings.role_models,
    paddleocr_api_token: settings.paddleocr_api_token_configured ? MASKED_SECRET : "",
  };
}

export function Settings() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [fields, setFields] = useState<SettingsSave>(EMPTY_FIELDS);
  const [busy, setBusy] = useState<boolean>(false);
  const [message, setMessage] = useState<string>("");

  useEffect(() => {
    fetchSettings()
      .then((data) => {
        setSettings(data);
        setFields(fieldsFromSettings(data));
      })
      .catch((err: unknown) => setMessage(String(err)));
  }, []);

  const set = (key: keyof Omit<SettingsSave, "role_models">) => (
    event: ChangeEvent<HTMLInputElement>,
  ): void => {
    setFields((prev) => ({ ...prev, [key]: event.target.value }));
  };

  const setRole = (key: RoleKey) => (event: ChangeEvent<HTMLInputElement>): void => {
    setFields((prev) => ({
      ...prev,
      role_models: { ...prev.role_models, [key]: event.target.value },
    }));
  };

  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const payload = {
        ...fields,
        llm_api_key: fields.llm_api_key === MASKED_SECRET ? "" : fields.llm_api_key,
        paddleocr_api_token:
          fields.paddleocr_api_token === MASKED_SECRET ? "" : fields.paddleocr_api_token,
      };
      const saved = await saveSettings(payload);
      setSettings(saved);
      setFields(fieldsFromSettings(saved));
      setMessage("Saved global settings.");
    } catch (err) {
      setMessage(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card settings-card">
      <div className="section-head">
        <h2>Settings</h2>
        {settings && <span className="hint">{settings.config_path}</span>}
      </div>

      <form className="settings-form" onSubmit={(event) => void submit(event)}>
        <fieldset>
          <legend>LLM Provider</legend>
          <div className="form-grid">
            <label className="full">
              <span className="label-row">
                <span>API key</span>
                {settings?.llm_api_key_configured && <b>Configured</b>}
              </span>
              <input
                type="text"
                value={fields.llm_api_key}
                onChange={set("llm_api_key")}
                placeholder="API key"
              />
            </label>
            <label>
              Base URL
              <input value={fields.llm_base_url} onChange={set("llm_base_url")} />
            </label>
            <label>
              Default model
              <input value={fields.llm_model} onChange={set("llm_model")} />
            </label>
          </div>

          <div className="role-grid">
            {ROLES.map((role) => (
              <label key={role.key}>
                {role.label}
                <input value={fields.role_models[role.key]} onChange={setRole(role.key)} />
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend>OCR</legend>
          <div className="form-grid">
            <label className="full">
              <span className="label-row">
                <span>PaddleOCR key</span>
                {settings?.paddleocr_api_token_configured && <b>Configured</b>}
              </span>
              <input
                type="text"
                value={fields.paddleocr_api_token}
                onChange={set("paddleocr_api_token")}
                placeholder="API key"
              />
            </label>
          </div>
        </fieldset>

        <div className="form-actions">
          <button type="submit" disabled={busy}>
            {busy ? "Saving..." : "Save settings"}
          </button>
          {message && <span className={message.startsWith("Saved") ? "ok" : "errors"}>{message}</span>}
        </div>
      </form>
    </section>
  );
}
