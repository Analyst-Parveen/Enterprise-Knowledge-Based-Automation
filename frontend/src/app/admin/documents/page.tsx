"use client";

import { AdminOnly } from "@/components/admin";
import { PageHeader } from "@/components/shell";
import {
  Badge,
  Card,
  CardBody,
  EmptyState,
  ErrorState,
  Skeleton,
  Stat,
  statusTone,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

export default function AdminDocumentsPage() {
  return (
    <AdminOnly>
      <AdminDocumentsView />
    </AdminOnly>
  );
}

function AdminDocumentsView() {
  const docs = useAsync(() => api.documents.list({ limit: 200 }), []);
  const items = docs.data?.items ?? [];

  const failed = items.filter((d) => d.status === "failed").length;
  const processing = items.filter((d) => ["pending", "processing"].includes(d.status)).length;
  const chunks = items.reduce((sum, d) => sum + d.chunk_count, 0);

  return (
    <>
      <PageHeader
        title="Documents"
        description="Every document in this tenant, with its ingestion outcome. Admin scope is this tenant only."
      />

      <section className="mb-6 grid gap-3 sm:grid-cols-4">
        <Stat label="Total" value={docs.data?.total ?? 0} />
        <Stat label="Indexed chunks" value={chunks} />
        <Stat label="Processing" value={processing} />
        <Stat label="Failed" value={failed} tone={failed > 0 ? "danger" : undefined} />
      </section>

      <Card>
        {docs.loading ? (
          <CardBody className="space-y-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-8" />
            ))}
          </CardBody>
        ) : docs.error ? (
          <CardBody>
            <ErrorState message={docs.error} onRetry={docs.reload} />
          </CardBody>
        ) : items.length === 0 ? (
          <EmptyState title="No documents" hint="Nothing has been uploaded to this tenant yet." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Owner</Th>
                <Th>Modality</Th>
                <Th>Department</Th>
                <Th>Status</Th>
                <Th className="text-right">Chunks</Th>
                <Th className="text-right">Version</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((doc) => (
                <tr key={doc.id}>
                  <Td className="max-w-[18rem] truncate">{doc.name}</Td>
                  <Td className="font-mono text-xs text-muted">{doc.owner_id.slice(0, 12)}</Td>
                  <Td className="text-muted">{doc.modality}</Td>
                  <Td className="capitalize text-muted">{doc.department ?? "—"}</Td>
                  <Td>
                    <Badge tone={statusTone(doc.status)}>{doc.status}</Badge>
                  </Td>
                  <Td className="text-right tabular-nums">{doc.chunk_count}</Td>
                  <Td className="text-right tabular-nums text-muted">v{doc.version}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
