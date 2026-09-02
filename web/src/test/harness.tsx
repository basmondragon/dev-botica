import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { ToastProvider } from "@/ui/toast";
import type { Me } from "@/api/queries";

export const MARCELA: Me = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Marcela Ríos",
  email: "marcela.rios@la45.co",
  role: "admin",
  platform_admin: false,
  tenant: {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Droguerías La 45",
    slug: "demo-la-45",
    status: "active",
  },
  location_id: null,
  location_name: null,
  readable_location_ids: [],
  app_version: "0.1.0",
  admin_console: false,
};

export const ANDRES: Me = {
  ...MARCELA,
  id: "33333333-3333-3333-3333-333333333333",
  name: "Andrés Peña",
  email: "andres.pena@la45.co",
  role: "cashier",
  location_id: "44444444-4444-4444-4444-444444444444",
  location_name: "Chapinero",
};

export function renderWithProviders(ui: ReactNode, me?: Me) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  if (me) client.setQueryData(["me"], me);
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  );
}
