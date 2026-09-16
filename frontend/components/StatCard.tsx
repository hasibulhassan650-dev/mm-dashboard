interface Props {
  label: string;
  value: string;
  sub?: string;
  color?: "blue" | "green" | "amber" | "red";
}

// Semantic tone → theme token, so the tint follows light/dark like every panel.
const TONE: Record<NonNullable<Props["color"]>, string> = {
  blue: "var(--info)", green: "var(--pos)", amber: "var(--warn)", red: "var(--neg)",
};

export default function StatCard({ label, value, sub, color = "blue" }: Props) {
  const tone = TONE[color];
  return (
    <div className="rounded-xl border p-4" style={{
      borderColor: `color-mix(in oklab, ${tone} 40%, transparent)`,
      background: `color-mix(in oklab, ${tone} 10%, var(--panel))`,
    }}>
      <p className="text-xs t-dim uppercase tracking-wide mb-1">{label}</p>
      <p className="text-2xl font-bold t-fg">{value}</p>
      {sub && <p className="text-xs t-dim mt-1">{sub}</p>}
    </div>
  );
}
