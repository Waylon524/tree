import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initApi } from "./api";
import { LanguageProvider } from "./i18n";
import "./styles.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("root element missing");

// Resolve the API base + token (from the Tauri shell when present) before the
// app renders, so inside the desktop shell the connect screen is skipped.
void initApi().then((initialBootstrap) => {
  createRoot(rootEl).render(
    <StrictMode>
      <LanguageProvider>
        <App initialBootstrap={initialBootstrap} />
      </LanguageProvider>
    </StrictMode>,
  );
});
