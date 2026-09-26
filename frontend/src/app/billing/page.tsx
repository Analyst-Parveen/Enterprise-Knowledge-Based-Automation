"use client";

import * as React from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  CheckCircle2,
  CreditCard,
  Loader2,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import { PageHeader, useSession } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  ErrorState,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// types - the backend owns every price, so nothing here is calculated locally
// ---------------------------------------------------------------------------
type Interval = "monthly" | "yearly";

type Plan = {
  id: string;
  code: string;
  name: string;
  interval: string;
  currency: string;
  base_amount_paise: number;
  entitlements: Record<string, number>;
};

type Quote = {
  plan_id: string;
  base_paise: number;
  gst_paise: number;
  gateway_fee_paise: number;
  gateway_fee_charged_paise: number;
  total_paise: number;
  customer_pays_gateway_fee: boolean;
};

type Subscription = {
  status: string;
  plan_id: string | null;
  complimentary: boolean;
  current_period_end: string | null;
  payments_configured: boolean;
};

type Payment = {
  id: string;
  status: string;
  total_paise: number;
  method: string | null;
  created_at: string;
};

type Usage = {
  used: number;
  limit: number | null;
  remaining: number | null;
  period_start: string;
  period_end: string;
};

/** idle → confirming → processing → success | failed | cancelled */
type Phase = "idle" | "confirming" | "processing" | "success" | "failed" | "cancelled";

type RazorpayInstance = {
  open: () => void;
  on: (event: string, handler: (payload: unknown) => void) => void;
};

// ---------------------------------------------------------------------------
// formatting
// ---------------------------------------------------------------------------
/** Paise → rupees. Whole amounts lose the trailing .00 so prices read cleanly. */
function inr(paise: number): string {
  const rupees = paise / 100;
  return `₹${rupees.toLocaleString("en-IN", {
    minimumFractionDigits: paise % 100 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  })}`;
}

function count(value: number): string {
  return value.toLocaleString("en-IN");
}

function shortDate(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

// Entitlement keys the catalog defines today, in the order they read best.
// Values always come from the database; only the wording lives here.
const ENTITLEMENT_LABELS: Record<string, string> = {
  monthly_llm_tokens: "AI tokens per month",
  tenant_api_per_min: "API requests per minute",
  tenant_upload_per_min: "Document uploads per minute",
  max_concurrent_jobs: "Concurrent ingestion jobs",
};

function entitlementLines(plan: Plan): string[] {
  const keys = Object.keys(ENTITLEMENT_LABELS).filter((key) => key in plan.entitlements);
  const extras = Object.keys(plan.entitlements).filter((key) => !(key in ENTITLEMENT_LABELS));
  return [...keys, ...extras].map((key) => {
    const label = ENTITLEMENT_LABELS[key] ?? key.replace(/_/g, " ");
    return `${count(plan.entitlements[key])} ${label}`;
  });
}

function paymentTone(status: string): "ok" | "danger" | "neutral" {
  const value = status.toLowerCase();
  if (value.includes("captur") || value.includes("paid") || value.includes("success")) return "ok";
  if (value.includes("fail")) return "danger";
  return "neutral";
}

export default function BillingPage() {
  const { me } = useSession();
  const isAdmin = me?.role === "admin";

  const [billingInterval, setBillingInterval] = React.useState<Interval>("monthly");
  const [plans, setPlans] = React.useState<Plan[]>([]);
  const [subscription, setSubscription] = React.useState<Subscription | null>(null);
  const [payments, setPayments] = React.useState<Payment[]>([]);
  const [usage, setUsage] = React.useState<Usage | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const [selected, setSelected] = React.useState<Plan | null>(null);
  const [quote, setQuote] = React.useState<Quote | null>(null);
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [notice, setNotice] = React.useState<string | null>(null);
  const [cancelling, setCancelling] = React.useState(false);

  // One in-flight payment at a time, whatever the button does.
  const inFlight = React.useRef(false);
  const confirmRef = React.useRef<HTMLDivElement | null>(null);

  const refresh = React.useCallback(async () => {
    const [sub, history, snapshot] = await Promise.allSettled([
      api.billing.subscription(),
      api.billing.payments(),
      api.billing.usage(),
    ]);
    if (sub.status === "fulfilled") setSubscription(sub.value);
    if (history.status === "fulfilled") setPayments(history.value);
    if (snapshot.status === "fulfilled") setUsage(snapshot.value);
  }, []);

  React.useEffect(() => {
    // Razorpay Checkout is loaded once, from Razorpay's own CDN.
    if (!document.querySelector("script[data-razorpay-checkout]")) {
      const script = document.createElement("script");
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.async = true;
      script.dataset.razorpayCheckout = "true";
      document.body.appendChild(script);
    }
    let alive = true;
    void (async () => {
      const catalog = await api.billing.plans().catch((err: Error) => {
        if (alive) setError(err.message);
        return [] as Plan[];
      });
      if (!alive) return;
      setPlans(catalog);
      await refresh();
      if (alive) setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, [refresh]);

  const currentPlanId = subscription?.plan_id ?? null;
  const currentPlan = plans.find((plan) => plan.id === currentPlanId) ?? null;
  const isPaid = Boolean(subscription && subscription.status === "active" && !subscription.complimentary);
  const paymentsConfigured = subscription?.payments_configured ?? false;

  // One card per plan code, showing the row for the chosen interval.
  const codes = Array.from(new Set(plans.map((plan) => plan.code)));
  const cards = codes
    .map((code) => plans.find((plan) => plan.code === code && plan.interval === billingInterval))
    .filter((plan): plan is Plan => Boolean(plan));

  function yearlySaving(plan: Plan): number | null {
    if (plan.interval !== "yearly") return null;
    const monthly = plans.find((row) => row.code === plan.code && row.interval === "monthly");
    if (!monthly) return null;
    const full = monthly.base_amount_paise * 12;
    if (full <= plan.base_amount_paise) return null;
    return Math.round(((full - plan.base_amount_paise) / full) * 100);
  }

  async function beginCheckout(plan: Plan) {
    setError(null);
    setNotice(null);
    setSelected(plan);
    setQuote(null);
    setPhase("confirming");
    try {
      // The quote - base, GST, fee, total - is calculated by the backend.
      setQuote(await api.billing.quote(plan.id));
      requestAnimationFrame(() => confirmRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }));
    } catch (err) {
      setPhase("idle");
      setSelected(null);
      setError(err instanceof Error ? err.message : "Could not price this plan.");
    }
  }

  async function pay() {
    if (!selected || inFlight.current) return;
    inFlight.current = true;
    setPhase("processing");
    setError(null);
    try {
      // Only the plan id is sent. The backend resolves the Razorpay plan,
      // the price and the final amount from the database.
      const session = await api.billing.checkout(selected.id);
      const Razorpay = (window as unknown as { Razorpay?: new (options: Record<string, unknown>) => RazorpayInstance })
        .Razorpay;
      if (!Razorpay) {
        inFlight.current = false;
        setPhase("failed");
        setError("Razorpay Checkout did not load. Refresh the page and try again.");
        return;
      }
      let settled = false;
      const checkout = new Razorpay({
        key: session.key_id,
        subscription_id: session.subscription_id,
        name: "Enterprise Knowledge AI",
        description: `${selected.name} · billed ${selected.interval}`,
        prefill: me?.email ? { email: me.email } : undefined,
        handler: () => {
          settled = true;
          inFlight.current = false;
          setPhase("success");
          void refresh();
        },
        modal: {
          ondismiss: () => {
            if (settled) return;
            inFlight.current = false;
            setPhase("cancelled");
          },
        },
      });
      checkout.on("payment.failed", () => {
        settled = true;
        inFlight.current = false;
        setPhase("failed");
        setError("The payment did not go through. No subscription was changed.");
      });
      checkout.open();
    } catch (err) {
      inFlight.current = false;
      setPhase("failed");
      setError(err instanceof Error ? err.message : "Checkout could not be started.");
    }
  }

  function dismissCheckout() {
    setPhase("idle");
    setSelected(null);
    setQuote(null);
    setError(null);
  }

  async function cancelSubscription() {
    setCancelling(true);
    setError(null);
    try {
      await api.billing.cancel();
      setNotice("Renewal stopped. Your plan stays active until the end of the current period.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not cancel the subscription.");
    } finally {
      setCancelling(false);
    }
  }

  const quotaExhausted = usage?.remaining === 0 && usage?.limit !== null;
  const usedPct =
    usage && usage.limit ? Math.min(100, Math.round((usage.used / usage.limit) * 100)) : 0;

  return (
    <>
      <PageHeader
        title="Billing"
        description="The subscription belongs to your company. Only a company admin can purchase or cancel it."
      />

      {error ? (
        <div className="mb-4">
          <ErrorState message={error} onRetry={() => setError(null)} />
        </div>
      ) : null}

      {notice ? (
        <p
          role="status"
          className="mb-4 flex items-center gap-2 rounded-xl border border-ok/30 bg-ok/5 px-4 py-3 text-sm text-fg"
        >
          <CheckCircle2 aria-hidden className="h-4 w-4 text-ok" />
          {notice}
        </p>
      ) : null}

      {/* ---------------- current plan + usage ---------------- */}
      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              <CreditCard aria-hidden className="h-4 w-4 text-muted" />
              Current plan
            </CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            {loading ? (
              <Skeleton className="h-16 w-full" />
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-xl font-semibold tracking-tight text-fg">
                    {currentPlan?.name ?? "No plan"}
                  </p>
                  {subscription?.complimentary ? (
                    <Badge tone="info">Complimentary</Badge>
                  ) : null}
                  {subscription ? (
                    <Badge tone={subscription.status === "active" ? "ok" : "warn"} dot>
                      {subscription.status}
                    </Badge>
                  ) : null}
                </div>
                <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                  <div className="flex justify-between gap-3 sm:block">
                    <dt className="text-xs text-muted">Billing period</dt>
                    <dd className="text-fg">{currentPlan ? currentPlan.interval : "—"}</dd>
                  </div>
                  <div className="flex justify-between gap-3 sm:block">
                    <dt className="text-xs text-muted">Renews / ends</dt>
                    <dd className="tabular-nums text-fg">
                      {shortDate(subscription?.current_period_end ?? null)}
                    </dd>
                  </div>
                </dl>
                {subscription?.complimentary ? (
                  <p className="text-xs text-muted">
                    Your workspace runs on a complimentary Basic plan. Subscribe to raise your limits.
                  </p>
                ) : null}
                {!paymentsConfigured ? (
                  <p className="flex items-start gap-2 text-xs text-muted">
                    <AlertCircle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />
                    Payments are not configured on this environment yet, so checkout is unavailable.
                  </p>
                ) : null}
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AI usage this month</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            {loading ? (
              <Skeleton className="h-16 w-full" />
            ) : usage ? (
              <>
                <div className="grid grid-cols-3 gap-3 text-center">
                  <div>
                    <p className="text-xs text-muted">Used</p>
                    <p className="text-lg font-semibold tabular-nums text-fg">{count(usage.used)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted">Limit</p>
                    <p className="text-lg font-semibold tabular-nums text-fg">
                      {usage.limit === null ? "—" : count(usage.limit)}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted">Remaining</p>
                    <p
                      className={`text-lg font-semibold tabular-nums ${
                        quotaExhausted ? "text-danger" : "text-fg"
                      }`}
                    >
                      {usage.remaining === null ? "—" : count(usage.remaining)}
                    </p>
                  </div>
                </div>
                {usage.limit ? (
                  <div
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={usedPct}
                    aria-label="Monthly AI token usage"
                    className="h-2 overflow-hidden rounded-full bg-border"
                  >
                    <div
                      className={`h-full rounded-full ${
                        usedPct >= 100 ? "bg-danger" : usedPct >= 80 ? "bg-warn" : "bg-accent"
                      }`}
                      style={{ width: `${usedPct}%` }}
                    />
                  </div>
                ) : null}
                <p className="text-xs text-muted">
                  {shortDate(usage.period_start)} – {shortDate(usage.period_end)} · tokens, per company
                </p>
                {quotaExhausted ? (
                  <div className="rounded-lg border border-danger/30 bg-danger/5 p-3">
                    <p className="text-sm text-fg">
                      Your monthly AI usage limit has been reached. Please upgrade your plan to
                      continue using AI services.
                    </p>
                    <Button
                      variant="primary"
                      size="sm"
                      className="mt-2"
                      onClick={() => document.getElementById("plans")?.scrollIntoView({ behavior: "smooth" })}
                    >
                      Upgrade plan
                      <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-muted">Usage is unavailable right now.</p>
            )}
          </CardBody>
        </Card>
      </div>

      {/* ---------------- plans ---------------- */}
      <section id="plans" aria-labelledby="plans-heading" className="scroll-mt-6">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="plans-heading" className="text-base font-semibold text-fg">
              Plans
            </h2>
            <p className="text-xs text-muted">Prices exclude GST. The exact payable amount is shown before you pay.</p>
          </div>
          <div
            role="group"
            aria-label="Billing period"
            className="inline-flex rounded-lg border border-border bg-surface-2 p-0.5"
          >
            {(["monthly", "yearly"] as Interval[]).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={billingInterval === option}
                onClick={() => setBillingInterval(option)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                  billingInterval === option
                    ? "bg-surface text-fg shadow-card"
                    : "text-muted hover:text-fg"
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="grid gap-4 md:grid-cols-3">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {cards.map((plan) => {
              const isCurrent = plan.id === currentPlanId;
              const saving = yearlySaving(plan);
              const ctaLabel = isCurrent ? "Current plan" : isPaid ? "Upgrade" : "Subscribe";
              const disabled =
                !isAdmin || isCurrent || !paymentsConfigured || phase === "processing";
              return (
                <Card
                  key={plan.id}
                  className={isCurrent ? "border-accent/60 ring-1 ring-accent/30" : undefined}
                >
                  <CardHeader className="flex items-center justify-between gap-2">
                    <CardTitle>{plan.name}</CardTitle>
                    {isCurrent ? <Badge tone="ok">Current plan</Badge> : null}
                    {!isCurrent && saving ? <Badge tone="info">Save {saving}%</Badge> : null}
                  </CardHeader>
                  <CardBody className="flex h-full flex-col gap-4">
                    <div>
                      <p className="text-3xl font-semibold tracking-tight tabular-nums text-fg">
                        {inr(plan.base_amount_paise)}
                      </p>
                      <p className="text-xs text-muted">
                        per {plan.interval === "yearly" ? "year" : "month"} · plus GST
                      </p>
                    </div>
                    <ul className="space-y-1.5 text-sm">
                      {entitlementLines(plan).map((line) => (
                        <li key={line} className="flex items-start gap-2">
                          <Check aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ok" />
                          <span className="text-fg">{line}</span>
                        </li>
                      ))}
                    </ul>
                    <div className="mt-auto space-y-2">
                      <Button
                        type="button"
                        variant={isCurrent ? "secondary" : "primary"}
                        className="w-full"
                        disabled={disabled}
                        onClick={() => void beginCheckout(plan)}
                      >
                        {ctaLabel}
                      </Button>
                      {!isAdmin ? (
                        <p className="text-xs text-muted">
                          Ask your company admin to change the subscription.
                        </p>
                      ) : null}
                    </div>
                  </CardBody>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      {/* ---------------- confirmation / payment ---------------- */}
      {selected && phase !== "idle" ? (
        <div ref={confirmRef} className="mt-6">
          <Card className="border-accent/40">
            <CardHeader>
              <CardTitle>
                <ShieldCheck aria-hidden className="h-4 w-4 text-accent" />
                Confirm your subscription
              </CardTitle>
            </CardHeader>
            <CardBody className="space-y-4">
              <div aria-live="polite">
                {phase === "success" ? (
                  <p className="flex items-start gap-2 rounded-lg border border-ok/30 bg-ok/5 p-3 text-sm text-fg">
                    <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-ok" />
                    Payment received. Your subscription is activated once our server verifies the
                    payment with Razorpay - this page updates automatically.
                  </p>
                ) : null}
                {phase === "failed" ? (
                  <p className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/5 p-3 text-sm text-fg">
                    <XCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
                    The payment failed. Nothing was charged to your company and your current plan is
                    unchanged.
                  </p>
                ) : null}
                {phase === "cancelled" ? (
                  <p className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-fg">
                    <AlertCircle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
                    Payment cancelled. You can try again whenever you are ready.
                  </p>
                ) : null}
              </div>

              <dl className="space-y-2 text-sm">
                <div className="flex items-center justify-between gap-4">
                  <dt className="text-muted">Plan</dt>
                  <dd className="font-medium text-fg">
                    {selected.name} · billed {selected.interval}
                  </dd>
                </div>
                {quote ? (
                  <>
                    <div className="flex items-center justify-between gap-4">
                      <dt className="text-muted">Subscription</dt>
                      <dd className="tabular-nums text-fg">{inr(quote.base_paise)}</dd>
                    </div>
                    <div className="flex items-center justify-between gap-4">
                      <dt className="text-muted">GST</dt>
                      <dd className="tabular-nums text-fg">{inr(quote.gst_paise)}</dd>
                    </div>
                    <div className="flex items-start justify-between gap-4">
                      <dt className="text-muted">
                        Payment gateway fee
                        <span className="block text-xs text-muted">
                          {quote.customer_pays_gateway_fee
                            ? "Added to this payment"
                            : "Deducted from our settlement - not added to your bill"}
                        </span>
                      </dt>
                      <dd className="tabular-nums text-fg">
                        {quote.customer_pays_gateway_fee
                          ? inr(quote.gateway_fee_charged_paise)
                          : inr(0)}
                      </dd>
                    </div>
                    <div className="flex items-center justify-between gap-4 border-t border-border pt-3">
                      <dt className="font-medium text-fg">Total payable now</dt>
                      <dd className="text-2xl font-semibold tabular-nums text-fg">
                        {inr(quote.total_paise)}
                      </dd>
                    </div>
                  </>
                ) : (
                  <Skeleton className="h-24 w-full" />
                )}
              </dl>

              <p className="text-xs text-muted">
                Your subscription will be activated after successful payment verification.
              </p>

              <div className="flex flex-wrap items-center gap-2">
                {phase === "failed" || phase === "cancelled" ? (
                  <Button type="button" onClick={() => void pay()} disabled={!quote}>
                    Retry payment
                  </Button>
                ) : phase === "success" ? (
                  <Button type="button" variant="secondary" onClick={dismissCheckout}>
                    Done
                  </Button>
                ) : (
                  <Button
                    type="button"
                    onClick={() => void pay()}
                    disabled={!quote || phase === "processing"}
                  >
                    {phase === "processing" ? (
                      <>
                        <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
                        Processing…
                      </>
                    ) : (
                      `Pay ${quote ? inr(quote.total_paise) : ""}`
                    )}
                  </Button>
                )}
                {phase === "success" ? null : (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={dismissCheckout}
                    disabled={phase === "processing"}
                  >
                    Close
                  </Button>
                )}
                <span className="flex items-center gap-1.5 text-xs text-muted">
                  <ShieldCheck aria-hidden className="h-3.5 w-3.5" />
                  Secure payment powered by Razorpay
                </span>
              </div>
            </CardBody>
          </Card>
        </div>
      ) : null}

      {/* ---------------- payment history ---------------- */}
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Payment history</CardTitle>
        </CardHeader>
        <CardBody className="p-0">
          {loading ? (
            <div className="p-5">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : payments.length === 0 ? (
            <p className="p-5 text-sm text-muted">No payments yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <thead>
                  <tr>
                    <Th>Date</Th>
                    <Th>Status</Th>
                    <Th>Method</Th>
                    <Th className="text-right">Amount</Th>
                  </tr>
                </thead>
                <tbody>
                  {payments.map((payment) => (
                    <tr key={payment.id}>
                      <Td className="whitespace-nowrap tabular-nums">{shortDate(payment.created_at)}</Td>
                      <Td>
                        <Badge tone={paymentTone(payment.status)}>{payment.status}</Badge>
                      </Td>
                      <Td>{payment.method ?? "—"}</Td>
                      <Td className="text-right tabular-nums">{inr(payment.total_paise)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
          )}
        </CardBody>
      </Card>

      {/* ---------------- cancellation ---------------- */}
      {isAdmin && isPaid ? (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Cancel subscription</CardTitle>
          </CardHeader>
          <CardBody className="flex flex-wrap items-center justify-between gap-3">
            <p className="max-w-xl text-sm text-muted">
              Renewal stops at the end of the current billing period. Your company keeps full access
              until {shortDate(subscription?.current_period_end ?? null)}.
            </p>
            <Button
              type="button"
              variant="secondary"
              disabled={cancelling || subscription?.status !== "active"}
              onClick={() => void cancelSubscription()}
            >
              {cancelling ? "Cancelling…" : "Cancel at period end"}
            </Button>
          </CardBody>
        </Card>
      ) : null}
    </>
  );
}
