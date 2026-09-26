"use client";

import { useRouter } from "next/navigation";

import { Button, Card, CardBody } from "@/components/ui";

export const QUOTA_MESSAGE =
  "Your monthly AI usage limit has been reached. Please upgrade your plan to continue using AI services.";

export type TokenQuota = {
  used: number;
  limit: number | null;
  remaining: number | null;
};

export function QuotaMeter({ quota }: { quota: TokenQuota | null }) {
  if (!quota || quota.limit === null || quota.remaining === null) return null;
  return (
    <p className="text-xs text-muted">
      Used {quota.used.toLocaleString()} / Limit {quota.limit.toLocaleString()} / Remaining{" "}
      {quota.remaining.toLocaleString()}
    </p>
  );
}

export function QuotaUpgrade({ quota }: { quota: TokenQuota | null }) {
  const router = useRouter();
  return (
    <Card>
      <CardBody className="space-y-3">
        <p className="text-sm text-fg">{QUOTA_MESSAGE}</p>
        <QuotaMeter quota={quota} />
        <Button type="button" onClick={() => router.push("/billing")}>
          Upgrade plan
        </Button>
      </CardBody>
    </Card>
  );
}
