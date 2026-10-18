import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import "./index.css";
import { Layout } from "./components/Layout";
import { Overview } from "./pages/Overview";
import { RunTrace } from "./pages/RunTrace";
import { PrAnalysis } from "./pages/PrAnalysis";
import { Decisions } from "./pages/Decisions";
import { Install } from "./pages/Install";
import { Backfill, BackfillLatest } from "./pages/Backfill";
import { NotFound } from "./pages/NotFound";

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Overview /> },
      { path: "/runs/:id", element: <RunTrace /> },
      { path: "/pr/:owner/:repo/:number", element: <PrAnalysis /> },
      { path: "/decisions", element: <Decisions /> },
      { path: "/decisions/:docId", element: <Decisions /> },
      { path: "/backfill", element: <BackfillLatest /> },
      { path: "/backfill/:owner/:repo", element: <Backfill /> },
      { path: "/install", element: <Install /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
