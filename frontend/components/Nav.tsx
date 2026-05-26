"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/",           label: "Overview" },
  { href: "/cashflows",  label: "Cash Flows" },
  { href: "/omo",        label: "OMO" },
  { href: "/yields",     label: "Yields" },
  { href: "/callmoney",  label: "Call Money" },
  { href: "/fx",         label: "FX Auctions" },
  { href: "/securities", label: "Securities" },
];

export default function Nav() {
  const path = usePathname();
  return (
    <header className="border-b border-gray-800 bg-gray-900">
      <div className="max-w-7xl mx-auto px-4 flex items-center gap-8 h-14">
        <span className="font-semibold text-white tracking-tight">MM Dashboard</span>
        <nav className="flex gap-1">
          {links.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`px-3 py-1.5 rounded text-sm transition-colors ${
                path === href
                  ? "bg-blue-600 text-white"
                  : "text-gray-400 hover:text-white hover:bg-gray-800"
              }`}
            >
              {label}
            </Link>
          ))}
        </nav>
        <span className="ml-auto text-xs text-gray-500">Bangladesh Bank · Live Data</span>
      </div>
    </header>
  );
}
