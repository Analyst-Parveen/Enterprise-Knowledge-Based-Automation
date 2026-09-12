"use client";

/**
 * User management for one company.
 *
 * Everything here is scoped to the admin's own company, and not because this
 * page filters it: the API takes the tenant from the verified token and offers
 * no parameter for anyone else's. There is no company selector to build.
 *
 * Two lockouts are refused rather than warned about - an admin changing its own
 * role or status, and any change that would leave the company with no active
 * administrator - so a company can never end a session unable to administer
 * itself.
 */

import {
  CheckCircle2,
  Clock,
  KeyRound,
  Loader2,
  Mail,
  ShieldCheck,
  UserMinus,
  UserPlus,
  Users,
} from "lucide-react";
import * as React from "react";

import { TenantAdminOnly } from "@/components/admin";
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
import {
  DEPARTMENTS,
  ROLE_LABELS,
  type Department,
  type TenantAssignableRole,
  type UserOut,
} from "@/types/api";

export default function AdminUsersPage() {
  return (
    <TenantAdminOnly>
      <UsersView />
    </TenantAdminOnly>
  );
}

function UsersView() {
  const { me } = useSession();
  const users = useAsync(() => api.admin.users.list(), []);
  const [inviting, setInviting] = React.useState(false);

  const items = users.data?.items ?? [];
  const activeAdmins = items.filter((u) => u.role === "admin" && u.is_active).length;
  const pending = items.filter((u) => !u.last_login_at && u.is_active).length;

  return (
    <>
      <PageHeader
        title="Users"
        description={`Invite and manage the people in ${me?.tenant_name ?? "your company"}. Everyone you create here belongs to your company and no other.`}
        action={
          <Button onClick={() => setInviting((open) => !open)}>
            <UserPlus aria-hidden className="h-4 w-4" />
            Invite a user
          </Button>
        }
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Users" value={items.length} icon={Users} />
        <Stat label="Active" value={items.filter((u) => u.is_active).length} />
        <Stat label="Administrators" value={activeAdmins} icon={ShieldCheck} iconTone="violet" />
        <Stat
          label="Yet to sign in"
          value={pending}
          icon={Clock}
          iconTone={pending ? "warn" : "accent"}
          hint={pending ? "Invitations sent, first sign-in pending" : undefined}
        />
      </div>

      {inviting ? (
        <div className="mb-6">
          <InviteUser
            onDone={() => {
              setInviting(false);
              users.reload();
            }}
            onCancel={() => setInviting(false)}
          />
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>{me?.tenant_name ?? "Your company"}</CardTitle>
        </CardHeader>
        {users.loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : users.error ? (
          <div className="p-4">
            <ErrorState message={users.error} onRetry={users.reload} />
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            title="No users yet"
            hint="Invite your first colleague. They receive a one-time password by email and choose their own on first sign-in."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Person</Th>
                <Th>Role</Th>
                <Th>Department</Th>
                <Th>Status</Th>
                <Th>Last sign-in</Th>
                <Th>Actions</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((user) => (
                <UserRow
                  key={user.id}
                  user={user}
                  isSelf={user.id === me?.user_id || user.email === me?.email}
                  lastActiveAdmin={user.role === "admin" && user.is_active && activeAdmins <= 1}
                  onChanged={users.reload}
                />
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card className="mt-6">
        <CardBody className="space-y-2 text-xs leading-relaxed text-muted">
          <p>
            <strong className="text-fg">You never handle a password.</strong> Amazon Cognito
            emails a one-time password and the person replaces it on first sign-in, so there is
            nothing to share and nothing to leak.
          </p>
          <p>
            <strong className="text-fg">The role list stops at admin.</strong> A company admin can
            create members and fellow admins. Platform roles are not offered here and are
            rejected by the API even if the request is crafted by hand.
          </p>
          <p>
            <strong className="text-fg">Deactivating is immediate.</strong> The account is
            disabled and every live token for it is revoked, rather than working until it
            happens to expire.
          </p>
        </CardBody>
      </Card>
    </>
  );
}

function UserRow({
  user,
  isSelf,
  lastActiveAdmin,
  onChanged,
}: {
  user: UserOut;
  isSelf: boolean;
  lastActiveAdmin: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  // The API refuses both of these too. Disabling them here just avoids
  // offering an action that is certain to fail.
  const locked = isSelf || lastActiveAdmin;

  async function run(action: string, work: () => Promise<unknown>, message?: string) {
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      await work();
      if (message) setNotice(message);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "That change did not apply.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <tr className={user.is_active ? undefined : "opacity-60"}>
      <Td>
        <span className="font-medium text-fg">{user.display_name ?? user.email}</span>
        <span className="block text-xs text-muted">{user.email}</span>
        {error ? <span className="block text-xs text-danger">{error}</span> : null}
        {notice ? <span className="block text-xs text-ok">{notice}</span> : null}
      </Td>
      <Td>
        <Select
          aria-label={`Role for ${user.email}`}
          value={user.role}
          disabled={locked || busy !== null}
          onChange={(e) =>
            void run("role", () =>
              api.admin.users.update(user.id, {
                role: e.target.value as TenantAssignableRole,
              }),
            )
          }
          className="max-w-[11rem]"
        >
          <option value="user">{ROLE_LABELS.user}</option>
          <option value="admin">{ROLE_LABELS.admin}</option>
        </Select>
      </Td>
      <Td>
        <Select
          aria-label={`Department for ${user.email}`}
          value={user.department ?? ""}
          disabled={busy !== null}
          onChange={(e) =>
            void run("department", () =>
              api.admin.users.update(user.id, {
                department: (e.target.value || null) as Department | null,
              }),
            )
          }
          className="max-w-[10rem]"
        >
          <option value="">—</option>
          {DEPARTMENTS.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </Select>
      </Td>
      <Td>
        {!user.is_active ? (
          <Badge tone="warn">Deactivated</Badge>
        ) : user.last_login_at ? (
          <Badge tone="ok">Active</Badge>
        ) : (
          <Badge tone="info">Invited</Badge>
        )}
      </Td>
      <Td className="whitespace-nowrap text-muted">
        {user.last_login_at ? new Date(user.last_login_at).toLocaleDateString() : "—"}
      </Td>
      <Td>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={isSelf || busy !== null}
            title={isSelf ? "Use 'Forgot your password?' on the sign-in page instead" : undefined}
            onClick={() =>
              void run(
                "reset",
                () => api.admin.users.resetPassword(user.id),
                "A one-time password is on its way to them.",
              )
            }
          >
            {busy === "reset" ? (
              <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <KeyRound aria-hidden className="h-3.5 w-3.5" />
            )}
            Reset password
          </Button>
          <Button
            size="sm"
            variant={user.is_active ? "ghost" : "secondary"}
            disabled={locked || busy !== null}
            title={
              isSelf
                ? "You cannot deactivate your own account"
                : lastActiveAdmin
                  ? "This is the only active administrator - promote someone else first"
                  : undefined
            }
            onClick={() =>
              void run("status", () =>
                api.admin.users.update(user.id, { is_active: !user.is_active }),
              )
            }
          >
            {busy === "status" ? (
              <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
            ) : user.is_active ? (
              <UserMinus aria-hidden className="h-3.5 w-3.5" />
            ) : (
              <CheckCircle2 aria-hidden className="h-3.5 w-3.5" />
            )}
            {user.is_active ? "Deactivate" : "Reactivate"}
          </Button>
        </div>
      </Td>
    </tr>
  );
}

function InviteUser({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [email, setEmail] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [role, setRole] = React.useState<TenantAssignableRole>("user");
  const [department, setDepartment] = React.useState<Department | "">("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [sent, setSent] = React.useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.admin.users.invite({
        email: email.trim(),
        display_name: displayName.trim() || null,
        role,
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
            Invitation sent
          </p>
          <p className="text-muted">{sent}</p>
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
        <CardTitle>Invite a user</CardTitle>
      </CardHeader>
      <CardBody>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <Label htmlFor="invite-email">Work email</Label>
              <div className="relative">
                <Mail
                  aria-hidden
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
                />
                <Input
                  id="invite-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="hr@company.com"
                  className="pl-9"
                  required
                />
              </div>
            </div>
            <div>
              <Label htmlFor="invite-name">Full name (optional)</Label>
              <Input
                id="invite-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="HR User"
              />
            </div>
            <div>
              <Label htmlFor="invite-role">Role</Label>
              <Select
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value as TenantAssignableRole)}
              >
                <option value="user">{ROLE_LABELS.user}</option>
                <option value="admin">{ROLE_LABELS.admin}</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="invite-department">Department</Label>
              <Select
                id="invite-department"
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
              Cancel
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
