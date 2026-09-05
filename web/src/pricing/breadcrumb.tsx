import { Link } from "@tanstack/react-router";

/**
 * §B.8.5 · `Precios`. One route, so the breadcrumb is one segment and it is
 * not a link to itself.
 */
export function PricingBreadcrumb() {
  return (
    <nav aria-label="Ruta" className="flex items-center gap-1.5 text-12">
      <Link
        to="/pricing"
        className="text-ink-label transition-colors duration-140 hover:text-ink"
      >
        Precios
      </Link>
    </nav>
  );
}
