"use client";

/**
 * The onboarding trail across every company.
 *
 * This is the one cross-tenant view in the product, and it is deliberately
 * narrow: the API restricts it to tenant and user lifecycle events by type, so
 * it can never widen into a window onto a company's chat, retrieval or
 * documents. See .claude/rules/tenant-isolation.md section 2.
 */

import { Building2, ShieldCheck, UserPlus } from "lucide-react";
import * as React from "react";

import { EventTable, PlatformAdminOnly } from "@/components/admin";
import { PageHeader } from "@/components/shell";
import { Card, CardBody, CardHeader, CardTitle, Stat } from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

export default function PlatformAuditPage() {
  const events = useAsync(() => api.platform.audit(200), []);
  const all = events.data ?? [];

  const count = (prefix: string) =>
    all.filter((event) => event.event_type.startsWith(prefix)).length;

  return (
    <PlatformAdminOnly>
      <PageHeader
        title="Onboarding Trail"
        description="Every company created, every administrator invited, every role changed - with the operator who did it and the correlation id to trace it."
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Stat label="Company events" value={count("tenant.")} icon={Building2} />
        <Stat label="User events" value={count("user.")} icon={UserPlus} iconTone="violet" />
        <Stat
          label="Elevated actions"
          value={all.filter((e) => e.severity !== "info").length}
          icon={ShieldCheck}
          iconTone="warn"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Control-plane events</CardTitle>
        </CardHeader>
        <EventTable
          events={events.data}
          loading={events.loading}
          error={events.error}
          onRetry={events.reload}
          emptyTitle="No onboarding activity yet"
          emptyHint="Creating a company and inviting its administrator will both appear here."
        />
      </Card>

      <Card className="mt-6">
        <CardBody className="text-xs leading-relaxed text-muted">
          This view lists lifecycle events only. A company&apos;s documents,
          conversations and metrics are not reachable from the platform role at all -
          onboarding a tenant is not a key to it.
        </CardBody>
      </Card>
    </PlatformAdminOnly>
  );
}
