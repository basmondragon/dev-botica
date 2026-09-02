import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { Button } from "./button";
import { isEntryOrSelectionTarget } from "./keyboard-target";

export interface Shortcut {
  keys: string[];
  does: string;
}

/**
 * §B.13.2 · the `?` sheet. Every shortcut in the system appears here, and
 * `⌘K` appears as reserved and bound to nothing.
 */
export const OFFICE_SHORTCUTS: Shortcut[] = [
  { keys: ["j"], does: "Bajar una fila" },
  { keys: ["k"], does: "Subir una fila" },
  { keys: ["Enter"], does: "Abrir la fila" },
  { keys: ["x"], does: "Marcar la fila" },
  { keys: ["Esc"], does: "Cerrar, o quitar la selección" },
  { keys: ["/"], does: "Ir al buscador" },
  { keys: ["⌘", ","], does: "Abrir Ajustes" },
  { keys: ["g", "p"], does: "Ir a Panel" },
  { keys: ["g", "i"], does: "Ir a Inventario" },
  { keys: ["g", "c"], does: "Ir a Compras" },
  { keys: ["g", "r"], does: "Ir a Precios" },
  { keys: ["g", "m"], does: "Ir a Mostrador" },
  { keys: ["g", "s"], does: "Ir a Sedes" },
  { keys: ["g", "e"], does: "Ir a Reportes" },
  { keys: ["?"], does: "Ver los atajos" },
];

export function KeyboardSheet({
  shortcuts = OFFICE_SHORTCUTS,
}: {
  shortcuts?: Shortcut[];
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "?" && !isEntryOrSelectionTarget(event.target)) {
        event.preventDefault();
        setOpen((current) => !current);
      } else if (event.key === "Escape" && open) {
        event.stopPropagation();
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-scrim p-8"
      onClick={() => setOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Atajos de teclado"
        onClick={(event) => event.stopPropagation()}
        className="mt-[12vh] w-[480px] max-w-full rounded-card border border-edge-soft bg-surface shadow-overlay"
      >
        <header className="flex h-10 items-center justify-between gap-4 border-b border-hairline bg-chrome px-5">
          <h2 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
            Atajos de teclado
          </h2>
          <Button
            variant="ghost"
            size="xs"
            iconOnly
            aria-label="Cerrar los atajos"
            onClick={() => setOpen(false)}
          >
            <X aria-hidden className="size-3.5" />
          </Button>
        </header>
        <ul className="max-h-[60vh] overflow-y-auto p-5">
          {shortcuts.map((shortcut) => (
            <li
              key={shortcut.does}
              className="flex h-10 items-center justify-between gap-4 border-b border-hairline last:border-b-0"
            >
              <span className="text-14 text-ink-body">{shortcut.does}</span>
              <span className="flex shrink-0 items-center gap-1.5">
                {shortcut.keys.map((key) => (
                  <kbd
                    key={key}
                    className="flex h-6 min-w-6 items-center justify-center rounded-mark border border-hairline px-2 font-mono text-10 uppercase tracking-eyebrow text-ink-label"
                  >
                    {key}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
