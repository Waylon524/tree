import { useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { saveSetup } from "../api";

type Fields = {
  llm_api_key: string;
  llm_base_url: string;
  llm_model: string;
  paddleocr_api_token: string;
};

const EMPTY: Fields = {
  llm_api_key: "",
  llm_base_url: "",
  llm_model: "",
  paddleocr_api_token: "",
};

export function SetupForm() {
  const [fields, setFields] = useState<Fields>(EMPTY);
  const [result, setResult] = useState<string>("");

  const set = (key: keyof Fields) => (event: ChangeEvent<HTMLInputElement>) =>
    setFields((prev) => ({ ...prev, [key]: event.target.value }));

  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    try {
      setResult(await saveSetup(fields));
    } catch (err) {
      setResult(String(err));
    }
  };

  return (
    <div className="card">
      <h2>Setup</h2>
      <form className="setup" onSubmit={(event) => void submit(event)}>
        <label>
          LLM API key
          <input type="password" value={fields.llm_api_key} onChange={set("llm_api_key")} />
        </label>
        <label>
          LLM base URL
          <input
            value={fields.llm_base_url}
            onChange={set("llm_base_url")}
            placeholder="https://api.deepseek.com"
          />
        </label>
        <label>
          LLM model
          <input
            value={fields.llm_model}
            onChange={set("llm_model")}
            placeholder="deepseek-v4-flash"
          />
        </label>
        <label>
          PaddleOCR token
          <input
            type="password"
            value={fields.paddleocr_api_token}
            onChange={set("paddleocr_api_token")}
          />
        </label>
        <button type="submit">Save</button>
        {result && <div className="ok" dangerouslySetInnerHTML={{ __html: result }} />}
      </form>
    </div>
  );
}
