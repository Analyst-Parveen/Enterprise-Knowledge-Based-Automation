"use client";

import Link from "next/link";

import { PageHeader, useSession } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
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
import { DEPARTMENTS } from "@/types/api";

export default function DashboardPage() {
  const { me } = useSession();
  const docs = useAsync(() => api.documents.list({ limit: 8 }), []);
  const metrics = useAsync(
    () => (me?.role === "admin" ? api.admin.metrics() : Promise.resolve(null)),
    [me?.role],
  );

  const byDepartment = DEPARTMENTS.map((dept) => ({
    dept,
    count: (docs.data?.items ?? []).filter((d) => d.department === dept).length,
  }));

  return (
    <>
      <PageHeader
        title="Enterprise Knowledge AI"
        description="Search your company knowledge, with citations and confidence on every answer."
        action={
          <Link href="/chat">
            <Button>Ask a question</Button>
          </Link>
        }
      />

      {/* ---- search entry ------------------------------------------------ */}
      <Card className="mb-6">
        <CardBody className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-fg">Ask your knowledge base</p>
            <p className="text-xs text-muted">
              Every answer is restricted to your tenant and cites the documents it used.
            </p>
          </div>
          <Link href="/chat">
            <Button variant="secondary">Open chat</Button>
          </Link>
          <Link href="/agents">
            <Button variant="secondary">Run a workflow</Button>
          </Link>
        </CardBody>
      </Card>

      {/* ---- stats ------------------------------------------------------- */}
      <section className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {docs.loading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />)
        ) : (
          <>
            <Stat label="Documents" value={docs.data?.total ?? 0} hint="in your tenant" />
            <Stat
              label="Ready to search"
              value={(docs.data?.items ?? []).filter((d) => d.status === "ready").length}
              hint="ingestion complete"
            />
            <Stat
              label="Queries"
              value={metrics.data?.usage.total_requests ?? "—"}
              hint={me?.role === "admin" ? "all time" : "admin only"}
            />
            <Stat
              label="Estimated cost"
              value={
                metrics.data
                  ? `$${metrics.data.usage.total_estimated_cost.toFixed(4)}`
                  : "—"
              }
              hint={me?.role === "admin" ? "AI spend to date" : "admin only"}
            />
          </>
        )}
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ---- recent documents ----------------------------------------- */}
        <Card className="lg:col-span-2">
          <CardHeader className="flex items-center justify-between">
            <CardTitle>Recent documents</CardTitle>
            <Link href="/documents" className="text-xs text-accent hover:underline">
              View all
            </Link>
          </CardHeader>

          {docs.loading ? (
            <CardBody className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-8" />
              ))}
            </CardBody>
          ) : docs.error ? (
            <CardBody>
              <ErrorState message={docs.error} onRetry={docs.reload} />
            </CardBody>
          ) : (docs.data?.items.length ?? 0) === 0 ? (
            <EmptyState
              title="No documents yet"
              hint="Upload a PDF, spreadsheet, diagram or recording to build your knowledge base."
              action={
                <Link href="/documents">
                  <Button size="sm" className="mt-2">
                    Upload a document
                  </Button>
                </Link>
              }
            />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Department</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Chunks</Th>
                </tr>
              </thead>
              <tbody>
                {docs.data!.items.map((doc) => (
                  <tr key={doc.id}>
                    <Td className="max-w-[18rem] truncate">{doc.name}</Td>
                    <Td className="capitalize text-muted">{doc.department ?? "—"}</Td>
                    <Td>
                      <Badge tone={statusTone(doc.status)}>{doc.status}</Badge>
                    </Td>
                    <Td className="text-right tabular-nums">{doc.chunk_count}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        {/* ---- departments ----------------------------------------------- */}
        <Card>
          <CardHeader>
            <CardTitle>Departments</CardTitle>
          </CardHeader>
          <CardBody className="space-y-1.5">
            {byDepartment.map(({ dept, count }) => (
              <Link
                key={dept}
                href={`/departments?d=${dept}`}
                className="flex items-center justify-between rounded-md px-2 py-1.5 text-sm hover:bg-border/40"
              >
                <span className="capitalize text-fg">{dept}</span>
                <span className="tabular-nums text-muted">{count}</span>
              </Link>
            ))}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
