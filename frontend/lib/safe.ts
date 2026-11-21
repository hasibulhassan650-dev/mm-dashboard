// A failed API call must never render as a legitimate zero / empty list / "all clear".
// The api getters below THROW on failure; a page that can still show something useful wraps each call in
// `t.soft(name, promise, fallback)`: the fallback keeps the render going, and `t.failed` names every source
// that did NOT answer so the page can say so (components/Unavailable.tsx) instead of showing the fallback as data.

export interface Tracker {
  /** names of the sources that failed, in call order */
  failed: string[];
  soft<T>(name: string, p: Promise<T>, fallback: T): Promise<T>;
}

export function tracker(): Tracker {
  const failed: string[] = [];
  return {
    failed,
    async soft<T>(name: string, p: Promise<T>, fallback: T): Promise<T> {
      try { return await p; } catch { failed.push(name); return fallback; }
    },
  };
}
