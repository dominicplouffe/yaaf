import { expect, Page, test } from "@playwright/test";
import { failOnPageErrors, row } from "./desk";

/* The core flows, through the screen, on the demo portfolio. Each one ends by
   asking the server whether the decision landed, because a card vanishing
   proves only that the screen hid it. Each acts on its own project, so their
   order does not matter. */

const FILINGS = "Read The Filings";

/** Decisions are held for a few seconds with an Undo before they are sent
    (web/src/undo.tsx); wait for the request itself, not the click. */
function sent(page: Page, path: string) {
  return page.waitForResponse(
    (r) => r.url().includes(path) && r.request().method() === "POST",
    { timeout: 20_000 });
}

test("answer the CEO's question with the option it recommends", async ({ page }) => {
  const errors = failOnPageErrors(page);
  const filings = await row(page.request, FILINGS);
  expect(filings.pending_asks.map((a) => a.title)).toContain("Which fiscal year should be the baseline?");

  await page.goto(`/app/#/projects/${filings.id}`);
  const card = page.locator(".ov-dcard").filter({ hasText: "Which fiscal year should be the baseline?" });
  await expect(card).toBeVisible();
  // The recommendation is said once, in its own box — not again in the question.
  await expect(card.locator(".sg-rec")).toHaveCount(1);
  await expect(card).not.toContainText("What I recommend:");

  const answered = sent(page, `/admin/projects/${filings.id}/boardroom`);
  await card.getByRole("button", { name: /Each filer's own FY2019 · recommended/ }).click();
  await expect(card).toHaveCount(0);
  expect((await answered).ok()).toBeTruthy();
  // The janitor marks an ask answered on its next pass once he has spoken.
  await expect.poll(async () => (await row(page.request, FILINGS)).pending_asks.length,
    { timeout: 60_000, intervals: [2_000] }).toBe(0);
  expect(errors).toEqual([]);
});

test("approve a spend from the project page, past the money check", async ({ page }) => {
  const errors = failOnPageErrors(page);
  const filings = await row(page.request, FILINGS);
  const approval = filings.pending_approvals.find((a) => a.title.startsWith("Buy 12 months of EDGAR"));
  expect(approval?.recommendation).toMatch(/^Approve it/);

  await page.goto(`/app/#/projects/${filings.id}`);
  const card = page.locator(".ov-dcard").filter({ hasText: "Buy 12 months of EDGAR" });
  await expect(card.locator(".sg-rec")).toContainText(approval!.recommendation!);

  await card.getByRole("button", { name: "Approve $75" }).click();
  // Money that leaves cannot be called back, so the one yes asks first.
  await expect(card).toContainText("a purchase cannot be taken back");
  const decided = sent(page, `/admin/approvals/${approval!.id}/decide`);
  await card.getByRole("button", { name: "Approve $75" }).click();
  expect((await decided).ok()).toBeTruthy();
  await expect.poll(async () => (await row(page.request, FILINGS)).pending_approvals
    .some((a) => a.id === approval!.id)).toBe(false);
  expect(errors).toEqual([]);
});

test("accept the proposed scope from its decision card", async ({ page }) => {
  const errors = failOnPageErrors(page);
  const argument = await row(page.request, "Settle An Argument");
  const proposed = argument.scope_items?.length ?? 0;
  expect(proposed).toBeGreaterThan(0);

  await page.goto(`/app/#/projects/${argument.id}`);
  const card = page.locator(".ov-dcard").filter({ hasText: "Proposed scope" });
  const accept = card.getByRole("button", { name: /^Accept (all )?\d+ outcomes?$/ });
  await expect(accept).toBeVisible();
  await accept.click();
  await expect.poll(async () => (await row(page.request, "Settle An Argument")).scope_items?.length ?? 0)
    .toBe(0);
  await expect(page.locator(".ov-dcard").filter({ hasText: "Proposed scope" })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("complete a finished delivery from its sign-off", async ({ page }) => {
  const errors = failOnPageErrors(page);
  const first = await row(page.request, "Your First Hour");
  expect(first.pending_signoff).not.toBeNull();

  await page.goto(`/app/#/projects/${first.id}`);
  const card = page.locator(".ov-dcard").filter({ hasText: "Completing writes the receipt" });
  await card.getByRole("button", { name: "Complete & write receipt…" }).click();
  const done = sent(page, `/admin/projects/${first.id}/complete`);
  await card.getByRole("button", { name: "Yes, complete it" }).click();
  expect((await done).ok()).toBeTruthy();
  await expect.poll(async () => (await row(page.request, "Your First Hour")).status).toBe("completed");
  expect(errors).toEqual([]);
});

test("start a draft project from its page", async ({ page }) => {
  const errors = failOnPageErrors(page);
  const red = await row(page.request, "A Red Team That Can Block");
  expect(red.status).toBe("draft");

  await page.goto(`/app/#/projects/${red.id}`);
  const started = sent(page, `/admin/projects/${red.id}/start`);
  await page.getByRole("button", { name: "Start", exact: true }).click();
  expect((await started).ok()).toBeTruthy();
  await expect.poll(async () => (await row(page.request, "A Red Team That Can Block")).status)
    .toBe("running");
  await expect(page.getByRole("button", { name: "Start", exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

/* Every screen a chairman reaches from the nav, on a project with weeks of
   history: none may throw while rendering. There is no error boundary, so a
   render that throws is a blank page and this is what notices. */
test("every page renders on a project with history", async ({ page }) => {
  const errors = failOnPageErrors(page);
  const id = (await row(page.request, "Tell Me What Changed")).id;
  const routes = [
    "/home", "/inbox", "/inbox?tab=updates", "/projects", "/projects/new", "/roles", "/settings",
    `/projects/${id}`, `/projects/${id}/results`, `/projects/${id}/chat`, `/projects/${id}/team`,
    `/projects/${id}/settings`, `/projects/${id}/details/roadmap`, `/projects/${id}/details/tasks`,
    `/projects/${id}/details/deliverables`, `/projects/${id}/details/spend`,
    `/projects/${id}/details/reviews`, `/projects/${id}/details/activity`,
  ];
  for (const route of routes) {
    await page.goto(`/app/#${route}`);
    await expect(page.locator("main"), route).not.toBeEmpty();
    await expect(page.getByText("Loading…").first(), route).toBeHidden();
    expect(errors, route).toEqual([]);
  }
});
