"use client";

import { AdminOnly } from "@/components/admin";
import { PageHeader } from "@/components/shell";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  ErrorState,
  Skeleton,
  Stat,
} from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

export default function DeploymentsPage() {
  return (
    <AdminOnly>
      <DeploymentsView />
    </AdminOnly>
  );
}

function DeploymentsView() {
  const health = useAsync(() => api.health(), []);

  return (
    <>
      <PageHeader
        title="Deployments"
        description="Environment status and the release lifecycle this platform is deployed with."
      />

      <section className="mb-6 grid gap-3 sm:grid-cols-3">
        {health.loading ? (
          Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-24" />)
        ) : health.error ? (
          <div className="sm:col-span-3">
            <ErrorState message={health.error} onRetry={health.reload} />
          </div>
        ) : (
          <>
            <Stat
              label="API status"
              value={
                <Badge tone={health.data!.status === "ok" ? "ok" : "danger"}>
                  {health.data!.status}
                </Badge>
              }
            />
            <Stat label="Environment" value={health.data!.environment} />
            <Stat label="Burn rate" value="~$0.072/hr" hint="ALB + one Fargate task" />
          </>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Blue-green release</CardTitle>
          </CardHeader>
          <CardBody>
            <ol className="space-y-2 text-xs">
              {[
                ["Blue is live", "The current task set serves all production traffic."],
                ["Green is created", "CodeDeploy starts a new task set on the new image SHA."],
                ["Green is health-checked", "The test listener probes green: liveness, database, Qdrant, Redis, and one real RAG query."],
                ["Traffic shifts", "Only after health checks pass does the ALB move the production listener to green."],
                ["Blue is kept", "The old task set stays alive for the rollback window, then terminates."],
              ].map(([title, detail], index) => (
                <li key={title} className="flex gap-2 rounded-md bg-bg p-2">
                  <span className="w-4 shrink-0 tabular-nums text-muted">{index + 1}.</span>
                  <span>
                    <span className="font-medium text-fg">{title}</span>
                    <span className="text-muted"> — {detail}</span>
                  </span>
                </li>
              ))}
            </ol>
            <p className="mt-3 text-xs text-muted">
              Rollback is a traffic shift back to blue. It never destroys infrastructure, data or
              secrets.
            </p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Lifecycle</CardTitle>
          </CardHeader>
          <CardBody>
            <ul className="space-y-1.5 font-mono text-xs">
              {[
                ["scripts/cost-check.sh", "spend and what is running"],
                ["scripts/deploy.sh", "plan, apply, release, verify, seed, test"],
                ["scripts/verify.sh", "read-only health and AI pipeline check"],
                ["scripts/test-e2e.sh", "full user journeys"],
                ["scripts/rollback.sh", "shift traffic back to the last good revision"],
                ["scripts/destroy.sh", "tear down the ephemeral stack"],
              ].map(([cmd, note]) => (
                <li key={cmd} className="flex flex-wrap items-baseline gap-2">
                  <span className="text-accent">{cmd}</span>
                  <span className="font-sans text-muted">{note}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-muted">
              The environment is ephemeral by design. A destroyed environment costs $0/hour, and{" "}
              <span className="font-medium text-fg">destroy.sh is the normal end of a session</span>
              , not an emergency measure.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
