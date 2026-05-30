"use client";
import { useEffect, useState } from "react";
import { fmtDateTime, timeAgo } from "@/lib/format";

/** Small "Updated …" badge. Absolute time renders on server (hydration-safe);
 *  relative age is filled in after mount and refreshes every 60s. */
export default function Freshness({ updated, label = "Updated" }: { updated: string | null; label?: string }) {
  const [ago, setAgo] = useState<string>("");

  useEffect(() => {
    if (!updated) return;
    const tick = () => setAgo(timeAgo(updated));
    tick();
    const t = setInterval(tick, 60_000);
    return () => clearInterval(t);
  }, [updated]);

  if (!updated) return null;

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-gray-500" title={fmtDateTime(updated)}>
      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500/70" />
      {label} {fmtDateTime(updated)}
      {ago && <span className="text-gray-600">· {ago}</span>}
    </span>
  );
}
