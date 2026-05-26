interface Props {
  label: string;
  value: string;
  sub?: string;
  color?: "blue" | "green" | "amber" | "red";
}

const colors = {
  blue:  "border-blue-500/40 bg-blue-500/10",
  green: "border-green-500/40 bg-green-500/10",
  amber: "border-amber-500/40 bg-amber-500/10",
  red:   "border-red-500/40 bg-red-500/10",
};

export default function StatCard({ label, value, sub, color = "blue" }: Props) {
  return (
    <div className={`rounded-xl border p-4 ${colors[color]}`}>
      <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">{label}</p>
      <p className="text-2xl font-bold text-white">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}
