import { SOURCES } from "@/lib/sources";

/**
 * "Source:" line — where the numbers on a page come from, with links to the
 * Bangladesh Bank page the fetcher reads, the table, and who publishes it.
 * Entries live in lib/sources.ts, every URL there is checked against the
 * fetchers by tests/test_source_links.py. A computed (derived) dataset has no
 * URL of its own: it says what it is derived from and links those.
 *
 * Server component. Place it just above <RelatedLinks>.
 */
export default function SourceLink({ k }: { k: string | string[] }) {
  const keys = Array.isArray(k) ? k : [k];
  const items = keys.map((key) => ({ key, s: SOURCES[key] })).filter((x) => x.s);
  if (items.length === 0) return null;

  return (
    <div className="related" style={{ fontSize: 11.5, gap: 8, rowGap: 4 }}>
      <span className="related-tag">Source</span>
      {items.map(({ key, s }) => (
        <span key={key} className="t-mute" style={{ display: "inline-flex", flexWrap: "wrap", alignItems: "baseline", gap: 6 }}>
          {s.urls.map((u) => (
            <a key={u.href} href={u.href} target="_blank" rel="noreferrer" className="t-info hover:underline">
              {u.label} ↗
            </a>
          ))}
          {s.derivedFrom && (
            <span>
              {s.label}: derived from{" "}
              {s.derivedFrom.map((d, i) => {
                const src = SOURCES[d];
                const first = src?.urls[0];
                return (
                  <span key={d}>
                    {i > 0 && ", "}
                    {first
                      ? <a href={first.href} target="_blank" rel="noreferrer" className="t-info hover:underline">{src.label}</a>
                      : (src?.label ?? d)}
                  </span>
                );
              })}
            </span>
          )}
          <span title={s.table ? `table ${s.table}` : undefined}>
            {s.cite}{s.table ? ` · table ${s.table}` : ""}
          </span>
        </span>
      ))}
    </div>
  );
}
