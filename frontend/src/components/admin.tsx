"use client";

import * as React from "react";

import { useSession } from "@/components/shell";
import { Badge, Card, EmptyState, ErrorState, Skeleton, statusTone, Table, Td, Th } from "@/components/ui";
import type { AuditEvent } from "@/types/api";

/**
 * Role gates.
 *
 * These are convenience only - they hide UI a role cannot use. The real
 * authorization is the server-side check on every endpoint behind them. Hiding
 * a page is not authorization. See .claude/rules/security.md section 2.
 *
 * Each gate mirrors exactly one backend dependency, so the UI and the API can
 * never drift into disagreeing about who belongs where:
 *
 *   AdminOnly          -> AdminUser         (require_admin)
 *   TenantAdminOnly    -> TenantAdminUser   (require_tenant_admin)
 *   PlatformAdminOnly  -> PlatformAdminUser (require_platform_admin)
 */
function Denied({ title, hint }: { title: string; hint: string }) {
  return (
    <Card>
      <EmptyState title={title} hint={hint} />
    </Card>
  );
}

export function AdminOnly({ children }: { children: React.ReactNode }) {
  const { me } = useSession();

  if (me?.role !== "admin" && me?.role !== "platform_admin") {
    return (
      <Denied
        title="Administrator access required"
        hint="This page is restricted to administrators. The API enforces this independently of the UI."
      />
    );
  }
  return <>{children}</>;
}

/**
 * Managing a company's users needs the company's own admin.
 *
 * A platform operator is excluded on purpose: it onboards a company and then
 * stays out of the company's user directory.
 */
export function TenantAdminOnly({ children }: { children: React.ReactNode }) {
  const { me } = useSession();

  if (me?.role !== "admin") {
    return (
      <Denied
        title="Company administrator access required"
        hint={
          me?.role === "platform_admin"
            ? "Platform operators onboard a company and its first administrator, but do not manage its users. Use Companies instead."
            : "This page is restricted to administrators of your company. The API enforces this independently of the UI."
        }
      />
    );
  }
  return <>{children}</>;
}

export function PlatformAdminOnly({ children }: { children: React.ReactNode }) {
  const { me } = useSession();

  if (me?.role !== "platform_admin") {
    return (
      <Denied
        title="Platform access required"
        hint="Creating and managing companies is restricted to the service provider. Your company's own administration is under Company admin."
      />
    );
  }
  return <>{children}</>;
}

/** Shared renderer for the audit and security event tables. */
export function EventTable({
  events,
  loading,
  error,
  onRetry,
  emptyTitle,
  emptyHint,
}: {
  events: AuditEvent[] | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  emptyTitle: string;
  emptyHint: string;
}) {
  if (loading) {
    return (
      <div className="space-y-2 p-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-8" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-4">
        <ErrorState message={error} onRetry={onRetry} />
      </div>
    );
  }
  if (!events || events.length === 0) {
    return <EmptyState title={emptyTitle} hint={emptyHint} />;
  }

  return (
    <Table>
      <thead>
        <tr>
          <Th>When</Th>
          <Th>Event</Th>
          <Th>Severity</Th>
          <Th>Reason</Th>
          <Th>Resource</Th>
          <Th>Correlation</Th>
        </tr>
      </thead>
      <tbody>
        {events.map((event) => (
          <tr key={event.id}>
            <Td className="whitespace-nowrap text-muted">
              {new Date(event.created_at).toLocaleString()}
            </Td>
            <Td className="font-mono text-xs">{event.event_type}</Td>
            <Td>
              <Badge tone={statusTone(event.severity)}>{event.severity}</Badge>
            </Td>
            {/* Reason CODES only - never the offending payload. */}
            <Td className="max-w-[16rem] truncate text-muted">{event.reason ?? "—"}</Td>
            <Td className="max-w-[12rem] truncate text-muted">{event.resource_id ?? "—"}</Td>
            <Td className="font-mono text-xs opacity-60">
              {event.correlation_id?.slice(0, 8) ?? "—"}
            </Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
