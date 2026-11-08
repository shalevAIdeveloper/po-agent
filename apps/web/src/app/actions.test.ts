// @vitest-environment node
/**
 * Server Actions with Supabase, the API, requireUser and redirect mocked (A15).
 * The real flows are covered end to end in e2e/auth*.spec.ts.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

class Redirect extends Error {
  constructor(readonly to: string) {
    super(`redirect:${to}`);
  }
}

const auth = vi.hoisted(() => ({
  signInWithPassword: vi.fn(),
  signOut: vi.fn(),
  verifyOtp: vi.fn(),
  updateUser: vi.fn(),
  resetPasswordForEmail: vi.fn(),
  getClaims: vi.fn(),
}));
const apiMock = vi.hoisted(() => ({ createOrganization: vi.fn(), getMe: vi.fn() }));
const requireUser = vi.hoisted(() => vi.fn());

vi.mock("server-only", () => ({}));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Redirect(to);
  },
}));
vi.mock("@/lib/supabase/server", () => ({
  createSupabaseServerClient: async () => ({ auth }),
}));
vi.mock("@/lib/auth/session", () => ({ requireUser }));
vi.mock("@/lib/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api/client")>();
  return { ...original, api: apiMock };
});

const { ApiError } = await import("@/lib/api/client");
const { login } = await import("@/app/login/actions");
const { logout } = await import("@/app/app/actions");
const { confirmEmailLink } = await import("@/app/auth/confirm/actions");
const { setPassword } = await import("@/app/set-password/actions");
const { requestPasswordReset } = await import("@/app/forgot-password/actions");
const { createOrganization } = await import("@/app/onboarding/actions");

const HASH = "a3f1c2e4b5d6a7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c";
const USER = {
  userId: "u1",
  email: "buyer@acme.test",
  accessToken: "token-abc",
  recentEmailLinkSignIn: true,
};

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.set(key, value);
  return data;
}

async function redirectOf(promise: Promise<unknown>): Promise<string> {
  const error = await promise.catch((caught: unknown) => caught);
  expect(error).toBeInstanceOf(Redirect);
  return (error as Redirect).to;
}

beforeEach(() => {
  vi.clearAllMocks();
  requireUser.mockResolvedValue(USER);
  auth.getClaims.mockResolvedValue({ data: null, error: null });
  auth.signOut.mockResolvedValue({ error: null });
});

describe("login", () => {
  it("signs in and redirects to a sanitized `next`", async () => {
    auth.signInWithPassword.mockResolvedValue({ error: null });

    const to = await redirectOf(
      login({ attempt: 0 }, form({ email: " a@b.co ", password: "pw", next: "/.//evil.com" })),
    );

    expect(to).toBe("/app");
    expect(auth.signInWithPassword).toHaveBeenCalledWith({ email: "a@b.co", password: "pw" });
  });

  it("returns one generic error, echoes the email only, and counts the attempt", async () => {
    auth.signInWithPassword.mockResolvedValue({ error: { status: 400, code: "invalid_credentials" } });

    const state = await login({ attempt: 2 }, form({ email: "a@b.co", password: "secret-pw" }));

    expect(state).toEqual({
      attempt: 3,
      error: "The email or password is incorrect.",
      values: { email: "a@b.co" },
    });
    expect(JSON.stringify(state)).not.toContain("secret-pw");
  });

  it("does not call Auth for invalid input", async () => {
    const state = await login({ attempt: 0 }, form({ email: "nope", password: "" }));

    expect(state.fieldErrors).toEqual({
      email: "Enter a valid email address.",
      password: "Enter your password.",
    });
    expect(auth.signInWithPassword).not.toHaveBeenCalled();
  });
});

describe("logout", () => {
  it("ends this browser's session and goes to /login", async () => {
    auth.signOut.mockResolvedValue({ error: null });

    expect(await redirectOf(logout())).toBe("/login");
    expect(auth.signOut).toHaveBeenCalledWith({ scope: "local" });
  });
});

describe("confirmEmailLink", () => {
  it("verifies only allowed link types and goes to /set-password", async () => {
    auth.verifyOtp.mockResolvedValue({ error: null });

    const to = await redirectOf(
      confirmEmailLink({ attempt: 0 }, form({ type: "invite", token_hash: HASH })),
    );

    expect(to).toBe("/set-password");
    expect(auth.verifyOtp).toHaveBeenCalledWith({ type: "invite", token_hash: HASH });
  });

  it("signs out a different signed-in user before verifying (no silent session swap)", async () => {
    auth.getClaims.mockResolvedValue({ data: { claims: { sub: "someone-else" } }, error: null });
    auth.verifyOtp.mockResolvedValue({ error: null });

    await redirectOf(confirmEmailLink({ attempt: 0 }, form({ type: "recovery", token_hash: HASH })));

    expect(auth.signOut).toHaveBeenCalledWith({ scope: "local" });
    expect(auth.signOut.mock.invocationCallOrder[0]).toBeLessThan(
      auth.verifyOtp.mock.invocationCallOrder[0] ?? 0,
    );
  });

  it("does not sign anyone out when nobody is signed in", async () => {
    auth.verifyOtp.mockResolvedValue({ error: null });

    await redirectOf(confirmEmailLink({ attempt: 0 }, form({ type: "invite", token_hash: HASH })));

    expect(auth.signOut).not.toHaveBeenCalled();
  });

  it("never sends other types (e.g. email_change) to Auth", async () => {
    const state = await confirmEmailLink(
      { attempt: 0 },
      form({ type: "email_change", token_hash: HASH }),
    );

    expect(state).toMatchObject({ expired: true, error: "This link is not valid." });
    expect(auth.verifyOtp).not.toHaveBeenCalled();
  });

  it.each([
    [{ status: 403, code: "otp_expired" }, { expired: true, error: expect.stringMatching(/expired or has already been used/) }],
    [{ status: 429 }, { error: expect.stringMatching(/Too many attempts/) }],
    [{ status: 0 }, { error: expect.stringMatching(/couldn't check your link/) }],
  ])("maps %j", async (error, expected) => {
    auth.verifyOtp.mockResolvedValue({ error });

    const state = await confirmEmailLink({ attempt: 0 }, form({ type: "recovery", token_hash: HASH }));

    expect(state).toMatchObject(expected);
  });
});

describe("setPassword", () => {
  it("requires a signed-in user before anything else", async () => {
    requireUser.mockRejectedValue(new Redirect("/login"));

    expect(
      await redirectOf(setPassword({ attempt: 0 }, form({ password: "x", confirm: "x" }))),
    ).toBe("/login");
    expect(auth.updateUser).not.toHaveBeenCalled();
  });

  it("refuses unless the session came from an email link in the last 15 minutes", async () => {
    requireUser.mockResolvedValue({ ...USER, recentEmailLinkSignIn: false });

    const state = await setPassword(
      { attempt: 0 },
      form({ password: "a-new-long-password", confirm: "a-new-long-password" }),
    );

    expect(state.error).toMatch(/open a new password link/);
    expect(auth.updateUser).not.toHaveBeenCalled();
  });

  it("validates locally, never echoes the password, and does not call Auth", async () => {
    const state = await setPassword({ attempt: 0 }, form({ password: "short-pw", confirm: "short-pw" }));

    expect(state).toEqual({ attempt: 1, fieldErrors: { password: "Use at least 12 characters." } });
    expect(auth.updateUser).not.toHaveBeenCalled();
  });

  it("saves the password untrimmed and continues to /app", async () => {
    auth.updateUser.mockResolvedValue({ error: null });

    const to = await redirectOf(
      setPassword({ attempt: 0 }, form({ password: " twelve chars ", confirm: " twelve chars " })),
    );

    expect(to).toBe("/app");
    expect(auth.updateUser).toHaveBeenCalledWith({ password: " twelve chars " });
  });

  it("maps Auth's refusal", async () => {
    auth.updateUser.mockResolvedValue({ error: { status: 422, code: "weak_password", reasons: ["pwned"] } });

    const state = await setPassword(
      { attempt: 0 },
      form({ password: "password1234", confirm: "password1234" }),
    );

    expect(state.error).toMatch(/data breach/);
    expect(JSON.stringify(state)).not.toContain("password1234");
  });
});

describe("requestPasswordReset", () => {
  it.each([[{ error: null }], [{ error: { status: 400, code: "user_not_found" } }]])(
    "gives the same notice whether or not the account exists (%j)",
    async (result) => {
      auth.resetPasswordForEmail.mockResolvedValue(result);

      const state = await requestPasswordReset({ attempt: 0 }, form({ email: "who@acme.test" }));

      expect(state.notice).toMatch(/If an account exists for that email/);
      expect(state.error).toBeUndefined();
      expect(auth.resetPasswordForEmail).toHaveBeenCalledWith("who@acme.test");
    },
  );

  it("answers a rate limit with the same notice (it only fires for real accounts)", async () => {
    auth.resetPasswordForEmail.mockResolvedValue({
      error: { status: 429, code: "over_email_send_rate_limit" },
    });

    const state = await requestPasswordReset({ attempt: 0 }, form({ email: "a@b.co" }));

    expect(state.notice).toMatch(/If an account exists/);
    expect(state.error).toBeUndefined();
  });

  it("reports an outage", async () => {
    auth.resetPasswordForEmail.mockResolvedValue({ error: { status: 500 } });

    const state = await requestPasswordReset({ attempt: 0 }, form({ email: "a@b.co" }));

    expect(state.error).toMatch(/couldn't send/);
  });

  it("rejects an invalid email without calling Auth", async () => {
    const state = await requestPasswordReset({ attempt: 0 }, form({ email: "nope" }));

    expect(state.fieldErrors?.email).toBe("Enter a valid email address.");
    expect(auth.resetPasswordForEmail).not.toHaveBeenCalled();
  });
});

describe("createOrganization", () => {
  it("creates the org with the user's token and goes to /app", async () => {
    apiMock.createOrganization.mockResolvedValue({});

    const to = await redirectOf(
      createOrganization({ attempt: 0 }, form({ name: "  Acme Supply ", slug: "acme-supply" })),
    );

    expect(to).toBe("/app");
    expect(apiMock.createOrganization).toHaveBeenCalledWith("token-abc", {
      name: "Acme Supply",
      slug: "acme-supply",
    });
  });

  it("validates before calling the API", async () => {
    const state = await createOrganization({ attempt: 0 }, form({ name: " ", slug: "Bad Slug" }));

    expect(Object.keys(state.fieldErrors ?? {})).toEqual(["name", "slug"]);
    expect(apiMock.createOrganization).not.toHaveBeenCalled();
  });

  it.each([
    ["slug_unavailable", 409, { fieldErrors: { slug: "This address isn't available. Try another one." } }],
    ["invalid_organization", 422, { fieldErrors: { slug: "This address isn't available. Try another one." } }],
    ["organization_limit_reached", 409, { error: expect.stringMatching(/limit of organizations/) }],
    [undefined, 401, { error: expect.stringMatching(/session has ended/) }],
    ["busy", 503, { error: expect.stringMatching(/busy/) }],
  ])("maps %s (%d) and keeps the values", async (code, status, expected) => {
    apiMock.createOrganization.mockRejectedValue(new ApiError("http", { status, code }));

    const state = await createOrganization({ attempt: 0 }, form({ name: "Acme", slug: "acme" }));

    expect(state).toMatchObject({ ...expected, values: { name: "Acme", slug: "acme" } });
  });
});
