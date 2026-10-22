import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";

import { Layout } from "./components/Layout";
import { ApiError } from "./lib/api";
import { JobPage } from "./pages/JobPage";
import { NewJobPage } from "./pages/NewJobPage";
import { NotFound } from "./pages/NotFound";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Client errors (401/404/422) are answers, not outages: do not retry them.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<NewJobPage />} />
            <Route path="/jobs/:jobId" element={<JobPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
