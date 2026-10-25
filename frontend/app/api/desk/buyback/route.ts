import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE, authSecret, verifyToken } from "@/lib/auth";
import { API_BASE } from "@/lib/api";

/**
 * The desk buyback figure, on its way to the API.
 *
 * Why this hop exists at all: the FastAPI write route needs a shared secret,
 * and a secret the browser can see is not a secret. The page posts here —
 * same origin, so no CORS and no preflight — and this handler, running on the
 * server, adds `X-Desk-Secret` and forwards. The API's CORS stays GET-only
 * and is not widened for the one route that writes.
 *
 * Three gates, all failing closed:
 *   1. the session cookie must verify (the site gate also covers this path,
 *      but a write route should not depend on a matcher pattern staying right);
 *   2. DESK_WRITE_SECRET must be set here, or there is nothing to forward;
 *   3. the API refuses again on its own side if its copy is unset.
 */
export async function POST(req: NextRequest) {
  const token = req.cookies.get(AUTH_COOKIE)?.value;
  if (!(await verifyToken(token, authSecret()))) {
    return NextResponse.json({ ok: false, error: "not signed in" }, { status: 401 });
  }

  const secret = process.env.DESK_WRITE_SECRET;
  if (!secret) {
    return NextResponse.json({
      ok: false,
      error: "Desk entry is not configured. Set DESK_WRITE_SECRET on both the site and "
           + "the API in Vercel; until then writes are refused rather than left open.",
    }, { status: 503 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "expected a JSON body" }, { status: 400 });
  }

  try {
    const r = await fetch(`${API_BASE}/api/desk/buyback`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Desk-Secret": secret },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const text = await r.text();
    // Pass the API's own status and message straight through: a validation
    // message that says which field is wrong is worth more than "failed".
    return new NextResponse(text, {
      status: r.status,
      headers: { "Content-Type": r.headers.get("Content-Type") ?? "application/json" },
    });
  } catch (e) {
    return NextResponse.json({
      ok: false, error: `could not reach the API: ${(e as Error).message}`,
    }, { status: 502 });
  }
}
