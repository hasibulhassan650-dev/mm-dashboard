"use client"; // Error boundaries must be Client Components

import { useEffect } from "react";

export default function Error({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="stub">
      <div className="stub-ic" style={{ background: "color-mix(in oklab, var(--neg) 14%, transparent)", color: "var(--neg)", fontSize: 26, fontWeight: 700 }}>!</div>
      <h2>Couldn&apos;t load this data</h2>
      <p>The data service may be temporarily unavailable. This is usually transient — try again in a moment.</p>
      <button
        onClick={() => unstable_retry()}
        style={{
          padding: "7px 16px", borderRadius: 7, fontSize: 13, fontWeight: 500,
          background: "var(--accent-soft)", color: "var(--accent)", border: "1px solid color-mix(in oklab, var(--accent) 40%, transparent)",
        }}
      >
        Try again
      </button>
      {error.digest && <p className="mono" style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 12 }}>ref: {error.digest}</p>}
    </div>
  );
}
