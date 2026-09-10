"use client";

import * as React from "react";

import { ApiClientError } from "@/lib/api";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Loads data on mount and exposes loading/empty/error state, which every
 * data-backed view in this app is required to render.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: React.DependencyList = []): AsyncState<T> {
  const [data, setData] = React.useState<T | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [nonce, setNonce] = React.useState(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const callback = React.useCallback(fn, deps);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    callback()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiClientError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Unexpected error",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [callback, nonce]);

  return { data, loading, error, reload: () => setNonce((n) => n + 1) };
}
