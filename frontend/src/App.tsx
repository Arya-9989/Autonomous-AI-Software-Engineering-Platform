import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes, StaticRouter, useParams } from "react-router";

import { ApiError } from "./lib/api";
import { useTheme } from "./lib/theme";
import { Toaster } from "./lib/toast";

// Both halves load on demand: boot.js preloads the one the current URL needs, in parallel with
// the entry, and the prerendered HTML is on screen meanwhile (hydration waits for the chunk).
const LandingPage = lazy(() => import("./landing/LandingPage"));
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

/** `location` renders a fixed URL (build-time prerendering of the product page). */
export function App({ location }: { location?: string }) {
  const [theme] = useTheme();
  const routes = (
    <Suspense fallback={<AppFallback />}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/jobs/:jobId" element={<LegacyJobRedirect />} />
        <Route path="/app/*" element={<AppShell />} />
        <Route path="*" element={<AppShell notFound />} />
      </Routes>
    </Suspense>
  );
  return (
    <QueryClientProvider client={queryClient}>
      {location === undefined ? (
        <BrowserRouter>{routes}</BrowserRouter>
      ) : (
        <StaticRouter location={location}>{routes}</StaticRouter>
      )}
      <Suspense fallback={null}>
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
      </Suspense>
    </QueryClientProvider>
  );
}
