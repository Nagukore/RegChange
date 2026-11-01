import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { EventStreamProvider } from "./lib/useEventStream";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The SSE stream pushes invalidations, so aggressive polling is wasteful.
      refetchOnWindowFocus: false,
      staleTime: 15_000,
      retry: (failureCount, error) => {
        // A missing credential (503) will not fix itself on retry.
        const status = (error as { status?: number })?.status;
        if (status === 503 || status === 404) return false;
        return failureCount < 2;
      },
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <EventStreamProvider>
          <App />
        </EventStreamProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
