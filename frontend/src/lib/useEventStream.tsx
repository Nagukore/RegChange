/**
 * Live pipeline events over Server-Sent Events.
 *
 * A single EventSource is shared by the whole app through context: the browser
 * caps concurrent connections per origin, and one stream per screen would both
 * waste connections and duplicate events.
 *
 * Events that complete a pipeline stage invalidate the matching React Query
 * keys, so tables refresh themselves without polling.
 */
import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { SSE_URL } from "./api";
import type { PipelineEvent } from "./types";

const MAX_EVENTS = 300;

interface EventStreamValue {
  events: PipelineEvent[];
  connected: boolean;
  /** Most recent non-heartbeat event, convenient for toast-style surfacing. */
  latest: PipelineEvent | null;
  clear: () => void;
}

const EventStreamContext = createContext<EventStreamValue>({
  events: [],
  connected: false,
  latest: null,
  clear: () => {},
});

/** Which query keys to refetch when a given event arrives. */
const INVALIDATION_MAP: Record<string, string[][]> = {
  documents_found: [["regulations"], ["discovery"], ["dashboard"]],
  extraction_completed: [["regulations"], ["dashboard"]],
  analysis_completed: [["changes"], ["regulations"], ["dashboard"]],
  impact_mapping_completed: [["impacts"], ["changes"], ["dashboard"]],
  test_generation_completed: [["testCases"], ["impacts"], ["dashboard"]],
  execution_started: [["executionRuns"], ["dashboard"]],
  execution_progress: [["executionRuns"]],
  execution_completed: [["executionRuns"], ["testCases"], ["dashboard"]],
  approval_recorded: [["testCases"], ["impacts"], ["dashboard"]],
  agent_activity: [["activity"]],
  pipeline_failed: [["dashboard"], ["activity"]],
};

export function EventStreamProvider({ children }: { children: ReactNode }) {
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const queryClient = useQueryClient();
  const sourceRef = useRef<EventSource | null>(null);
  // Coalesce invalidations: an execution run emits one event per test, and
  // refetching on each would hammer the API.
  const pendingRef = useRef<Set<string>>(new Set());
  const flushRef = useRef<number | null>(null);

  const scheduleInvalidation = useCallback(
    (type: string) => {
      const keys = INVALIDATION_MAP[type];
      if (!keys) return;
      keys.forEach((key) => pendingRef.current.add(JSON.stringify(key)));
      if (flushRef.current !== null) return;
      flushRef.current = window.setTimeout(() => {
        const queued = Array.from(pendingRef.current);
        pendingRef.current.clear();
        flushRef.current = null;
        queued.forEach((serialised) => {
          queryClient.invalidateQueries({ queryKey: JSON.parse(serialised) as string[] });
        });
      }, 350);
    },
    [queryClient],
  );

  useEffect(() => {
    const source = new EventSource(SSE_URL);
    sourceRef.current = source;
    // React StrictMode mounts effects twice in development. Closing the first
    // EventSource fires an error event, which would otherwise flip `connected`
    // back to false after the second one has already opened.
    let cancelled = false;

    source.onopen = () => {
      if (!cancelled) setConnected(true);
    };
    source.onerror = () => {
      // EventSource reconnects on its own; reflect the gap in the UI only.
      if (!cancelled) setConnected(false);
    };

    const handle = (raw: MessageEvent) => {
      if (cancelled) return;
      let parsed: PipelineEvent;
      try {
        parsed = JSON.parse(raw.data) as PipelineEvent;
      } catch {
        return;
      }
      if (parsed.type === "heartbeat") {
        setConnected(true);
        return;
      }
      setEvents((previous) => [parsed, ...previous].slice(0, MAX_EVENTS));
      scheduleInvalidation(parsed.type);
    };

    // The server names each event, so a generic onmessage never fires; every
    // known type must be registered explicitly.
    const types = [
      "discovery_started",
      "documents_found",
      "extraction_started",
      "extraction_completed",
      "analysis_started",
      "analysis_completed",
      "impact_mapping_started",
      "impact_mapping_completed",
      "test_generation_started",
      "test_generation_completed",
      "execution_started",
      "execution_progress",
      "execution_completed",
      "agent_activity",
      "approval_recorded",
      "pipeline_failed",
      "heartbeat",
    ];
    types.forEach((type) => source.addEventListener(type, handle as EventListener));

    return () => {
      cancelled = true;
      types.forEach((type) => source.removeEventListener(type, handle as EventListener));
      if (flushRef.current !== null) window.clearTimeout(flushRef.current);
      source.close();
      sourceRef.current = null;
    };
  }, [scheduleInvalidation]);

  const value = useMemo<EventStreamValue>(
    () => ({
      events,
      connected,
      latest: events.find((event) => event.type !== "agent_activity") ?? events[0] ?? null,
      clear: () => setEvents([]),
    }),
    [events, connected],
  );

  return <EventStreamContext.Provider value={value}>{children}</EventStreamContext.Provider>;
}

export function useEventStream() {
  return useContext(EventStreamContext);
}

/** Events filtered to one pipeline correlation id. */
export function useCorrelatedEvents(correlationId: string | null | undefined) {
  const { events } = useEventStream();
  return useMemo(
    () =>
      correlationId ? events.filter((event) => event.correlation_id === correlationId) : [],
    [events, correlationId],
  );
}
