import { useState } from "react";
import { ApiError } from "@/api/client";
import {
  usePurchasingSettings,
  useSavePurchasingSettings,
  type PurchasingSettings,
} from "@/api/purchasing";
import { Button } from "@/ui/button";
import { Checkbox, Field, Input } from "@/ui/field";
import { RegionError, SkeletonBar } from "@/ui/states";
import { useToast } from "@/ui/toast";
import { SectionHeading } from "./section";

/**
 * §B.8.4·4 · **Ajustes · Compras**, a section of the settings dialog and not a
 * route -- reached at `/purchasing?settings=purchasing`, so `Escape` returns
 * exactly where you were.
 *
 * `Obligatorio` sits in the help slot rather than as an asterisk (§B.5.7), and
 * every field is validated on blur and again on submit by the server, which is
 * the only place the relation between the two promotion thresholds is checked.
 */
export function PurchasingSection() {
  const settings = usePurchasingSettings();
  if (settings.isPending) return <SectionSkeleton />;
  if (settings.isError || !settings.data) {
    return (
      <RegionError
        title="No pudimos cargar los ajustes de compras."
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

const NUMBERS: {
  key: keyof PurchasingSettings;
  label: string;
  help: string;
}[] = [
  {
    key: "default_lead_time_days",
    label: "Días de entrega por defecto",
    help: "Obligatorio. Lo que planea el modelo mientras un proveedor no tenga entregas observadas.",
  },
  {
    key: "target_coverage_days",
    label: "Cobertura objetivo (días)",
    help: "Obligatorio. Los días de venta que una orden busca dejar en góndola.",
  },
  {
    key: "order_cap_value",
    label: "Tope por orden (pesos)",
    help: "Obligatorio. Un resguardo contra un pronóstico que se salió de rango una mañana, no un presupuesto.",
  },
  {
    key: "order_cap_weeks_per_line",
    label: "Tope por línea (semanas de venta)",
    help: "Obligatorio. Lo máximo que una línea pide, medido en semanas de venta de esa referencia.",
  },
  {
    key: "refresh_hour",
    label: "Hora del recálculo",
    help: "Obligatorio. Hora local. Las órdenes se generan una hora después.",
  },
  {
    key: "learned_min_weeks",
    label: "Semanas mínimas para «Aprendida»",
    help: "Obligatorio. Semanas útiles, después de descartar las que la referencia estuvo en quiebre.",
  },
  {
    key: "category_default_min_items",
    label: "Referencias mínimas por categoría",
    help: "Obligatorio. Cuántas referencias de una categoría deben estar medidas antes de que su mediana pueda sostener a una sin histórico.",
  },
];

const SHARES: {
  key: keyof PurchasingSettings;
  label: string;
  help: string;
}[] = [
  {
    key: "service_level",
    label: "Nivel de servicio",
    help: "Obligatorio. Entre 0,5 y 0,999. Define la existencia de seguridad.",
  },
  {
    key: "learned_max_rse",
    label: "Error máximo para ascender",
    help: "Obligatorio. Un producto pasa a «Aprendida» cuando su error estándar relativo baja de este valor.",
  },
  {
    key: "learned_demote_rse",
    label: "Error para descender",
    help: "Obligatorio. Tiene que ser mayor que el anterior: si son iguales, un producto cambia de base cada mañana.",
  },
];

const FLAGS: {
  key: keyof PurchasingSettings;
  label: string;
  help: string;
}[] = [
  {
    key: "seasonal_multiplier_enabled",
    label: "Ajuste por temporada",
    help: "Usa lo que la categoría hizo en esta misma semana el año pasado. Necesita un año de histórico: sin él no cambia nada.",
  },
  {
    key: "reason_text_enabled",
    label: "Redacción del modelo",
    help: "El modelo redacta la columna «Por qué». Sin ella cada línea muestra su motivo calculado y la orden funciona igual.",
  },
  {
    key: "write_model_stock_policies",
    label: "El modelo escribe el punto de reorden",
    help: "Cuando está activo, el modelo escribe el punto de reorden. Nunca reemplaza un umbral definido a mano.",
  },
];

function Form({ values }: { values: PurchasingSettings }) {
  const save = useSavePurchasingSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(values).map(([key, value]) => [key, String(value)]),
    ),
  );
  const [flags, setFlags] = useState<Record<string, boolean>>(() => ({
    seasonal_multiplier_enabled: values.seasonal_multiplier_enabled,
    reason_text_enabled: values.reason_text_enabled,
    write_model_stock_policies: values.write_model_stock_policies,
  }));
  const [failure, setFailure] = useState("");

  function submit() {
    setFailure("");
    const body: Record<string, unknown> = { ...flags };
    for (const field of NUMBERS) body[field.key] = Number(draft[field.key]);
    for (const field of SHARES) {
      body[field.key] = Number(String(draft[field.key]).replace(",", "."));
    }
    save.mutate(body as Partial<PurchasingSettings>, {
      onSuccess: () => toast("Ajustes de compras guardados"),
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
        title="Compras"
        description="Cómo el modelo propone una orden: qué cobertura busca, cuándo un producto pasa a estar aprendido y qué escribe de vuelta."
      />
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <div className="grid grid-cols-2 gap-4">
          {NUMBERS.map((field) => (
            <Field
              key={field.key}
              label={field.label}
              help={field.help}
              htmlFor={`purchasing-${field.key}`}
            >
              <Input
                id={`purchasing-${field.key}`}
                value={draft[field.key] ?? ""}
                inputMode="numeric"
                className="tabular-nums"
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    [field.key]: event.currentTarget.value,
                  }))
                }
              />
            </Field>
          ))}
          {SHARES.map((field) => (
            <Field
              key={field.key}
              label={field.label}
              help={field.help}
              htmlFor={`purchasing-${field.key}`}
            >
              <Input
                id={`purchasing-${field.key}`}
                value={draft[field.key] ?? ""}
                inputMode="decimal"
                className="tabular-nums"
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    [field.key]: event.currentTarget.value,
                  }))
                }
              />
            </Field>
          ))}
        </div>

        <div className="mt-6 flex flex-col gap-4 border-t border-hairline pt-6">
          {FLAGS.map((field) => (
            <div key={field.key}>
              <Checkbox
                label={field.label}
                checked={flags[field.key] ?? false}
                onChange={(next) =>
                  setFlags((current) => ({ ...current, [field.key]: next }))
                }
              />
              <p className="ml-6 mt-1 text-12 text-ink-label">{field.help}</p>
            </div>
          ))}
        </div>

        {failure ? (
          <p role="alert" className="mt-4 text-12 text-critical">
            {failure}
          </p>
        ) : null}
      </div>

      <div className="mt-4 flex shrink-0 justify-end border-t border-hairline pt-4">
        <Button variant="primary" busy={save.isPending} onClick={submit}>
          Guardar cambios
        </Button>
      </div>
    </div>
  );
}

function SectionSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <SkeletonBar className="h-5 w-40" />
      <div className="grid grid-cols-2 gap-4">
        {[0, 1, 2, 3, 4, 5].map((one) => (
          <div key={one} className="flex flex-col gap-2">
            <SkeletonBar className="h-3 w-32" />
            <SkeletonBar className="h-[34px] w-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
