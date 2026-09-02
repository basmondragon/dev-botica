import { useNavigate, useSearch } from "@tanstack/react-router";
import { Building2, ScrollText, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Role } from "@/api/queries";

export interface SettingsSection {
  id: string;
  label: string;
  group: string;
  icon: LucideIcon;
  roles: Role[];
}

const OFFICE: Role[] = ["owner", "admin", "platform_admin"];

/**
 * §B.8.4·4 · **S0 renders three rail items and no more.** A section a later
 * stage owns is not in the rail at all: §B.10.2's rule is that a section a
 * capability can empty is gated at its header, not inside its body.
 *
 * The full rail design-system names -- Sedes y dispositivos, Facturación
 * electrónica, Precios y topes, Asistente, Cumplimiento, Exportaciones -- is
 * added by the stages that own those sections.
 */
export const SETTINGS: SettingsSection[] = [
  {
    id: "general",
    label: "General",
    group: "Organización",
    icon: Building2,
    roles: OFFICE,
  },
  {
    id: "people",
    label: "Personas",
    group: "Organización",
    icon: Users,
    roles: OFFICE,
  },
  {
    id: "activity",
    label: "Actividad",
    group: "Registros",
    icon: ScrollText,
    roles: OFFICE,
  },
];

export const SETTINGS_GROUPS = ["Organización", "Registros"] as const;

export function sectionById(id: string | undefined) {
  return SETTINGS.find((section) => section.id === id);
}

export function reachableSections(role: Role | undefined) {
  return SETTINGS.filter((section) => !!role && section.roles.includes(role));
}

export function useSettingsDialog() {
  const search = useSearch({ strict: false }) as { settings?: string };
  const navigate = useNavigate();
  const id = typeof search.settings === "string" ? search.settings : undefined;

  return {
    id,
    open: id !== undefined,
    show: (next: string, replace = false) => {
      void navigate({
        to: ".",
        search: (previous: object) => ({ ...previous, settings: next }),
        replace,
      });
    },
    close: () => {
      void navigate({
        to: ".",
        search: ({ settings: _closed, ...rest }: { settings?: string }) => rest,
      });
    },
  };
}
