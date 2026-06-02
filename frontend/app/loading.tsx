const bar = (w: number, h: number, o = 1) => ({
  width: w, height: h, borderRadius: 6, background: "var(--border)", opacity: o,
});

export default function Loading() {
  return (
    <div className="animate-pulse" aria-busy="true" aria-label="Loading">
      <div className="kpi-strip">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="kpi">
            <div style={bar(80, 11)} />
            <div style={{ ...bar(96, 26), margin: "10px 0 8px" }} />
            <div style={bar(60, 11, 0.6)} />
          </div>
        ))}
      </div>
      <div className="grid12">
        {[7, 5].map((span, i) => (
          <section key={i} className="panel" style={{ gridColumn: `span ${span}` }}>
            <div className="panel-head"><div style={bar(140, 14)} /></div>
            <div className="panel-body"><div style={{ ...bar(0, 280, 0.4), width: "100%" }} /></div>
          </section>
        ))}
      </div>
    </div>
  );
}
