"use client";
import { useRouter, usePathname, useSearchParams } from "next/navigation";

/**
 * Unified date-range control: quick presets + custom From/To pickers, all
 * writing ?from&to to the URL. Server pages read those, filter the data, and
 * hand the same slice to charts AND export — so "export the customised part"
 * is automatic. `min`/`max` are the data's real bounds (clamps the pickers);
 * presets are computed off `max` (the latest data date), not wall-clock.
 */
const PRESETS: { label: string; days: number | "all" }[] = [
  { label: "1M", days: 30 }, { label: "3M", days: 90 }, { label: "6M", days: 180 },
  { label: "1Y", days: 365 }, { label: "2Y", days: 730 }, { label: "All", days: "all" },
];

function iso(d: Date) { return d.toISOString().slice(0, 10); }
function minus(dateStr: string, days: number) {
  const d = new Date(dateStr + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() - days); return iso(d);
}

export default function DateRangeControl({ min, max, from, to }: { min: string; max: string; from: string; to: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  function apply(f: string, t: string) {
    const p = new URLSearchParams(searchParams.toString());
    p.delete("days");
    p.set("from", f); p.set("to", t);
    router.push(`${pathname}?${p.toString()}`);
  }

  function preset(days: number | "all") {
    const t = max;
    const f = days === "all" ? min : (minus(max, days) < min ? min : minus(max, days));
    apply(f, t);
  }

  // which preset (if any) is currently active
  const activeDays = to === max
    ? PRESETS.find((p) => (p.days === "all" ? from === min : from === minus(max, p.days) || (from === min && minus(max, p.days) < min)))?.label
    : undefined;

  return (
    <div className="drc">
      <div className="drc-presets">
        {PRESETS.map((p) => (
          <button key={p.label} className={"seg-b" + (activeDays === p.label ? " on" : "")} onClick={() => preset(p.days)}>
            {p.label}
          </button>
        ))}
      </div>
      <div className="drc-range">
        <input type="date" value={from} min={min} max={to} onChange={(e) => e.target.value && apply(e.target.value, to)} aria-label="From date" />
        <span className="drc-dash">→</span>
        <input type="date" value={to} min={from} max={max} onChange={(e) => e.target.value && apply(from, e.target.value)} aria-label="To date" />
      </div>
    </div>
  );
}
