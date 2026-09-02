import { X } from "lucide-react";
import type { Me } from "@/api/queries";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { useFocusTrap, useScrimDismiss } from "@/ui/panel";
import { EmptyState } from "@/ui/states";
import { GeneralSection } from "./general";
import { PeopleSection } from "./people";
import { ActivitySection } from "./activity";
import {
  SETTINGS_GROUPS,
  reachableSections,
  sectionById,
  useSettingsDialog,
} from "./use-settings";

/**
 * §B.8.4·4 · **Ajustes is one dialog, not routes.** 1120 × 720 capped at the
 * viewport, L3, centred over the scrim. The open section is a search param on
 * whatever route is showing, so the dialog never takes the page out from under
 * anyone and `Escape` returns exactly where you were.
 */
export function SettingsDialog({ me }: { me: Me }) {
  const settings = useSettingsDialog();
  // §B.8.3 · a `cashier` does not reach this dialog. Not by the gear, which is
  // absent from their footer; not by ⌘,; and not by a pasted `?settings=`
  // param, which is what this gate is for.
  if (!settings.open || reachableSections(me.role).length === 0) return null;
  return <Dialog me={me} id={settings.id!} />;
}

function Dialog({ me, id }: { me: Me; id: string }) {
  const settings = useSettingsDialog();
  const trap = useFocusTrap(true);
  const scrim = useScrimDismiss(settings.close);
  const reachable = reachableSections(me.role);
  const current = sectionById(id);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-8"
      {...scrim}
    >
      <div
        ref={trap}
        role="dialog"
        aria-modal="true"
        aria-label="Ajustes"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            settings.close();
          }
        }}
        className={cn(
          "flex h-[720px] max-h-full w-[1120px] max-w-full overflow-hidden",
          "rounded-panel border border-edge-soft bg-surface shadow-overlay",
        )}
      >
        <nav
          aria-label="Secciones de ajustes"
          className="flex w-60 shrink-0 flex-col gap-4 overflow-y-auto border-r border-hairline bg-chrome p-3"
        >
          {SETTINGS_GROUPS.map((group) => {
            const items = reachable.filter(
              (section) => section.group === group,
            );
            if (items.length === 0) return null;
            return (
              <div key={group}>
                <p className="px-2.5 pb-2 font-mono text-10 uppercase tracking-eyebrow text-ink-note">
                  {group}
                </p>
                <div className="flex flex-col gap-0.5">
                  {items.map((section) => {
                    const active = section.id === id;
                    return (
                      <button
                        key={section.id}
                        type="button"
                        aria-current={active ? "page" : undefined}
                        onClick={() => settings.show(section.id, true)}
                        className={cn(
                          "flex h-[34px] items-center gap-2.5 rounded-control px-2.5 text-14",
                          "transition-[background-color,color] duration-140 ease-out",
                          active
                            ? "bg-surface font-medium text-ink shadow-segment"
                            : "text-ink-body hover:text-ink",
                        )}
                      >
                        <section.icon
                          aria-hidden
                          strokeWidth={1.5}
                          className="size-4 shrink-0"
                        />
                        {section.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-11 shrink-0 items-center justify-end px-3">
            <Button
              variant="ghost"
              size="sm"
              iconOnly
              aria-label="Cerrar los ajustes"
              onClick={settings.close}
            >
              <X aria-hidden className="size-4" />
            </Button>
          </div>
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 pb-6 pt-1">
            {current?.id === "general" ? (
              <GeneralSection me={me} />
            ) : current?.id === "people" ? (
              <PeopleSection me={me} />
            ) : current?.id === "activity" ? (
              <ActivitySection />
            ) : (
              <EmptyState
                kind="deliberate"
                title="Esa sección no existe."
                body="Elija una sección del panel de la izquierda."
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
