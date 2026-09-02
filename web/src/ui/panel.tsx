import {
  useEffect,
  useId,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { X } from "lucide-react";
import { Button } from "./button";
import { cn } from "./cn";
import { Field, Input } from "./field";

/** §B.2 · L2. A plane inside a plane drops a step rather than repeating the
 *  frame -- `nested` is that step. */
export function Panel({
  children,
  className,
  nested,
}: {
  children: ReactNode;
  className?: string;
  nested?: boolean;
}) {
  return (
    <section
      className={cn(
        nested
          ? "rounded-card border border-hairline"
          : "rounded-panel border border-edge-soft bg-surface shadow-plane",
        className,
      )}
    >
      {children}
    </section>
  );
}

/** §A.19.2 · the section card's 40px L0 header, its title in the eyebrow face. */
export function SectionHeader({
  title,
  counter,
  action,
}: {
  title: string;
  counter?: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex h-10 items-center gap-4 border-b border-hairline bg-chrome px-5">
      <h2 className="min-w-0 truncate font-mono text-10 uppercase tracking-eyebrow text-ink-note">
        {title}
      </h2>
      {counter ? (
        <span className="ml-auto shrink-0 text-11 text-ink-note">
          {counter}
        </span>
      ) : null}
      {action ? <span className="ml-auto shrink-0">{action}</span> : null}
    </header>
  );
}

/**
 * §B.13.1 · modals and overlaying sheets trap focus and restore it to their
 * trigger. **A pushing record panel does not trap focus** -- the table behind it
 * stays navigable, which is what `j`/`k` is for.
 */
export function useFocusTrap(open: boolean) {
  const ref = useRef<HTMLDivElement | null>(null);
  const restoreTo = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);
  /* eslint-disable react-hooks/refs */
  if (open && !wasOpen.current)
    restoreTo.current = document.activeElement as HTMLElement | null;
  wasOpen.current = open;
  /* eslint-enable react-hooks/refs */

  useEffect(() => {
    if (!open) return;
    const previous = restoreTo.current;
    const container = ref.current;
    const focusable = () =>
      Array.from(
        container?.querySelectorAll<HTMLElement>(
          "a[href], button:not([disabled]), input:not([disabled]), " +
            "select:not([disabled]), textarea:not([disabled]), " +
            '[tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
    if (!container?.contains(document.activeElement)) focusable()[0]?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previous?.focus();
    };
  }, [open]);
  return ref;
}

/**
 * Click-outside for a scrim, read from where the press *began*. A drag that
 * starts inside the dialog and ends on the scrim dispatches its click on the
 * scrim, because that is their nearest common ancestor -- so the click's own
 * target says "outside" for what was really a text selection.
 */
export function useScrimDismiss(onDismiss: () => void, blocked?: boolean) {
  const pressedOnScrim = useRef(false);
  return {
    onMouseDown(event: MouseEvent<HTMLElement>) {
      pressedOnScrim.current = event.target === event.currentTarget;
    },
    onClick(event: MouseEvent<HTMLElement>) {
      if (
        !blocked &&
        pressedOnScrim.current &&
        event.target === event.currentTarget
      )
        onDismiss();
    },
  };
}

/**
 * §B.8.5 · the 440px record panel. It **pushes** the content region rather than
 * overlaying it, and takes no scrim, so the table behind it stays navigable --
 * which is the whole point of `j`/`k`. A footer sits below the scrolling body
 * on the panel's own surface: it must never scroll away from what it acts on.
 */
export function RecordPanel({
  title,
  open,
  onClose,
  footer,
  children,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  footer?: ReactNode;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <aside
      aria-label={title}
      className="flex w-[440px] shrink-0 flex-col overflow-hidden border-l border-edge-soft bg-surface"
    >
      <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-hairline px-5">
        <h2 className="min-w-0 truncate text-20 tracking-display text-ink">
          {title}
        </h2>
        <Button
          variant="ghost"
          size="sm"
          iconOnly
          aria-label="Cerrar el panel"
          onClick={onClose}
        >
          <X aria-hidden className="size-4" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
      {footer ? (
        <div className="shrink-0 border-t border-hairline px-5 py-3">
          {footer}
        </div>
      ) : null}
    </aside>
  );
}

/**
 * §B.8.5 · a modal is 560px for a confirmation and 720px for a form, L3, always
 * scrimmed, always focus-trapped, restoring focus to its trigger on close.
 */
export function Modal({
  open,
  title,
  size = "form",
  busy,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  size?: "confirm" | "form";
  busy?: boolean;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const trap = useFocusTrap(open);
  const scrim = useScrimDismiss(onClose, busy);
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-scrim p-8"
      {...scrim}
    >
      <div
        ref={trap}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            if (!busy) onClose();
          }
        }}
        className={cn(
          "max-w-full rounded-card border border-edge-soft bg-surface shadow-overlay",
          size === "confirm" ? "w-[560px]" : "w-[720px]",
        )}
      >
        <div className="p-5">
          <h2 className="text-20 tracking-display text-ink">{title}</h2>
          {children}
        </div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-hairline px-5 py-4">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}

/**
 * §B.8.5 + §B.6.2 · a confirmation names the consequence in its body and puts
 * the consequence in the button -- `Eliminar a Andrés Peña` rather than
 * `Aceptar`. This is the one place a destructive button is filled.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  busyLabel,
  confirmText,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  busyLabel?: string;
  /** Typed back before the confirm button arms. */
  confirmText?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return open ? (
    <ConfirmBody
      title={title}
      body={body}
      confirmLabel={confirmLabel}
      busyLabel={busyLabel}
      confirmText={confirmText}
      busy={busy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  ) : null;
}

function ConfirmBody({
  title,
  body,
  confirmLabel,
  busyLabel,
  confirmText,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  busyLabel?: string;
  confirmText?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const inputId = useId();
  const [typed, setTyped] = useState("");
  const answered = typed.trim() === confirmText;
  const armed = !confirmText || answered;

  return (
    <Modal
      open
      title={title}
      size="confirm"
      busy={busy}
      onClose={onCancel}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancelar
          </Button>
          <Button
            variant="destructive"
            confirming
            onClick={() => {
              setTyped("");
              onConfirm();
            }}
            busy={busy}
            busyLabel={busyLabel}
            disabled={!armed}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="mt-2 text-14 text-ink-body">{body}</p>
      {confirmText ? (
        <Field
          label={`Escriba ${confirmText} para confirmar`}
          htmlFor={inputId}
          error={typed && !answered ? `Eso no es ${confirmText}.` : undefined}
          className="mt-4"
        >
          <Input
            id={inputId}
            value={typed}
            invalid={!!typed && !answered}
            disabled={busy}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setTyped(event.currentTarget.value)}
          />
        </Field>
      ) : null}
    </Modal>
  );
}
