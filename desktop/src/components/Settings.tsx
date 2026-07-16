import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import {
  fetchPrompts,
  fetchSettings,
  resetAllPrompts,
  resetPrompt,
  savePrompt,
  saveSettings,
} from "../api";
import type {
  AdvancedSettings,
  PromptKey,
  PromptSettings,
  RoleKey,
  RoleModels,
  SettingsData,
  SettingsSave,
} from "../api";
import { useLang, useT } from "../i18n";
import { Materials } from "./Materials";
import { Button } from "./ui/Button";
import { Field } from "./ui/Field";
import { Message } from "./ui/Message";
import { Toggle } from "./ui/Toggle";

const ROLES: RoleKey[] = ["examiner", "student", "writer", "archivist", "dagger"];
const PROMPT_GROUPS: PromptKey[] = [
  "examiner",
  "student",
  "writer",
  "fast_writer",
  "archivist_clean",
  "archivist_mtu",
  "dagger",
  "dagger_prerequisites",
];

const EMPTY_ROLE_MODELS: RoleModels = {
  examiner: "",
  student: "",
  writer: "",
  archivist: "",
  dagger: "",
};

const EMPTY_ADVANCED_FIELDS: AdvancedSettings = {
  max_iterations: "5",
  max_active_node_runs: "3",
  max_examiner_span_nodes: "3",
  max_retries: "3",
  max_format_retries: "2",
  llm_timeout_sec: "480",
  llm_provider_concurrency: "4",
  llm_context_window: "1000000",
  llm_max_output_tokens: "131072",
  llm_prompt_safety_tokens: "1024",
  pro_degradation_threshold: "3",
  pro_degradation_cooldown_sec: "600",
  source_ingest_concurrency: "4",
  source_ocr_concurrency: "5",
  source_ocr_pdf_max_pages_per_job: "99",
  source_ocr_upload_interval_sec: "5",
  source_embedding_concurrency: "1",
  archivist_mtu_cut_timeout_sec: "480",
  archivist_mtu_repair_attempts: "8",
  archivist_chunk_concurrency: "2",
  dagger_build_timeout_sec: "480",
  dagger_repair_attempts: "3",
  dagger_prerequisite_concurrency: "3",
  dagger_max_nodes_per_call: "400",
  dagger_embed_cluster_enabled: true,
  dagger_cluster_similarity_threshold: "0.80",
  dagger_cluster_top_k: "5",
  dagger_cluster_max_size: "8",
  dagger_cluster_auto_accept_singleton: true,
  dagger_cluster_auto_accept_same_collection: false,
};

const EMPTY_FIELDS: SettingsSave = {
  llm_api_key: "",
  llm_base_url: "",
  llm_model: "",
  llm_provider_profile: "auto",
  role_models: EMPTY_ROLE_MODELS,
  paddleocr_api_token: "",
  paddleocr_api_url: "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
  paddleocr_model: "PaddleOCR-VL-1.6",
  llama_server_ctx: "22000",
  source_mtu_chunk_tokens: "20000",
  node_run_mode: "standard",
  ...EMPTY_ADVANCED_FIELDS,
};

const MASKED_SECRET = "***";

type TextFieldKey = Exclude<
  keyof SettingsSave,
  | "role_models"
  | "dagger_embed_cluster_enabled"
  | "dagger_cluster_auto_accept_singleton"
  | "dagger_cluster_auto_accept_same_collection"
>;

interface AdvancedField {
  key: keyof AdvancedSettings;
  kind: "number" | "boolean";
  min?: string;
  max?: string;
  step?: string;
}

interface AdvancedGroup {
  titleKey: string;
  fields: AdvancedField[];
}

function SecretField({
  label,
  configured,
  value,
  onChange,
}: {
  label: string;
  configured: boolean;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  const t = useT();
  const [revealed, setRevealed] = useState(false);
  return (
    <div className="full secret-field">
      <span className="label-row">
        <span>{label}</span>
        {configured && <b>{t("settings.configured")}</b>}
      </span>
      <span className="secret-input-row">
        <input
          aria-label={label}
          type={revealed ? "text" : "password"}
          value={value}
          onChange={onChange}
          placeholder={label}
          autoComplete="off"
          spellCheck={false}
        />
        <Button variant="ghost" onClick={() => setRevealed((current) => !current)}>
          {revealed ? t("settings.hideSecret") : t("settings.showSecret")}
        </Button>
      </span>
    </div>
  );
}

const ADVANCED_GROUPS: AdvancedGroup[] = [
  {
    titleKey: "settings.advanced.nodeRun",
    fields: [
      { key: "max_iterations", kind: "number", min: "1", max: "50" },
      { key: "max_active_node_runs", kind: "number", min: "1", max: "32" },
      { key: "max_examiner_span_nodes", kind: "number", min: "1", max: "20" },
    ],
  },
  {
    titleKey: "settings.advanced.llm",
    fields: [
      { key: "max_retries", kind: "number", min: "0", max: "20" },
      { key: "max_format_retries", kind: "number", min: "0", max: "10" },
      { key: "llm_timeout_sec", kind: "number", min: "10", max: "3600", step: "1" },
      { key: "llm_provider_concurrency", kind: "number", min: "1", max: "32" },
      { key: "llm_context_window", kind: "number", min: "1024", max: "2000000" },
      { key: "llm_max_output_tokens", kind: "number", min: "1", max: "1000000" },
      { key: "llm_prompt_safety_tokens", kind: "number", min: "0", max: "100000" },
      { key: "pro_degradation_threshold", kind: "number", min: "1", max: "20" },
      { key: "pro_degradation_cooldown_sec", kind: "number", min: "0", max: "86400" },
    ],
  },
  {
    titleKey: "settings.advanced.source",
    fields: [
      { key: "source_ingest_concurrency", kind: "number", min: "1", max: "64" },
      { key: "source_ocr_concurrency", kind: "number", min: "1", max: "32" },
      { key: "source_ocr_pdf_max_pages_per_job", kind: "number", min: "1", max: "500" },
      { key: "source_ocr_upload_interval_sec", kind: "number", min: "0", max: "120", step: "0.1" },
      { key: "source_embedding_concurrency", kind: "number", min: "1", max: "16" },
    ],
  },
  {
    titleKey: "settings.advanced.archivist",
    fields: [
      { key: "archivist_mtu_cut_timeout_sec", kind: "number", min: "10", max: "3600" },
      { key: "archivist_mtu_repair_attempts", kind: "number", min: "0", max: "20" },
      { key: "archivist_chunk_concurrency", kind: "number", min: "1", max: "16" },
    ],
  },
  {
    titleKey: "settings.advanced.dagger",
    fields: [
      { key: "dagger_build_timeout_sec", kind: "number", min: "10", max: "3600" },
      { key: "dagger_repair_attempts", kind: "number", min: "0", max: "20" },
      { key: "dagger_prerequisite_concurrency", kind: "number", min: "1", max: "32" },
      { key: "dagger_max_nodes_per_call", kind: "number", min: "1", max: "5000" },
      { key: "dagger_embed_cluster_enabled", kind: "boolean" },
      { key: "dagger_cluster_similarity_threshold", kind: "number", min: "0", max: "1", step: "0.01" },
      { key: "dagger_cluster_top_k", kind: "number", min: "1", max: "100" },
      { key: "dagger_cluster_max_size", kind: "number", min: "1", max: "100" },
      { key: "dagger_cluster_auto_accept_singleton", kind: "boolean" },
      { key: "dagger_cluster_auto_accept_same_collection", kind: "boolean" },
    ],
  },
];

function advancedFieldsFromSettings(settings: SettingsData): AdvancedSettings {
  return {
    max_iterations: String(settings.max_iterations),
    max_active_node_runs: String(settings.max_active_node_runs),
    max_examiner_span_nodes: String(settings.max_examiner_span_nodes),
    max_retries: String(settings.max_retries),
    max_format_retries: String(settings.max_format_retries),
    llm_timeout_sec: String(settings.llm_timeout_sec),
    llm_provider_concurrency: String(settings.llm_provider_concurrency),
    llm_context_window: String(settings.llm_context_window),
    llm_max_output_tokens: String(settings.llm_max_output_tokens),
    llm_prompt_safety_tokens: String(settings.llm_prompt_safety_tokens),
    pro_degradation_threshold: String(settings.pro_degradation_threshold),
    pro_degradation_cooldown_sec: String(settings.pro_degradation_cooldown_sec),
    source_ingest_concurrency: String(settings.source_ingest_concurrency),
    source_ocr_concurrency: String(settings.source_ocr_concurrency),
    source_ocr_pdf_max_pages_per_job: String(settings.source_ocr_pdf_max_pages_per_job),
    source_ocr_upload_interval_sec: String(settings.source_ocr_upload_interval_sec),
    source_embedding_concurrency: String(settings.source_embedding_concurrency),
    archivist_mtu_cut_timeout_sec: String(settings.archivist_mtu_cut_timeout_sec),
    archivist_mtu_repair_attempts: String(settings.archivist_mtu_repair_attempts),
    archivist_chunk_concurrency: String(settings.archivist_chunk_concurrency),
    dagger_build_timeout_sec: String(settings.dagger_build_timeout_sec),
    dagger_repair_attempts: String(settings.dagger_repair_attempts),
    dagger_prerequisite_concurrency: String(settings.dagger_prerequisite_concurrency),
    dagger_max_nodes_per_call: String(settings.dagger_max_nodes_per_call),
    dagger_embed_cluster_enabled: settings.dagger_embed_cluster_enabled,
    dagger_cluster_similarity_threshold: String(settings.dagger_cluster_similarity_threshold),
    dagger_cluster_top_k: String(settings.dagger_cluster_top_k),
    dagger_cluster_max_size: String(settings.dagger_cluster_max_size),
    dagger_cluster_auto_accept_singleton: settings.dagger_cluster_auto_accept_singleton,
    dagger_cluster_auto_accept_same_collection: settings.dagger_cluster_auto_accept_same_collection,
  };
}

function fieldsFromSettings(settings: SettingsData): SettingsSave {
  return {
    llm_api_key: settings.llm_api_key_configured ? MASKED_SECRET : "",
    llm_base_url: settings.llm_base_url,
    llm_model: settings.llm_model,
    llm_provider_profile: settings.llm_provider_profile,
    role_models: settings.role_models,
    paddleocr_api_token: settings.paddleocr_api_token_configured ? MASKED_SECRET : "",
    paddleocr_api_url: settings.paddleocr_api_url,
    paddleocr_model: settings.paddleocr_model,
    llama_server_ctx: String(settings.llama_server_ctx),
    source_mtu_chunk_tokens: String(settings.source_mtu_chunk_tokens),
    node_run_mode: settings.node_run_mode,
    ...advancedFieldsFromSettings(settings),
  };
}

function promptDraftsFromSettings(promptSettings: PromptSettings): Partial<Record<PromptKey, string>> {
  return Object.fromEntries(
    promptSettings.prompts.map((item) => [item.key, item.current_text]),
  ) as Partial<Record<PromptKey, string>>;
}

export function Settings() {
  const t = useT();
  const { lang, setLang } = useLang();
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [promptSettings, setPromptSettings] = useState<PromptSettings | null>(null);
  const [fields, setFields] = useState<SettingsSave>(EMPTY_FIELDS);
  const [promptDrafts, setPromptDrafts] = useState<Partial<Record<PromptKey, string>>>({});
  const [busy, setBusy] = useState<boolean>(false);
  const [promptBusy, setPromptBusy] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [promptMessage, setPromptMessage] = useState<string>("");
  const [ok, setOk] = useState<boolean>(false);
  const [promptOk, setPromptOk] = useState<boolean>(false);

  useEffect(() => {
    Promise.all([fetchSettings(), fetchPrompts()])
      .then(([settingsData, promptsData]) => {
        setSettings(settingsData);
        setFields(fieldsFromSettings(settingsData));
        setPromptSettings(promptsData);
        setPromptDrafts(promptDraftsFromSettings(promptsData));
      })
      .catch((err: unknown) => {
        setOk(false);
        setMessage(String(err));
      });
  }, []);

  const setText = (key: TextFieldKey) => (event: ChangeEvent<HTMLInputElement>): void => {
    setFields((prev) => ({ ...prev, [key]: event.target.value }));
  };

  const setAdvancedText =
    (key: keyof AdvancedSettings) => (event: ChangeEvent<HTMLInputElement>): void => {
      setFields((prev) => ({ ...prev, [key]: event.target.value }));
    };

  const setRole = (key: RoleKey) => (event: ChangeEvent<HTMLInputElement>): void => {
    setFields((prev) => ({
      ...prev,
      role_models: { ...prev.role_models, [key]: event.target.value },
    }));
  };

  const setPromptDraft =
    (key: PromptKey) => (event: ChangeEvent<HTMLTextAreaElement>): void => {
      setPromptDrafts((prev) => ({ ...prev, [key]: event.target.value }));
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
      const impact = saved.invalidated_stages.length
        ? ` ${t("settings.invalidatedStages")}: ${saved.invalidated_stages.join(" → ")}`
        : "";
      setMessage(`${t("settings.saved")}${impact}`);
    } catch (err) {
      setOk(false);
      setMessage(String(err));
    } finally {
      setBusy(false);
    }
  };

  const applyPrompts = (next: PromptSettings): void => {
    setPromptSettings(next);
    setPromptDrafts(promptDraftsFromSettings(next));
  };

  const savePromptDraft = async (key: PromptKey): Promise<void> => {
    setPromptBusy(key);
    setPromptMessage("");
    try {
      applyPrompts(await savePrompt(key, promptDrafts[key] ?? ""));
      setPromptOk(true);
      setPromptMessage(t("settings.promptSaved"));
    } catch (err) {
      setPromptOk(false);
      setPromptMessage(String(err));
    } finally {
      setPromptBusy("");
    }
  };

  const restorePrompt = async (key: PromptKey): Promise<void> => {
    setPromptBusy(key);
    setPromptMessage("");
    try {
      applyPrompts(await resetPrompt(key));
      setPromptOk(true);
      setPromptMessage(t("settings.promptReset"));
    } catch (err) {
      setPromptOk(false);
      setPromptMessage(String(err));
    } finally {
      setPromptBusy("");
    }
  };

  const restoreAllPrompts = async (): Promise<void> => {
    setPromptBusy("all");
    setPromptMessage("");
    try {
      applyPrompts(await resetAllPrompts());
      setPromptOk(true);
      setPromptMessage(t("settings.promptResetAll"));
    } catch (err) {
      setPromptOk(false);
      setPromptMessage(String(err));
    } finally {
      setPromptBusy("");
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
        <Message kind="hint">{t("settings.privacyNotice")}</Message>

        <form className="settings-form" onSubmit={(event) => void submit(event)}>
          <fieldset>
            <legend>
              {t("tend.fertilizer")} <small className="legend-note">({t("tend.note.llm")})</small>
            </legend>
            <div className="form-grid">
              <SecretField
                label={t("settings.apiKey")}
                configured={Boolean(settings?.llm_api_key_configured)}
                value={fields.llm_api_key}
                onChange={setText("llm_api_key")}
              />
              <Field
                label={t("settings.baseUrl")}
                value={fields.llm_base_url}
                onChange={setText("llm_base_url")}
              />
              <Field
                label={t("settings.defaultModel")}
                value={fields.llm_model}
                onChange={setText("llm_model")}
              />
              <Field
                label={t("settings.providerProfile")}
                value={fields.llm_provider_profile}
                onChange={setText("llm_provider_profile")}
              />
            </div>

            <div className="role-grid">
              {ROLES.map((role) => (
                <Field
                  key={role}
                  label={t(`role.${role}`)}
                  value={fields.role_models[role]}
                  onChange={setRole(role)}
                />
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>
              {t("tend.gather")} <small className="legend-note">({t("tend.note.ocr")})</small>
            </legend>
            <div className="form-grid">
              <SecretField
                label={t("settings.paddleKey")}
                configured={Boolean(settings?.paddleocr_api_token_configured)}
                value={fields.paddleocr_api_token}
                onChange={setText("paddleocr_api_token")}
              />
              <Field
                label={t("settings.paddleUrl")}
                value={fields.paddleocr_api_url}
                onChange={setText("paddleocr_api_url")}
              />
              <Field
                label={t("settings.paddleModel")}
                value={fields.paddleocr_model}
                onChange={setText("paddleocr_model")}
              />
            </div>
          </fieldset>

          <fieldset>
            <legend>
              {t("tend.climate")} <small className="legend-note">({t("tend.note.runtime")})</small>
            </legend>
            <div className="node-run-mode">
              <div>
                <strong>{t("settings.nodeRunMode")}</strong>
                <p className="hint">
                  {fields.node_run_mode === "fast"
                    ? t("settings.nodeRunMode.fastHint")
                    : t("settings.nodeRunMode.standardHint")}
                </p>
              </div>
              <div className="mode-toggle" role="group" aria-label={t("settings.nodeRunMode")}>
                <button
                  type="button"
                  className={fields.node_run_mode === "standard" ? "active" : ""}
                  aria-pressed={fields.node_run_mode === "standard"}
                  onClick={() => setFields((prev) => ({ ...prev, node_run_mode: "standard" }))}
                >
                  {t("settings.nodeRunMode.standard")}
                </button>
                <button
                  type="button"
                  className={fields.node_run_mode === "fast" ? "active" : ""}
                  aria-pressed={fields.node_run_mode === "fast"}
                  onClick={() => setFields((prev) => ({ ...prev, node_run_mode: "fast" }))}
                >
                  {t("settings.nodeRunMode.fast")}
                </button>
              </div>
            </div>
            <div className="form-grid">
              <Field
                label={t("settings.llamaCtx")}
                type="number"
                min="1024"
                max="32768"
                step="1"
                value={fields.llama_server_ctx}
                onChange={setText("llama_server_ctx")}
              />
              <Field
                label={t("settings.mtuChunk")}
                type="number"
                min="500"
                max="32768"
                step="1"
                value={fields.source_mtu_chunk_tokens}
                onChange={setText("source_mtu_chunk_tokens")}
              />
            </div>
          </fieldset>

          <details className="settings-details">
            <summary>{t("settings.advanced.title")}</summary>
            <p className="hint">{t("settings.restartHint")}</p>
            {ADVANCED_GROUPS.map((group) => (
              <div className="advanced-group" key={group.titleKey}>
                <h3>{t(group.titleKey)}</h3>
                <div className="form-grid">
                  {group.fields.map((field) =>
                    field.kind === "boolean" ? (
                      <Toggle
                        key={field.key}
                        className="toggle-label"
                        checked={Boolean(fields[field.key])}
                        onChange={(checked) =>
                          setFields((prev) => ({ ...prev, [field.key]: checked }))
                        }
                        label={<span>{t(`settings.advanced.${field.key}`)}</span>}
                      />
                    ) : (
                      <Field
                        key={field.key}
                        label={t(`settings.advanced.${field.key}`)}
                        type="number"
                        min={field.min}
                        max={field.max}
                        step={field.step ?? "1"}
                        value={String(fields[field.key])}
                        onChange={setAdvancedText(field.key)}
                      />
                    ),
                  )}
                </div>
              </div>
            ))}
          </details>

          {settings && (
            <details className="settings-details settings-storage">
              <summary>{t("settings.storageDetails")}</summary>
              <code className="hint">{settings.config_path}</code>
            </details>
          )}

          <div className="form-actions">
            <Button type="submit" disabled={busy}>
              {busy ? t("common.saving") : t("settings.save")}
            </Button>
            {message && (
              <Message kind={ok ? "ok" : "error"} inline>
                {message}
              </Message>
            )}
          </div>
        </form>
      </section>

      <section className="card settings-card">
        <details className="settings-details prompt-details">
          <summary>{t("settings.prompts.title")}</summary>
          <p className="hint">{t("settings.prompts.warning")}</p>
          {promptSettings && <span className="hint">{promptSettings.path}</span>}
          <div className="prompt-actions">
            <Button disabled={promptBusy === "all"} onClick={() => void restoreAllPrompts()}>
              {t("settings.prompts.resetAll")}
            </Button>
            {promptMessage && (
              <Message kind={promptOk ? "ok" : "error"} inline>
                {promptMessage}
              </Message>
            )}
          </div>
          <div className="prompt-list">
            {promptSettings?.prompts
              .filter((item) => PROMPT_GROUPS.includes(item.key))
              .map((item) => (
                <article className="prompt-editor" key={item.key}>
                  <div className="prompt-editor-head">
                    <div>
                      <h3>{t(`prompt.${item.key}`)}</h3>
                      <span className="hint">
                        {item.is_custom ? t("settings.prompts.custom") : t("settings.prompts.default")}
                        {item.base_changed ? ` · ${t("settings.prompts.baseChanged")}` : ""}
                      </span>
                    </div>
                    <div className="prompt-editor-actions">
                      <Button
                        disabled={promptBusy === item.key}
                        onClick={() => void savePromptDraft(item.key)}
                      >
                        {promptBusy === item.key ? t("common.saving") : t("common.save")}
                      </Button>
                      <Button
                        disabled={promptBusy === item.key}
                        onClick={() => void restorePrompt(item.key)}
                      >
                        {t("settings.prompts.reset")}
                      </Button>
                    </div>
                  </div>
                  <textarea
                    value={promptDrafts[item.key] ?? item.current_text}
                    onChange={setPromptDraft(item.key)}
                    spellCheck={false}
                  />
                </article>
              ))}
          </div>
        </details>
      </section>
    </div>
  );
}
