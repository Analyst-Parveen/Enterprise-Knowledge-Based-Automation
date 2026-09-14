"use client";

/**
 * Company onboarding, for the service provider.
 *
 * Two steps, in the order the hierarchy requires: create the company, then
 * invite the one person who will run it. A company with no administrator is
 * shown as unfinished rather than silently left half-built, because that is the
 * state an onboarding call actually ends in when the second step is skipped.
 *
 * No password is ever chosen here. The invitee gets a one-time password from
 * Cognito and replaces it on first sign-in, so nothing shareable exists.
 */

import {
  Building2,
  CheckCircle2,
  CircleDashed,
  Loader2,
  Mail,
  Pause,
  Play,
  Plus,
  ShieldCheck,
  UserPlus,
} from "lucide-react";
import * as React from "react";

import { PlatformAdminOnly } from "@/components/admin";
import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Input,
  Label,
  Select,
  Skeleton,
  Stat,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api, ApiClientError } from "@/lib/api";
import { DEPARTMENTS, type Department, type TenantOut } from "@/types/api";

/**
 * Mirrors services/onboarding.slugify_tenant_id so the operator sees the id
 * before committing to it. The server validates it again regardless - this is
 * a preview, not the rule.
 */
function slugify(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/^-+|-+$/g, "");
}

export default function PlatformTenantsPage() {
  return (
    <PlatformAdminOnly>
      <PageHeader
        title="Companies"
        description="Onboard a customer, invite the administrator who will run it, and watch the seats fill up. Creating a company here never grants access to its documents."
      />
      <TenantRegistry />
    </PlatformAdminOnly>
  );
}

function TenantRegistry() {
  const tenants = useAsync(() => api.platform.tenants(), []);
  const [onboarding, setOnboarding] = React.useState(false);
  const [invitingTo, setInvitingTo] = React.useState<TenantOut | null>(null);

  const items = tenants.data?.items ?? [];
  const unfinished = items.filter((t) => t.admin_count === 0);

  return (
    <div className="space-y-6">
      {/* ---- portfolio at a glance --------------------------------------- */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Companies" value={items.length} icon={Building2} />
        <Stat label="Active" value={items.filter((t) => t.is_active).length} tone="ok" />
        <Stat
          label="Awaiting an admin"
          value={unfinished.length}
          tone={unfinished.length ? "warn" : "neutral"}
        />
        <Stat
          label="Seats provisioned"
          value={items.reduce((sum, t) => sum + t.user_count, 0)}
        />
      </div>

      {unfinished.length > 0 ? (
        <Card className="border-warn/40 bg-warn/5">
          <CardBody className="flex flex-wrap items-center gap-3 text-sm">
            <CircleDashed aria-hidden className="h-4 w-4 shrink-0 text-warn" />
            <span className="text-fg">
              {unfinished.length === 1
                ? `${unfinished[0].name} has no administrator yet and nobody can sign in to it.`
                : `${unfinished.length} companies have no administrator yet and nobody can sign in to them.`}
            </span>
            <Button size="sm" variant="secondary" onClick={() => setInvitingTo(unfinished[0])}>
              <UserPlus aria-hidden className="h-3.5 w-3.5" />
              Invite {unfinished[0].name.split(" ")[0]}&apos;s admin
            </Button>
          </CardBody>
        </Card>
      ) : null}

      {/* ---- onboarding -------------------------------------------------- */}
      {onboarding ? (
        <OnboardCompany
          onDone={(created) => {
            setOnboarding(false);
            tenants.reload();
            // Step two, immediately: an unfinished company is the failure mode.
            setInvitingTo(created);
          }}
          onCancel={() => setOnboarding(false)}
        />
      ) : (
        <div className="flex justify-end">
          <Button onClick={() => setOnboarding(true)}>
            <Plus aria-hidden className="h-4 w-4" />
            Onboard a company
          </Button>
        </div>
      )}

      {invitingTo ? (
        <InviteAdministrator
          tenant={invitingTo}
          onDone={() => {
            setInvitingTo(null);
            tenants.reload();
          }}
          onCancel={() => setInvitingTo(null)}
        />
      ) : null}

      {/* ---- the registry ------------------------------------------------ */}
      <Card>
        <CardHeader>
          <CardTitle>Registry</CardTitle>
        </CardHeader>
        {tenants.loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : tenants.error ? (
          <div className="p-4">
            <ErrorState message={tenants.error} onRetry={tenants.reload} />
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            title="No companies yet"
            hint="Onboard the first one. You will be asked for its name, then for the administrator who should run it."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Company</Th>
                <Th>Tenant id</Th>
                <Th>Status</Th>
                <Th>Admins</Th>
                <Th>Users</Th>
                <Th>Onboarded</Th>
                <Th>Actions</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((tenant) => (
                <TenantRow
                  key={tenant.id}
                  tenant={tenant}
                  onInvite={() => setInvitingTo(tenant)}
                  onChanged={tenants.reload}
                />
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

function TenantRow({
  tenant,
  onInvite,
  onChanged,
}: {
  tenant: TenantOut;
  onInvite: () => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function toggleActive() {
    setBusy(true);
    setError(null);
    try {
      await api.platform.updateTenant(tenant.id, { is_active: !tenant.is_active });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Could not update the company.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr className={tenant.is_active ? undefined : "opacity-60"}>
      <Td>
        <span className="font-medium text-fg">{tenant.name}</span>
        {tenant.contact_email ? (
          <span className="block text-xs text-muted">{tenant.contact_email}</span>
        ) : null}
        {error ? <span className="block text-xs text-danger">{error}</span> : null}
      </Td>
      <Td className="font-mono text-xs">{tenant.id}</Td>
      <Td>
        <Badge tone={tenant.is_active ? "ok" : "warn"}>
          {tenant.is_active ? "Active" : "Suspended"}
        </Badge>
      </Td>
      <Td>
        {tenant.admin_count > 0 ? (
          <span className="inline-flex items-center gap-1.5 text-ok">
            <CheckCircle2 aria-hidden className="h-3.5 w-3.5" />
            {tenant.admin_count}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-warn">
            <CircleDashed aria-hidden className="h-3.5 w-3.5" />
            none
          </span>
        )}
      </Td>
      <Td>
        {tenant.active_user_count}
        {tenant.user_count !== tenant.active_user_count ? (
          <span className="text-muted"> / {tenant.user_count}</span>
        ) : null}
      </Td>
      <Td className="whitespace-nowrap text-muted">
        {new Date(tenant.created_at).toLocaleDateString()}
      </Td>
      <Td>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={onInvite} disabled={!tenant.is_active}>
            <UserPlus aria-hidden className="h-3.5 w-3.5" />
            Invite admin
          </Button>
          <Button size="sm" variant="ghost" onClick={toggleActive} disabled={busy}>
            {busy ? (
              <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
            ) : tenant.is_active ? (
              <Pause aria-hidden className="h-3.5 w-3.5" />
            ) : (
              <Play aria-hidden className="h-3.5 w-3.5" />
            )}
            {tenant.is_active ? "Suspend" : "Reactivate"}
          </Button>
        </div>
      </Td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// step 1: the company
// ---------------------------------------------------------------------------
function OnboardCompany({
  onDone,
  onCancel,
}: {
  onDone: (created: TenantOut) => void;
  onCancel: () => void;
}) {
  const [name, setName] = React.useState("");
  const [tenantId, setTenantId] = React.useState("");
  const [contactEmail, setContactEmail] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  // The id follows the name until the operator types their own.
  const [idEdited, setIdEdited] = React.useState(false);
  const effectiveId = idEdited ? tenantId : slugify(name);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.platform.createTenant({
        name: name.trim(),
        tenant_id: effectiveId || undefined,
        contact_email: contactEmail.trim() || null,
      });
      onDone(created);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Could not create the company.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="border-accent/40">
      <CardHeader>
        <CardTitle>Step 1 of 2 — the company</CardTitle>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Label htmlFor="company-name">Registered company name</Label>
              <Input
                id="company-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Infinity Assurance Solutions Private Limited"
                required
              />
            </div>
            <div>
              <Label htmlFor="tenant-id">Tenant id</Label>
              <Input
                id="tenant-id"
                value={effectiveId}
                onChange={(e) => {
                  setIdEdited(true);
                  setTenantId(e.target.value);
                }}
                placeholder="infinity-assurance"
                required
              />
              <p className="mt-1 text-[11px] leading-relaxed text-muted">
                Permanent. It becomes this company&apos;s storage prefix, retrieval filter and
                cache namespace, so it cannot be changed later.
              </p>
            </div>
            <div>
              <Label htmlFor="contact-email">Primary contact (optional)</Label>
              <Input
                id="contact-email"
                type="email"
                value={contactEmail}
                onChange={(e) => setContactEmail(e.target.value)}
                placeholder="admin@infinity.example"
              />
              <p className="mt-1 text-[11px] leading-relaxed text-muted">
                Recorded on the account. Not a credential and not a login.
              </p>
            </div>
          </div>

          {error ? (
            <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">
              {error}
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !name.trim() || !effectiveId}>
              {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
              Create and continue
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// step 2: the administrator
// ---------------------------------------------------------------------------
function InviteAdministrator({
  tenant,
  onDone,
  onCancel,
}: {
  tenant: TenantOut;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [email, setEmail] = React.useState(tenant.contact_email ?? "");
  const [displayName, setDisplayName] = React.useState("");
  const [department, setDepartment] = React.useState<Department | "">("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [sent, setSent] = React.useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.platform.inviteTenantAdmin(tenant.id, {
        email: email.trim(),
        display_name: displayName.trim() || null,
        department: department || null,
      });
      setSent(result.message);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Could not send the invitation.");
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <Card className="border-ok/40 bg-ok/5">
        <CardBody className="space-y-3 text-sm">
          <p className="flex items-center gap-2 font-medium text-fg">
            <CheckCircle2 aria-hidden className="h-4 w-4 text-ok" />
            {tenant.name} is ready
          </p>
          <p className="text-muted">{sent}</p>
          <p className="flex items-start gap-2 text-xs text-muted">
            <ShieldCheck aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              You never see their password. They set their own on first sign-in, and from then
              on they manage their own company&apos;s users without you.
            </span>
          </p>
          <Button size="sm" onClick={onDone}>
            Done
          </Button>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card className="border-accent/40">
      <CardHeader>
        <CardTitle>Step 2 of 2 — {tenant.name}&apos;s administrator</CardTitle>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="admin-email">Work email</Label>
              <div className="relative">
                <Mail
                  aria-hidden
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
                />
                <Input
                  id="admin-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@infinity.example"
                  className="pl-9"
                  required
                />
              </div>
            </div>
            <div>
              <Label htmlFor="admin-name">Full name (optional)</Label>
              <Input
                id="admin-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Infinity Admin"
              />
            </div>
            <div>
              <Label htmlFor="admin-department">Department (optional)</Label>
              <Select
                id="admin-department"
                value={department}
                onChange={(e) => setDepartment(e.target.value as Department | "")}
              >
                <option value="">—</option>
                {DEPARTMENTS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <p className="rounded-lg bg-surface-2 px-3 py-2 text-xs leading-relaxed text-muted">
            They will be created as this company&apos;s <strong className="text-fg">admin</strong>.
            The role is not selectable: this screen issues exactly one kind of account, so a
            platform operator cannot be created here by mistake.
          </p>

          {error ? (
            <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger">
              {error}
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !email.trim()}>
              {busy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
              Send invitation
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
              Later
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
