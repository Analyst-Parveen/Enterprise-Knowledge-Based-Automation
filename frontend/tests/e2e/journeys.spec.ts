/**
 * End-to-end user journeys.
 *
 * Requires the backend and the seeded demo data to be running:
 *   docker compose -f infra/docker/docker-compose.yml up -d
 *   cd backend && alembic upgrade head && python -m seeds.seed
 *   E2E_USER_TOKEN=$(cd backend && python -m seeds.dev_token 2>/dev/null)
 *
 * Journeys without a token still run and assert the auth gate itself.
 *
 * Two sign-in paths exist and both are exercised here. Real users sign in with
 * an email and a password; the token box is a developer affordance that only
 * renders against a local API. The password journeys assert the gate and the
 * error handling without needing a seeded Cognito account, so they run
 * unconditionally.
 */

import { expect, test, type Page } from "@playwright/test";

const USER_TOKEN = process.env.E2E_USER_TOKEN ?? "";
const ADMIN_TOKEN = process.env.E2E_ADMIN_TOKEN ?? "";
const PLATFORM_TOKEN = process.env.E2E_PLATFORM_TOKEN ?? "";

/** The developer token box lives behind a disclosure and must be opened. */
async function signIn(page: Page, token: string) {
  await page.goto("/");
  await page.getByText("Developer sign-in").click();
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Use token" }).click();
  await expect(page.getByRole("heading", { name: "Enterprise Knowledge AI" })).toBeVisible({
    timeout: 15_000,
  });
}

async function openDevSignIn(page: Page) {
  await page.goto("/");
  await page.getByText("Developer sign-in").click();
  await expect(page.getByLabel("Access token")).toBeVisible();
}

test.describe("authentication", () => {
  test("unauthenticated visitor sees the sign-in gate", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByLabel("Work email")).toBeVisible();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
  });

  test("the gate asks for a password, not a token", async ({ page }) => {
    await page.goto("/");
    // The token box exists for local development, but it is not the offer made
    // to a user: it is collapsed behind a disclosure.
    await expect(page.getByLabel("Access token")).toBeHidden();
    await expect(page.getByText(/accounts are created by invitation only/i)).toBeVisible();
  });

  test("wrong credentials are refused without revealing which half was wrong", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Work email").fill("nobody@example.com");
    await page.getByLabel("Password", { exact: true }).fill("WrongPassword!2026");
    await page.getByRole("button", { name: "Sign in" }).click();

    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible({ timeout: 20_000 });
    // "Incorrect email or password" - never "no such user".
    await expect(alert).not.toContainText(/no such|not found|unknown user/i);
    await expect(page.getByLabel("Work email")).toBeVisible();
  });

  test("password recovery can be reached and returns to sign-in", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Forgot your password?" }).click();
    await expect(page.getByRole("heading", { name: "Reset your password" })).toBeVisible();
    await page.getByRole("button", { name: /back to sign in/i }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("an invalid token is rejected", async ({ page }) => {
    await openDevSignIn(page);
    await page.getByLabel("Access token").fill("not.a.real.token");
    await page.getByRole("button", { name: "Use token" }).click();
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

  test("a non-admin cannot reach platform pages", async ({ page }) => {
    await page.goto("/platform/tenants");
    await expect(page.getByText(/platform access required/i)).toBeVisible();
  });

  test("a non-admin is offered no administration navigation", async ({ page }) => {
    await expect(page.getByRole("link", { name: "Users" })).toBeHidden();
    await expect(page.getByRole("link", { name: "Companies" })).toBeHidden();
  });

  test("signing out returns to the gate", async ({ page }) => {
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("a signed-out session cannot be restored by going back", async ({ page }) => {
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });
});

test.describe("admin journeys", () => {
  test.skip(!ADMIN_TOKEN, "E2E_ADMIN_TOKEN is not set");

  test.beforeEach(async ({ page }) => {
    await signIn(page, ADMIN_TOKEN);
  });

  test("admin navigation is visible", async ({ page }) => {
    await expect(page.getByText("Company admin", { exact: true })).toBeVisible();
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

  test("the company admin manages its own users", async ({ page }) => {
    await page.goto("/admin/users");
    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Invite a user" })).toBeVisible();
  });

  test("the invite form offers no platform role", async ({ page }) => {
    await page.goto("/admin/users");
    await page.getByRole("button", { name: "Invite a user" }).click();

    const roles = page.getByLabel("Role", { exact: true });
    await expect(roles).toBeVisible();
    await expect(roles.locator("option")).toHaveCount(2);
    await expect(roles.locator("option[value='platform_admin']")).toHaveCount(0);
  });

  test("a company admin cannot create a company", async ({ page }) => {
    await page.goto("/platform/tenants");
    await expect(page.getByText(/platform access required/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "Onboard a company" })).toBeHidden();
  });

  test("my company shows the tenant, not a tenant list", async ({ page }) => {
    await page.goto("/admin/tenants");
    await expect(page.getByRole("heading", { name: "My Company" })).toBeVisible();
    await expect(page.getByText("Tenant ID")).toBeVisible();
  });
});

test.describe("platform journeys", () => {
  test.skip(!PLATFORM_TOKEN, "E2E_PLATFORM_TOKEN is not set");

  test.beforeEach(async ({ page }) => {
    await signIn(page, PLATFORM_TOKEN);
  });

  test("the platform operator sees the company registry", async ({ page }) => {
    await page.goto("/platform/tenants");
    await expect(page.getByRole("heading", { name: "Companies" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Onboard a company" })).toBeVisible();
  });

  test("onboarding previews the tenant id from the company name", async ({ page }) => {
    await page.goto("/platform/tenants");
    await page.getByRole("button", { name: "Onboard a company" }).click();
    await page
      .getByLabel("Registered company name")
      .fill("Infinity Assurance Solutions Private Limited");

    await expect(page.getByLabel("Tenant id")).toHaveValue(
      "infinity-assurance-solutions-private-limited",
    );
  });

  test("the onboarding trail is scoped to lifecycle events", async ({ page }) => {
    await page.goto("/platform/audit");
    await expect(page.getByRole("heading", { name: "Onboarding Trail" })).toBeVisible();
    await expect(page.getByText(/lifecycle events only/i)).toBeVisible();
  });

  test("a platform operator does not manage a company's users", async ({ page }) => {
    await page.goto("/admin/users");
    await expect(page.getByText(/company administrator access required/i)).toBeVisible();
  });
});
