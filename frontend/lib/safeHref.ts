/**
 * An external link taken from stored data (a news article, a BB release) is shown only if it is a plain http(s) URL.
 * `javascript:`, `data:` and the like become "#", so a poisoned source row can never become a clickable script.
 * (Links we write ourselves - lib/sources.ts - are constants, test-guarded to be URLs the fetchers read.)
 */
export function safeHref(url: string | null | undefined): string {
  if (!url) return "#";
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : "#";
  } catch {
    return "#";
  }
}
