import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { FirstRunGate } from "./app/FirstRunGate";
import { RouterProvider } from "./app/router";
import { Shell } from "./app/Shell";
import "./styles.css";
import "./configuration.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");

createRoot(root).render(
  <StrictMode>
    <RouterProvider>
      <FirstRunGate>
        <Shell />
      </FirstRunGate>
    </RouterProvider>
  </StrictMode>,
);
