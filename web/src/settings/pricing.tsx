import { useRef, useState } from "react";
import { ApiError } from "@/api/client";
import {
  useImportCaps,
  usePricingCaps,
  usePricingSettings,
  useSavePricingSettings,
  useSetCap,
  type PricingSettings,
} from "@/api/pricing";
import { Button } from "@/ui/button";
import { Checkbox, Field, Input } from "@/ui/field";
import { count, dayMonth, DOT, money } from "@/ui/format";
import { RegionError, SkeletonBar } from "@/ui/states";
import { Badge } from "@/ui/status";
import { useToast } from "@/ui/toast";
import { SectionHeading } from "./section";

/**
 * §B.8.4·4 · **Ajustes · Operación · Precios y topes**, a section of the
 * settings dialog and not a route -- reached at `/pricing?settings=pricing`, so
 * `Escape` returns exactly where you were.
 *
 * **This section also hosts the caps themselves**, because `Precios y topes` is
 * the place a regente looking for a cap will look, and because the header slot
 * that might otherwise have held them is not a settings surface.
 */
export function PricingSection() {
  const settings = usePricingSettings();
  if (settings.isPending) return <SectionSkeleton />;
  if (settings.isError || !settings.data) {
    return (
      <RegionError
        title="No pudimos cargar los ajustes de precios."
        detail={
          settings.error instanceof ApiError
            ? settings.error.message
            : "El servidor no respondió."
        }
        requestId={
          settings.error instanceof ApiError
            ? settings.error.requestId
            : undefined
        }
        onRetry={() => void settings.refetch()}
      />
    );
  }
  return <Form values={settings.data} />;
}

/** What each field will take, checked here **and** again by the server, which
 *  is the only place the whole group is checked at once. */
const BOUNDS: Record<string, { low: number; high: number; whole?: boolean }> = {
  margin_goal_pct: { low: 0.1, high: 95 },
  max_single_step_pct: { low: 0.1, high: 50 },
  rounding_unit: { low: 1, high: 10_000, whole: true },
  min_days_between_changes: { low: 0, high: 365, whole: true },
};

/** §B.5.7 · the refusal a field renders in its own help slot. `margin_goal_pct`
 *  is the one field an empty value is legal on: clearing it is how a tenant
 *  returns to the first-morning state. */
function check(key: string, raw: string): string | undefined {
  const text = raw.trim();
  if (!text) {
    return key === "margin_goal_pct" ? undefined : "Este campo es obligatorio.";
  }
  const value = Number(text.replace(",", "."));
  const bound = BOUNDS[key];
  if (!bound || Number.isNaN(value)) return "Escriba un número.";
  if (bound.whole && !Number.isInteger(value))
    return "Escriba un número entero.";
  if (value < bound.low || value > bound.high)
    return `Debe estar entre ${bound.low} y ${bound.high}.`;
  return undefined;
}

const NUMBERS: {
  key: keyof PricingSettings;
  label: string;
  help: string;
}[] = [
  {
    key: "margin_goal_pct",
    label: "Meta de margen",
    // The help text names the **consequence**, not the field.
    help:
      "Las referencias por debajo de esta meta reciben una propuesta de alza, " +
      "aunque todavía no tengan elasticidad estimada. Ninguna propuesta cambia " +
      "un precio por sí sola.",
  },
  {
    key: "max_single_step_pct",
    label: "Cambio máximo por ajuste",
    help: "Obligatorio. El mayor movimiento que puede proponer cualquiera de los dos motores.",
  },
  {
    key: "rounding_unit",
    label: "Redondeo",
    help: "Obligatorio. En pesos, sobre el precio que paga el cliente. $15.637 no es un precio que una droguería cobre.",
  },
  {
    key: "min_days_between_changes",
    label: "Días mínimos entre ajustes",
    help: "Obligatorio. Una referencia cuyo precio cambió dentro de esta ventana no se vuelve a proponer, la haya cambiado quien la haya cambiado.",
  },
];

function Form({ values }: { values: PricingSettings }) {
  const save = useSavePricingSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<Record<string, string>>(() => ({
    margin_goal_pct:
      values.margin_goal_pct === null ? "" : String(values.margin_goal_pct),
    max_single_step_pct: String(values.max_single_step_pct),
    rounding_unit: String(values.rounding_unit),
    min_days_between_changes: String(values.min_days_between_changes),
  }));
  const [allowRaise, setAllowRaise] = useState(values.allow_raise_without_cap);
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const [failure, setFailure] = useState("");

  function submit() {
    setFailure("");
    // On submit as well as on blur: a field never touched is a field never
    // blurred, and the server's own refusal is a round trip away.
    const found: Record<string, string | undefined> = {};
    for (const field of NUMBERS) {
      found[field.key] = check(field.key, draft[field.key] ?? "");
    }
    setErrors(found);
    if (Object.values(found).some(Boolean)) return;
    const goal = draft.margin_goal_pct?.trim() ?? "";
    const body: Record<string, unknown> = {
      allow_raise_without_cap: allowRaise,
      max_single_step_pct: Number(
        String(draft.max_single_step_pct).replace(",", "."),
      ),
      rounding_unit: Number(draft.rounding_unit),
      min_days_between_changes: Number(draft.min_days_between_changes),
    };
    // **Clearing the goal is a legal write**, and it is how a tenant returns to
    // the first-morning state -- so it needs a way to say *null* that an unset
    // field cannot be confused with.
    if (goal) body.margin_goal_pct = Number(goal.replace(",", "."));
    else body.clear_margin_goal = true;

    save.mutate(body as Partial<PricingSettings>, {
      onSuccess: () => toast("Ajustes de precios guardados"),
      onError: (error) =>
        setFailure(
          error instanceof ApiError
            ? error.message
            : "No pudimos guardar los ajustes.",
        ),
    });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SectionHeading
        title="Precios y topes"
        description="Qué margen busca la red, cuánto puede moverse un precio de una vez, y qué referencias tienen un precio máximo regulado cargado."
      />
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <div className="grid grid-cols-2 gap-4">
          {NUMBERS.map((field) => (
            <Field
              key={field.key}
              label={field.label}
              // §B.5.7 · the error **replaces** the help in the same slot, so
              // the field stack never changes height on a refusal.
              help={field.help}
              error={errors[field.key]}
              htmlFor={`pricing-${field.key}`}
            >
              <Input
                id={`pricing-${field.key}`}
                value={draft[field.key] ?? ""}
                inputMode="decimal"
                invalid={!!errors[field.key]}
                className="tabular-nums"
                placeholder={
                  field.key === "margin_goal_pct" ? "Sin definir" : undefined
                }
                onBlur={(event) =>
                  setErrors((current) => ({
                    ...current,
                    [field.key]: check(field.key, event.currentTarget.value),
                  }))
                }
                onChange={(event) => {
                  // A refusal clears the moment the figure changes.
                  setErrors((current) => ({
                    ...current,
                    [field.key]: undefined,
                  }));
                  setDraft((current) => ({
                    ...current,
                    [field.key]: event.currentTarget.value,
                  }));
                }}
              />
            </Field>
          ))}
        </div>

        <div className="mt-6 border-t border-hairline pt-6">
          <Checkbox
            label="Permitir subir precios sin tope conocido"
            checked={allowRaise}
            onChange={setAllowRaise}
          />
          <p className="ml-6 mt-1 text-12 text-ink-label">
            Solo para referencias sin precio máximo regulado cargado. Queda
            registrado en la actividad.
          </p>
        </div>

        {failure ? (
          <p role="alert" className="mt-4 text-12 text-critical">
            {failure}
          </p>
        ) : null}

        <div className="mt-6 border-t border-hairline pt-6">
          <CapList />
        </div>
      </div>

      <div className="mt-4 flex shrink-0 justify-end border-t border-hairline pt-4">
        <Button variant="primary" busy={save.isPending} onClick={submit}>
          Guardar cambios
        </Button>
      </div>
    </div>
  );
}

/**
 * The loaded caps, as a `Compact` 40px list (§B.4.1), with a `xs` secondary per
 * row to clear one and a CSV load beside the heading.
 *
 * **A null cap means *unknown*, never *uncapped***, so clearing one returns the
 * reference to `unknown` rather than to `not_regulated` -- the second is a claim
 * somebody made, and this control is not where it gets made by accident.
 */
function CapList() {
  const caps = usePricingCaps();
  const clear = useSetCap();
  const load = useImportCaps();
  const toast = useToast();
  const file = useRef<HTMLInputElement | null>(null);
  const [report, setReport] = useState<string>("");

  const rows = caps.data ?? [];
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-16 font-medium text-ink">Topes regulados</h3>
          <p className="mt-1 max-w-[560px] text-12 text-ink-label">
            El precio máximo que la ley permite cobrar por una referencia bajo
            control de la CNPMDM. Sin un tope cargado la referencia queda en
            «desconocido», que no es lo mismo que «sin tope».
          </p>
        </div>
        <div className="shrink-0">
          <input
            ref={file}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={async (event) => {
              const chosen = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              if (!chosen) return;
              const text = await chosen.text();
              load.mutate(text, {
                onSuccess: (answer) =>
                  setReport(
                    `${count(answer.loaded)} topes cargados` +
                      (answer.unmatched.length
                        ? ` ${DOT} ${count(answer.unmatched.length)} códigos sin referencia`
                        : "") +
                      (answer.refused.length
                        ? ` ${DOT} ${count(answer.refused.length)} filas rechazadas`
                        : ""),
                  ),
                onError: (error) =>
                  setReport(
                    error instanceof ApiError
                      ? error.message
                      : "No pudimos leer el archivo.",
                  ),
              });
            }}
          />
          <Button
            variant="secondary"
            size="sm"
            busy={load.isPending}
            onClick={() => file.current?.click()}
          >
            Cargar topes (CSV)
          </Button>
        </div>
      </div>
      {report ? <p className="text-12 text-ink-body">{report}</p> : null}

      {caps.isPending ? (
        <SkeletonBar className="h-10 w-full" />
      ) : rows.length === 0 ? (
        <p className="text-12 text-ink-note">
          Todavía no hay ningún tope cargado. Mientras no los haya, ninguna
          referencia recibe una propuesta de alza.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-hairline rounded-card border border-edge-soft">
          {rows.map((row) => (
            <li
              key={String(row.item_id)}
              className="flex h-10 items-center gap-3 px-3 text-12"
            >
              <span className="min-w-0 flex-1 truncate text-ink">
                {row.name}
                <span className="text-ink-label">{` ${DOT} ${row.presentation}`}</span>
              </span>
              {row.above_cap ? (
                <Badge family="critical" dot="solid">
                  Sobre el tope
                </Badge>
              ) : null}
              <span className="shrink-0 tabular-nums text-ink">
                {money(Number(row.regulated_max_price ?? 0))}
              </span>
              <span className="w-44 shrink-0 truncate text-11 text-ink-note">
                {row.source}
                {row.set_at ? ` ${DOT} ${dayMonth(row.set_at)}` : ""}
              </span>
              <Button
                variant="ghost"
                size="xs"
                onClick={() =>
                  clear.mutate(
                    { itemId: String(row.item_id), cap_status: "unknown" },
                    {
                      onSuccess: () => toast("Se quitó el tope regulado."),
                      onError: (error) =>
                        toast(
                          error instanceof ApiError
                            ? error.message
                            : "No pudimos quitar el tope.",
                        ),
                    },
                  )
                }
              >
                Quitar
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SectionSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <SkeletonBar className="h-5 w-40" />
      <div className="grid grid-cols-2 gap-4">
        {[0, 1, 2, 3].map((one) => (
          <div key={one} className="flex flex-col gap-2">
            <SkeletonBar className="h-3 w-32" />
            <SkeletonBar className="h-[34px] w-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
