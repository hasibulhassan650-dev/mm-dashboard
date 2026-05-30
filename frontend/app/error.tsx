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
    <div className="flex flex-col items-center justify-center py-20 text-center space-y-4">
      <div className="w-10 h-10 rounded-full border border-red-800 bg-red-950/40 flex items-center justify-center text-red-400 text-xl">
        !
      </div>
      <div>
        <h2 className="text-lg font-semibold text-white">Couldn&apos;t load this data</h2>
        <p className="text-sm text-gray-400 mt-1 max-w-md">
          The data service may be temporarily unavailable. This is usually transient — try again in a moment.
        </p>
      </div>
      <button
        onClick={() => unstable_retry()}
        className="px-4 py-1.5 rounded text-sm font-medium bg-teal-950 text-teal-400 border border-teal-800/60 hover:bg-teal-900/60 hover:text-teal-300 transition-colors"
      >
        Try again
      </button>
      {error.digest && (
        <p className="text-xs text-gray-600 font-mono">ref: {error.digest}</p>
      )}
    </div>
  );
}
