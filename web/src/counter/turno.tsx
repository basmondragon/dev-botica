import { useState } from "react";
import type { ShiftDoc } from "@/sync/registry";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Field, Input } from "@/ui/field";
import { money as pesosOf, time } from "@/ui/format";
import { Modal } from "@/ui/panel";
import { RegionError } from "@/ui/states";
import { pesos, toCents } from "./money";

/**
 * `Apertura de turno` and `Cierre de turno` — both 560px modals at counter
 * density, both **offline-capable client writes**.
 *
 * The till cannot sell without an open turno, and that is not a network block:
 * opening is local. A sale outside a cash session cannot be reconciled, and the
 * server's own `CHECK` makes it impossible anyway.
 */

export function OpenShift({
  open,
  onOpen,
  busy,
  failure,
}: {
  open: boolean;
  onOpen: (openingFloat: number) => void;
  busy?: boolean;
  failure?: string;
}) {
  const [value, setValue] = useState("");
  const [touched, setTouched] = useState(false);
  // Derived during render rather than in an effect, the way the quantity
  // stepper derives its draft from a committed value.
  const [session, setSession] = useState(false);
  if (open !== session) {
    setSession(open);
    setValue("");
    setTouched(false);
  }

  const invalid = touched && !/^\d+$/.test(value.trim());

  return (
    <Modal
      open={open}
      title="Apertura de turno"
      size="confirm"
      busy={busy}
      // **The dialog has no dismissal**, because there is nothing behind it: a
      // till with no open turno cannot sell, and a cashier who closed this
      // would be looking at an empty ticket they could not use.
      onClose={() => undefined}
      footer={
        <Button
          size="lg"
          variant="primary"
          busy={busy}
          busyLabel="Abriendo"
          onClick={() => {
            setTouched(true);
            if (/^\d+$/.test(value.trim())) onOpen(Number(value.trim()) * 100);
          }}
        >
          Abrir turno
        </Button>
      }
    >
      <div className="mt-5 flex flex-col gap-4">
        <p className="text-14 text-ink-body">
          Cuente el efectivo con el que abre la caja. El turno se abre en este
          equipo y no necesita conexión.
        </p>
        <Field
          label="Efectivo inicial en caja"
          htmlFor="opening-float"
          error={invalid ? "Escriba el monto en pesos, sin puntos." : undefined}
          required
        >
          <Input
            id="opening-float"
            autoFocus
            inputMode="numeric"
            invalid={invalid}
            className="h-control-counter text-20"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              setTouched(true);
              if (/^\d+$/.test(value.trim()))
                onOpen(Number(value.trim()) * 100);
            }}
          />
        </Field>
        {failure ? (
          <RegionError
            title="No pudimos abrir el turno en este equipo."
            detail={failure}
          />
        ) : null}
      </div>
    </Modal>
  );
}

export interface CloseReport {
  openingFloat: number;
  cashSales: number;
  cashReturns: number;
  expected: number;
}

/**
 * `Cierre de turno` — the shift's own arithmetic **before** it asks for
 * anything, and then one field.
 *
 * **The variance is never suppressed, never rounded away and never hidden
 * behind a confirmation that offers to make it zero** (ledger, disputed
 * columns). If operations are still queued the dialog says so and closes
 * anyway: a cash count is not a sync gate.
 */
export function CloseShift({
  open,
  shift,
  report,
  pending,
  onClose,
  onConfirm,
  busy,
  failure,
}: {
  open: boolean;
  shift: ShiftDoc | null;
  report: CloseReport;
  pending: number;
  onClose: () => void;
  onConfirm: (declaredTotal: number) => void;
  busy?: boolean;
  failure?: string;
}) {
  const [value, setValue] = useState("");
  const [touched, setTouched] = useState(false);
  const [session, setSession] = useState(false);
  if (open !== session) {
    setSession(open);
    setValue("");
    setTouched(false);
  }

  const counted = toCents(value.trim() || "0");
  const invalid = touched && !/^\d+$/.test(value.trim());
  const difference = counted - report.expected;
  const magnitude = Math.abs(difference);

  return (
    <Modal
      open={open}
      title="Cierre de turno"
      size="confirm"
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <Button size="lg" variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            size="lg"
            variant="primary"
            busy={busy}
            busyLabel="Cerrando"
            onClick={() => {
              setTouched(true);
              if (/^\d+$/.test(value.trim())) onConfirm(counted);
            }}
          >
            Cerrar turno
          </Button>
        </>
      }
    >
      <div className="mt-5 flex flex-col gap-4">
        <p className="text-14 text-ink-body">
          Turno abierto {shift ? time(shift.opened_at) : "—"}
        </p>
        <dl className="flex flex-col gap-2 border-t border-hairline pt-4">
          <Line label="Efectivo inicial" value={report.openingFloat} />
          <Line label="Ventas en efectivo" value={report.cashSales} />
          <Line label="Devoluciones en efectivo" value={-report.cashReturns} />
          <Line label="Efectivo esperado" value={report.expected} strong />
        </dl>
        <Field
          label="Efectivo contado"
          htmlFor="declared-total"
          error={invalid ? "Escriba el monto en pesos, sin puntos." : undefined}
          required
        >
          <Input
            id="declared-total"
            autoFocus
            inputMode="numeric"
            invalid={invalid}
            className="h-control-counter text-20"
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </Field>
        <div className="flex items-baseline justify-between border-t border-hairline pt-4">
          <span className="text-12 text-ink-body">Diferencia</span>
          <span
            className={cn(
              "text-20 tabular-nums",
              // §B.12.3 · the colour is never the only signal: the figure
              // carries its own sign and the record panel restates it.
              magnitude === 0
                ? "text-ink"
                : magnitude >= MATERIAL
                  ? "text-critical"
                  : "text-warning",
            )}
          >
            {pesosOf(pesos(difference))}
          </span>
        </div>
        {pending > 0 ? (
          <p className="text-12 text-ink-note">
            {pending} {pending === 1 ? "operación" : "operaciones"} sin enviar.
            El turno se cierra igual y se envían cuando vuelva la conexión.
          </p>
        ) : null}
        {failure ? (
          <RegionError
            title="No pudimos cerrar el turno en este equipo."
            detail={failure}
          />
        ) : null}
      </div>
    </Modal>
  );
}

/**
 * The threshold above which a difference is critical rather than a warning,
 * **stated by the surface rather than guessed** (§B.7): fifty thousand pesos is
 * roughly a day's float and is the figure a supervisor would want to hear about
 * the same afternoon. Nothing blocks on it — a shift closes with any variance,
 * because S4 owns no settings group in which a blocking threshold could live
 * (*Gated on*).
 */
export const MATERIAL = 5_000_000;

function Line({
  label,
  value,
  strong,
}: {
  label: string;
  value: number;
  strong?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between">
      <dt className={cn("text-12", strong ? "text-ink" : "text-ink-body")}>
        {label}
      </dt>
      <dd
        className={cn("tabular-nums text-ink", strong ? "text-16" : "text-14")}
      >
        {pesosOf(pesos(value))}
      </dd>
    </div>
  );
}
