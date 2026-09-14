"use client";

import { PageHeader, useSession } from "@/components/shell";
import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Skeleton,
  Stat,
} from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

export default function UsagePage() {
  const { me } = useSession();
  const metrics = useAsync(
    () => (me?.role === "admin" ? api.admin.metrics() : Promise.resolve(null)),
    [me?.role],
  );

  return (
    <>
      <PageHeader
        title="Usage"
        description="Tokens, latency and estimated cost for your tenant. Every AI call is accounted for."
      />

      {me?.role !== "admin" ? (
        <Card>
          <EmptyState
            title="Usage metrics are admin-only"
            hint="Operational metrics are restricted to administrators of your tenant. Ask an admin for access."
          />
        </Card>
      ) : metrics.loading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : metrics.error ? (
        <ErrorState message={metrics.error} onRetry={metrics.reload} />
      ) : (
        <>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Stat label="Requests" value={metrics.data!.usage.total_requests} />
            <Stat
              label="Estimated cost"
              value={`$${metrics.data!.usage.total_estimated_cost.toFixed(4)}`}
              hint="against a $20 ceiling"
            />
            <Stat
              label="Avg latency"
              value={`${metrics.data!.usage.avg_latency_ms.toFixed(0)} ms`}
            />
            <Stat
              label="Input tokens"
              value={metrics.data!.usage.total_input_tokens.toLocaleString()}
            />
            <Stat
              label="Output tokens"
              value={metrics.data!.usage.total_output_tokens.toLocaleString()}
            />
            <Stat
              label="Cache hit rate"
              value={`${(metrics.data!.usage.cache_hit_rate * 100).toFixed(0)}%`}
              hint="cached answers cost nothing extra"
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Knowledge base</CardTitle>
            </CardHeader>
            <CardBody className="grid gap-3 sm:grid-cols-3">
              <Stat label="Documents" value={metrics.data!.documents} />
              <Stat label="Indexed chunks" value={metrics.data!.chunks} />
              <Stat
                label="Ingestion failures"
                value={metrics.data!.ingestion_failures}
                tone={metrics.data!.ingestion_failures > 0 ? "warn" : undefined}
              />
            </CardBody>
          </Card>
        </>
      )}
    </>
  );
}
