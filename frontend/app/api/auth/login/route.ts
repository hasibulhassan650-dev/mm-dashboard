import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE, AUTH_TTL, authSecret, makeToken, sitePassword } from "@/lib/auth";

export async function POST(req: NextRequest) {
  let password = "";
  try {
    password = (await req.json())?.password ?? "";
  } catch {
    /* empty body → treated as wrong password */
  }

  const expected = sitePassword();
  if (!expected || password !== expected) {
    return NextResponse.json({ ok: false }, { status: 401 });
  }

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
