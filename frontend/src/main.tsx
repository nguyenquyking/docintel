import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router";
import Layout from "./components/Layout";
import "./index.css";
import Classify from "./pages/Classify";
import Cost from "./pages/Cost";
import Dashboard from "./pages/Dashboard";
import DocumentDetail from "./pages/DocumentDetail";
import Documents from "./pages/Documents";
import EvalCompare from "./pages/EvalCompare";
import EvalRun from "./pages/EvalRun";
import Evaluation from "./pages/Evaluation";
import Jobs from "./pages/Jobs";
import Monitoring from "./pages/Monitoring";
import Review from "./pages/Review";
import SearchPage from "./pages/Search";

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/documents/:id" element={<DocumentDetail />} />
            <Route path="/classify" element={<Classify />} />
            <Route path="/review" element={<Review />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/eval" element={<Evaluation />} />
            <Route path="/eval/runs/:id" element={<EvalRun />} />
            <Route path="/eval/compare" element={<EvalCompare />} />
            <Route path="/monitoring" element={<Monitoring />} />
            <Route path="/cost" element={<Cost />} />
            <Route path="/jobs" element={<Jobs />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
