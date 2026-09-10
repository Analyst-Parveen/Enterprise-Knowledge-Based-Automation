"use client";

import { AdminOnly } from "@/components/admin";
import { PageHeader, useSession } from "@/components/shell";
import { Badge, Card, CardBody, CardHeader, CardTitle, Table, Td, Th } from "@/components/ui";

export default function AdminUsersPage() {
  return (
    <AdminOnly>
      <UsersView />
    </AdminOnly>
  );
}

function UsersView() {
  const { me } = useSession();

  return (
    <>
      <PageHeader
        title="Users"
        description="Identities are managed in Amazon Cognito. This page shows how they map into the platform."
      />

      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Your session</CardTitle>
        </CardHeader>
        <Table>
          <thead>
            <tr>
              <Th>User</Th>
              <Th>Email</Th>
              <Th>Role</Th>
              <Th>Tenant</Th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <Td className="font-mono text-xs">{me?.user_id}</Td>
              <Td>{me?.email ?? "—"}</Td>
              <Td>
                <Badge tone={me?.role === "admin" ? "accent" : "neutral"}>{me?.role}</Badge>
              </Td>
              <Td className="font-mono text-xs">{me?.tenant_id}</Td>
            </tr>
          </tbody>
        </Table>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>How identity works here</CardTitle>
        </CardHeader>
        <CardBody className="space-y-3 text-xs text-muted">
          <p>
            <strong className="text-fg">Cognito is the source of truth.</strong> Users are created
            in the Cognito user pool with two custom attributes:{" "}
            <code className="rounded bg-border/50 px-1">custom:tenant_id</code> and{" "}
            <code className="rounded bg-border/50 px-1">custom:role</code>.
          </p>
          <p>
            <strong className="text-fg">The token is the only authority.</strong> The backend reads
            the tenant and role from the verified JWT and ignores any value a client sends in a
            body, header or query string.
          </p>
          <p>
            <strong className="text-fg">Admin does not mean cross-tenant.</strong> An administrator
            gets operational metrics and audit logs for their own tenant. No admin role in this
            system can read another tenant&apos;s documents.
          </p>
          <p>
            User creation and role changes are done in Cognito (console or CLI), not here — this
            keeps a single identity authority rather than two that can drift apart.
          </p>
        </CardBody>
      </Card>
    </>
  );
}
