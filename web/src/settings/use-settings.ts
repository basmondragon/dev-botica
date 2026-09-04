import { useNavigate, useSearch } from "@tanstack/react-router";
import {
  Building2,
  FileText,
  FlaskConical,
  MonitorSmartphone,
  ScrollText,
  Truck,
  UserRound,
  Users,
} from "lucide-react";
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
 * §B.8.4·4 · a section a later stage owns is not in the rail at all: §B.10.2's
 * rule is that a section a capability can empty is gated at its header, not
 * inside its body.
 *
 * S0 shipped three items under **Organización** and **Registros**; S1 adds the
 * **Catálogo** group and its three; S2 adds **Sedes y dispositivos**; S5 adds
 * the **Operación** group and its first section, Facturación electrónica. The
 * rest of the rail design-system names -- Precios y topes, Asistente,
 * Cumplimiento, Exportaciones -- is added by the stages that own those
 * sections.
 *
 * **The `sync` settings group is a block inside `Sedes y dispositivos`, not a
 * tenth rail item.** §B.8.4·4 fixes nine sections and none of them is
 * `Sincronización`; the controls go where an administrator is already looking.
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
    id: "devices",
    label: "Sedes y dispositivos",
    group: "Organización",
    icon: MonitorSmartphone,
    roles: OFFICE,
  },
  {
    id: "catalog-taxonomy",
    label: "Laboratorios y categorías",
    group: "Catálogo",
    icon: FlaskConical,
    roles: OFFICE,
  },
  {
    id: "catalog-suppliers",
    label: "Proveedores",
    group: "Catálogo",
    icon: Truck,
    roles: OFFICE,
  },
  {
    id: "catalog-customers",
    label: "Clientes",
    group: "Catálogo",
    icon: UserRound,
    roles: OFFICE,
  },
  {
    // §B.8.4·4 · **Operación**, and S5's is the group's first section.
    //
    // **This is the only surface in the product that ever mentions the handoff
    // being off** (architecture §8), which is why the section is in the rail
    // for every office role even when nothing is connected: it is where a
    // person goes to turn it on, and a section that appeared only once it was
    // already on would be one nobody could reach.
    id: "invoicing",
    label: "Facturación electrónica",
    group: "Operación",
    icon: FileText,
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

export const SETTINGS_GROUPS = [
  "Organización",
  "Catálogo",
  "Operación",
  "Registros",
] as const;

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
