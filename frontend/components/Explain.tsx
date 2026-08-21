"use client";
import * as React from "react";
import Link from "next/link";
import { lookup, slug } from "@/lib/glossary";
import { concept } from "@/lib/concepts";

/**
 * Inline jargon terms that teach on demand.
 *
 * The technical term always STAYS on the page — the page is written for the
 * desk and reads in the desk's language. What changes is that any term can be
 * opened to a full explanation of the concept: intuition, mechanics with a
 * worked example on this dashboard's real numbers, how to read it here, and the
 * usual misunderstanding. Deep enough to actually learn from, rather than a
 * dictionary line.
 */

/** Hover = short definition. Click = the whole concept. */
export function Term({ t, children }: { t: string; children?: React.ReactNode }) {
  const [hover, setHover] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const entry = lookup(t);
  const full = concept(t);
  const label = children ?? t;

  // Unknown term: render plain text rather than a dead affordance, so a typo
  // degrades to ordinary copy instead of an underline that does nothing.
  if (!entry && !full) return <>{label}</>;

  const short = entry?.eli10 ?? entry?.def ?? full?.oneLine ?? "";
  const title = entry?.term ?? full?.term ?? t;

  return (
    <>
      <span style={{ position: "relative", display: "inline-block" }}
        onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        <button
          type="button"
          onClick={() => { setOpen(true); setHover(false); }}
          aria-label={`Explain ${title}`}
          style={{
            font: "inherit", color: "inherit", background: "none", border: "none",
            padding: 0, cursor: "pointer", textAlign: "left",
            borderBottom: "1px dotted var(--accent)",
          }}>
          {label}
        </button>
        {hover && (
          <span role="tooltip" style={{
            position: "absolute", left: 0, top: "1.6em", zIndex: 60, width: 300,
            background: "var(--panel-2)", border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)", padding: "9px 11px",
            fontSize: 11.5, lineHeight: 1.6, color: "var(--fg-dim)",
            boxShadow: "0 10px 30px rgba(0,0,0,.45)", fontWeight: 400,
            whiteSpace: "normal", textTransform: "none", letterSpacing: 0,
          }}>
            <span style={{ display: "block", fontWeight: 600, color: "var(--accent)", marginBottom: 3 }}>
              {title}{entry?.full ? ` — ${entry.full}` : ""}
            </span>
            {short}
            <span style={{ display: "block", marginTop: 5, fontSize: 10.5, color: "var(--fg-mute)" }}>
              {full ? "click to learn the whole concept" : "click for the glossary entry"}
            </span>
          </span>
        )}
      </span>
      {open && <ConceptModal term={t} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Full-concept reader. Falls back to the glossary definition if no long-form
 *  explanation has been written for this term yet. */
function ConceptModal({ term, onClose }: { term: string; onClose: () => void }) {
  const c = concept(term);
  const entry = lookup(term);
  const title = c?.term ?? entry?.term ?? term;

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    // Stop the page behind the reader from scrolling under it.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div onClick={onClose} role="dialog" aria-modal="true" aria-label={`${title} explained`}
      style={{
        position: "fixed", inset: 0, zIndex: 200, background: "rgba(0,0,0,.62)",
        display: "grid", placeItems: "center", padding: 20,
        animation: "overlayIn .12s ease",
      }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: 760, maxWidth: "100%", maxHeight: "86vh", overflowY: "auto",
        background: "var(--panel)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)", boxShadow: "0 24px 70px rgba(0,0,0,.55)",
      }}>
        <header className="panel-head" style={{
          position: "sticky", top: 0, background: "var(--panel)", zIndex: 1,
          borderBottom: "1px solid var(--border)",
        }}>
          <div className="panel-titles">
            <h3 className="panel-title">{title}{entry?.full ? ` — ${entry.full}` : ""}</h3>
            <span className="panel-sub">the whole concept, from scratch</span>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close"
            style={{ width: 30, height: 30 }}>✕</button>
        </header>

        <div className="panel-body" style={{ fontSize: 13, lineHeight: 1.8, color: "var(--fg-dim)" }}>
          {c ? (
            <>
              <p style={{ fontSize: 14, color: "var(--fg)", fontWeight: 500, marginBottom: 16 }}>
                {c.oneLine}
              </p>

              <Section title="The intuition">{c.intuition}</Section>

              <Section title="How it actually works">
                {c.mechanics.map((p, i) => (
                  <p key={i} style={{ marginBottom: i === c.mechanics.length - 1 ? 0 : 10 }}>{p}</p>
                ))}
              </Section>

              <Section title="How to read it on this page">{c.onThisPage}</Section>

              <Section title="The usual mistake" tone="warn">{c.pitfall}</Section>
            </>
          ) : (
            <>
              <p style={{ color: "var(--fg)" }}>{entry?.def}</p>
              {entry?.eli10 && (
                <p style={{ marginTop: 10 }}>
                  <b style={{ color: "var(--accent)" }}>In plain words: </b>{entry.eli10}
                </p>
              )}
              <p style={{ marginTop: 12, fontSize: 12, color: "var(--fg-mute)" }}>
                A full walkthrough has not been written for this term yet.
              </p>
            </>
          )}

          <div style={{ marginTop: 20, paddingTop: 12, borderTop: "1px solid var(--border-soft)" }}>
            <Link href={`/glossary#${slug(title)}`} onClick={onClose}
              style={{ color: "var(--accent)", fontSize: 12 }}>
              See this in the full glossary →
            </Link>
            <span style={{ float: "right", fontSize: 11.5, color: "var(--fg-mute)" }}>Esc to close</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children, tone }: {
  title: string; children: React.ReactNode; tone?: "warn";
}) {
  return (
    <section style={{ marginBottom: 16 }}>
      <h4 style={{
        fontSize: 11, fontWeight: 700, letterSpacing: ".6px", textTransform: "uppercase",
        color: tone === "warn" ? "var(--warn)" : "var(--accent)", margin: "0 0 5px",
      }}>{title}</h4>
      {typeof children === "string" ? <p style={{ margin: 0 }}>{children}</p> : children}
    </section>
  );
}
