import Link from "next/link";

/**
 * "See also" cross-links — connects a page to the ones a money-market reader
 * naturally jumps to next, so the dashboard reads as one linked terminal
 * rather than siloed pages.
 */
export interface Related { href: string; label: string; why: string }

export default function RelatedLinks({ items }: { items: Related[] }) {
  return (
    <div className="related">
      <span className="related-tag">See also</span>
      {items.map((r) => (
        <Link key={r.href} href={r.href} className="related-link">
          <span className="related-lb">{r.label}</span>
          <span className="related-why">{r.why}</span>
          <span className="related-arrow">→</span>
        </Link>
      ))}
    </div>
  );
}
