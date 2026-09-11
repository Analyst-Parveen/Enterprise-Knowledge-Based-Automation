"use client";

import {
  Activity,
  BarChart3,
  Bot,
  Building2,
  ClipboardList,
  FileText,
  Gauge,
  KeyRound,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  MessageSquareText,
  MessagesSquare,
  Quote,
  Rocket,
  ShieldCheck,
  Sparkles,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { api, ApiClientError, clearToken, getToken, setToken } from "@/lib/api";
import type { Me } from "@/types/api";

import { Badge, Button, Card, Input, Label, cn } from "./ui";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const USER_NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/chat", label: "Knowledge Chat", icon: MessageSquareText },
  { href: "/agents", label: "Workflows", icon: Bot },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/departments", label: "Departments", icon: Building2 },
  { href: "/usage", label: "Usage", icon: BarChart3 },
  { href: "/feedback", label: "Feedback", icon: MessagesSquare },
];

const ADMIN_NAV: NavItem[] = [
  { href: "/admin/users", label: "Users", icon: Users },
  { href: "/admin/tenants", label: "Tenants", icon: Building2 },
  { href: "/admin/documents", label: "Documents", icon: FileText },
  { href: "/admin/metrics", label: "AI Metrics", icon: Gauge },
  { href: "/admin/security", label: "Security", icon: ShieldCheck },
  { href: "/admin/audit", label: "Audit Logs", icon: ClipboardList },
  { href: "/admin/deployments", label: "Deployments", icon: Rocket },
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
// brand
// ---------------------------------------------------------------------------
function BrandMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand text-white shadow-glow",
        className,
      )}
    >
      <Sparkles className="h-[18px] w-[18px]" />
    </span>
  );
}

// ---------------------------------------------------------------------------
// sign-in
// ---------------------------------------------------------------------------
const SIGN_IN_POINTS: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: Quote,
    title: "Cited answers",
    body: "Every answer links back to the document, page and section it came from.",
  },
  {
    icon: ShieldCheck,
    title: "Tenant isolation",
    body: "Retrieval, cache and storage are filtered by the tenant in your verified token.",
  },
  {
    icon: Activity,
    title: "Guarded pipeline",
    body: "Prompt-injection scanning, output guardrails and confidence on every response.",
  },
];

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
    <main className="grid min-h-dvh lg:grid-cols-2">
      {/* ---- brand panel -------------------------------------------------- */}
      <section className="relative hidden overflow-hidden bg-brand p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-24 h-96 w-96 rounded-full bg-white/10 blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-32 -left-16 h-96 w-96 rounded-full bg-white/10 blur-3xl"
        />
        <div className="relative flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/15 ring-1 ring-white/25">
            <Sparkles aria-hidden className="h-5 w-5" />
          </span>
          <span className="text-sm font-semibold tracking-wide">Enterprise Knowledge AI</span>
        </div>

        <div className="relative max-w-md">
          <p className="text-3xl font-semibold leading-tight tracking-tight">
            Your company knowledge, answered with evidence.
          </p>
          <ul className="mt-8 space-y-5">
            {SIGN_IN_POINTS.map(({ icon: Icon, title, body }) => (
              <li key={title} className="flex gap-3">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/15 ring-1 ring-white/20">
                  <Icon aria-hidden className="h-4 w-4" />
                </span>
                <div>
                  <p className="text-sm font-semibold">{title}</p>
                  <p className="text-sm text-white/80">{body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs text-white/70">
          Retrieval-augmented generation on Amazon Bedrock · multi-tenant · auditable
        </p>
      </section>

      {/* ---- form --------------------------------------------------------- */}
      <section className="flex items-center justify-center p-6">
        <Card className="w-full max-w-md animate-fade-in p-7 shadow-lift">
          <BrandMark className="mb-5" />
          <h1 className="text-xl font-semibold tracking-tight">Enterprise Knowledge AI</h1>
          <p className="mt-1 text-sm text-muted">
            Sign in with your Cognito access token to continue.
          </p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            <div>
              <Label htmlFor="token">Access token</Label>
              <div className="relative">
                <KeyRound
                  aria-hidden
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
                />
                <Input
                  id="token"
                  value={token}
                  onChange={(e) => setTokenValue(e.target.value)}
                  placeholder="eyJhbGciOi..."
                  autoComplete="off"
                  className="pl-9"
                  required
                />
              </div>
            </div>
            {error ? (
              <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">
                {error}
              </p>
            ) : null}
            <Button type="submit" size="lg" disabled={busy || !token.trim()} className="w-full">
              {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <p className="mt-6 border-t border-border pt-4 text-xs leading-relaxed text-muted">
            In local development, mint a token with{" "}
            <code className="rounded bg-border/60 px-1 py-0.5 font-mono text-[11px] text-fg">
              python -m seeds.dev_token
            </code>
            . On AWS this comes from the Cognito hosted UI.
          </p>
        </Card>
      </section>
    </main>
  );
}

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------
function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavLink({ href, label, icon: Icon }: NavItem) {
  const pathname = usePathname();
  const active = isActive(pathname, href);
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-all duration-150",
        active
          ? "bg-accent/10 font-medium text-accent"
          : "text-muted hover:translate-x-0.5 hover:bg-surface-2 hover:text-fg",
      )}
    >
      {active ? (
        <span aria-hidden className="absolute inset-y-1.5 left-0 w-1 rounded-r-full bg-brand" />
      ) : null}
      <Icon
        aria-hidden
        className={cn(
          "h-4 w-4 shrink-0 transition-colors",
          active ? "text-accent" : "text-muted group-hover:text-fg",
        )}
      />
      {label}
    </Link>
  );
}

function initials(me: Me | null): string {
  const source = me?.email ?? me?.user_id ?? "?";
  const name = source.split("@")[0] ?? source;
  const parts = name.split(/[._\s-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || name.slice(0, 2).toUpperCase();
}

function Chrome({ children }: { children: React.ReactNode }) {
  const { me, signOut } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = React.useState(false);

  // Close the mobile menu whenever the route changes.
  React.useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  return (
    <div className="flex min-h-dvh flex-col lg:flex-row">
      <aside className="z-20 border-b border-border bg-surface/95 backdrop-blur lg:sticky lg:top-0 lg:flex lg:h-dvh lg:w-64 lg:shrink-0 lg:flex-col lg:border-b-0 lg:border-r">
        {/* brand + mobile toggle */}
        <div className="flex items-center justify-between gap-3 px-4 py-4">
          <Link href="/dashboard" className="flex min-w-0 items-center gap-3">
            <BrandMark />
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-fg">
                Enterprise Knowledge AI
              </span>
              <span className="block text-[11px] text-muted">Knowledge &amp; automation</span>
            </span>
          </Link>
          <Button
            variant="ghost"
            size="sm"
            className="h-9 w-9 px-0 lg:hidden"
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            aria-controls="primary-navigation"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X aria-hidden className="h-5 w-5" /> : <Menu aria-hidden className="h-5 w-5" />}
          </Button>
        </div>

        {/* One nav element, shown on desktop and toggled on mobile. */}
        <div
          id="primary-navigation"
          className={cn("flex-1 flex-col overflow-y-auto lg:flex", menuOpen ? "flex" : "hidden")}
        >
          <nav aria-label="Primary" className="space-y-0.5 px-3 pb-4">
            <p className="px-3 pb-1.5 pt-1 text-[11px] font-semibold uppercase tracking-wider text-muted/80">
              Workspace
            </p>
            {USER_NAV.map((item) => (
              <NavLink key={item.href} {...item} />
            ))}

            {me?.role === "admin" ? (
              <>
                <p className="px-3 pb-1.5 pt-5 text-[11px] font-semibold uppercase tracking-wider text-muted/80">
                  Admin
                </p>
                {ADMIN_NAV.map((item) => (
                  <NavLink key={item.href} {...item} />
                ))}
              </>
            ) : null}
          </nav>

          {/* user card */}
          <div className="mt-auto border-t border-border p-4">
            <div className="flex items-center gap-3">
              <span
                aria-hidden
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand text-xs font-semibold text-white"
              >
                {initials(me)}
              </span>
              <div className="min-w-0 flex-1 text-xs">
                <p className="truncate font-medium text-fg">{me?.email ?? me?.user_id}</p>
                <p className="truncate text-muted">
                  tenant <span className="font-mono">{me?.tenant_id}</span>
                </p>
              </div>
              <Badge tone={me?.role === "admin" ? "accent" : "neutral"}>{me?.role}</Badge>
            </div>
            <Button
              variant="secondary"
              size="sm"
              className="mt-3 w-full"
              onClick={() => {
                signOut();
                router.push("/");
              }}
            >
              <LogOut aria-hidden className="h-3.5 w-3.5" />
              Sign out
            </Button>
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <div className="mx-auto w-full max-w-7xl animate-fade-in p-4 sm:p-6 lg:p-8">{children}</div>
      </main>
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
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3">
        <BrandMark />
        <p className="flex items-center gap-2 text-sm text-muted">
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          Loading…
        </p>
      </div>
    );
  }
  if (!me) return <SignIn />;
  return <Chrome>{children}</Chrome>;
}

/** Page header used by every route. Picks up the matching navigation icon. */
export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  const pathname = usePathname();
  const Icon = [...ADMIN_NAV, ...USER_NAV].find((item) => isActive(pathname, item.href))?.icon;

  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 items-start gap-3">
        {Icon ? (
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/10 text-accent">
            <Icon aria-hidden className="h-5 w-5" />
          </span>
        ) : null}
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-fg sm:text-2xl">{title}</h1>
          {description ? <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p> : null}
        </div>
      </div>
      {action}
    </header>
  );
}
