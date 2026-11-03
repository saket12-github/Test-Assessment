import { useEffect, useRef } from "react";

// Calls `fn` now and every `ms` while the tab is visible. Stops on unmount or when deps change.
export function usePolling(fn, ms, deps) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let stopped = false;
    let timer;
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === "visible") {
        try {
          await fnRef.current();
        } catch {
          /* the callback shows its own errors */
        }
      }
      if (!stopped) timer = setTimeout(tick, ms);
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
