import { useCallback, useEffect, useState } from 'react';

export interface AsyncState<T> { data: T | null; loading: boolean; error: string | null; reload: () => void }

export function useApi<T>(loader: () => Promise<T>, dependencies: readonly unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let active = true; setLoading(true); setError(null);
    void loader().then((result) => { if (active) setData(result); }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'Something went wrong'); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // The caller controls when the loader should be re-run through dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, revision]);
  return { data, loading, error, reload };
}
