/**
 * End-to-end user journeys.
 *
 * Requires the backend and the seeded demo data to be running:
 *   docker compose -f infra/docker/docker-compose.yml up -d
 *   cd backend && alembic upgrade head && python -m seeds.seed
 *   E2E_USER_TOKEN=$(cd backend && python -m seeds.dev_token 2>/dev/null)
 *
 * Journeys without a token still run and assert the auth gate itself.
 */

import { expect, test, type Page } from "@playwright/test";

const USER_TOKEN = process.env.E2E_USER_TOKEN ?? "";
const ADMIN_TOKEN = process.env.E2E_ADMIN_TOKEN ?? "";

async function signIn(page: Page, token: string) {
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Enterprise Knowledge AI" })).toBeVisible({
    timeout: 15_000,
  });
}

test.describe("authentication", () => {
  test("unauthenticated visitor sees the sign-in gate", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByLabel("Access token")).toBeVisible();
  });

  test("an invalid token is rejected", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Access token").fill("not.a.real.token");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByLabel("Access token")).toBeVisible();
  });
});

test.describe("user journeys", () => {
  test.skip(!USER_TOKEN, "E2E_USER_TOKEN is not set");

  test.beforeEach(async ({ page }) => {
    await signIn(page, USER_TOKEN);
  });

  test("dashboard renders seeded data, not an empty shell", async ({ page }) => {
    await expect(page.getByText("Documents").first()).toBeVisible();
    await expect(page.getByText("Recent documents")).toBeVisible();
    await expect(page.getByText("Departments").first()).toBeVisible();
  });

  test("chat returns an answer with the full response envelope", async ({ page }) => {
    await page.getByRole("link", { name: "Knowledge Chat" }).click();
    await page.getByLabel("Your question").fill("What is the domestic hotel limit?");
    await page.getByRole("button", { name: "Ask" }).click();

    // Latency, tokens and cost are all part of the visible contract.
    await expect(page.getByText(/ms$/).first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/tok$/).first()).toBeVisible();
    await expect(page.getByText(/^\$0\./).first()).toBeVisible();
  });

  test("documents page lists the knowledge base", async ({ page }) => {
    await page.getByRole("link", { name: "Documents" }).first().click();
    await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
    await expect(page.getByText(/documents$/)).toBeVisible();
  });

  test("departments page shows all seven departments", async ({ page }) => {
    await page.getByRole("link", { name: "Departments" }).click();
    for (const dept of ["hr", "finance", "legal", "sales", "marketing", "operations", "technical"]) {
      await expect(page.getByText(dept, { exact: true }).first()).toBeVisible();
    }
  });

  test("workflows page lists the agentic workflows", async ({ page }) => {
    await page.getByRole("link", { name: "Workflows" }).click();
    await expect(page.getByLabel("Workflow")).toBeVisible();
  });

  test("feedback can be submitted", async ({ page }) => {
    await page.getByRole("link", { name: "Feedback" }).click();
    await page.getByLabel("Details").fill("Playwright end-to-end check.");
    await page.getByRole("button", { name: "Send feedback" }).click();
    await expect(page.getByText(/your feedback was recorded/i)).toBeVisible({ timeout: 15_000 });
  });

  test("a non-admin cannot reach admin pages", async ({ page }) => {
    await page.goto("/admin/metrics");
    await expect(page.getByText(/administrator access required/i)).toBeVisible();
  });

  test("signing out returns to the gate", async ({ page }) => {
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByLabel("Access token")).toBeVisible();
  });
});

test.describe("admin journeys", () => {
  test.skip(!ADMIN_TOKEN, "E2E_ADMIN_TOKEN is not set");

  test.beforeEach(async ({ page }) => {
    await signIn(page, ADMIN_TOKEN);
  });

  test("admin navigation is visible", async ({ page }) => {
    await expect(page.getByText("Admin", { exact: true })).toBeVisible();
  });

  test("AI metrics render", async ({ page }) => {
    await page.goto("/admin/metrics");
    await expect(page.getByRole("heading", { name: "AI Metrics" })).toBeVisible();
    await expect(page.getByText("Estimated cost")).toBeVisible();
  });

  test("audit log renders", async ({ page }) => {
    await page.goto("/admin/audit");
    await expect(page.getByRole("heading", { name: "Audit Logs" })).toBeVisible();
  });

  test("security page renders", async ({ page }) => {
    await page.goto("/admin/security");
    await expect(page.getByRole("heading", { name: "Security" })).toBeVisible();

    // exact: true is required here. getByText matches case-insensitive
    // substrings by default, so a bare "Critical" also matches the lowercase
    // "critical" severity badge in the event table and trips strict mode -
    // passing or failing depending on which rows happen to be rendered.
    await expect(page.getByText("Critical", { exact: true })).toBeVisible();
    await expect(page.getByText("Errors", { exact: true })).toBeVisible();
  });

  test("deployments page explains blue-green", async ({ page }) => {
    await page.goto("/admin/deployments");
    await expect(page.getByText(/blue-green release/i)).toBeVisible();
  });
});
