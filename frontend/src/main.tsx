import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { initSentry } from "./sentry";
import "@/styles/tokens.css";
import "@/styles/global.css";
import "@/styles/workspace-shell.css";
import "@/styles/chapter-planning.css";
import "@/styles/project-hub.css";
import "@/styles/lore-repository.css";
import "@/styles/foreshadow-planning.css";

// Initialize Sentry error monitoring (no-op if DSN not set)
initSentry();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
