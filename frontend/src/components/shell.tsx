"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { api, ApiClientError, clearToken, getToken, setToken } from "@/lib/api";
import type { Me } from "@/types/api";

import { Button, Card, Input, Label, cn } from "./ui";

const USER_NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/chat", label: "Knowledge Chat" },
  { href: "/agents", label: "Workflows" },
  { href: "/documents", label: "Documents" },
  { href: "/departments", label: "Departments" },
  { href: "/usage", label: "Usage" },
  { href: "/feedback", label: "Feedback" },
];

const ADMIN_NAV = [
  { href: "/admin/users", label: "Users" },
  { href: "/admin/tenants", label: "Tenants" },
  { href: "/admin/documents", label: "Documents" },
  { href: "/admin/metrics", label: "AI Metrics" },
  { href: "/admin/security", label: "Security" },
  { href: "/admin/audit", label: "Audit Logs" },
  { href: "/admin/deployments", label: "Deployments" },
];

// ---------------------------------------------------------------------------
// session
// ---------------------------------------------------------------------------
interface SessionValue {
  me: Me | null;
  loading: boolean;
  error: string | null;
  signIn: (token: string) => Promise<void>;
  signOut: () => void;
}

const SessionContext = React.createContext<SessionValue | null>(null);

export function useSession(): SessionValue {
  const value = React.useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <AppShell>");
  return value;
}

function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = React.useState<Me | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    if (!getToken()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      setMe(await api.me());
      setError(null);
    } catch (err) {
      setMe(null);
      if (err instanceof ApiClientError && err.status !== 401) setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const signIn = React.useCallback(
    async (token: string) => {
      setToken(token);
      setLoading(true);
      await load();
    },
    [load],
  );

  const signOut = React.useCallback(() => {
    clearToken();
    setMe(null);
  }, []);

  return (
    <SessionContext.Provider value={{ me, loading, error, signIn, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// sign-in
// ---------------------------------------------------------------------------
function SignIn() {
  const { signIn } = useSession();
  const [token, setTokenValue] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(token.trim());
      if (!getToken()) setError("That token was rejected.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <Card className="w-full max-w-md p-6">
        <h1 className="text-lg font-semibold">Enterprise Knowledge AI</h1>
        <p className="mt-1 text-sm text-muted">
          Sign in with your Cognito access token to continue.
        </p>

        <form onSubmit={submit} className="mt-5 space-y-3">
          <div>
            <Label htmlFor="token">Access token</Label>
            <Input
              id="token"
              value={token}
              onChange={(e) => setTokenValue(e.target.value)}
              placeholder="eyJhbGciOi..."
              autoComplete="off"
              required
            />
          </div>
          {error ? <p className="text-xs text-danger">{error}</p> : null}
          <Button type="submit" disabled={busy || !token.trim()} className="w-full">
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        <p className="mt-5 border-t border-border pt-4 text-xs text-muted">
          In local development, mint a token with{" "}
          <code className="rounded bg-border/50 px-1">python -m seeds.dev_token</code>. On AWS this
          comes from the Cognito hosted UI.
        </p>
      </Card>
    </main>
  );
}

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------
function NavLink({ href, label }: { href: string; label: string }) {
  const pathname = usePathname();
  const active = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "block rounded-md px-3 py-1.5 text-sm transition",
        active ? "bg-accent/15 font-medium text-accent" : "text-muted hover:bg-surface hover:text-fg",
      )}
    >
      {label}
    </Link>
  );
}

function Chrome({ children }: { children: React.ReactNode }) {
  const { me, signOut } = useSession();
  const router = useRouter();

  return (
    <div className="flex min-h-dvh flex-col lg:flex-row">
      <aside className="border-b border-border bg-surface lg:w-60 lg:shrink-0 lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between px-4 py-4">
          <Link href="/dashboard" className="text-sm font-semibold">
            Enterprise Knowledge AI
          </Link>
        </div>

        <nav className="space-y-0.5 px-2 pb-4">
          {USER_NAV.map((item) => (
            <NavLink key={item.href} {...item} />
          ))}

          {me?.role === "admin" ? (
            <>
              <p className="px-3 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wide text-muted">
                Admin
              </p>
              {ADMIN_NAV.map((item) => (
                <NavLink key={item.href} {...item} />
              ))}
            </>
          ) : null}
        </nav>

        <div className="border-t border-border px-4 py-3 text-xs lg:mt-auto">
          <p className="truncate font-medium text-fg">{me?.email ?? me?.user_id}</p>
          <p className="truncate text-muted">
            {me?.role} · tenant <span className="font-mono">{me?.tenant_id}</span>
          </p>
          <Button
            variant="ghost"
            size="sm"
            className="mt-2 px-0"
            onClick={() => {
              signOut();
              router.push("/");
            }}
          >
            Sign out
          </Button>
        </div>
      </aside>

      <main className="min-w-0 flex-1 p-4 lg:p-8">{children}</main>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <Gate>{children}</Gate>
    </SessionProvider>
  );
}

function Gate({ children }: { children: React.ReactNode }) {
  const { me, loading } = useSession();

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <p className="text-sm text-muted">Loading…</p>
      </div>
    );
  }
  if (!me) return <SignIn />;
  return <Chrome>{children}</Chrome>;
}

/** Page header used by every route. */
export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-fg">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
      </div>
      {action}
    </header>
  );
}
