import { useState } from "react";
import { ApiError } from "@/api/client";
import {
  useAssistantMetrics,
  useAssistantSettings,
  useCrossSellRules,
  useDeactivateWarning,
  useItemWarnings,
  useRefreshRules,
  useAssistantQueries,
  useSaveAssistantSettings,
  useSaveWarning,
  type AssistantSettings,
  type ItemWarning,
  type WarningSeverity,
} from "@/api/assistant";
import { Button } from "@/ui/button";
import { Checkbox, Field, Input, RadioGroup, Textarea } from "@/ui/field";
import { RecordPanel } from "@/ui/panel";
import { cn } from "@/ui/cn";
import { DOT, count as countOf, dayMonth, percent } from "@/ui/format";
import { ProgressBar } from "@/ui/tile";
import { RegionError, SkeletonBar } from "@/ui/states";
import { Badge, StatusDot } from "@/ui/status";
import { useToast } from "@/ui/toast";
import {
  BAND_LABEL,
  BAND_MEANING,
  BASIS_LABEL,
  MODE_LABEL,
  NEVER_MATCHED,
  OUTPUT_CHECK,
  SEVERITY_MEANING,
  WARNING_SOURCE_LABEL,
  WARNING_TYPE_LABEL,
} from "@/assistant/vocabulary";
import { SectionHeading } from "./section";

/**
 * §B.8.4·4 · **Ajustes · Operación · Asistente**, a section of the settings
 * dialog and not a route — reached at `/mostrador?settings=assistant`, so
 * `Escape` returns exactly where you were.
 *
 * Four blocks at `t-16`/500 titles, separated by space and hairlines and never
 * by nested cards: the two switches with their consequences written out, what
 * is kept of what a customer said, the safety layer, and the mined rules.
 *
 * **This surface is online-only and says so plainly.** It is an office surface;
 * neither it nor the query log is in the registry and neither reaches a device.
 * There is no offline editing of a safety warning and no queued write of one: a
 * blocking warning approved during a blackout and applied twenty minutes later
 * is a warning that did not exist for twenty minutes, and the till has no way
 * to know that.
 */
export function AssistantSection() {
  const settings = useAssistantSettings();
  if (settings.isPending) return <SectionSkeleton />;
  if (settings.isError || !settings.data) {
    return (
      <RegionError
        title="Sin conexión"
        detail="Los ajustes del asistente necesitan conexión. El mostrador sigue sugiriendo con normalidad."
        requestId={
          settings.error instanceof ApiError
            ? settings.error.requestId
            : undefined
        }
        onRetry={() => void settings.refetch()}
      />
    );
  }
  return <Body values={settings.data} />;
}

function SectionSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <SkeletonBar className="h-4 w-40" />
      <SkeletonBar className="h-3 w-full" />
      <SkeletonBar className="h-3 w-4/5" />
    </div>
  );
}

const PERIOD_DAYS = 30;

function Body({ values }: { values: AssistantSettings }) {
  const metrics = useAssistantMetrics({ days: PERIOD_DAYS });
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto pb-2">
      <ReadOut />
      <Estado values={values} />
      <Hairline />
      <Datos values={values} />
      <Hairline />
      <Advertencias />
      <Hairline />
      <Reglas />
      <Hairline />
      <Registro />
      <p className="sr-only">
        {metrics.data ? `${metrics.data.offered} sugerencias` : ""}
      </p>
    </div>
  );
}

function Hairline() {
  return <hr className="border-t border-hairline" />;
}

/** §A.11 · the decimal comma, on the one currency in this product that is not
 *  the peso. The gateway bills in dollars and the cap is set in them. */
function dollars(value: number | string, places = 2): string {
  return `US$ ${Number(value).toFixed(places).replace(".", ",")}`;
}

/**
 * The acceptance read-out this stage owns **until S9 draws it properly**. Every
 * figure carries its own definition, because a comparison whose definition is
 * not written down is a number nobody can argue with and nobody should believe.
 */
function ReadOut() {
  const metrics = useAssistantMetrics({ days: PERIOD_DAYS });
  const figures = metrics.data;
  return (
    <section>
      <SectionHeading
        title="Cómo va el asistente"
        description={`Últimos ${PERIOD_DAYS} días, en toda la red.`}
      />
      <dl className="grid grid-cols-4 gap-4">
        <Figure
          label="Sugerencias ofrecidas"
          value={figures ? countOf(figures.offered) : "—"}
          note="Una fila por tarjeta mostrada, no por tarjeta aceptada."
        />
        <Figure
          label="Aceptadas"
          value={figures ? countOf(figures.accepted) : "—"}
          note="La tarjeta se convirtió en una línea del tiquete."
        />
        <Figure
          label="Tasa de aceptación"
          value={
            figures?.rate === null || figures?.rate === undefined
              ? "—"
              : percent(figures.rate * 100)
          }
          note="Aceptadas sobre ofrecidas, en ventas de mostrador cerradas."
        />
        <Figure
          label="Descartadas por el control de salida"
          value={figures ? countOf(figures.rejected_queries) : "—"}
          note="Respuestas del modelo que no llegaron a la pantalla."
        />
      </dl>
    </section>
  );
}

function Figure({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string;
}) {
  return (
    <div title={note}>
      <dt className="text-11 text-ink-note">{label}</dt>
      <dd className="mt-1 text-20 tabular-nums text-ink">{value}</dd>
    </div>
  );
}

/**
 * **Two switches, and they are different.** Turning the column off returns S4's
 * product search to the full-height left column it held before; turning the
 * model off leaves the chips, the filter, the ranking, the cards and the notice
 * all working and sends no transcript anywhere.
 */
function Estado({ values }: { values: AssistantSettings }) {
  const save = useSaveAssistantSettings();
  const metrics = useAssistantMetrics({ days: PERIOD_DAYS });
  const toast = useToast();
  const spent = Number(metrics.data?.spend_month_to_date ?? 0);
  const cap = Number(metrics.data?.spend_cap ?? values.monthly_spend_cap_usd);
  const write = (patch: Partial<AssistantSettings>) =>
    save.mutate(patch, {
      onSuccess: () => toast("Ajustes guardados"),
      onError: (error) =>
        toast(
          error instanceof ApiError ? error.message : "No pudimos guardar.",
        ),
    });

  return (
    <section>
      <SectionHeading title="Estado" />
      <div className="flex flex-col gap-3">
        <div>
          <Checkbox
            checked={values.enabled}
            label="Mostrar el asistente en el mostrador"
            onChange={(next) => write({ enabled: next })}
          />
          <p className="ml-6 mt-1 max-w-[560px] text-12 text-ink-label">
            Si lo apaga, la columna desaparece y la búsqueda de productos vuelve
            a ocupar toda la izquierda. No se guarda ninguna consulta.
          </p>
        </div>
        <div>
          <Checkbox
            checked={values.model_enabled}
            label="Usar el modelo para redactar la recomendación"
            onChange={(next) => write({ model_enabled: next })}
          />
          <p className="ml-6 mt-1 max-w-[560px] text-12 text-ink-label">
            Si lo apaga, el mostrador sigue igual: los síntomas, el filtro de
            seguridad, las tarjetas y el aviso siguen funcionando, y ninguna
            palabra del cliente sale de esta instalación.
          </p>
        </div>
        {!values.model_enabled ? (
          <p className="text-11 text-ink-note">
            Pendiente de decidir si el texto del cliente puede salir hacia un
            proveedor de modelos (Ley 1581, dato de salud).
          </p>
        ) : null}
      </div>

      <dl className="mt-5 flex gap-10">
        <div>
          <dt className="text-11 text-ink-note">Modelo</dt>
          <dd className="mt-1 text-14 text-ink">
            {values.model || "El del despliegue"}
          </dd>
        </div>
        <div>
          <dt className="text-11 text-ink-note">Tiempo de espera</dt>
          <dd className="mt-1 text-14 tabular-nums text-ink">
            {countOf(values.model_timeout_ms)} ms
          </dd>
        </div>
      </dl>

      <div className="mt-5 max-w-[420px]">
        <p className="flex items-baseline justify-between text-12 text-ink-body">
          <span>Tope mensual</span>
          {/* **Dollars, and said so.** The gateway bills in USD and this is the
              one figure in the product that is not pesos; rendering it through
              the peso formatter would show a US$ 25 cap as `$2.500` (§A.11). */}
          <span className="tabular-nums text-ink">
            {dollars(spent)} {DOT} {dollars(cap)}
          </span>
        </p>
        <div className="mt-2">
          <ProgressBar fill={cap > 0 ? (spent / cap) * 100 : 0} />
        </div>
      </div>
    </section>
  );
}

function Datos({ values }: { values: AssistantSettings }) {
  const save = useSaveAssistantSettings();
  const toast = useToast();
  const [days, setDays] = useState(String(values.transcript_retention_days));
  return (
    <section>
      <SectionHeading title="Datos del cliente" />
      <div className="flex flex-col gap-3">
        <div>
          <Checkbox
            checked={values.retain_transcripts}
            label="Guardar lo que dice el cliente"
            onChange={(next) =>
              save.mutate(
                { retain_transcripts: next },
                { onSuccess: () => toast("Ajustes guardados") },
              )
            }
          />
          <p className="ml-6 mt-1 max-w-[560px] text-12 text-ink-label">
            Si lo apaga, la caja envía solo los síntomas extraídos y nunca las
            palabras.
          </p>
        </div>
        <Field label="Días que se guarda" htmlFor="assistant-retention">
          <Input
            id="assistant-retention"
            value={days}
            inputMode="numeric"
            onChange={(event) => setDays(event.target.value)}
            onBlur={() => {
              const next = Number(days);
              if (Number.isInteger(next) && next >= 1 && next <= 365) {
                save.mutate(
                  { transcript_retention_days: next },
                  { onSuccess: () => toast("Ajustes guardados") },
                );
              }
            }}
          />
        </Field>
        <p className="max-w-[560px] text-12 text-ink-body">
          Lo que dice el cliente es un dato de salud. Botica lo guarda solo
          durante el tiempo que usted indique aquí.
        </p>
      </div>
    </section>
  );
}

/** §B.4.1 · a `Compact` table at 40px rows. `severity` is the one badge column
 *  this surface has. */
function Advertencias() {
  const warnings = useItemWarnings({
    page: 1,
    page_size: 50,
    order: "asc",
  });
  const deactivate = useDeactivateWarning();
  const toast = useToast();
  const [editing, setEditing] = useState<ItemWarning | null>(null);
  const rows = warnings.data?.rows ?? [];
  return (
    <section>
      <SectionHeading
        title="Advertencias de producto"
        description="La capa de seguridad. Una advertencia bloqueante saca el producto de las sugerencias; una informativa lo deja con su condición escrita en la tarjeta."
      />
      {warnings.isPending ? (
        <SkeletonBar className="h-3 w-full" />
      ) : rows.length === 0 ? (
        <p className="text-14 text-ink-body">
          Todavía no hay advertencias cargadas para este catálogo.
        </p>
      ) : (
        <table className="w-full text-12">
          <thead>
            <tr className="border-b border-hairline text-left text-11 text-ink-note">
              <th className="py-2 font-normal">Producto</th>
              <th className="py-2 font-normal">Tipo</th>
              <th className="py-2 font-normal">Gravedad</th>
              <th className="py-2 font-normal">Texto</th>
              <th className="py-2 font-normal">Origen</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="h-10 border-b border-hairline">
                <td className="max-w-[220px] truncate pr-3 text-ink">
                  {row.item_name}
                </td>
                <td className="pr-3 text-ink-body">
                  {WARNING_TYPE_LABEL[row.type] ?? row.type}
                </td>
                <td className="pr-3">
                  <Badge
                    family={SEVERITY_MEANING[row.severity]?.family ?? "neutral"}
                  >
                    {SEVERITY_MEANING[row.severity]?.label ?? row.severity}
                  </Badge>
                </td>
                <td className="max-w-[300px] truncate pr-3 text-ink-body">
                  {row.text}
                </td>
                <td className="pr-3 text-ink-note">
                  {WARNING_SOURCE_LABEL[row.source] ?? row.source}
                  {/* **A trigger that has never matched an extraction in 30
                      días** is a warning that never fires, which is the failure
                      the closed vocabulary exists to prevent one door along. */}
                  {row.never_matched ? (
                    <span className="ml-2 inline-flex items-center gap-[7px] text-11 text-ink-note">
                      <StatusDot family="neutral" dot="hollow" />
                      {NEVER_MATCHED}
                    </span>
                  ) : null}
                </td>
                <td className="whitespace-nowrap text-right">
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => setEditing(row)}
                  >
                    Editar
                  </Button>
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() =>
                      deactivate.mutate(row.id, {
                        onSuccess: () => toast("Advertencia desactivada"),
                      })
                    }
                  >
                    Desactivar
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Health />
      <WarningEditor row={editing} onClose={() => setEditing(null)} />
    </section>
  );
}

/**
 * §B.8.5 · the record panel, with **the Spanish `text` and the structured
 * `triggers` side by side**.
 *
 * They are two fields because they are two things: `text` is the sentence a
 * cashier reads out and a `conditional` card prints verbatim; `triggers` is
 * what the filter evaluates. A `triggers` value outside the closed vocabulary
 * is a **field-scope error naming the key** (§B.10.3) and never a save that
 * half-works — which is the server's refusal, rendered where it belongs.
 */
function WarningEditor({
  row,
  onClose,
}: {
  row: ItemWarning | null;
  onClose: () => void;
}) {
  const save = useSaveWarning();
  const toast = useToast();
  const [text, setText] = useState("");
  const [triggers, setTriggers] = useState("");
  const [severity, setSeverity] = useState<WarningSeverity>("blocking");
  const [held, setHeld] = useState<string | null>(null);
  const [refusal, setRefusal] = useState("");

  // React's own "adjust state when a prop changes": opening a different row
  // fills the fields from it rather than from the last one edited.
  if (row && held !== row.id) {
    setHeld(row.id);
    setText(row.text);
    setTriggers(JSON.stringify(row.triggers, null, 2));
    setSeverity(row.severity);
    setRefusal("");
  }
  if (!row) return null;

  return (
    <RecordPanel
      title={row.item_name}
      open
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            size="sm"
            variant="primary"
            busy={save.isPending}
            busyLabel="Guardando"
            onClick={() => {
              let parsed: Record<string, unknown>[];
              try {
                parsed = JSON.parse(triggers) as Record<string, unknown>[];
              } catch {
                setRefusal("Las condiciones no están bien escritas.");
                return;
              }
              save.mutate(
                { id: row.id, values: { text, severity, triggers: parsed } },
                {
                  onSuccess: () => {
                    toast("Advertencia guardada");
                    onClose();
                  },
                  onError: (error) =>
                    setRefusal(
                      error instanceof ApiError
                        ? error.message
                        : "No pudimos guardar la advertencia.",
                    ),
                },
              );
            }}
          >
            Guardar
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4 p-5">
        <Field
          label="Texto que lee el cajero"
          htmlFor="warning-text"
          help="Se imprime tal cual en la tarjeta Con condición. Ni el modelo ni nadie lo reescribe."
        >
          <Textarea
            id="warning-text"
            rows={3}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </Field>
        <Field
          label="Condiciones que evalúa el filtro"
          htmlFor="warning-triggers"
          help="Vocabulario cerrado: symptom, population, interacts_with_ingredient, duration_days."
          error={refusal || undefined}
        >
          <Textarea
            id="warning-triggers"
            rows={8}
            value={triggers}
            className="font-mono text-12"
            onChange={(event) => setTriggers(event.target.value)}
          />
        </Field>
        <RadioGroup
          legend="Gravedad"
          name="warning-severity"
          value={severity}
          options={[
            { value: "blocking", label: SEVERITY_MEANING.blocking!.label },
            { value: "advisory", label: SEVERITY_MEANING.advisory!.label },
          ]}
          onChange={(next) => setSeverity(next as WarningSeverity)}
        />
      </div>
    </RecordPanel>
  );
}

/**
 * What `assistant.health_check` computes, read off the same call the figures
 * above come from — **the job and the screen agree because they run the same
 * three counts**, and there is no second endpoint for it.
 */
function Health() {
  const metrics = useAssistantMetrics({ days: PERIOD_DAYS });
  const report = metrics.data;
  if (!report) return null;
  return (
    <p className="mt-3 text-11 text-ink-note">
      {report.rejection_rate === null
        ? "El control de salida no ha visto respuestas del modelo todavía."
        : `El control de salida descarta el ${percent(report.rejection_rate * 100)} de las respuestas.`}
      {report.unmapped_symptom_keys.length > 0
        ? ` ${DOT} ${countOf(report.unmapped_symptom_keys.length)} síntomas sin categoría asociada.`
        : ""}
      {report.dormant_warnings.length > 0
        ? ` ${DOT} ${countOf(report.dormant_warnings.length)} advertencias que nunca se han activado.`
        : ""}
    </p>
  );
}

/**
 * The mined rules, read-only.
 *
 * **The column that used to read `Confianza` is `% del ancla`**, because it is
 * the statistic `confidence` — P(B presente | A presente) — and `Confianza`
 * now carries `confidence_band`, which is the model confidence §1 asks every
 * model surface to show. Two different quantities under one Spanish word on one
 * table is how the wrong number ends up under that label.
 */
function Reglas() {
  const rules = useCrossSellRules({ page: 1, page_size: 25, order: "desc" });
  const refresh = useRefreshRules();
  const toast = useToast();
  const rows = rules.data?.rows ?? [];
  // The provenance line is composed from three columns every rule row already
  // carries. There is no endpoint for it, and there is no run table behind it.
  const line = rows[0];
  return (
    <section>
      <SectionHeading
        title="Reglas de la red"
        action={
          <Button
            size="sm"
            variant="secondary"
            busy={refresh.isPending}
            busyLabel="Calculando"
            onClick={() =>
              refresh.mutate(undefined, {
                onSuccess: (data) => toast(data.detail),
              })
            }
          >
            Recalcular
          </Button>
        }
      />
      {rows.length === 0 ? (
        /* §B.10.2 · **stated as a ramp rather than as a fault** (§1). Without
           that last sentence a regente reads an empty table as a broken
           assistant and the cold-start floor is discovered as a defect. */
        <div className="max-w-[560px]">
          <p className="text-16 text-ink">Todavía no hay reglas de la red</p>
          <p className="mt-2 text-14 text-ink-body">
            Se calculan cada domingo sobre las ventas de la red. Una pareja
            entra en la lista cuando aparece en suficientes tiquetes. El
            mostrador sigue sugiriendo mientras tanto.
          </p>
        </div>
      ) : (
        <>
          <table className="w-full text-12">
            <thead>
              <tr className="border-b border-hairline text-left text-11 text-ink-note">
                <th className="py-2 font-normal">Producto A</th>
                <th className="py-2 font-normal">Producto B</th>
                <th className="py-2 text-right font-normal">% del ancla</th>
                <th className="py-2 text-right font-normal">Lift</th>
                <th className="py-2 text-right font-normal">Soporte</th>
                <th className="py-2 text-right font-normal">Ventana</th>
                <th className="py-2 font-normal">Base</th>
                <th className="py-2 font-normal">Confianza</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="h-10 border-b border-hairline">
                  <td className="max-w-[190px] truncate pr-3 text-ink">
                    {row.item_a}
                  </td>
                  <td className="max-w-[190px] truncate pr-3 text-ink">
                    {row.item_b}
                  </td>
                  <td className="pr-3 text-right tabular-nums text-ink-body">
                    {percent(Number(row.confidence) * 100)}
                  </td>
                  <td className="pr-3 text-right tabular-nums text-ink-body">
                    {Number(row.lift).toFixed(2).replace(".", ",")}
                  </td>
                  <td className="pr-3 text-right tabular-nums text-ink-body">
                    {countOf(row.support)}
                  </td>
                  <td className="pr-3 text-right tabular-nums text-ink-body">
                    {countOf(row.ticket_count)}
                  </td>
                  <td className="pr-3 text-ink-body">
                    {BASIS_LABEL[row.basis]}
                  </td>
                  <td>
                    <Badge
                      family={BAND_MEANING[row.confidence_band].family}
                      dot={BAND_MEANING[row.confidence_band].dot}
                    >
                      {BAND_LABEL[row.confidence_band]}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {line ? (
            <p className={cn("mt-3 text-11 text-ink-note")}>
              Reglas calculadas sobre {line.window} {DOT}{" "}
              {countOf(line.ticket_count)} tiquetes {DOT} actualizadas el{" "}
              {dayMonth(line.computed_at)}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

/**
 * `Registro del asistente` — **a block here rather than an eighth nav item**
 * (§B.8.4·6's own reasoning). It is opened when somebody is investigating, not
 * daily, and an eighth item in a nav a cashier uses costs more than it returns.
 *
 * *The measurement that would change it:* if the pilot's regente opens it more
 * than weekly, it earns a place under Reportes, which is S9's surface and a
 * one-line addition to S9's rail.
 */
function Registro() {
  const rows = useAssistantQueries({
    page: 1,
    page_size: 25,
    order: "desc",
    days: PERIOD_DAYS,
  });
  const found = rows.data?.rows ?? [];
  return (
    <section>
      <SectionHeading
        title="Registro del asistente"
        description={`Las consultas de los últimos ${PERIOD_DAYS} días. Solo se ve lo que la retención ha conservado.`}
      />
      {found.length === 0 ? (
        <p className="text-14 text-ink-body">
          Todavía no se ha consultado el asistente en este periodo.
        </p>
      ) : (
        <table className="w-full text-12">
          <thead>
            <tr className="border-b border-hairline text-left text-11 text-ink-note">
              <th className="py-2 font-normal">Fecha</th>
              <th className="py-2 font-normal">Sede</th>
              <th className="py-2 font-normal">Cajero</th>
              <th className="py-2 font-normal">Síntomas</th>
              <th className="py-2 font-normal">Modo</th>
              <th className="py-2 font-normal">Control</th>
              <th className="py-2 text-right font-normal">Aceptadas</th>
              <th className="py-2 text-right font-normal">Costo</th>
            </tr>
          </thead>
          <tbody>
            {found.map((row) => (
              <tr key={row.id} className="h-10 border-b border-hairline">
                <td className="pr-3 tabular-nums text-ink-body">
                  {dayMonth(row.recorded_at)}
                </td>
                <td className="pr-3 text-ink-body">{row.location_name}</td>
                <td className="pr-3 text-ink-body">{row.user_name ?? "—"}</td>
                <td className="max-w-[260px] truncate pr-3 text-ink-body">
                  {/* §B.9.2 tier 3 · a row whose retention has elapsed renders
                      `—` with its own reading — **never a blank and never a
                      zero**. */}
                  {row.purged ? (
                    <span
                      className="text-ink-soft"
                      title="depurado por retención"
                    >
                      —
                    </span>
                  ) : (
                    ((row.symptoms ?? []) as { label?: string }[])
                      .map((fact) => fact.label ?? "")
                      .filter(Boolean)
                      .join(` ${DOT} `) || "—"
                  )}
                </td>
                <td className="pr-3 text-ink-body">{MODE_LABEL[row.mode]}</td>
                <td className="pr-3">
                  <Badge
                    family={
                      OUTPUT_CHECK[
                        row.output_check_passed ? "passed" : "rejected"
                      ].family
                    }
                    dot={
                      OUTPUT_CHECK[
                        row.output_check_passed ? "passed" : "rejected"
                      ].dot
                    }
                  >
                    {
                      OUTPUT_CHECK[
                        row.output_check_passed ? "passed" : "rejected"
                      ].label
                    }
                  </Badge>
                </td>
                <td className="pr-3 text-right tabular-nums text-ink-body">
                  {countOf(row.accepted_count)} de {countOf(row.offered_count)}
                </td>
                <td className="text-right tabular-nums text-ink-body">
                  {Number(row.cost_usd) === 0 ? "—" : dollars(row.cost_usd, 4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
