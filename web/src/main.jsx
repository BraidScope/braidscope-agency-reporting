// SPDX-License-Identifier: AGPL-3.0-only
import React from "react";
import { createRoot } from "react-dom/client";
import "./styles/shadcn.css";
import App from "./App.jsx";
import AppErrorBoundary from "./components/AppErrorBoundary.jsx";
import InsecureConnectionBanner from "./components/InsecureConnectionBanner.jsx";
import { TooltipProvider } from "@/components/ui/tooltip";
import { BrowserRouter } from "react-router-dom";
import { UnsavedChangesProvider } from "./lib/UnsavedChangesContext.jsx";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <BrowserRouter>
        <TooltipProvider>
          <UnsavedChangesProvider>
            <InsecureConnectionBanner />
            <App />
          </UnsavedChangesProvider>
        </TooltipProvider>
      </BrowserRouter>
    </AppErrorBoundary>
  </React.StrictMode>
);
