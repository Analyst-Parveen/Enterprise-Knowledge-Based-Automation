/**
 * The single typed API client. No fetch calls anywhere else in the app.
 *
 * The correlation ID is generated here and sent on every request, so a click in
 * the browser can be traced through the API, the backend, the AI calls and
 * LangSmith. See PROJECT.md section 10.
 */

import type {
  AdminMetrics,
  AgentResponse,
  AuditEvent,
  ChatResponse,
  Department,
  DocumentList,
  DocumentOut,
  IngestionJob,
  Me,
  WorkflowId,
  WorkflowInfo,
} from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "ekba.token";

export class ApiClientError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly correlationId: string,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

export function newCorrelationId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return Math.random().toString(36).slice(2).padEnd(32, "0");
}

// ---------------------------------------------------------------------------
// token storage
//
// The token lives in sessionStorage, not a cookie: this app is a pure SPA
// against a separate API origin, and sessionStorage keeps it out of every
// cross-site request automatically. It is cleared when the tab closes.
// ---------------------------------------------------------------------------
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* private mode - the session simply will not persist */
  }
}

export function clearToken(): void {
  try {
    window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

// ---------------------------------------------------------------------------
// core request
// ---------------------------------------------------------------------------
async function request<T>(
  path: string,
  init: RequestInit & { correlationId?: string } = {},
): Promise<T> {
  const correlationId = init.correlationId ?? newCorrelationId();
  const token = getToken();

  const headers = new Headers(init.headers);
  headers.set("X-Correlation-ID", correlationId);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiClientError(
      0,
      "network_error",
      "Could not reach the API. Is the backend running?",
      correlationId,
    );
  }

  if (response.status === 204) return undefined as T;

  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const code = payload?.error?.code ?? "unknown_error";
    const message = payload?.error?.message ?? `Request failed (${response.status})`;
    if (response.status === 401) clearToken();
    throw new ApiClientError(
      response.status,
      code,
      message,
      payload?.correlation_id ?? correlationId,
    );
  }

  return payload as T;
}

// ---------------------------------------------------------------------------
// endpoints
// ---------------------------------------------------------------------------
export const api = {
  health: () => request<{ status: string; environment: string }>("/api/v1/health"),

  me: () => request<Me>("/api/v1/me"),

  documents: {
    list: (params: { limit?: number; offset?: number; department?: Department } = {}) => {
      const query = new URLSearchParams();
      if (params.limit) query.set("limit", String(params.limit));
      if (params.offset) query.set("offset", String(params.offset));
      if (params.department) query.set("department", params.department);
      const suffix = query.toString() ? `?${query}` : "";
      return request<DocumentList>(`/api/v1/documents${suffix}`);
    },

    get: (id: string) => request<DocumentOut>(`/api/v1/documents/${id}`),

    status: (id: string) => request<IngestionJob>(`/api/v1/documents/${id}/status`),

    upload: (file: File, department?: Department) => {
      const form = new FormData();
      form.append("file", file);
      if (department) form.append("department", department);
      return request<{ document: DocumentOut; job_id: string; message: string }>(
        "/api/v1/documents",
        { method: "POST", body: form },
      );
    },

    remove: (id: string) =>
      request<{ document_id: string; deleted: boolean }>(`/api/v1/documents/${id}`, {
        method: "DELETE",
      }),

    downloadUrl: (id: string) =>
      request<{ url: string }>(`/api/v1/documents/${id}/download`),
  },

  chat: (body: {
    question: string;
    conversation_id?: string | null;
    department?: Department | null;
    document_ids?: string[] | null;
  }) =>
    request<ChatResponse>("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  feedback: (body: {
    message_id?: string | null;
    rating: number;
    reason?: string | null;
    comment?: string | null;
  }) =>
    request<{ id: string; status: string }>("/api/v1/feedback", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  agents: {
    workflows: () => request<WorkflowInfo[]>("/api/v1/agents/workflows"),
    run: (body: {
      workflow: WorkflowId;
      question: string;
      department?: Department | null;
      document_ids?: string[] | null;
    }) =>
      request<AgentResponse>("/api/v1/agents/run", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },

  admin: {
    metrics: () => request<AdminMetrics>("/api/v1/admin/metrics"),
    audit: (limit = 100) => request<AuditEvent[]>(`/api/v1/admin/audit?limit=${limit}`),
    security: (limit = 100) => request<AuditEvent[]>(`/api/v1/admin/security?limit=${limit}`),
  },
};
