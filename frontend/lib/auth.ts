// Tiny stateless session for the site password gate. Edge-safe (Web Crypto
// only), so it works in both middleware (edge) and the login route.
//
// The session cookie is `${exp}.${HMAC_SHA256(exp, secret)}` where `exp` is a
// unix-second expiry. Verifying re-computes the HMAC and checks exp>now, so a
// token can't be forged and expires on its own. The gate re-issues the cookie
// on every request (sliding window) → 30 min of INACTIVITY logs you out.
//
// The real password lives in the SITE_PASSWORD env var (set in Vercel, never
// committed — this repo is public). AUTH_SECRET signs the token; if unset we
// fall back to SITE_PASSWORD as the signing key.

export const AUTH_COOKIE = "mm_session";
export const AUTH_TTL = 30 * 60; // 30 minutes, in seconds

const enc = new TextEncoder();

function b64url(buf: ArrayBuffer): string {
  let s = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

async function hmac(msg: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return b64url(await crypto.subtle.sign("HMAC", key, enc.encode(msg)));
}

export function sitePassword(): string {
  return process.env.SITE_PASSWORD || "";
}

export function authSecret(): string {
  return process.env.AUTH_SECRET || process.env.SITE_PASSWORD || "";
}

export async function makeToken(secret: string): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + AUTH_TTL;
  return `${exp}.${await hmac(String(exp), secret)}`;
}

export async function verifyToken(token: string | undefined, secret: string): Promise<boolean> {
  if (!token || !secret) return false;
  const dot = token.indexOf(".");
  if (dot < 0) return false;
  const exp = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  if (!/^\d+$/.test(exp) || parseInt(exp, 10) < Math.floor(Date.now() / 1000)) return false;
  return (await hmac(exp, secret)) === sig;
}
