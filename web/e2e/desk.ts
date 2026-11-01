import { APIRequestContext, expect, Page } from "@playwright/test";

/* What the server says, read the way the pages read it. A spec acts through
   the screen and then asks the server whether it happened: a card vanishing
   proves only that the screen hid it. */

export type Row = {
  id: string;
  name: string;
  status: string;
  pending_approvals: { id: string; title: string; recommendation?: string | null }[];
  pending_asks: { id: string; title: string }[];
  pending_signoff: unknown | null;
  scope_items?: { id: string }[];
  needs_you: { total: number };
};

export async function row(request: APIRequestContext, name: string): Promise<Row> {
  const res = await request.get("/observe/desk");
  expect(res.ok()).toBeTruthy();
  const { projects } = (await res.json()) as { projects: Row[] };
  const hit = projects.find((p) => p.name === name);
  if (!hit) throw new Error(`no project named ${name} on the desk`);
  return hit;
}

/** Fail the spec on any uncaught error in the page: a render that throws
    blanks the screen, and nothing else would notice. */
export function failOnPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}
