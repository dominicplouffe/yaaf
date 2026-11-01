import { expect, test as setup } from "@playwright/test";

/* Sign in once, through the real form, and keep the session cookie for every
   spec after it. The credentials are the demo seed's (tests/e2e/demo.py). */
setup("the chairman signs in", async ({ page }) => {
  await page.goto("/app/");
  await page.getByLabel("Email").fill("dominic@example.com");
  await page.getByLabel("Password").fill("demo-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/Good (morning|afternoon|evening)/);
  await page.context().storageState({ path: "e2e/.auth/chairman.json" });
});
