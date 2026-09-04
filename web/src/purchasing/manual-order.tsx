import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Plus, Trash2 } from "lucide-react";
import { useSuppliers } from "@/api/catalog";
import { api, toApiError } from "@/api/client";
import { useLocations } from "@/api/queries";
import { ItemCombobox } from "@/catalog/item-combobox";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { Modal } from "@/ui/panel";
import { Select } from "@/ui/select";
import { useMutation, useQueryClient } from "@tanstack/react-query";

interface Draft {
  key: number;
  itemId: string;
  name: string;
  quantity: string;
}

/**
 * **A manual order** (`source = manual`), so the enum value is not dead: a
 * supplier, a sede and typed lines.
 *
 * **`suggested_quantity` is null on every line it writes, not zero.** Nobody
 * proposed anything, and a zero there would enter the deviation measurement as
 * a proposal of nothing -- which is the one number this stage exists to keep
 * honest. The server writes the null; this form simply has no field for it.
 */
export function ManualOrderModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const locations = useLocations(open);
  const suppliers = useSuppliers(open);
  const [locationId, setLocationId] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [lines, setLines] = useState<Draft[]>([
    { key: 1, itemId: "", name: "", quantity: "" },
  ]);
  const [failure, setFailure] = useState("");

  const create = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/api/purchase-orders", {
        body: {
          location_id: locationId,
          supplier_id: supplierId,
          lines: lines
            .filter((line) => line.itemId && Number(line.quantity) > 0)
            .map((line) => ({
              item_id: line.itemId,
              quantity: Number(line.quantity),
            })),
        },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos crear la orden.");
      return data;
    },
    onSuccess: (order) => {
      void client.invalidateQueries({ queryKey: ["purchase-orders"] });
      void client.invalidateQueries({ queryKey: ["nav-counters"] });
      onClose();
      void navigate({ to: "/purchasing", search: { orden: order.id } });
    },
    onError: (error: unknown) =>
      setFailure(
        error instanceof Error ? error.message : "No pudimos crear la orden.",
      ),
  });

  const ready =
    !!locationId &&
    !!supplierId &&
    lines.some((line) => line.itemId && Number(line.quantity) > 0);

  return (
    <Modal
      open={open}
      title="Nueva orden"
      busy={create.isPending}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            disabled={!ready}
            busy={create.isPending}
            onClick={() => {
              setFailure("");
              create.mutate();
            }}
          >
            Crear orden
          </Button>
        </>
      }
    >
      <div className="mt-4 grid grid-cols-2 gap-4">
        <Field label="Sede" htmlFor="manual-order-sede">
          <Select
            id="manual-order-sede"
            value={locationId}
            onValueChange={(value) => setLocationId(String(value))}
            options={[
              { value: "", label: "Elija una sede" },
              ...(locations.data ?? []).map((one) => ({
                value: one.id,
                label: one.name,
              })),
            ]}
          />
        </Field>
        <Field label="Proveedor" htmlFor="manual-order-proveedor">
          <Select
            id="manual-order-proveedor"
            value={supplierId}
            onValueChange={(value) => setSupplierId(String(value))}
            options={[
              { value: "", label: "Elija un proveedor" },
              ...(suppliers.data ?? []).map((one) => ({
                value: one.id,
                label: one.name,
              })),
            ]}
          />
        </Field>
      </div>

      <div className="mt-5 flex flex-col gap-2">
        {lines.map((line, index) => (
          <div key={line.key} className="flex items-end gap-2">
            <div className="min-w-0 flex-1">
              <ItemCombobox
                value={line.itemId}
                type="product"
                ariaLabel={`Producto de la línea ${index + 1}`}
                onChange={(id, item) =>
                  setLines((current) =>
                    current.map((one) =>
                      one.key === line.key
                        ? { ...one, itemId: id, name: item?.name ?? "" }
                        : one,
                    ),
                  )
                }
              />
            </div>
            <Input
              value={line.quantity}
              inputMode="numeric"
              aria-label={`Cantidad de la línea ${index + 1}`}
              className="w-24 text-right tabular-nums"
              onChange={(event) =>
                setLines((current) =>
                  current.map((one) =>
                    one.key === line.key
                      ? { ...one, quantity: event.currentTarget.value }
                      : one,
                  ),
                )
              }
            />
            <Button
              variant="ghost"
              size="sm"
              iconOnly
              aria-label={`Quitar la línea ${index + 1}`}
              disabled={lines.length === 1}
              onClick={() =>
                setLines((current) =>
                  current.filter((one) => one.key !== line.key),
                )
              }
            >
              <Trash2 aria-hidden className="size-4" />
            </Button>
          </div>
        ))}
        <Button
          variant="ghost"
          size="sm"
          className="self-start"
          onClick={() =>
            setLines((current) => [
              ...current,
              { key: Date.now(), itemId: "", name: "", quantity: "" },
            ])
          }
        >
          <Plus aria-hidden className="size-4" />
          Agregar línea
        </Button>
      </div>

      {failure ? (
        <p role="alert" className="mt-4 text-12 text-critical">
          {failure}
        </p>
      ) : null}
    </Modal>
  );
}
