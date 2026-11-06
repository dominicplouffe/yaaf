import { expect, test } from "@playwright/test";
import { failOnPageErrors } from "./desk";

/* App-wide Settings on the demo install, which holds no provider key: it lands
   on what needs you, every rail entry states its section's state rather than
   describing it, each section opens without an error, and an old link still
   lands where it did (docs/ux-redesign/settings). */

const SECTIONS: [rail: RegExp, heading: string][] = [
  [/^Providers/, "Providers"], [/^Models/, "Models"], [/^Model prices/, "Model prices"],
  [/^Spend/, "Spend"], [/^Web search/, "Web search"], [/^Founder/, "Founder"], [/^Slack/, "Slack"],
  [/^People/, "People"], [/^Account/, "Account"],
];

test("settings lands on what needs you, and every section opens", async ({ page }) => {
  const errors = failOnPageErrors(page);
  await page.goto("/app/#/settings");
  const main = page.locator(".set-main");
  await expect(main.getByRole("heading", { name: "Overview", level: 2 })).toBeVisible();
  await expect(main).toContainText("No provider is connected yet");
  const rail = page.getByRole("navigation", { name: "Settings sections" });
  // A state, not a description of the section.
  await expect(rail.getByRole("button", { name: /^Providers/ })).toContainText("None connected");
  for (const [entry, heading] of SECTIONS) {
    await rail.getByRole("button", { name: entry }).click();
    await expect(main.getByRole("heading", { name: heading, level: 2 })).toBeVisible();
    await expect(page.getByText("Loading…").first()).toBeHidden();
  }
  // "credentials" is what Providers was called; refusal texts still say it.
  await page.goto("/app/#/settings?section=credentials");
  await expect(main.getByRole("heading", { name: "Providers", level: 2 })).toBeVisible();
  expect(errors).toEqual([]);
});

test("a settings switch is held with Undo before it is sent", async ({ page }) => {
  await page.goto("/app/#/settings?section=models");
  const toggle = page.getByRole("switch", { name: /Re-read model lists/ });
  const before = await toggle.getAttribute("aria-checked");
  await toggle.click();
  const toast = page.locator(".undo-toast");
  await expect(toast).toBeVisible();
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(toggle).toHaveAttribute("aria-checked", before ?? "true");
});
