import { GLOSSARY, GlossaryEntry, slug } from "@/lib/glossary";

export const metadata = { title: "Glossary · BB Market Intelligence" };

const ORDER: GlossaryEntry["category"][] = [
  "Forecasting", "Rates", "Operations", "Securities", "Metrics", "FX", "Macro", "Instruments",
];

export default function GlossaryPage() {
  const byCat = new Map<string, GlossaryEntry[]>();
  for (const e of GLOSSARY) {
    if (!byCat.has(e.category)) byCat.set(e.category, []);
    byCat.get(e.category)!.push(e);
  }

  return (
    <div className="grid12">
      {ORDER.filter((c) => byCat.has(c)).map((cat) => (
        <section key={cat} className="panel" style={{ gridColumn: "span 6" }}>
          <header className="panel-head">
            <div className="panel-titles"><h3 className="panel-title" style={{ color: "var(--accent)", textTransform: "uppercase", letterSpacing: ".5px", fontSize: 12 }}>{cat}</h3></div>
          </header>
          <div className="panel-body">
            {byCat.get(cat)!.map((e) => (
              <div key={e.term} id={slug(e.term)} style={{ padding: "10px 0", borderBottom: "1px solid var(--border-soft)", scrollMarginTop: 88 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--fg)" }}>
                  {e.term}
                  {e.full && <span style={{ display: "block", fontSize: 11, fontWeight: 400, color: "var(--fg-mute)" }}>{e.full}</span>}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--fg-dim)", marginTop: 3, lineHeight: 1.55 }}>{e.def}</div>
                {e.eli10 && (
                  <div style={{
                    fontSize: 12, color: "var(--fg-mute)", marginTop: 6, lineHeight: 1.55,
                    paddingLeft: 9, borderLeft: "2px solid var(--accent-soft)",
                  }}>
                    <span style={{ color: "var(--accent)", fontWeight: 600 }}>In plain words: </span>
                    {e.eli10}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
