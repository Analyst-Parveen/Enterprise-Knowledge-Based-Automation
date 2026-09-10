"use client";

import { AdminOnly, EventTable } from "@/components/admin";
import { PageHeader } from "@/components/shell";
import { Card } from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

export default function AuditPage() {
  return (
    <AdminOnly>
      <AuditView />
    </AdminOnly>
  );
}

function AuditView() {
  const events = useAsync(() => api.admin.audit(200), []);

  return (
    <>
      <PageHeader
        title="Audit Logs"
        description="Every authentication, authorization, upload and deletion event for this tenant."
      />
      <Card>
        <EventTable
          events={events.data}
          loading={events.loading}
          error={events.error}
          onRetry={events.reload}
          emptyTitle="No audit events yet"
          emptyHint="Events appear here as people sign in, upload, query and delete."
        />
      </Card>
    </>
  );
}
