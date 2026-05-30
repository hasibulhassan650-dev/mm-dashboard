export default function Loading() {
  return (
    <div className="space-y-6 animate-pulse" aria-busy="true" aria-label="Loading">
      {/* header */}
      <div className="space-y-2">
        <div className="h-6 w-64 rounded bg-gray-800" />
        <div className="h-4 w-96 max-w-full rounded bg-gray-800/60" />
      </div>
      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="rounded-lg border border-gray-800 bg-gray-900 p-3 space-y-2">
            <div className="h-3 w-20 rounded bg-gray-800" />
            <div className="h-7 w-24 rounded bg-gray-800" />
            <div className="h-3 w-16 rounded bg-gray-800/60" />
          </div>
        ))}
      </div>
      {/* chart blocks */}
      <div className="grid md:grid-cols-2 gap-4">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div className="h-4 w-40 rounded bg-gray-800 mb-4" />
            <div className="h-56 rounded bg-gray-800/40" />
          </div>
        ))}
      </div>
    </div>
  );
}
