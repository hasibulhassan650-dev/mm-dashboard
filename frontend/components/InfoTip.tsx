"use client";
import { useState } from "react";
import { GLOSSARY } from "@/lib/glossary";

const BY_TERM = new Map(GLOSSARY.map(e => [e.term.toLowerCase(), e]));

/** Small "?" bubble that shows a glossary definition on hover/tap.
 *  Pass `term` to pull from the shared glossary, or `text` for ad-hoc copy. */
export default function InfoTip({ term, text }: { term?: string; text?: string }) {
  const [open, setOpen] = useState(false);
  const entry = term ? BY_TERM.get(term.toLowerCase()) : undefined;
  const body = text ?? (entry ? `${entry.full ? entry.full + " — " : ""}${entry.def}` : term);
  if (!body) return null;

  return (
    <span className="relative inline-flex items-center align-middle">
      <button
        type="button"
        aria-label={term ? `Definition of ${term}` : "More info"}
        onClick={() => setOpen(o => !o)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="ml-1 w-3.5 h-3.5 inline-flex items-center justify-center rounded-full border border-gray-600 text-[9px] leading-none text-gray-400 hover:border-teal-500 hover:text-teal-400 transition-colors"
      >
        ?
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute left-0 top-5 z-50 w-64 rounded-lg border border-gray-700 bg-gray-950 p-2.5 text-xs font-normal leading-relaxed text-gray-300 shadow-xl"
        >
          {term && <span className="block font-semibold text-teal-400 mb-0.5">{term}</span>}
          {body}
        </span>
      )}
    </span>
  );
}
