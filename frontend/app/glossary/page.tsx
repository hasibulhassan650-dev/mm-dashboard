import { GLOSSARY, GlossaryEntry } from "@/lib/glossary";

export const metadata = { title: "Glossary · BB Market Intelligence" };

const ORDER: GlossaryEntry["category"][] = [
  "Rates", "Operations", "Securities", "Metrics", "FX", "Macro", "Instruments",
];

export default function GlossaryPage() {
  const byCat = new Map<string, GlossaryEntry[]>();
  for (const e of GLOSSARY) {
    if (!byCat.has(e.category)) byCat.set(e.category, []);
    byCat.get(e.category)!.push(e);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Glossary</h1>
        <p className="text-sm text-gray-400">
          Money-market terminology used across this dashboard · Bangladesh Bank conventions
        </p>
      </div>

      {ORDER.filter(c => byCat.has(c)).map(cat => (
        <div key={cat} className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-teal-400 mb-3 uppercase tracking-wide">{cat}</h2>
          <dl className="space-y-3">
            {byCat.get(cat)!.map(e => (
              <div key={e.term} className="grid md:grid-cols-[180px_1fr] gap-1 md:gap-4">
                <dt className="text-sm font-semibold text-white">
                  {e.term}
                  {e.full && <span className="block text-xs font-normal text-gray-500">{e.full}</span>}
                </dt>
                <dd className="text-sm text-gray-400 leading-relaxed">{e.def}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}
