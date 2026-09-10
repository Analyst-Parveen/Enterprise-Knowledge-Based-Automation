"use client";

import * as React from "react";

import { useSession } from "@/components/shell";
import { Badge, Card, EmptyState, ErrorState, Skeleton, statusTone, Table, Td, Th } from "@/components/ui";
import type { AuditEvent } from "@/types/api";

/**
 * Admin gate.
 *
 * This is convenience only - it hides UI a non-admin cannot use. The real
 * authorization is the server-side role check on every admin endpoint. Hiding a
 * page is not authorization. See .claude/rules/security.md section 2.
 */
export function AdminOnly({ children }: { children: React.ReactNode }) {
  const { me } = useSession();

  if (me?.role !== "admin") {
    return (
      <Card>
        <EmptyState
          title="Administrator access required"
          hint="This page is restricted to administrators of your tenant. The API enforces this independently of the UI."
        />
      </Card>
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
