"use client";

import { AdminOnly } from "@/components/admin";
import { PageHeader } from "@/components/shell";
import {
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

export default function AdminMetricsPage() {
  return (
    <AdminOnly>
      <MetricsView />
    </AdminOnly>
  );
}

function MetricsView() {
  const metrics = useAsync(() => api.admin.metrics(), []);

  // The header renders in EVERY state. Returning early before it meant a failed
  // request produced a page with no title, which reads as a broken app rather
  // than as one view failing to load.
  const header = (
    <PageHeader
      title="AI Metrics"
      description="Model performance, token usage and spend for this tenant."
    />
  );

  if (metrics.loading) {
    return (
      <>
        {header}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      </>
    );
  }

  if (metrics.error) {
    return (
      <>
        {header}
        <ErrorState message={metrics.error} onRetry={metrics.reload} />
      </>
    );
  }

  const data = metrics.data!;
  const spendPct = (data.usage.total_estimated_cost / 20) * 100;

  return (
    <>
      {header}

      <section className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Total requests" value={data.usage.total_requests} />
        <Stat
          label="Estimated cost"
          value={`$${data.usage.total_estimated_cost.toFixed(4)}`}
          hint={`${spendPct.toFixed(1)}% of the $20 ceiling`}
          tone={spendPct > 80 ? "danger" : spendPct > 50 ? "warn" : undefined}
        />
        <Stat label="Avg latency" value={`${data.usage.avg_latency_ms.toFixed(0)} ms`} />
        <Stat
          label="Cache hit rate"
          value={`${(data.usage.cache_hit_rate * 100).toFixed(0)}%`}
        />
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Tokens</CardTitle>
          </CardHeader>
          <CardBody className="grid gap-3 sm:grid-cols-2">
            <Stat label="Input" value={data.usage.total_input_tokens.toLocaleString()} />
            <Stat label="Output" value={data.usage.total_output_tokens.toLocaleString()} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Retrieval</CardTitle>
          </CardHeader>
          <CardBody className="grid gap-3 sm:grid-cols-2">
            <Stat label="Documents" value={data.documents} />
            <Stat label="Indexed chunks" value={data.chunks} />
            <Stat
              label="Ingestion failures"
              value={data.ingestion_failures}
              tone={data.ingestion_failures > 0 ? "warn" : undefined}
            />
            <Stat
              label="Security events"
              value={data.security_events}
              tone={data.security_events > 0 ? "danger" : undefined}
            />
          </CardBody>
        </Card>
      </div>
    </>
  );
}
