import { useState } from "react";
import { ApiError } from "@/api/client";
import { useCreateMove, type StockRow } from "@/api/inventory";
import type { Me } from "@/api/queries";
import { Button } from "@/ui/button";
import { Field, Input, Textarea } from "@/ui/field";
import { count } from "@/ui/format";
import { Modal } from "@/ui/panel";
import { Select } from "@/ui/select";
import { RegionError } from "@/ui/states";
import { useToast } from "@/ui/toast";
import {
  DIRECT_TYPES,
  MOVE_REASON,
  MOVE_TYPE,
  REASONS_BY_TYPE,
} from "./vocabulary";

/**
 * The direct-movement dialog, reached from the record panel.
 *
 * One type, one quantity, one reason from the fixed vocabulary and an optional
 * note. **The confirm button states the consequence** -- `Restar 12 unidades del
 * lote A-2291 en Chapinero` -- and destructive is the confirm and never a form
 * default (§B.6.2).
 *
 * **A `cashier` may write `Merma` and `Vencimiento` and may not write `Ajuste`**
 * (design-system §B.17·3, answered in S3's *UI*). A negative movement a cashier
 * can point at on a shelf is something the person who found it should record
 * while it is in their hand, and requiring a regente for it means it never gets
 * recorded. A positive adjustment is the one movement in the product that
 * creates value out of nothing, and it is the exact shape of a loss being
 * covered -- so the type is **absent from the selector**, not disabled, and the
 * endpoint refuses it if called directly.
 */
export function MovementDialog({
  open,
  row,
  me,
  onClose,
}: {
  open: boolean;
  row: StockRow;
  me: Me;
  onClose: () => void;
}) {
  const elevated = me.role !== "cashier";
  const available = DIRECT_TYPES.filter(
    (type) => elevated || type !== "adjustment",
  );
  const [type, setType] = useState<(typeof DIRECT_TYPES)[number]>(
    available[0] ?? "shrinkage",
  );
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState(
    REASONS_BY_TYPE[available[0] ?? "shrinkage"]![0]!,
  );
  const [note, setNote] = useState("");
  const create = useCreateMove();
  const toast = useToast();

  const units = Number(quantity);
  const valid = Number.isInteger(units) && units > 0;
  const where = row.lot_code
    ? `del lote ${row.lot_code} en ${row.location_name}`
    : `en ${row.location_name}`;
  const consequence = valid
    ? `${type === "adjustment" ? "Ajustar" : "Restar"} ${count(units)} ${
        units === 1 ? "unidad" : "unidades"
      } ${where}`
    : "Registrar movimiento";

  function submit() {
    if (!valid) return;
    create.mutate(
      {
        location_id: row.location_id,
        item_id: row.item_id,
        lot_id: row.lot_id,
        // `shrinkage` and `expiry` always subtract, so the quantity is a count
        // and the sign is the type's. `adjustment` is the one direct type that
        // takes either sign, and the button above already said which.
        quantity: units,
        type,
        reason: reason as never,
        note,
      },
      {
        onSuccess: () => {
          toast(`Movimiento registrado ${where}.`);
          onClose();
          setQuantity("");
          setNote("");
        },
        // **The dialog stays open with its values until the move is
        // appended** (§B.10.1). A dialog that closes optimistically on an
        // append is a dialog that loses the reason somebody typed.
      },
    );
  }

  return (
    <Modal
      open={open}
      title="Registrar movimiento"
      size="confirm"
      busy={create.isPending}
      onClose={onClose}
      footer={
        <>
          <Button
            variant="secondary"
            size="sm"
            onClick={onClose}
            disabled={create.isPending}
          >
            Cancelar
          </Button>
          <Button
            variant="destructive"
            size="sm"
            busy={create.isPending}
            disabled={!valid}
            onClick={submit}
          >
            {consequence}
          </Button>
        </>
      }
    >
      <div className="mt-4 flex flex-col gap-4">
        <p className="text-14 text-ink-body">
          {row.item_name}
          {row.lot_code ? ` · lote ${row.lot_code}` : ""} · {row.location_name}
          {" · "}
          <span className="tabular-nums">{count(row.quantity)}</span> en
          existencia.
        </p>

        <Field label="Tipo">
          <Select
            value={type}
            onValueChange={(next) => {
              const chosen = next as (typeof DIRECT_TYPES)[number];
              setType(chosen);
              setReason(REASONS_BY_TYPE[chosen]![0]!);
            }}
            options={available.map((one) => ({
              value: one,
              label: MOVE_TYPE[one]!,
            }))}
          />
        </Field>

        <Field
          label="Cantidad en unidades base"
          error={
            quantity !== "" && !valid
              ? "Escriba un número entero de unidades mayor que cero."
              : undefined
          }
        >
          <Input
            inputMode="numeric"
            value={quantity}
            aria-invalid={quantity !== "" && !valid}
            onChange={(event) => setQuantity(event.currentTarget.value)}
          />
        </Field>

        <Field label="Motivo">
          <Select
            value={reason}
            onValueChange={setReason}
            options={(REASONS_BY_TYPE[type] ?? []).map((one) => ({
              value: one,
              label: MOVE_REASON[one]!,
            }))}
          />
        </Field>

        <Field label="Nota (opcional)">
          <Textarea
            rows={2}
            value={note}
            onChange={(event) => setNote(event.currentTarget.value)}
          />
        </Field>

        {create.isError ? (
          <RegionError
            title="No pudimos registrar el movimiento."
            detail={(create.error as Error).message}
            requestId={
              create.error instanceof ApiError
                ? create.error.requestId
                : undefined
            }
            onRetry={submit}
          />
        ) : null}
      </div>
    </Modal>
  );
}
