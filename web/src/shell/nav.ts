import {
  BarChart3,
  LayoutDashboard,
  MessageCircle,
  Package,
  ShoppingCart,
  Store,
  Tag,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Role } from "@/api/queries";

export interface NavItem {
  /** The key `/api/nav-counters` reports this item's count under. */
  key: string;
  label: string;
  to: string;
  icon: LucideIcon;
  roles: Role[];
  /** §B.13.2 · the `g` sequence that reaches it. */
  goto: string;
}

const OFFICE: Role[] = ["owner", "admin", "platform_admin"];
const EVERYONE: Role[] = ["owner", "admin", "cashier", "platform_admin"];

/**
 * §A.13.1 · seven items, flat, in the drawn order. Seven is the ceiling for a
 * flat list and Botica is at it: everything a network *configures* or *audits*
 * is a section of the settings dialog and appears nowhere here.
 *
 * §B.8.3 · a `cashier` sees **Mostrador** and a read-only **Inventario**, and
 * nothing else. The prototype's Mostrador screen draws a cashier the full
 * seven-item nav; that is a prototype artefact and must not be reproduced.
 */
export const NAV: NavItem[] = [
  {
    key: "dashboard",
    label: "Panel",
    to: "/dashboard",
    icon: LayoutDashboard,
    roles: OFFICE,
    goto: "p",
  },
  {
    key: "inventory",
    label: "Inventario",
    to: "/inventory",
    icon: Package,
    roles: EVERYONE,
    goto: "i",
  },
  {
    key: "purchasing",
    label: "Compras",
    to: "/purchasing",
    icon: ShoppingCart,
    roles: OFFICE,
    goto: "c",
  },
  {
    key: "pricing",
    label: "Precios",
    to: "/pricing",
    icon: Tag,
    roles: OFFICE,
    goto: "r",
  },
  {
    key: "counter",
    label: "Mostrador",
    to: "/counter",
    icon: MessageCircle,
    roles: EVERYONE,
    goto: "m",
  },
  {
    key: "locations",
    label: "Sedes",
    to: "/locations",
    icon: Store,
    roles: OFFICE,
    goto: "s",
  },
  {
    key: "reports",
    label: "Reportes",
    to: "/reports",
    icon: BarChart3,
    roles: OFFICE,
    goto: "e",
  },
];

/**
 * §B.8.3 · the four Spanish role labels, decided in S0. The handoff draws
 * `Marcela Ríos · Administradora` and `Andrés Peña · Mostrador · Chapinero`,
 * and design-system reproduces drawn strings verbatim -- so the feminine for
 * the two gendered nouns matches the drawing rather than inventing a third
 * convention beside it.
 */
const ROLE_LABEL: Record<Role, string> = {
  platform_admin: "Plataforma",
  owner: "Propietaria",
  admin: "Administradora",
  cashier: "Mostrador",
};

export function roleLabel(role: Role | undefined) {
  return role ? ROLE_LABEL[role] : "";
}

export function roleList(roles: Role[]) {
  const names = roles
    .filter((role) => role !== "platform_admin")
    .map(roleLabel);
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} o ${names[names.length - 1]}`;
}

export function canReach(item: NavItem, role: Role | undefined) {
  return !!role && item.roles.includes(role);
}

export function itemFor(pathname: string) {
  return NAV.find((item) => pathname.startsWith(item.to));
}

export function reachable(role: Role | undefined) {
  return NAV.filter((item) => canReach(item, role));
}

/** §B.8.3 · `/` redirects by role: Panel for `owner` and `admin`, Mostrador for
 *  a `cashier`. */
export function landingFor(role: Role | undefined) {
  return role === "cashier" ? "/counter" : "/dashboard";
}

export function gotoTargets(role: Role | undefined) {
  const map = new Map<string, NavItem>();
  for (const item of reachable(role)) map.set(item.goto, item);
  return map;
}
