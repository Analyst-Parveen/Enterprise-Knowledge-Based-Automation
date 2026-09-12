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
  Mail,
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

import { api, ApiClientError, clearToken, getToken, saveSession, setToken } from "@/lib/api";
import { NEW_PASSWORD_REQUIRED, ROLE_LABELS, type Me, type SessionResponse } from "@/types/api";

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
  { href: "/admin/tenants", label: "My Company", icon: Building2 },
  { href: "/admin/documents", label: "Documents", icon: FileText },
  { href: "/admin/metrics", label: "AI Metrics", icon: Gauge },
  { href: "/admin/security", label: "Security", icon: ShieldCheck },
  { href: "/admin/audit", label: "Audit Logs", icon: ClipboardList },
  { href: "/admin/deployments", label: "Deployments", icon: Rocket },
];

/**
 * The service provider's own navigation.
 *
 * A platform operator sees the control plane and nothing else: no chat, no
 * documents, no departments. That is not decoration - the platform tenant holds
 * no documents, and onboarding a company deliberately grants no access to its
 * content. See .claude/rules/tenant-isolation.md section 2.
 */
const PLATFORM_NAV: NavItem[] = [
  { href: "/platform/tenants", label: "Companies", icon: Building2 },
  { href: "/platform/audit", label: "Onboarding Trail", icon: ClipboardList },
];

const PLATFORM_OPS_NAV: NavItem[] = [
  { href: "/admin/security", label: "Security", icon: ShieldCheck },
  { href: "/admin/deployments", label: "Deployments", icon: Rocket },
];

const ALL_NAV = [...USER_NAV, ...ADMIN_NAV, ...PLATFORM_NAV, ...PLATFORM_OPS_NAV];

/**
 * The paste-a-token path, kept for local debugging only.
 *
 * It renders solely when the API is localhost, so the deployed sign-in page has
 * no token field at all. It is a developer convenience, never a way in: the
 * backend refuses dev-signed tokens outside a dev environment regardless.
 */
const DEV_SIGN_IN = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").includes(
  "localhost",
);

// ---------------------------------------------------------------------------
// session
// ---------------------------------------------------------------------------
interface SessionValue {
  me: Me | null;
  loading: boolean;
  error: string | null;
  /** Exchange credentials for a session. Returns a challenge when one is due. */
  signInWithPassword: (email: string, password: string) => Promise<SessionResponse>;
  /** Complete a first sign-in on an invited account. */
  completeNewPassword: (
    email: string,
    challengeSession: string,
    newPassword: string,
  ) => Promise<void>;
  /** Adopt a token directly. Local debugging only - see DEV_SIGN_IN. */
  signIn: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
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

  const adopt = React.useCallback(
    async (session: SessionResponse) => {
      if (!session.token) return;
      saveSession({
        token: session.token,
        refresh_token: session.refresh_token,
        expires_in: session.expires_in,
      });
      setLoading(true);
      await load();
    },
    [load],
  );

  const signInWithPassword = React.useCallback(
    async (email: string, password: string) => {
      const session = await api.auth.login(email, password);
      if (session.token) await adopt(session);
      return session;
    },
    [adopt],
  );

  const completeNewPassword = React.useCallback(
    async (email: string, challengeSession: string, newPassword: string) => {
      await adopt(
        await api.auth.newPassword({
          email,
          challenge_session: challengeSession,
          new_password: newPassword,
        }),
      );
    },
    [adopt],
  );

  const signIn = React.useCallback(
    async (token: string) => {
      setToken(token);
      setLoading(true);
      await load();
    },
    [load],
  );

  const signOut = React.useCallback(async () => {
    // Ask the directory to revoke every token for this identity first. If that
    // call fails the local session is still cleared - a sign-out must never
    // leave the user signed in.
    try {
      await api.auth.logout();
    } catch {
      /* already signed out, offline, or the token had expired */
    }
    clearToken();
    setMe(null);
  }, []);

  return (
    <SessionContext.Provider
      value={{ me, loading, error, signInWithPassword, completeNewPassword, signIn, signOut }}
    >
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

type Stage = "credentials" | "new-password" | "forgot" | "reset";

const MIN_PASSWORD_LENGTH = 12;

/** Mirrors the Cognito pool policy, so the form fails before the network does. */
function passwordComplaint(password: string, confirmation: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (!/[a-z]/.test(password)) return "Include a lower-case letter.";
  if (!/[A-Z]/.test(password)) return "Include an upper-case letter.";
  if (!/[0-9]/.test(password)) return "Include a number.";
  if (!/[^A-Za-z0-9]/.test(password)) return "Include a symbol.";
  if (password !== confirmation) return "The two passwords do not match.";
  return null;
}

function SignIn() {
  const { signIn, signInWithPassword, completeNewPassword } = useSession();

  const [stage, setStage] = React.useState<Stage>("credentials");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [confirmation, setConfirmation] = React.useState("");
  const [code, setCode] = React.useState("");
  const [challengeSession, setChallengeSession] = React.useState("");
  const [notice, setNotice] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const [token, setTokenValue] = React.useState("");

  function fail(err: unknown) {
    setError(err instanceof Error ? err.message : "Something went wrong. Try again.");
  }

  async function attempt(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  const submitCredentials = (event: React.FormEvent) => {
    event.preventDefault();
    void attempt(async () => {
      const session = await signInWithPassword(email.trim(), password);
      if (session.challenge === NEW_PASSWORD_REQUIRED && session.challenge_session) {
        // An invited account signing in for the first time. Not an error.
        setChallengeSession(session.challenge_session);
        setPassword("");
        setNotice("Welcome. Choose a password to finish setting up your account.");
        setStage("new-password");
      } else if (!session.token) {
        setNotice(null);
        setError("This account needs to be reset before it can sign in.");
      }
    });
  };

  const submitNewPassword = (event: React.FormEvent) => {
    event.preventDefault();
    const complaint = passwordComplaint(newPassword, confirmation);
    if (complaint) {
      setError(complaint);
      return;
    }
    void attempt(async () => {
      await completeNewPassword(email.trim(), challengeSession, newPassword);
    });
  };

  const submitForgot = (event: React.FormEvent) => {
    event.preventDefault();
    void attempt(async () => {
      const result = await api.auth.forgotPassword(email.trim());
      setNotice(result.message);
      setStage("reset");
    });
  };

  const submitReset = (event: React.FormEvent) => {
    event.preventDefault();
    const complaint = passwordComplaint(newPassword, confirmation);
    if (complaint) {
      setError(complaint);
      return;
    }
    void attempt(async () => {
      await api.auth.confirmPasswordReset({
        email: email.trim(),
        code: code.trim(),
        new_password: newPassword,
      });
      setNotice("Your password has been changed. Sign in with it.");
      setPassword("");
      setNewPassword("");
      setConfirmation("");
      setCode("");
      setStage("credentials");
    });
  };

  const submitToken = (event: React.FormEvent) => {
    event.preventDefault();
    void attempt(async () => {
      await signIn(token.trim());
      if (!getToken()) setError("That token was rejected.");
    });
  };

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
          <h1 className="text-xl font-semibold tracking-tight">
            {stage === "credentials" ? "Sign in" : null}
            {stage === "new-password" ? "Set your password" : null}
            {stage === "forgot" ? "Reset your password" : null}
            {stage === "reset" ? "Enter your reset code" : null}
          </h1>
          <p className="mt-1 text-sm text-muted">
            {stage === "credentials"
              ? "Use the work email address your administrator invited."
              : null}
            {stage === "new-password"
              ? "Your invitation password is temporary. This one is yours."
              : null}
            {stage === "forgot" ? "We will email you a code to set a new password." : null}
            {stage === "reset" ? "Check your email for the code we just sent." : null}
          </p>

          {notice ? (
            <p className="mt-4 rounded-lg bg-accent/10 px-3 py-2 text-xs leading-relaxed text-accent">
              {notice}
            </p>
          ) : null}

          {/* ---- stage: credentials ---------------------------------------- */}
          {stage === "credentials" ? (
            <form onSubmit={submitCredentials} className="mt-6 space-y-4">
              <div>
                <Label htmlFor="email">Work email</Label>
                <div className="relative">
                  <Mail
                    aria-hidden
                    className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
                  />
                  <Input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    autoComplete="username"
                    className="pl-9"
                    required
                  />
                </div>
              </div>
              <div>
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <KeyRound
                    aria-hidden
                    className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
                  />
                  <Input
                    id="password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    className="pl-9"
                    required
                  />
                </div>
              </div>
              {error ? <FormError message={error} /> : null}
              <Button
                type="submit"
                size="lg"
                disabled={busy || !email.trim() || !password}
                className="w-full"
              >
                {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
                {busy ? "Signing in…" : "Sign in"}
              </Button>
              <button
                type="button"
                onClick={() => {
                  setStage("forgot");
                  setError(null);
                  setNotice(null);
                }}
                className="w-full text-center text-xs text-muted underline-offset-2 hover:text-fg hover:underline"
              >
                Forgot your password?
              </button>
            </form>
          ) : null}

          {/* ---- stage: first sign-in -------------------------------------- */}
          {stage === "new-password" ? (
            <form onSubmit={submitNewPassword} className="mt-6 space-y-4">
              <PasswordFields
                newPassword={newPassword}
                confirmation={confirmation}
                onNewPassword={setNewPassword}
                onConfirmation={setConfirmation}
              />
              {error ? <FormError message={error} /> : null}
              <Button type="submit" size="lg" disabled={busy} className="w-full">
                {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
                {busy ? "Saving…" : "Set password and continue"}
              </Button>
            </form>
          ) : null}

          {/* ---- stage: forgot password ------------------------------------ */}
          {stage === "forgot" ? (
            <form onSubmit={submitForgot} className="mt-6 space-y-4">
              <div>
                <Label htmlFor="forgot-email">Work email</Label>
                <Input
                  id="forgot-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  autoComplete="username"
                  required
                />
              </div>
              {error ? <FormError message={error} /> : null}
              <Button type="submit" size="lg" disabled={busy || !email.trim()} className="w-full">
                {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
                Send reset code
              </Button>
              <BackToSignIn onClick={() => setStage("credentials")} />
            </form>
          ) : null}

          {/* ---- stage: confirm reset -------------------------------------- */}
          {stage === "reset" ? (
            <form onSubmit={submitReset} className="mt-6 space-y-4">
              <div>
                <Label htmlFor="code">Reset code</Label>
                <Input
                  id="code"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  required
                />
              </div>
              <PasswordFields
                newPassword={newPassword}
                confirmation={confirmation}
                onNewPassword={setNewPassword}
                onConfirmation={setConfirmation}
              />
              {error ? <FormError message={error} /> : null}
              <Button type="submit" size="lg" disabled={busy || !code.trim()} className="w-full">
                {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
                Change password
              </Button>
              <BackToSignIn onClick={() => setStage("credentials")} />
            </form>
          ) : null}

          <p className="mt-6 flex items-start gap-2 border-t border-border pt-4 text-xs leading-relaxed text-muted">
            <ShieldCheck aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              Your company and permissions come from your verified sign-in, never from this
              browser. Accounts are created by invitation only.
            </span>
          </p>

          {/* Local debugging only: absent entirely from a deployed build. */}
          {DEV_SIGN_IN ? (
            <details className="mt-4 rounded-lg border border-border/70 px-3 py-2">
              <summary className="cursor-pointer text-xs font-medium text-muted">
                Developer sign-in
              </summary>
              <form onSubmit={submitToken} className="mt-3 space-y-2">
                <Label htmlFor="token">Access token</Label>
                <Input
                  id="token"
                  value={token}
                  onChange={(e) => setTokenValue(e.target.value)}
                  placeholder="eyJhbGciOi..."
                  autoComplete="off"
                />
                <Button type="submit" variant="secondary" size="sm" disabled={busy || !token.trim()}>
                  Use token
                </Button>
                <p className="text-[11px] leading-relaxed text-muted">
                  Mint one with{" "}
                  <code className="rounded bg-border/60 px-1 py-0.5 font-mono text-[10px] text-fg">
                    python -m seeds.dev_token
                  </code>
                  . Rejected outside a dev environment.
                </p>
              </form>
            </details>
          ) : null}
        </Card>
      </section>
    </main>
  );
}

function FormError({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">
      {message}
    </p>
  );
}

function BackToSignIn({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-center text-xs text-muted underline-offset-2 hover:text-fg hover:underline"
    >
      Back to sign in
    </button>
  );
}

function PasswordFields({
  newPassword,
  confirmation,
  onNewPassword,
  onConfirmation,
}: {
  newPassword: string;
  confirmation: string;
  onNewPassword: (value: string) => void;
  onConfirmation: (value: string) => void;
}) {
  const complaint = newPassword ? passwordComplaint(newPassword, newPassword) : null;
  return (
    <>
      <div>
        <Label htmlFor="new-password">New password</Label>
        <Input
          id="new-password"
          type="password"
          value={newPassword}
          onChange={(e) => onNewPassword(e.target.value)}
          autoComplete="new-password"
          required
        />
        <p className="mt-1 text-[11px] text-muted">
          {complaint ?? `At least ${MIN_PASSWORD_LENGTH} characters, mixed case, a number and a symbol.`}
        </p>
      </div>
      <div>
        <Label htmlFor="confirm-password">Confirm password</Label>
        <Input
          id="confirm-password"
          type="password"
          value={confirmation}
          onChange={(e) => onConfirmation(e.target.value)}
          autoComplete="new-password"
          required
        />
      </div>
    </>
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

function NavSection({ label, items }: { label: string; items: NavItem[] }) {
  return (
    <>
      <p className="px-3 pb-1.5 pt-1 text-[11px] font-semibold uppercase tracking-wider text-muted/80 first:pt-1 [&:not(:first-child)]:pt-5">
        {label}
      </p>
      {items.map((item) => (
        <NavLink key={item.href} {...item} />
      ))}
    </>
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
  const [signingOut, setSigningOut] = React.useState(false);

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
          {/*
            Role-aware navigation. This hides what a role cannot use; it is not
            the security boundary. Every route behind these links is
            authorized again server-side.
          */}
          <nav aria-label="Primary" className="space-y-0.5 px-3 pb-4">
            {me?.role === "platform_admin" ? (
              <>
                <NavSection label="Platform" items={PLATFORM_NAV} />
                <NavSection label="Operations" items={PLATFORM_OPS_NAV} />
              </>
            ) : (
              <>
                <NavSection label="Workspace" items={USER_NAV} />
                {me?.role === "admin" ? (
                  <NavSection label="Company admin" items={ADMIN_NAV} />
                ) : null}
              </>
            )}
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
                <p className="truncate text-muted" title={me?.tenant_id}>
                  {me?.tenant_name ?? me?.tenant_id}
                </p>
              </div>
              <Badge
                tone={
                  me?.role === "platform_admin"
                    ? "violet"
                    : me?.role === "admin"
                      ? "accent"
                      : "neutral"
                }
              >
                {me ? ROLE_LABELS[me.role] : ""}
              </Badge>
            </div>
            <Button
              variant="secondary"
              size="sm"
              className="mt-3 w-full"
              disabled={signingOut}
              onClick={() => {
                setSigningOut(true);
                // Revoke server-side first, then leave. Awaiting it means a
                // user who clicks and closes the tab is still signed out.
                void signOut().finally(() => {
                  setSigningOut(false);
                  router.push("/");
                });
              }}
            >
              {signingOut ? (
                <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <LogOut aria-hidden className="h-3.5 w-3.5" />
              )}
              {signingOut ? "Signing out…" : "Sign out"}
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
  const Icon = ALL_NAV.find((item) => isActive(pathname, item.href))?.icon;

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
