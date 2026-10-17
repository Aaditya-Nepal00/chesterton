import { useEffect, useState } from "react";
import {
  getAggregateFromServer,
  getCountFromServer,
  onSnapshot,
  sum,
  type DocumentReference,
  type Query,
} from "firebase/firestore";

export interface Live<T> {
  data: T;
  loading: boolean;
  error: string | null;
}

function describe(err: unknown): string {
  const e = err as { code?: string; message?: string };
  return e?.code ? `${e.code}: ${e.message ?? ""}` : String(err);
}

/** Realtime query. `make` returns null when the query can't be built (no config). */
export function useLiveQuery<T>(
  make: () => Query | null,
  map: (id: string, data: Record<string, unknown>) => T,
  deps: unknown[],
): Live<T[]> {
  const [state, setState] = useState<Live<T[]>>({ data: [], loading: true, error: null });
  useEffect(() => {
    const q = make();
    if (!q) {
      setState({ data: [], loading: false, error: null });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    return onSnapshot(
      q,
      (snap) =>
        setState({
          data: snap.docs.map((d) => map(d.id, d.data())),
          loading: false,
          error: null,
        }),
      (err) => setState({ data: [], loading: false, error: describe(err) }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

/** Realtime single document. */
export function useLiveDoc<T>(
  make: () => DocumentReference | null,
  map: (id: string, data: Record<string, unknown>) => T,
  deps: unknown[],
): Live<T | null> {
  const [state, setState] = useState<Live<T | null>>({ data: null, loading: true, error: null });
  useEffect(() => {
    const ref = make();
    if (!ref) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    setState((s) => ({ ...s, loading: true }));
    return onSnapshot(
      ref,
      (snap) =>
        setState({
          data: snap.exists() ? map(snap.id, snap.data()) : null,
          loading: false,
          error: null,
        }),
      (err) => setState({ data: null, loading: false, error: describe(err) }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

/** count() aggregation (server-side, not realtime; refreshed when `refreshKey` changes). */
export function useCount(make: () => Query | null, refreshKey: unknown): Live<number | null> {
  const [state, setState] = useState<Live<number | null>>({ data: null, loading: true, error: null });
  useEffect(() => {
    const q = make();
    if (!q) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    getCountFromServer(q)
      .then((snap) => !cancelled && setState({ data: snap.data().count, loading: false, error: null }))
      .catch((err) => !cancelled && setState({ data: null, loading: false, error: describe(err) }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);
  return state;
}

/** sum() aggregations over numeric fields (server-side). */
export function useSums<K extends string>(
  make: () => Query | null,
  fields: Record<K, string>,
  refreshKey: unknown,
): Live<Record<K, number> | null> {
  const [state, setState] = useState<Live<Record<K, number> | null>>({ data: null, loading: true, error: null });
  useEffect(() => {
    const q = make();
    if (!q) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    // One aggregation per field: several sum()s in a single request need a composite
    // index, one sum() uses the automatic single-field index.
    const entries = Object.entries(fields) as [K, string][];
    Promise.all(
      entries.map(([k, f]) =>
        getAggregateFromServer(q, { v: sum(f) }).then((snap) => [k, snap.data().v ?? 0] as const),
      ),
    )
      .then((pairs) => {
        if (cancelled) return;
        setState({ data: Object.fromEntries(pairs) as Record<K, number>, loading: false, error: null });
      })
      .catch((err) => !cancelled && setState({ data: null, loading: false, error: describe(err) }));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);
  return state;
}

/** Re-render periodically so relative times stay fresh. */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
