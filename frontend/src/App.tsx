import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Tooltip } from "radix-ui";
import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router";
import { Toaster } from "sonner";

import { ApiError } from "./lib/api";
import { useTheme } from "./lib/theme";
import LandingPage from "./landing/LandingPage";

// The landing page ships in the entry chunk (it is the LCP); the app loads on demand.
const AppShell = lazy(() => import("./app/AppShell"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Client errors (401/404/422) are answers, not outages: do not retry them.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

/** Links shared before the app moved to /app keep working. */
function LegacyJobRedirect() {
  const { jobId = "" } = useParams();
  return <Navigate to={`/app/jobs/${encodeURIComponent(jobId)}`} replace />;
}

function AppFallback() {
  return <div className="min-h-dvh bg-canvas" aria-busy="true" />;
}

export function App() {
  const [theme] = useTheme();
  return (
    <QueryClientProvider client={queryClient}>
      <Tooltip.Provider delayDuration={400} skipDelayDuration={200}>
        <BrowserRouter>
          <Suspense fallback={<AppFallback />}>
            <Routes>
              <Route path="/" element={<LandingPage />} />
              <Route path="/jobs/:jobId" element={<LegacyJobRedirect />} />
              <Route path="/app/*" element={<AppShell />} />
              <Route path="*" element={<AppShell notFound />} />
            </Routes>
          </Suspense>
        </BrowserRouter>
        <Toaster
          theme={theme}
          position="bottom-center"
          closeButton={false}
          toastOptions={{
            classNames: {
              toast:
                "!rounded-[14px] !border-hairline !bg-raised !text-ink !shadow-[0_8px_40px_rgb(0_0_0/0.18)] !font-sans",
              description: "!text-ink-2",
            },
          }}
        />
      </Tooltip.Provider>
    </QueryClientProvider>
  );
}
