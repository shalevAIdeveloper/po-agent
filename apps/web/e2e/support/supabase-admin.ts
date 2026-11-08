/**
 * Test-only helpers that talk to the local Supabase stack with the secret key.
 * Never import from app code. Runs in the Playwright (Node) process only.
 */
import { randomUUID } from "node:crypto";

export type E2eEnv = {
  supabaseUrl: string;
  publishableKey: string;
  secretKey: string;
  apiUrl: string;
};

export function e2eEnv(): E2eEnv | null {
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

/** Creates an org owned by `user` through the real API (POST /orgs). */
export async function createOrganization(env: E2eEnv, user: TestUser, name: string): Promise<void> {
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
      body: JSON.stringify({ name, slug: `e2e-${randomUUID().slice(0, 8)}` }),
    }),
    "create organization",
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
