"use client";
import { useRouter, usePathname, useSearchParams } from "next/navigation";

interface Period { label: string; days: number }

export default function PeriodSelector({
  periods,
  current,
}: {
  periods: Period[];
  current: number;
}) {
  const router      = useRouter();
  const pathname    = usePathname();
  const searchParams = useSearchParams();

  function select(days: number) {
    const params = new URLSearchParams(searchParams.toString());
    params.set("days", String(days));
    router.push(`${pathname}?${params.toString()}`);
  }

  return (
    <div className="seg">
      {periods.map(p => (
        <button
          key={p.days}
          onClick={() => select(p.days)}
          className={"seg-b" + (current === p.days ? " on" : "")}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}
