/**
 * Date-range helpers shared by the server pages. resolveRange turns the URL's
 * ?from&to (plus the data's real min/max) into a concrete [from,to] window;
 * inRange filters a row by its date field. Presets in DateRangeControl are
 * computed off the latest DATA date, so filtering and the picker agree.
 */
export function minusDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

export function resolveRange(
  sp: { from?: string; to?: string },
  dates: (string | null | undefined)[],
  defaultDays = 90,
): { from: string; to: string; min: string; max: string } {
  const clean = dates.filter(Boolean).map((d) => (d as string).slice(0, 10)).sort();
  const min = clean[0] ?? "2007-01-01";
  const max = clean[clean.length - 1] ?? new Date().toISOString().slice(0, 10);
  let to = sp.to ? sp.to.slice(0, 10) : max;
  if (to > max) to = max;
  if (to < min) to = max;
  let from = sp.from ? sp.from.slice(0, 10) : minusDays(max, defaultDays);
  if (from < min) from = min;
  if (from > to) from = min;
  return { from, to, min, max };
}

export function inRange(dateStr: string | null | undefined, from: string, to: string): boolean {
  if (!dateStr) return false;
  const d = dateStr.slice(0, 10);
  return d >= from && d <= to;
}
