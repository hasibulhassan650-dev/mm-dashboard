import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE, AUTH_TTL, authSecret, makeToken, sitePassword, verifyToken } from "./lib/auth";

function cookieOpts() {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production", // localhost is http
    sameSite: "lax" as const,
    path: "/",
    maxAge: AUTH_TTL,
  };
}

// Next 16 renamed the "middleware" convention to "proxy" (same runtime & API).
export async function proxy(req: NextRequest) {
  // The gate stays OFF until SITE_PASSWORD is configured in the environment, so
  // shipping this code never locks the live site out before the env var is set.
  if (!sitePassword()) return NextResponse.next();

  const { pathname } = req.nextUrl;
  if (pathname === "/login" || pathname.startsWith("/api/auth")) {
    return NextResponse.next();
  }

  const secret = authSecret();
  const token = req.cookies.get(AUTH_COOKIE)?.value;
  if (await verifyToken(token, secret)) {
    // Valid session → refresh the cookie (sliding window). 30 min of inactivity
    // (no request) lets it expire, which forces the password again.
    const res = NextResponse.next();
    res.cookies.set(AUTH_COOKIE, await makeToken(secret), cookieOpts());
    return res;
  }

  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  // Gate every page/route except Next internals and static assets.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|robots.txt|manifest.webmanifest|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|woff|woff2|ttf)$).*)",
  ],
};
