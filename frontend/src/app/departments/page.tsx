"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  ErrorState,
  Skeleton,
  statusTone,
} from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { DEPARTMENTS } from "@/types/api";

function DepartmentsView() {
  const params = useSearchParams();
  const selected = params.get("d");
  const docs = useAsync(() => api.documents.list({ limit: 200 }), []);

  const grouped = DEPARTMENTS.map((dept) => ({
    dept,
    docs: (docs.data?.items ?? []).filter((d) => d.department === dept),
  }));

  return (
    <>
      <PageHeader
        title="Departments"
        description="Knowledge is organised by department. Retrieval can be scoped to one of them."
      />

      {docs.loading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : docs.error ? (
        <ErrorState message={docs.error} onRetry={docs.reload} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {grouped.map(({ dept, docs: items }) => (
            <Card key={dept} className={selected === dept ? "ring-1 ring-accent" : undefined}>
              <CardHeader className="flex items-center justify-between">
                <CardTitle className="capitalize">{dept}</CardTitle>
                <Badge tone={items.length ? "accent" : "neutral"}>{items.length}</Badge>
              </CardHeader>
              <CardBody>
                {items.length === 0 ? (
                  <p className="text-xs text-muted">No documents assigned yet.</p>
                ) : (
                  <ul className="space-y-1">
                    {items.slice(0, 4).map((doc) => (
                      <li key={doc.id} className="flex items-center justify-between gap-2 text-xs">
                        <span className="truncate text-fg">{doc.name}</span>
                        <Badge tone={statusTone(doc.status)}>{doc.status}</Badge>
                      </li>
                    ))}
                    {items.length > 4 ? (
                      <li className="text-xs text-muted">+{items.length - 4} more</li>
                    ) : null}
                  </ul>
                )}
                <Link href="/chat" className="mt-3 inline-block text-xs text-accent hover:underline">
                  Ask about {dept} →
                </Link>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

export default function DepartmentsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <DepartmentsView />
    </Suspense>
  );
}
