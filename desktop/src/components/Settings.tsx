import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { fetchSettings, saveSettings } from "../api";
import type { RoleKey, RoleModels, SettingsData, SettingsSave } from "../api";
import { useLang, useT } from "../i18n";
import { Materials } from "./Materials";

const ROLES: RoleKey[] = ["examiner", "student", "writer", "archivist", "dagger"];

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
  llama_server_ctx: "22000",
  source_mtu_chunk_tokens: "20000",
};

const MASKED_SECRET = "***";

function fieldsFromSettings(settings: SettingsData): SettingsSave {
  return {
    llm_api_key: settings.llm_api_key_configured ? MASKED_SECRET : "",
    llm_base_url: settings.llm_base_url,
    llm_model: settings.llm_model,
    role_models: settings.role_models,
    paddleocr_api_token: settings.paddleocr_api_token_configured ? MASKED_SECRET : "",
    llama_server_ctx: String(settings.llama_server_ctx),
    source_mtu_chunk_tokens: String(settings.source_mtu_chunk_tokens),
  };
}

export function Settings() {
  const t = useT();
  const { lang, setLang } = useLang();
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [fields, setFields] = useState<SettingsSave>(EMPTY_FIELDS);
  const [busy, setBusy] = useState<boolean>(false);
  const [message, setMessage] = useState<string>("");
  const [ok, setOk] = useState<boolean>(false);

  useEffect(() => {
    fetchSettings()
      .then((data) => {
        setSettings(data);
        setFields(fieldsFromSettings(data));
      })
      .catch((err: unknown) => {
        setOk(false);
        setMessage(String(err));
      });
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
      setOk(true);
      setMessage(t("settings.saved"));
    } catch (err) {
      setOk(false);
      setMessage(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tend">
      <Materials />

      <section className="card settings-card">
        <div className="section-head">
          <h2>{t("tend.title")}</h2>
          <div className="lang-toggle" role="group" aria-label={t("tend.language")}>
            <button
              type="button"
              className={lang === "zh" ? "active" : ""}
              onClick={() => setLang("zh")}
            >
              {t("tend.lang.zh")}
            </button>
            <button
              type="button"
              className={lang === "en" ? "active" : ""}
              onClick={() => setLang("en")}
            >
              {t("tend.lang.en")}
            </button>
          </div>
        </div>
        {settings && <span className="hint">{settings.config_path}</span>}

        <form className="settings-form" onSubmit={(event) => void submit(event)}>
          <fieldset>
            <legend>
              {t("tend.fertilizer")} <small className="legend-note">({t("tend.note.llm")})</small>
            </legend>
            <div className="form-grid">
              <label className="full">
                <span className="label-row">
                  <span>{t("settings.apiKey")}</span>
                  {settings?.llm_api_key_configured && <b>{t("settings.configured")}</b>}
                </span>
                <input
                  type="text"
                  value={fields.llm_api_key}
                  onChange={set("llm_api_key")}
                  placeholder={t("settings.apiKey")}
                />
              </label>
              <label>
                {t("settings.baseUrl")}
                <input value={fields.llm_base_url} onChange={set("llm_base_url")} />
              </label>
              <label>
                {t("settings.defaultModel")}
                <input value={fields.llm_model} onChange={set("llm_model")} />
              </label>
            </div>

            <div className="role-grid">
              {ROLES.map((role) => (
                <label key={role}>
                  {t(`role.${role}`)}
                  <input value={fields.role_models[role]} onChange={setRole(role)} />
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>
              {t("tend.gather")} <small className="legend-note">({t("tend.note.ocr")})</small>
            </legend>
            <div className="form-grid">
              <label className="full">
                <span className="label-row">
                  <span>{t("settings.paddleKey")}</span>
                  {settings?.paddleocr_api_token_configured && <b>{t("settings.configured")}</b>}
                </span>
                <input
                  type="text"
                  value={fields.paddleocr_api_token}
                  onChange={set("paddleocr_api_token")}
                  placeholder={t("settings.apiKey")}
                />
              </label>
            </div>
          </fieldset>

          <fieldset>
            <legend>
              {t("tend.climate")} <small className="legend-note">({t("tend.note.runtime")})</small>
            </legend>
            <div className="form-grid">
              <label>
                {t("settings.llamaCtx")}
                <input
                  type="number"
                  min="1024"
                  max="32768"
                  step="1"
                  value={fields.llama_server_ctx}
                  onChange={set("llama_server_ctx")}
                />
              </label>
              <label>
                {t("settings.mtuChunk")}
                <input
                  type="number"
                  min="500"
                  max="32768"
                  step="1"
                  value={fields.source_mtu_chunk_tokens}
                  onChange={set("source_mtu_chunk_tokens")}
                />
              </label>
            </div>
          </fieldset>

          <div className="form-actions">
            <button type="submit" disabled={busy}>
              {busy ? t("common.saving") : t("settings.save")}
            </button>
            {message && <span className={ok ? "ok" : "errors"}>{message}</span>}
          </div>
        </form>
      </section>
    </div>
  );
}
