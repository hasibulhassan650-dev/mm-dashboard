"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/",           label: "Overview"    },
  { href: "/cashflows",  label: "Cash Flows"  },
  { href: "/omo",        label: "OMO"         },
  { href: "/yields",     label: "Yields"      },
  { href: "/callmoney",  label: "Call Money"  },
  { href: "/fx",         label: "FX Auctions" },
  { href: "/refrate",    label: "Ref Rates"   },
  { href: "/securities", label: "Securities"  },
];

export default function Nav() {
  const path = usePathname();
  return (
    <header className="border-b border-gray-800 bg-gray-950">
      <div className="max-w-7xl mx-auto px-6">
        {/* Brand row */}
        <div className="flex items-center justify-between h-12 border-b border-gray-800/60">
          <div className="flex items-center gap-3">
            <span className="w-1.5 h-6 rounded-sm bg-teal-500" />
            <span className="text-sm font-semibold tracking-widest uppercase text-teal-400 letter-spacing-wide">
              BB Market Intelligence
            </span>
          </div>
          <span className="text-xs text-gray-500 font-mono">
            Bangladesh Bank · Live Data
          </span>
        </div>
        {/* Nav row */}
        <nav className="flex items-center gap-0.5 h-10 overflow-x-auto">
          {links.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`px-3 py-1 text-xs font-medium transition-colors whitespace-nowrap ${
                path === href
                  ? "text-teal-400 border-b-2 border-teal-500"
                  : "text-gray-400 hover:text-gray-200"
              }`}
            >
              {label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
