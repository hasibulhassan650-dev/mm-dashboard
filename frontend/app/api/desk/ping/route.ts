import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE, authSecret, verifyToken } from "@/lib/auth";
import { API_BASE } from "@/lib/api";

/**
 * Is desk entry actually wired up end to end?
 *
 * The write form used to just refuse, and the desk could not tell WHICH of the
 * two places was missing the secret: the site, the API, or both with different
 * values. This runs the round trip without writing anything and answers with the
 * stage that failed. It never returns the secret, and it never writes.
 *
 * Always HTTP 200 with a `stage`: a configuration problem is a normal answer to
 * "is it set up?", not a server error, and the UI needs to read it.
 */
export async function GET(req: NextRequest) {
  const token = req.cookies.get(AUTH_COOKIE)?.value;
  if (!(await verifyToken(token, authSecret()))) {
    return NextResponse.json({ ok: false, stage: "not_signed_in" }, { status: 401 });
  }

  const secret = process.env.DESK_WRITE_SECRET;
  if (!secret) {
    return NextResponse.json({
      ok: false, stage: "site_secret_missing",
      detail: "DESK_WRITE_SECRET is not set on the SITE project in Vercel.",
    });
  }

  try {
    const r = await fetch(`${API_BASE}/api/desk/ping`, {
      headers: { "X-Desk-Secret": secret }, cache: "no-store",
    });
    if (r.ok) return NextResponse.json({ ok: true, stage: "ok" });
    if (r.status === 503) {
      return NextResponse.json({
        ok: false, stage: "api_secret_missing",
        detail: "DESK_WRITE_SECRET is set on the site but NOT on the API project.",
      });
    }
    if (r.status === 403) {
      return NextResponse.json({
        ok: false, stage: "secret_mismatch",
        detail: "Both projects have a secret, but they are different values.",
      });
    }
    return NextResponse.json({
      ok: false, stage: "api_error", detail: `The API answered HTTP ${r.status}.`,
    });
  } catch (e) {
    return NextResponse.json({
      ok: false, stage: "api_unreachable",
      detail: `Could not reach the API: ${(e as Error).message}`,
    });
  }
}
