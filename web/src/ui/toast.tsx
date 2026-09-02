import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";

/**
 * §B.10.3 · a toast is L3, bottom-right, five seconds, dismissible, and
 * **never carries the only copy of an action**.
 */
interface Toast {
  id: number;
  text: string;
}

const ToastContext = createContext<((text: string) => void) | null>(null);

const LIFETIME = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const show = useCallback((text: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, text }]);
    window.setTimeout(
      () => setToasts((current) => current.filter((toast) => toast.id !== id)),
      LIFETIME,
    );
  }, []);

  const value = useMemo(() => show, [show]);

  return (
    <ToastContext value={value}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-5 right-5 z-70 flex flex-col items-end gap-2"
      >
        {toasts.map((toast) => (
          <button
            key={toast.id}
            type="button"
            onClick={() =>
              setToasts((current) =>
                current.filter((one) => one.id !== toast.id),
              )
            }
            className="pointer-events-auto rounded-control bg-ink px-4 py-2.5 text-14 text-canvas shadow-overlay"
          >
            {toast.text}
          </button>
        ))}
      </div>
    </ToastContext>
  );
}

export function useToast() {
  const show = useContext(ToastContext);
  return show ?? (() => {});
}
