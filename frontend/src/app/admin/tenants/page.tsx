"use client";

import { AdminOnly } from "@/components/admin";
import { PageHeader, useSession } from "@/components/shell";
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

export default function AdminTenantsPage() {
  return (
    <AdminOnly>
      <TenantsView />
    </AdminOnly>
  );
}

function TenantsView() {
  const { me } = useSession();
  const metrics = useAsync(() => api.admin.metrics(), []);
  // The company record itself, not just its metrics. There is no parameter on
  // this endpoint - it returns the caller's own company and nothing else.
  const company = useAsync(() => api.admin.tenant(), []);

  return (
    <>
      <PageHeader
        title="My Company"
        description="Your company's account, its seats, and how its data is kept apart from every other company on the platform."
      />

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>{company.data?.name ?? me?.tenant_name ?? "Your company"}</CardTitle>
        </CardHeader>
        {metrics.loading || company.loading ? (
          <CardBody>
            <Skeleton className="h-20" />
          </CardBody>
        ) : metrics.error || company.error ? (
          <CardBody>
            <ErrorState
              message={metrics.error ?? company.error ?? "Could not load your company."}
              onRetry={() => {
                metrics.reload();
                company.reload();
              }}
            />
          </CardBody>
        ) : (
          <CardBody className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Stat
              label="Tenant ID"
              value={<span className="font-mono text-sm">{me?.tenant_id}</span>}
            />
            <Stat
              label="Users"
              value={company.data?.active_user_count ?? 0}
              hint={`${company.data?.admin_count ?? 0} administrator(s)`}
            />
            <Stat label="Documents" value={metrics.data!.documents} />
            <Stat label="Indexed chunks" value={metrics.data!.chunks} />
            <Stat
              label="Onboarded"
              value={
                company.data
                  ? new Date(company.data.created_at).toLocaleDateString()
                  : "—"
              }
            />
          </CardBody>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>How isolation is enforced</CardTitle>
        </CardHeader>
        <CardBody>
          <p className="mb-3 text-xs text-muted">
            The tenant is read once from the verified JWT and applied at every layer. There is
            deliberately no code path that can query tenant data without it.
          </p>
          <ul className="space-y-2 text-xs">
            {[
              ["Database", "Every tenant table has a non-nullable, indexed tenant_id, and every read goes through a repository that requires the tenant context."],
              ["Vector store", "Every Qdrant search builds its tenant filter internally. No caller can supply or override the filter object."],
              ["Cache", "Every semantic-cache and rate-limit key is namespaced by tenant, and entries are re-checked on read."],
              ["Storage", "S3 keys are prefixed with the tenant, and every read verifies the prefix before returning bytes."],
              ["Agents", "LangGraph state carries the tenant from the entry node, and the tenant is re-verified when the workflow exits."],
              ["Citations", "A citation that resolves to another tenant's document is dropped before the response is returned."],
            ].map(([layer, detail]) => (
              <li key={layer} className="rounded-md bg-bg p-2">
                <span className="font-medium text-fg">{layer}</span>
                <span className="text-muted"> — {detail}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted">
            Cross-tenant access attempts are recorded as critical security events and appear on the
            Security page.
          </p>
        </CardBody>
      </Card>
    </>
  );
}
