"use client";

import { AdminOnly, EventTable } from "@/components/admin";
import { PageHeader } from "@/components/shell";
import { Card, CardBody, CardHeader, CardTitle, Stat } from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

export default function SecurityPage() {
  return (
    <AdminOnly>
      <SecurityView />
    </AdminOnly>
  );
}

function SecurityView() {
  const events = useAsync(() => api.admin.security(200), []);
  const rows = events.data ?? [];

  const critical = rows.filter((e) => e.severity === "critical").length;
  const errors = rows.filter((e) => e.severity === "error").length;
  const warnings = rows.filter((e) => e.severity === "warning").length;

  return (
    <>
      <PageHeader
        title="Security"
        description="Injection attempts, authorization denials, rate-limit trips and cross-tenant probes."
      />

      <section className="mb-6 grid gap-3 sm:grid-cols-3">
        <Stat
          label="Critical"
          value={critical}
          tone={critical > 0 ? "danger" : undefined}
          hint="cross-tenant or leakage"
        />
        <Stat label="Errors" value={errors} tone={errors > 0 ? "warn" : undefined} />
        <Stat label="Warnings" value={warnings} />
      </section>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>What is recorded</CardTitle>
        </CardHeader>
        <CardBody>
          <p className="text-xs text-muted">
            Security events store a reason <em>code</em> only — never the offending payload,
            credentials or document content. Each row carries the correlation ID so the full
            request can be traced through the logs.
          </p>
        </CardBody>
      </Card>

      <Card>
        <EventTable
          events={events.data}
          loading={events.loading}
          error={events.error}
          onRetry={events.reload}
          emptyTitle="No security events"
          emptyHint="Nothing suspicious has been recorded for this tenant."
        />
      </Card>
    </>
  );
}
