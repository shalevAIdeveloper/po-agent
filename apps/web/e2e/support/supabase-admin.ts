/**
 * Test-only helpers that talk to the local Supabase stack with the secret key.
 * Never import from app code. Runs in the Playwright (Node) process only.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

export type E2eEnv = {
  supabaseUrl: string;
  publishableKey: string;
  secretKey: string;
  apiUrl: string;
  mailpitUrl: string;
};

export function e2eEnv(): E2eEnv | null {
  // Same switch as playwright.config.ts: without E2E_AUTH=1 the API isn't started,
  // so the auth specs must skip even if a key happens to be set in the shell.
  if (process.env.E2E_AUTH !== "1") {
    return null;
  }
  const secretKey = process.env.E2E_SUPABASE_SECRET_KEY;
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!secretKey || !publishableKey) {
    return null;
  }
  return {
    supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321",
    publishableKey,
    secretKey,
    apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000",
    mailpitUrl: process.env.E2E_MAILPIT_URL ?? "http://127.0.0.1:54324",
  };
}

export type TestUser = { id: string; email: string; password: string };

async function expectOk(response: Response, what: string): Promise<Response> {
  if (!response.ok) {
    // Test data only; the body is a GoTrue/API error code, useful when CI fails.
    const body = await response.text().catch(() => "");
    throw new Error(`${what} failed: HTTP ${response.status} ${body.slice(0, 200)}`);
  }
  return response;
}

/** A confirmed user created through the admin API (sign-up is disabled: invite-only). */
export async function createUser(env: E2eEnv, label: string): Promise<TestUser> {
  const email = `e2e-${label}-${randomUUID().slice(0, 8)}@e2e.test`;
  const password = `E2e-${randomUUID()}`;
  const response = await expectOk(
    await fetch(`${env.supabaseUrl}/auth/v1/admin/users`, {
      method: "POST",
      headers: { apikey: env.secretKey, "content-type": "application/json" },
      body: JSON.stringify({ email, password, email_confirm: true }),
    }),
    "create user",
  );
  const { id } = (await response.json()) as { id: string };
  return { id, email, password };
}

/** Creates an org owned by `user` through the real API (POST /orgs); returns its slug. */
export async function createOrganization(
  env: E2eEnv,
  user: TestUser,
  name: string,
  slug = `e2e-${randomUUID().slice(0, 8)}`,
): Promise<string> {
  const session = await expectOk(
    await fetch(`${env.supabaseUrl}/auth/v1/token?grant_type=password`, {
      method: "POST",
      headers: { apikey: env.publishableKey, "content-type": "application/json" },
      body: JSON.stringify({ email: user.email, password: user.password }),
    }),
    "sign in",
  );
  const { access_token: token } = (await session.json()) as { access_token: string };
  await expectOk(
    await fetch(`${env.apiUrl}/orgs`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ name, slug }),
    }),
    "create organization",
  );
  return slug;
}

/** Invites a new user exactly as the dashboard does (GoTrue /invite); sends the email to Mailpit. */
export async function inviteUser(env: E2eEnv, label: string): Promise<TestUser> {
  const email = `e2e-${label}-${randomUUID().slice(0, 8)}@e2e.test`;
  const response = await expectOk(
    await fetch(`${env.supabaseUrl}/auth/v1/invite`, {
      method: "POST",
      headers: {
        apikey: env.secretKey,
        authorization: `Bearer ${env.secretKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ email }),
    }),
    "invite user",
  );
  const { id } = (await response.json()) as { id: string };
  // The password is chosen later, in the browser.
  return { id, email, password: `E2e-${randomUUID()}` };
}

export type EmailLinkParams = { type: string; tokenHash: string };

/**
 * The newest /auth/confirm link of `type` sent to `email`, read from Mailpit.
 * Returns only the query parameters: templates use the Site URL (port 3000),
 * while e2e runs on 3100, so tests open the path on their own base URL.
 */
export async function emailLink(
  env: E2eEnv,
  email: string,
  type: "invite" | "recovery",
  { after = 0 }: { after?: number } = {},
): Promise<EmailLinkParams> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    const search = await expectOk(
      await fetch(`${env.mailpitUrl}/api/v1/search?query=${encodeURIComponent(`to:"${email}"`)}`),
      "mailpit search",
    );
    const { messages } = (await search.json()) as {
      messages: { ID: string; Created: string }[];
    };
    for (const message of messages) {
      if (Date.parse(message.Created) < after) continue;
      const detail = await expectOk(
        await fetch(`${env.mailpitUrl}/api/v1/message/${message.ID}`),
        "mailpit message",
      );
      const { HTML } = (await detail.json()) as { HTML: string };
      const href = /href="([^"]*\/auth\/confirm\?[^"]*)"/.exec(HTML)?.[1]?.replaceAll("&amp;", "&");
      if (!href) continue;
      const url = new URL(href);
      if (url.searchParams.get("type") === type) {
        return { type, tokenHash: url.searchParams.get("token_hash") ?? "" };
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`no ${type} email for the test user within 15 s`);
}

export function confirmPath({ type, tokenHash }: EmailLinkParams): string {
  return `/auth/confirm?token_hash=${encodeURIComponent(tokenHash)}&type=${encodeURIComponent(type)}`;
}

/** Runs the real operator tool (apps/api/tools/add_member.py) against the local stack. */
export function runAddMember(
  env: E2eEnv,
  args: { email: string; org: string; role: "admin" | "reviewer" },
): string {
  return execFileSync(
    "uv",
    [
      "run",
      "python",
      "-m",
      "tools.add_member",
      "--email",
      args.email,
      "--org",
      args.org,
      "--role",
      args.role,
      "--actor",
      "e2e@po-agent.test",
      "--reason",
      "E2E: second buyer joins an existing org",
    ],
    {
      cwd: fileURLToPath(new URL("../../../api", import.meta.url)),
      env: {
        ...process.env,
        ADMIN_SUPABASE_URL: env.supabaseUrl,
        ADMIN_SUPABASE_SECRET_KEY: env.secretKey,
      },
      encoding: "utf8",
      timeout: 60_000,
    },
  );
}

/** Orgs first (a sole owner cannot be deleted, D7), then the user. Fails loudly on a leak. */
export async function deleteUser(env: E2eEnv, user: TestUser): Promise<void> {
  const headers = { apikey: env.secretKey, authorization: `Bearer ${env.secretKey}` };
  await expectOk(
    await fetch(`${env.supabaseUrl}/rest/v1/organizations?created_by=eq.${user.id}`, {
      method: "DELETE",
      headers,
    }),
    "delete organizations",
  );
  const response = await fetch(`${env.supabaseUrl}/auth/v1/admin/users/${user.id}`, {
    method: "DELETE",
    headers,
  });
  if (response.status !== 404) {
    await expectOk(response, "delete user");
  }
}
