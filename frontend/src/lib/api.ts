/**
 * The single typed API client. No fetch calls anywhere else in the app.
 *
 * The correlation ID is generated here and sent on every request, so a click in
 * the browser can be traced through the API, the backend, the AI calls and
 * LangSmith. See PROJECT.md section 10.
 */

import type {
  Acknowledged,
  AdminMetrics,
  AgentResponse,
  AuditEvent,
  ChatResponse,
  Department,
  DocumentList,
  DocumentOut,
  IngestionJob,
  InviteResult,
  Me,
  SessionResponse,
  TenantAssignableRole,
  TenantList,
  TenantOut,
  UserList,
  UserOut,
  WorkflowId,
  WorkflowInfo,
} from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "ekba.token";
const REFRESH_KEY = "ekba.refresh";
const EXPIRY_KEY = "ekba.expires_at";

/**
 * Renew this far before the token actually expires, so a long request started
 * just under the wire does not land on an expired token.
 */
const REFRESH_MARGIN_MS = 120_000;

/** Shown instead of the generic error when the backend reports `company_suspended`. */
export const COMPANY_SUSPENDED_MESSAGE =
  "Your company account has been suspended by the service provider. Please contact your service provider for assistance.";

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
// session storage
//
// The session lives in sessionStorage, not a cookie: this app is a pure SPA
// against a separate API origin, and sessionStorage keeps it out of every
// cross-site request automatically. It is cleared when the tab closes.
//
// The token here is the Cognito ID token, which is the only Cognito token that
// carries the tenant and role claims the API authorizes on.
// ---------------------------------------------------------------------------
function read(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* private mode - the session simply will not persist */
  }
}

export function getToken(): string | null {
  return read(TOKEN_KEY);
}

export function setToken(token: string): void {
  write(TOKEN_KEY, token);
}

export interface StoredSession {
  token: string;
  refresh_token?: string | null;
  expires_in?: number | null;
}

export function saveSession(session: StoredSession): void {
  write(TOKEN_KEY, session.token);
  if (session.refresh_token) write(REFRESH_KEY, session.refresh_token);
  if (session.expires_in) {
    write(EXPIRY_KEY, String(Date.now() + session.expires_in * 1000));
  }
}

export function clearToken(): void {
  try {
    [TOKEN_KEY, REFRESH_KEY, EXPIRY_KEY].forEach((k) =>
      window.sessionStorage.removeItem(k),
    );
  } catch {
    /* ignore */
  }
}

/** Milliseconds until the stored token expires, or null when unknown. */
export function timeUntilExpiry(): number | null {
  const raw = read(EXPIRY_KEY);
  if (!raw) return null;
  const remaining = Number(raw) - Date.now();
  return Number.isFinite(remaining) ? remaining : null;
}

// ---------------------------------------------------------------------------
// silent renewal
//
// Cognito ID tokens last an hour. Without this, a user reading a long document
// gets signed out mid-sentence; with it, the session renews underneath them and
// they never see it. A single in-flight promise is shared so a burst of
// requests triggers one refresh rather than a stampede.
// ---------------------------------------------------------------------------
let inFlightRefresh: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  const refreshToken = read(REFRESH_KEY);
  if (!refreshToken) return false;

  inFlightRefresh ??= (async () => {
    try {
      const renewed = await rawRequest<SessionResponse>("/api/v1/auth/refresh", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      // A refresh that comes back without a token is a failed refresh, not a
      // challenge - the challenge flow only happens on first sign-in.
      if (!renewed.token) return false;
      saveSession({ ...renewed, token: renewed.token });
      return true;
    } catch {
      return false;
    } finally {
      inFlightRefresh = null;
    }
  })();

  return inFlightRefresh;
}

// ---------------------------------------------------------------------------
// core request
// ---------------------------------------------------------------------------
async function rawRequest<T>(
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
    const message =
      code === "company_suspended"
        ? COMPANY_SUSPENDED_MESSAGE
        : (payload?.error?.message ?? `Request failed (${response.status})`);
    throw new ApiClientError(
      response.status,
      code,
      message,
      payload?.correlation_id ?? correlationId,
    );
  }

  return payload as T;
}

async function request<T>(
  path: string,
  init: RequestInit & { correlationId?: string } = {},
): Promise<T> {
  // Renew before the call when the token is nearly out, so the common case
  // never costs a failed request.
  const remaining = timeUntilExpiry();
  if (getToken() && remaining !== null && remaining < REFRESH_MARGIN_MS) {
    await refreshSession();
  }

  try {
    return await rawRequest<T>(path, init);
  } catch (err) {
    const expired = err instanceof ApiClientError && err.status === 401;
    if (!expired) throw err;

    // A 401 with a usable refresh token is an expiry, not a sign-out. One
    // retry: if the renewal also fails, the session really is over.
    if (await refreshSession()) {
      const retried = init.body instanceof FormData ? null : init;
      if (retried) return await rawRequest<T>(path, retried);
    }
    clearToken();
    throw err;
  }
}

// ---------------------------------------------------------------------------
// endpoints
// ---------------------------------------------------------------------------
export const api = {
  health: () => request<{ status: string; environment: string }>("/api/v1/health"),

  me: () => request<Me>("/api/v1/me"),

  /**
   * Sign-in and recovery. These are the only unauthenticated calls in the app,
   * so they use rawRequest: there is no session yet to renew, and a 401 here is
   * a wrong password rather than an expiry.
   */
  auth: {
    login: (email: string, password: string) =>
      rawRequest<SessionResponse>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }),

    /** Completes a first sign-in on an invited account. */
    newPassword: (body: {
      email: string;
      challenge_session: string;
      new_password: string;
    }) =>
      rawRequest<SessionResponse>("/api/v1/auth/new-password", {
        method: "POST",
        body: JSON.stringify(body),
      }),

    forgotPassword: (email: string) =>
      rawRequest<Acknowledged>("/api/v1/auth/forgot-password", {
        method: "POST",
        body: JSON.stringify({ email }),
      }),

    confirmPasswordReset: (body: {
      email: string;
      code: string;
      new_password: string;
    }) =>
      rawRequest<Acknowledged>("/api/v1/auth/confirm-password-reset", {
        method: "POST",
        body: JSON.stringify(body),
      }),

    /** Revokes every token for this identity, not just the browser's copy. */
    logout: () => request<Acknowledged>("/api/v1/auth/logout", { method: "POST" }),
  },

  /** Service-provider control plane. Every call is platform_admin only. */
  platform: {
    tenants: () => request<TenantList>("/api/v1/platform/tenants"),

    createTenant: (body: {
      name: string;
      tenant_id?: string;
      contact_email?: string | null;
    }) =>
      request<TenantOut>("/api/v1/platform/tenants", {
        method: "POST",
        body: JSON.stringify(body),
      }),

    updateTenant: (id: string, body: { name?: string; is_active?: boolean }) =>
      request<TenantOut>(`/api/v1/platform/tenants/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),

    inviteTenantAdmin: (
      id: string,
      body: { email: string; display_name?: string | null; department?: Department | null },
    ) =>
      request<InviteResult>(`/api/v1/platform/tenants/${id}/admins`, {
        method: "POST",
        body: JSON.stringify(body),
      }),

    audit: (limit = 200) =>
      request<AuditEvent[]>(`/api/v1/platform/audit?limit=${limit}`),
  },

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

    /** The caller's own company. There is no parameter for anyone else's. */
    tenant: () => request<TenantOut>("/api/v1/admin/tenant"),

    users: {
      list: () => request<UserList>("/api/v1/admin/users"),

      /**
       * Invite a colleague. No tenant is sent and none is accepted: the
       * company comes from the caller's own verified token.
       */
      invite: (body: {
        email: string;
        display_name?: string | null;
        role: TenantAssignableRole;
        department?: Department | null;
      }) =>
        request<InviteResult>("/api/v1/admin/users", {
          method: "POST",
          body: JSON.stringify(body),
        }),

      update: (
        id: string,
        body: {
          role?: TenantAssignableRole;
          department?: Department | null;
          is_active?: boolean;
        },
      ) =>
        request<UserOut>(`/api/v1/admin/users/${id}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        }),

      resetPassword: (id: string) =>
        request<Acknowledged>(`/api/v1/admin/users/${id}/reset-password`, {
          method: "POST",
        }),
    },
  },
};
