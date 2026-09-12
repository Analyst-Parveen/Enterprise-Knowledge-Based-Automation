"use client";

import {
  AlertTriangle,
  ArrowRight,
  AudioLines,
  Bot,
  Building2,
  CheckCircle2,
  ClipboardList,
  Clock,
  Coins,
  Database,
  FileText,
  Gauge,
  Image as ImageIcon,
  Layers,
  MessageSquareText,
  ShieldAlert,
  ShieldCheck,
  Table2,
  Timer,
  Upload,
  UserPlus,
  Video,
  Zap,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { BarList, RingMeter, SegmentedBar, type BarItem } from "@/components/charts";
import { useSession } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Skeleton,
  Stat,
  cn,
  statusTone,
  toneSoft,
  type Tone,
} from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { DEPARTMENTS, type AuditEvent, type DocumentOut, type Modality } from "@/types/api";

// The API caps a page at 200. Breakdowns below are computed over that page and
// say so when the tenant holds more - no number on this page is invented.
const SAMPLE_LIMIT = 200;
const RECENT_COUNT = 6;

const MODALITY: Record<Modality, { label: string; icon: LucideIcon; tone: Tone }> = {
  text: { label: "Text", icon: FileText, tone: "accent" },
  table: { label: "Tables", icon: Table2, tone: "info" },
  image: { label: "Images", icon: ImageIcon, tone: "violet" },
  audio: { label: "Audio", icon: AudioLines, tone: "ok" },
  video: { label: "Video", icon: Video, tone: "warn" },
};

const DEPARTMENT_TONES: Tone[] = ["accent", "info", "violet", "ok", "warn", "danger", "neutral"];

/** "hr" is an acronym; every other department reads as a capitalised word. */
function departmentLabel(dept: string | null): string {
  if (!dept) return "No department";
  return dept === "hr" ? "HR" : dept.charAt(0).toUpperCase() + dept.slice(1);
}

const dateFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

function greetingName(email: string | null | undefined, userId: string | undefined): string {
  const local = (email ?? userId ?? "").split("@")[0] ?? "";
  const first = local.split(/[._\s-]+/)[0] ?? "";
  return first ? first.charAt(0).toUpperCase() + first.slice(1) : "there";
}

function summarise(items: DocumentOut[]) {
  const count = (pred: (d: DocumentOut) => boolean) => items.filter(pred).length;
  return {
    ready: count((d) => d.status === "ready"),
    inProgress: count((d) => d.status === "pending" || d.status === "processing"),
    failed: count((d) => d.status === "failed"),
    chunks: items.reduce((sum, d) => sum + d.chunk_count, 0),
    byModality: (Object.keys(MODALITY) as Modality[]).map((m) => ({
      modality: m,
      count: count((d) => d.modality === m),
    })),
    byDepartment: DEPARTMENTS.map((dept) => ({ dept, count: count((d) => d.department === dept) })),
  };
}

export default function DashboardPage() {
  const { me } = useSession();
  if (me?.role === "platform_admin") {
    return <PlatformHome />;
  }
  return <TenantHome />;
}

/**
 * The service provider's landing page.
 *
 * A platform operator must never land on the knowledge dashboard: the platform
 * tenant holds no documents, and onboarding a company does not grant access to
 * its content. This view is registry and trail only.
 */
function PlatformHome() {
  const { me } = useSession();
  const tenants = useAsync(() => api.platform.tenants(), []);
  const trail = useAsync(() => api.platform.audit(8), []);

  const items = tenants.data?.items ?? [];
  const unfinished = items.filter((t) => t.admin_count === 0);
  const seats = items.reduce((sum, t) => sum + t.user_count, 0);
  const recent = trail.data ?? [];

  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
        <div aria-hidden className="absolute inset-x-0 top-0 h-1 bg-brand" />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full bg-accent2/15 blur-3xl"
        />
        <div className="relative grid gap-6 p-6 lg:grid-cols-[1fr_auto] lg:items-center lg:p-8">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="violet" dot>
                tenant <span className="font-mono">{me?.tenant_id}</span>
              </Badge>
              <Badge tone="violet">{me?.role}</Badge>
            </div>
            <p className="mt-4 text-sm font-medium text-muted">
              Welcome back, {greetingName(me?.email, me?.user_id)}
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">
              Control plane
            </h1>
            <p className="mt-2 max-w-xl text-sm text-muted">
              Onboard a company, invite the administrator who will run it, and watch the
              seats fill up. Creating a company here never grants access to its documents.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link href="/platform/tenants">
                <Button size="lg">
                  <Building2 aria-hidden className="h-4 w-4" />
                  Onboard a company
                </Button>
              </Link>
              <Link href="/platform/audit">
                <Button size="lg" variant="secondary">
                  <ClipboardList aria-hidden className="h-4 w-4" />
                  View onboarding trail
                </Button>
              </Link>
            </div>
          </div>

          <div className="flex items-center gap-5 rounded-xl border border-border bg-surface-2 p-4 sm:min-w-[18rem]">
            <dl className="w-full space-y-3 text-sm">
              <div className="flex items-center justify-between gap-4">
                <dt className="text-xs text-muted">Companies</dt>
                <dd className="font-semibold tabular-nums text-fg">
                  {tenants.loading ? "—" : items.length}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-4">
                <dt className="text-xs text-muted">Awaiting an admin</dt>
                <dd
                  className={cn(
                    "font-semibold tabular-nums",
                    unfinished.length ? "text-warn" : "text-ok",
                  )}
                >
                  {tenants.loading ? "—" : unfinished.length}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-4">
                <dt className="text-xs text-muted">Seats provisioned</dt>
                <dd className="font-semibold tabular-nums text-fg">
                  {tenants.loading ? "—" : seats}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </section>

      <section aria-label="Portfolio" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {tenants.loading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[108px]" />)
        ) : tenants.error ? (
          <div className="sm:col-span-2 xl:col-span-4">
            <ErrorState message={tenants.error} onRetry={tenants.reload} />
          </div>
        ) : (
          <>
            <Stat label="Companies" value={items.length} icon={Building2} iconTone="accent" />
            <Stat
              label="Active"
              value={items.filter((t) => t.is_active).length}
              icon={CheckCircle2}
              iconTone="ok"
            />
            <Stat
              label="Awaiting an admin"
              value={unfinished.length}
              hint={unfinished.length ? "nobody can sign in until you invite one" : "every company has an administrator"}
              icon={UserPlus}
              iconTone={unfinished.length ? "warn" : "accent"}
              tone={unfinished.length ? "warn" : undefined}
            />
            <Stat label="Seats provisioned" value={seats} icon={ShieldCheck} iconTone="violet" />
          </>
        )}
      </section>

      {unfinished.length > 0 ? (
        <Card className="border-warn/40 bg-warn/5">
          <CardBody className="flex flex-wrap items-center gap-3 text-sm">
            <UserPlus aria-hidden className="h-4 w-4 shrink-0 text-warn" />
            <span className="text-fg">
              {unfinished.length === 1
                ? `${unfinished[0].name} has no administrator yet.`
                : `${unfinished.length} companies have no administrator yet.`}
            </span>
            <Link href="/platform/tenants" className="ml-auto">
              <Button size="sm" variant="secondary">
                Invite an admin
                <ArrowRight aria-hidden className="h-3.5 w-3.5" />
              </Button>
            </Link>
          </CardBody>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>
            <ClipboardList aria-hidden className="h-4 w-4 text-accent" />
            Recent onboarding
          </CardTitle>
          <Link
            href="/platform/audit"
            className="inline-flex items-center gap-1 text-xs font-medium text-accent transition-colors hover:text-accent2"
          >
            Full trail
            <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </Link>
        </CardHeader>
        {trail.loading ? (
          <CardBody className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </CardBody>
        ) : trail.error ? (
          <CardBody>
            <ErrorState message={trail.error} onRetry={trail.reload} />
          </CardBody>
        ) : recent.length === 0 ? (
          <EmptyState
            icon={Building2}
            title="No companies yet"
            hint="Onboard the first one. You will be asked for its name, then for the administrator who should run it."
            action={
              <Link href="/platform/tenants">
                <Button size="sm" className="mt-2">
                  Onboard a company
                </Button>
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {recent.map((event: AuditEvent) => (
              <li key={event.id} className="flex items-center gap-3 px-5 py-3">
                <span className="min-w-0 flex-1">
                  <p className="truncate font-mono text-xs text-fg">{event.event_type}</p>
                  <p className="truncate text-xs text-muted">{event.reason ?? "—"}</p>
                </span>
                <Badge tone={statusTone(event.severity)}>{event.severity}</Badge>
                <span className="whitespace-nowrap text-xs text-muted">
                  {new Date(event.created_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <p className="flex items-start gap-2 rounded-xl border border-dashed border-border px-4 py-3 text-xs leading-relaxed text-muted">
        <ShieldCheck aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
        <span>
          Isolation holds here too. This page lists company names, seat counts and lifecycle
          events. A company&apos;s documents, conversations and metrics are not reachable from
          the platform role at all.
        </span>
      </p>
    </div>
  );
}

function TenantHome() {
  const { me } = useSession();
  const isAdmin = me?.role === "admin";

  const health = useAsync(() => api.health(), []);
  const docs = useAsync(() => api.documents.list({ limit: SAMPLE_LIMIT }), []);
  const metrics = useAsync(
    () => (isAdmin ? api.admin.metrics() : Promise.resolve(null)),
    [isAdmin],
  );

  const items = docs.data?.items ?? [];
  const total = docs.data?.total ?? 0;
  const stats = summarise(items);
  const partial = total > items.length;
  const readiness = items.length ? stats.ready / items.length : 0;
  const basis = partial
    ? `Based on the latest ${items.length} of ${total} documents`
    : `Across ${items.length} document${items.length === 1 ? "" : "s"}`;

  return (
    <div className="space-y-6">
      {/* ---- hero ----------------------------------------------------------- */}
      <section className="relative overflow-hidden rounded-2xl border border-border bg-surface shadow-card">
        <div aria-hidden className="absolute inset-x-0 top-0 h-1 bg-brand" />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full bg-accent/10 blur-3xl"
        />
        <div className="relative grid gap-6 p-6 lg:grid-cols-[1fr_auto] lg:items-center lg:p-8">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="accent" dot>
                tenant <span className="font-mono">{me?.tenant_id}</span>
              </Badge>
              <Badge tone={isAdmin ? "accent" : "neutral"}>{me?.role}</Badge>
            </div>
            <p className="mt-4 text-sm font-medium text-muted">
              Welcome back, {greetingName(me?.email, me?.user_id)}
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">
              Enterprise Knowledge AI
            </h1>
            <p className="mt-2 max-w-xl text-sm text-muted">
              Search your company knowledge, with citations and confidence on every answer.
              Everything you see is restricted to your tenant.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Link href="/chat">
                <Button size="lg">
                  <MessageSquareText aria-hidden className="h-4 w-4" />
                  Ask a question
                </Button>
              </Link>
              <Link href="/agents">
                <Button size="lg" variant="secondary">
                  <Bot aria-hidden className="h-4 w-4" />
                  Run a workflow
                </Button>
              </Link>
              <Link href="/documents">
                <Button size="lg" variant="secondary">
                  <Upload aria-hidden className="h-4 w-4" />
                  Upload a document
                </Button>
              </Link>
            </div>
          </div>

          {/* system status - live data only */}
          <div className="flex items-center gap-5 rounded-xl border border-border bg-surface-2 p-4 sm:min-w-[20rem]">
            {docs.loading ? (
              <Skeleton className="h-[88px] w-[88px] rounded-full" />
            ) : (
              <RingMeter value={readiness} label="Documents ready to search" tone="ok" />
            )}
            <dl className="space-y-2.5 text-sm">
              <div>
                <dt className="text-xs text-muted">API</dt>
                <dd className="flex items-center gap-2 font-medium">
                  {health.loading ? (
                    <span className="text-muted">Checking…</span>
                  ) : health.error || health.data?.status !== "ok" ? (
                    <>
                      <span aria-hidden className="h-2 w-2 rounded-full bg-danger" />
                      <span className="text-danger">Unreachable</span>
                    </>
                  ) : (
                    <>
                      <span aria-hidden className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ok opacity-60" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-ok" />
                      </span>
                      <span className="text-ok">Operational</span>
                    </>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Environment</dt>
                <dd className="font-mono text-xs font-medium text-fg">
                  {health.data?.environment ?? "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Ready to search</dt>
                <dd className="text-xs font-medium tabular-nums text-fg">
                  {docs.loading ? "—" : `${stats.ready} of ${items.length}`}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </section>

      {/* ---- key numbers ---------------------------------------------------- */}
      <section aria-label="Key numbers" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {docs.loading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[108px]" />)
        ) : (
          <>
            <Stat label="Documents" value={total} hint="in your tenant" icon={FileText} iconTone="accent" />
            <Stat
              label="Ready to search"
              value={stats.ready}
              hint={items.length ? `${Math.round(readiness * 100)}% ingestion complete` : "ingestion complete"}
              icon={CheckCircle2}
              iconTone="ok"
            />
            <Stat
              label="Indexed chunks"
              value={(isAdmin && metrics.data ? metrics.data.chunks : stats.chunks).toLocaleString()}
              hint={isAdmin && metrics.data ? "all documents in the tenant" : basis.toLowerCase()}
              icon={Layers}
              iconTone="violet"
            />
            <Stat
              label="In progress · failed"
              value={`${stats.inProgress} · ${stats.failed}`}
              hint={stats.failed ? "failed ingestions need attention" : "no failed ingestions"}
              tone={stats.failed ? "danger" : undefined}
              icon={stats.failed ? AlertTriangle : Clock}
              iconTone={stats.failed ? "danger" : "info"}
            />
          </>
        )}
      </section>

      {docs.error ? <ErrorState message={docs.error} onRetry={docs.reload} /> : null}

      {/* ---- knowledge base breakdown ---------------------------------------- */}
      <section className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>
              <Database aria-hidden className="h-4 w-4 text-accent" />
              Knowledge base
            </CardTitle>
            {!docs.loading && items.length ? <span className="text-xs text-muted">{basis}</span> : null}
          </CardHeader>
          <CardBody className="space-y-6">
            {docs.loading ? (
              <div className="space-y-3">
                <Skeleton className="h-3" />
                <Skeleton className="h-24" />
              </div>
            ) : items.length === 0 ? (
              <p className="text-sm text-muted">Nothing indexed yet - upload a document to begin.</p>
            ) : (
              <>
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
                    Ingestion status
                  </p>
                  <SegmentedBar
                    label="Ingestion status"
                    segments={[
                      { label: "ready", value: stats.ready, tone: "ok" },
                      { label: "in progress", value: stats.inProgress, tone: "info" },
                      { label: "failed", value: stats.failed, tone: "danger" },
                    ]}
                  />
                </div>
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
                    Content mix
                  </p>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                    {stats.byModality.map(({ modality, count }) => {
                      const { label, icon: Icon, tone } = MODALITY[modality];
                      return (
                        <div
                          key={modality}
                          className="flex flex-col items-start gap-3 rounded-xl border border-border p-3 transition-colors hover:border-accent/30 hover:bg-surface-2"
                        >
                          <span className={cn("flex h-8 w-8 items-center justify-center rounded-lg", toneSoft[tone])}>
                            <Icon aria-hidden className="h-4 w-4" />
                          </span>
                          <div>
                            <p className="text-lg font-semibold tabular-nums leading-none text-fg">{count}</p>
                            <p className="mt-1 text-xs text-muted">{label}</p>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>
              <Building2 aria-hidden className="h-4 w-4 text-accent" />
              Departments
            </CardTitle>
          </CardHeader>
          <CardBody className="p-3">
            {docs.loading ? (
              <div className="space-y-2 p-2">
                {Array.from({ length: 7 }).map((_, i) => (
                  <Skeleton key={i} className="h-8" />
                ))}
              </div>
            ) : (
              <BarList
                items={stats.byDepartment.map(
                  ({ dept, count }, i): BarItem => ({
                    label: departmentLabel(dept),
                    value: count,
                    tone: DEPARTMENT_TONES[i % DEPARTMENT_TONES.length],
                    href: `/departments?d=${dept}`,
                  }),
                )}
                renderLink={(item, row) => (
                  <Link href={item.href ?? "/departments"} className="block rounded-lg">
                    {row}
                  </Link>
                )}
              />
            )}
          </CardBody>
        </Card>
      </section>

      {/* ---- recent documents ------------------------------------------------ */}
      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>
            <Clock aria-hidden className="h-4 w-4 text-accent" />
            Recent documents
          </CardTitle>
          <Link
            href="/documents"
            className="inline-flex items-center gap-1 text-xs font-medium text-accent transition-colors hover:text-accent2"
          >
            View all
            <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </Link>
        </CardHeader>

        {docs.loading ? (
          <CardBody className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </CardBody>
        ) : docs.error ? (
          <CardBody>
            <ErrorState message={docs.error} onRetry={docs.reload} />
          </CardBody>
        ) : items.length === 0 ? (
          <EmptyState
            icon={Upload}
            title="No documents yet"
            hint="Upload a PDF, spreadsheet, diagram or recording to build your knowledge base."
            action={
              <Link href="/documents">
                <Button size="sm" className="mt-2">
                  Upload a document
                </Button>
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {items.slice(0, RECENT_COUNT).map((doc) => {
              const { icon: Icon, tone } = MODALITY[doc.modality] ?? MODALITY.text;
              return (
                <li
                  key={doc.id}
                  className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-surface-2"
                >
                  <span className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", toneSoft[tone])}>
                    <Icon aria-hidden className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-fg">{doc.name}</p>
                    <p className="truncate text-xs text-muted">
                      {departmentLabel(doc.department)}
                      {" · "}
                      {doc.chunk_count} chunks
                      {" · "}
                      {dateFormat.format(new Date(doc.created_at))}
                    </p>
                  </div>
                  <Badge tone={statusTone(doc.status)} dot>
                    {doc.status}
                  </Badge>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {/* ---- AI operations (admin data only) ----------------------------------- */}
      {isAdmin ? (
        <section aria-labelledby="ai-ops" className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div>
              <h2 id="ai-ops" className="text-base font-semibold text-fg">
                AI operations
              </h2>
              <p className="text-xs text-muted">Usage, cost and safety signals for this tenant.</p>
            </div>
            <Link
              href="/admin/metrics"
              className="inline-flex items-center gap-1 text-xs font-medium text-accent transition-colors hover:text-accent2"
            >
              Open AI metrics
              <ArrowRight aria-hidden className="h-3.5 w-3.5" />
            </Link>
          </div>

          {metrics.loading ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-[108px]" />
              ))}
            </div>
          ) : metrics.error ? (
            <ErrorState message={metrics.error} onRetry={metrics.reload} />
          ) : metrics.data ? (
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="grid gap-4 sm:grid-cols-2 lg:col-span-2">
                <Stat
                  label="Queries"
                  value={metrics.data.usage.total_requests.toLocaleString()}
                  hint="all time"
                  icon={Zap}
                  iconTone="accent"
                />
                <Stat
                  label="AI spend"
                  value={`$${metrics.data.usage.total_estimated_cost.toFixed(4)}`}
                  hint="estimated, to date"
                  icon={Coins}
                  iconTone="warn"
                />
                <Stat
                  label="Average latency"
                  value={`${Math.round(metrics.data.usage.avg_latency_ms)} ms`}
                  hint="per request"
                  icon={Timer}
                  iconTone="info"
                />
                <Stat
                  label="Tokens in · out"
                  value={`${metrics.data.usage.total_input_tokens.toLocaleString()} · ${metrics.data.usage.total_output_tokens.toLocaleString()}`}
                  hint="model usage"
                  icon={Gauge}
                  iconTone="violet"
                />
              </div>

              <Card>
                <CardBody className="flex h-full flex-col gap-5">
                  <div className="flex items-center gap-4">
                    <RingMeter
                      value={metrics.data.usage.cache_hit_rate}
                      label="Semantic cache hit rate"
                      tone="violet"
                    />
                    <div>
                      <p className="text-sm font-medium text-fg">Cache hit rate</p>
                      <p className="text-xs text-muted">Answers served from the semantic cache.</p>
                    </div>
                  </div>
                  <ul className="space-y-2 text-sm">
                    <li className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2 text-muted">
                        <AlertTriangle aria-hidden className="h-4 w-4" />
                        Ingestion failures
                      </span>
                      <Badge tone={metrics.data.ingestion_failures ? "danger" : "ok"} dot>
                        {metrics.data.ingestion_failures}
                      </Badge>
                    </li>
                    <li className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2 text-muted">
                        {metrics.data.security_events ? (
                          <ShieldAlert aria-hidden className="h-4 w-4" />
                        ) : (
                          <ShieldCheck aria-hidden className="h-4 w-4" />
                        )}
                        Security events
                      </span>
                      <Badge tone={metrics.data.security_events ? "warn" : "ok"} dot>
                        {metrics.data.security_events}
                      </Badge>
                    </li>
                  </ul>
                </CardBody>
              </Card>
            </div>
          ) : null}
        </section>
      ) : (
        <p className="flex items-center gap-2 rounded-xl border border-dashed border-border px-4 py-3 text-xs text-muted">
          <Gauge aria-hidden className="h-4 w-4 shrink-0" />
          Usage, cost and security analytics are available to administrators.
        </p>
      )}
    </div>
  );
}
