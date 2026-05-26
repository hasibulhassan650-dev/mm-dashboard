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
    <div className="flex items-center gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
      <span className="text-xs text-gray-500 px-1">Period:</span>
      {periods.map(p => (
        <button
          key={p.days}
          onClick={() => select(p.days)}
          className={`px-2.5 py-1 rounded text-xs transition-colors ${
            current === p.days
              ? "bg-blue-600 text-white"
              : "text-gray-400 hover:text-white hover:bg-gray-800"
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}
