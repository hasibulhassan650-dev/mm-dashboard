import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE, AUTH_TTL, authSecret, makeToken, verifyPassword } from "@/lib/auth";

// Best-effort brute-force brake: failures per client in a sliding window, kept in this instance's memory. Serverless
// instances do not share it, so it slows a guesser rather than stopping one (a WAF rate limit does that); it also adds a
// fixed delay to every failure so each guess costs time.
const WINDOW_MS = 10 * 60 * 1000;
const MAX_FAILS = 8;
const fails = new Map<string, number[]>();

function clientKey(req: NextRequest): string {
  return (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || "unknown";
}

export async function POST(req: NextRequest) {
  const key = clientKey(req);
  const now = Date.now();
  const recent = (fails.get(key) ?? []).filter((t) => now - t < WINDOW_MS);
  if (recent.length >= MAX_FAILS) {
    return NextResponse.json({ ok: false, error: "too many attempts, try again later" }, { status: 429 });
  }
  if (!process.env.AUTH_SECRET) {
    console.warn("AUTH_SECRET is not set: session cookies are signed with a key derivable from the public repository");
  }
  let password = "";
  try {
    password = (await req.json())?.password ?? "";
  } catch {
    /* empty body → treated as wrong password */
  }

  if (!(await verifyPassword(password))) {
    recent.push(now);
    fails.set(key, recent);
    await new Promise((r) => setTimeout(r, 600));
    return NextResponse.json({ ok: false }, { status: 401 });
  }
  fails.delete(key);

  const res = NextResponse.json({ ok: true });
  res.cookies.set(AUTH_COOKIE, await makeToken(authSecret()), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: AUTH_TTL,
  });
  return res;
}
