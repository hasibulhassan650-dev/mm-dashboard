// Site password gate — zero-setup. Edge-safe (Web Crypto only) so it works in
// both the proxy (edge) and the login route.
//
// The password is NOT stored here in plain text (this repo is public): only its
// SHA-256 hash is. Login hashes what the visitor types and compares. You can
// still override with a SITE_PASSWORD env var if you ever want it fully secret.
//
// The session cookie is `${exp}.${HMAC_SHA256(exp, secret)}` where `exp` is a
// unix-second expiry. Verifying re-computes the HMAC and checks exp>now, so a
// token can't be forged and expires on its own. The gate re-issues the cookie
// on every request (sliding window) → 30 min of INACTIVITY forces the password
// again.

export const AUTH_COOKIE = "mm_session";
export const AUTH_TTL = 30 * 60; // 30 minutes, in seconds

// SHA-256 of the site password. (Change the password by replacing this hash —
// see the note in this file's commit / README.)
const PASSWORD_SHA256 = "f916ebe5430d97ae5276678a22401f154343c4ea19b0fb60ec0af39e998192f7";

const enc = new TextEncoder();

function toHex(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += bytes[i].toString(16).padStart(2, "0");
  return s;
}

function b64url(buf: ArrayBuffer): string {
  let s = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

async function sha256hex(msg: string): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", enc.encode(msg)));
}

async function hmac(msg: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return b64url(await crypto.subtle.sign("HMAC", key, enc.encode(msg)));
}

/** Comparison whose time does not depend on where two strings first differ (so a guess cannot be refined byte by byte). */
export function safeEqual(a: string, b: string): boolean {
  const x = enc.encode(a), y = enc.encode(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

/** True if `input` is the site password (env override, else the baked-in hash). */
export async function verifyPassword(input: string): Promise<boolean> {
  if (!input) return false;
  const env = process.env.SITE_PASSWORD;
  if (env) return safeEqual(input, env);
  return safeEqual(await sha256hex(input), PASSWORD_SHA256);
}

/**
 * Signing key for session tokens - never empty, so the gate is always active.
 * SECURITY: the fallbacks are derivable from this PUBLIC repo (PASSWORD_SHA256 is committed). Set a random AUTH_SECRET in the
 * Vercel project, otherwise anyone can forge a session cookie. It is not made mandatory here only because an unset variable
 * would then lock the owner out of the site; the login route logs a warning on every login while it is missing.
 */
export function authSecret(): string {
  return process.env.AUTH_SECRET || process.env.SITE_PASSWORD || PASSWORD_SHA256;
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
  return safeEqual(await hmac(exp, secret), sig);
}
