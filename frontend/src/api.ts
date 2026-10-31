export class ApiError extends Error {}

async function readError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown; error?: { message?: unknown } };
    if (typeof body.detail === "string") return body.detail;
    if (typeof body.error?.message === "string") return body.error.message;
  } catch {
    /* not JSON */
  }
  return text || res.statusText;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) throw new ApiError(await readError(res));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function postForm<T>(path: string, file: File, fields: Record<string, string>): Promise<T> {
  const body = new FormData();
  body.append("file", file);
  for (const [key, value] of Object.entries(fields)) body.append(key, value);
  return api<T>(path, { method: "POST", body });
}

export function resumeRun(
  runId: string,
  orgId: string,
  userId: string,
  input: { message: string; decision: "confirm" | "skip" },
): Promise<AgentRun> {
  return api<AgentRun>(`/api/runs/${encodeURIComponent(runId)}/resume`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Org-Id": orgId,
      "X-User-Id": userId,
    },
    body: JSON.stringify({ input }),
  });
}

export function cancelRun(runId: string, orgId: string, userId: string): Promise<void> {
  return api<void>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    headers: { "X-Org-Id": orgId, "X-User-Id": userId },
  });
}

export type AgentRun = {
  id: string;
  status: string;
  output_json?: { text?: string } | null;
  error_json?: { message?: string; kind?: string } | null;
};

export function getRun(runId: string, orgId: string, userId: string): Promise<AgentRun> {
  return api<AgentRun>(`/api/runs/${encodeURIComponent(runId)}`, {
    headers: { "X-Org-Id": orgId, "X-User-Id": userId },
  });
}

export function getRunTrace(runId: string, orgId: string, userId: string): Promise<RunTrace> {
  return api<RunTrace>(`/api/runs/${encodeURIComponent(runId)}/trace`, {
    headers: { "X-Org-Id": orgId, "X-User-Id": userId },
  });
}

export type RunTrace = {
  run: AgentRun;
  steps: unknown;
  events?: { event_type: string; payload_json?: { tool_name?: string } }[];
};

const RUN_SETTLED = new Set(["succeeded", "failed", "cancelled", "waiting_for_user"]);

export async function waitForRun(
  runId: string,
  orgId: string,
  userId: string,
  signal?: AbortSignal,
  onTrace?: (trace: RunTrace) => void,
): Promise<AgentRun> {
  for (;;) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const trace = await getRunTrace(runId, orgId, userId);
    onTrace?.(trace);
    if (RUN_SETTLED.has(trace.run.status)) return trace.run;
    await new Promise((resolve) => {
      const timer = window.setTimeout(resolve, 800);
      const abort = () => {
        window.clearTimeout(timer);
        resolve(undefined);
      };
      signal?.addEventListener("abort", abort, { once: true });
    });
  }
}

export async function streamMessage(
  sid: string,
  body: unknown,
  onEvent: (event: Record<string, unknown>) => void | Promise<void>,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/sessions/${sid}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(await readError(res));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const chunks = buf.split("\n\n");
    buf = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((item) => item.startsWith("data: "));
      if (line) await onEvent(JSON.parse(line.slice(6)) as Record<string, unknown>);
    }
  }
}
