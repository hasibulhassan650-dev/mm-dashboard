"use client";
import { useRouter, usePathname, useSearchParams } from "next/navigation";

/**
 * Horizon selector for the schedule: writes ?years= to the URL, which the
 * server page reads. Same contract as DateRangeControl — the page filters on
 * it and hands the same slice to the table AND the export, so "export exactly
 * what I am looking at" needs no extra wiring.
 *
 * 20 is the cap because that is the API's cap, and because the longest bond on
 * issue matures in 2045 — past that the schedule is genuinely empty, not
 * unknown.
 */
const YEARS = [1, 2, 5, 10, 20];

export default function HorizonControl({ years }: { years: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  function pick(y: number) {
    const p = new URLSearchParams(searchParams.toString());
    p.set("years", String(y));
    router.push(`${pathname}?${p.toString()}`);
  }

  return (
    <div className="drc">
      <span className="panel-sub" style={{ marginRight: 2 }}>Horizon</span>
      <div className="drc-presets">
        {YEARS.map((y) => (
          <button key={y} className={"seg-b" + (years === y ? " on" : "")} onClick={() => pick(y)}>
            {y}Y
          </button>
        ))}
      </div>
    </div>
  );
}
