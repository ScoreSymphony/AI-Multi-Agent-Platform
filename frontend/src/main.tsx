import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "./app/router";
import { Shell } from "./app/Shell";
import "./styles.css";
import "./configuration.css";
import "./content.css";
import "./overview.css";
import "./agents.css";
import "./compute.css";
import "./evaluations.css";
import "./approvals.css";
import "./governance.css";
import "./notifications.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <RouterProvider>
      <Shell />
    </RouterProvider>
  </StrictMode>,
);
