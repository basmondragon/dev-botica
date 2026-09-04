import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import {
  useCanonicalDocument,
  useFiscalExports,
  useFiscalSummary,
  useInvoicingSettings,
  useSaveInvoicingSettings,
  type InvoicingSettings,
} from "@/api/fiscal";
import { useSales } from "@/api/counter";
import type { Me } from "@/api/queries";
import {
  DELIVERY_MODES,
  ENVIRONMENTS,
  FILE_FORMATS,
  WORK_LIST,
} from "@/fiscal/vocabulary";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { count, since } from "@/ui/format";
import { ConfirmDialog, Modal } from "@/ui/panel";
import { Select } from "@/ui/select";
import { BAR, RegionError, SkeletonBar } from "@/ui/states";
import { useToast } from "@/ui/toast";
import { SectionHeading } from "./section";
import { useSettingsDialog } from "./use-settings";

/**
 * §B.8.4·4 · **Ajustes · Facturación electrónica**, under **Operación**.
 *
 * **This is the only surface in the product that ever mentions the handoff
 * being off** (§8). With nothing connected it is one paragraph and one action,
 * stated in the neutral family as a fact and not as a problem: no count, no
 * badge, no warning family, here or anywhere else.
 *
 * **And Botica never claims to file anything.** It hands a complete sale to the
 * system the droguería already invoices with, exactly once, and records what
 * that system answers. It does not talk to the DIAN, does not generate a CUDE
 * and does not sign anything (§8, A9, §12) — and no string on this screen says
 * otherwise.
 */
export function InvoicingSection({ me }: { me: Me }) {
  const settings = useInvoicingSettings();

  if (settings.isPending) return <SectionSkeleton />;

  if (settings.isError) {
    return (
      <RegionError
        title="No pudimos cargar la configuración de facturación."
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

  return (
    <InvoicingForm
      me={me}
      data={settings.data!}
      key={`${settings.dataUpdatedAt}`}
    />
  );
}

function InvoicingForm({ me, data }: { me: Me; data: InvoicingSettings }) {
  const owner = me.role === "owner" || me.role === "platform_admin";
  const connected = !!data.target;

  return (
    <section className="min-h-0 flex-1 overflow-y-auto">
      <SectionHeading
        title="Facturación electrónica"
        description="Botica entrega cada venta al sistema con el que la droguería ya factura y registra lo que ese sistema responde. No emite documentos ni informa nada a la DIAN."
      />
      {connected ? (
        <Connected data={data} owner={owner} />
      ) : (
        <Disconnected data={data} owner={owner} />
      )}
    </section>
  );
}

/**
 * **One paragraph and one action.** Stated as a fact, in the neutral family:
 * an integration nobody has configured is off, not failing, and a product that
 * nags about unconfigured optional integrations trains its users to ignore its
 * warnings (§8).
 */
function Disconnected({
  data,
  owner,
}: {
  data: InvoicingSettings;
  owner: boolean;
}) {
  const save = useSaveInvoicingSettings();
  const toast = useToast();
  const [target, setTarget] = useState("");
  const [baseUrl, setBaseUrl] = useState(data.base_url);
  const chosen = data.available_targets.find((one) => one.id === target);

  return (
    <div className="flex max-w-[560px] flex-col gap-5">
      <p className="text-14 text-ink-body">
        Botica no está conectado a ningún sistema de facturación. Las ventas se
        registran normalmente; no se envía ningún documento.
      </p>

      {/* The **one bounded exception** to the silence: deliberately
          disconnecting a target while deliveries are held is a state a person
          created two clicks ago, and hiding it would hide their own decision
          from them. */}
      {data.held > 0 ? (
        <p className="text-12 text-ink-label">
          Quedan {count(data.held)} envíos sin resolver del sistema anterior.
          Siguen listados en {WORK_LIST}.
        </p>
      ) : null}

      {!owner ? (
        <p className="text-12 text-ink-label">
          Conectar un sistema de facturación requiere el perfil Propietaria.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          <Field label="Sistema de facturación" htmlFor="invoicing-target">
            <Select
              id="invoicing-target"
              value={target}
              onValueChange={setTarget}
              options={[
                { value: "", label: "Elija un sistema" },
                ...data.available_targets.map((one) => ({
                  value: one.id,
                  label: one.label,
                })),
              ]}
            />
          </Field>
          {chosen?.needs_base_url ? (
            <Field
              label="Dirección de su API"
              htmlFor="invoicing-base-url"
              help="La dirección que le dio el proveedor de su sistema de facturación."
            >
              <Input
                id="invoicing-base-url"
                value={baseUrl}
                inputMode="url"
                onChange={(event) => setBaseUrl(event.currentTarget.value)}
              />
            </Field>
          ) : null}
          {chosen?.needs_credential ? (
            <p className="text-12 text-ink-label">
              La clave de este sistema se configura en el servidor, no aquí. Si
              no está puesta, el envío queda apagado y esta pantalla lo dice.
            </p>
          ) : null}
          <div>
            <Button
              size="md"
              variant="primary"
              disabled={!target}
              busy={save.isPending}
              onClick={() =>
                save.mutate(
                  {
                    target,
                    base_url: baseUrl,
                    mapping: chosen?.mappings[0] ?? "",
                  },
                  {
                    onSuccess: () =>
                      toast(
                        "Se conectó el sistema de facturación. Se enviarán las ventas que cierren desde ahora.",
                      ),
                  },
                )
              }
            >
              Conectar sistema
            </Button>
          </div>
          {save.isError ? (
            <RegionError
              title="No pudimos conectar el sistema de facturación."
              detail={(save.error as Error).message}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}

/** Blocks separated by space and hairlines, never by nested cards (§B.8.4·4). */
function Connected({
  data,
  owner,
}: {
  data: InvoicingSettings;
  owner: boolean;
}) {
  const save = useSaveInvoicingSettings();
  const toast = useToast();
  const navigate = useNavigate();
  const settings = useSettingsDialog();
  const summary = useFiscalSummary();
  const exports = useFiscalExports(data.delivery.mode === "batched");
  const [disconnecting, setDisconnecting] = useState(false);
  const [example, setExample] = useState(false);
  const [draft, setDraft] = useState({
    environment: data.environment,
    base_url: data.base_url,
    mapping: data.mapping,
    delivery: { ...data.delivery },
    retry: { ...data.retry },
    notifications: data.notifications.join(", "),
  });
  const spec = data.available_targets.find((one) => one.id === data.target);

  const commit = () =>
    save.mutate(
      {
        ...(owner
          ? { environment: draft.environment, base_url: draft.base_url }
          : {}),
        mapping: draft.mapping,
        delivery: draft.delivery,
        retry: draft.retry,
        notifications: draft.notifications
          .split(",")
          .map((one) => one.trim())
          .filter(Boolean),
      },
      { onSuccess: () => toast("Se guardó la configuración de facturación.") },
    );

  return (
    <div className="flex max-w-[560px] flex-col gap-6">
      <Block title="Sistema">
        <Field label="Sistema de facturación">
          <Select
            value={data.target}
            disabled
            onValueChange={() => undefined}
            options={[
              { value: data.target, label: spec?.label ?? data.target },
            ]}
          />
        </Field>
        <Field label="Entorno">
          <Select
            value={draft.environment}
            disabled={!owner}
            onValueChange={(next) =>
              setDraft((one) => ({
                ...one,
                environment: next as InvoicingSettings["environment"],
              }))
            }
            options={ENVIRONMENTS}
          />
        </Field>
        {spec?.needs_base_url ? (
          <Field label="Dirección de su API">
            <Input
              value={draft.base_url}
              disabled={!owner}
              onChange={(event) =>
                setDraft((one) => ({
                  ...one,
                  base_url: event.currentTarget.value,
                }))
              }
            />
          </Field>
        ) : null}
        {/* The credential is shown **only as whether it resolved**. There is no
            field to type one into: it lives in the instance's secrets store,
            because a credential in a JSONB column is one every `admin` query
            can read (§9). */}
        {spec?.needs_credential ? (
          <Field
            label="Clave"
            error={
              data.credential_resolved
                ? undefined
                : "La clave de este sistema no está puesta en el servidor, así que el envío está apagado."
            }
          >
            <p className="text-14 text-ink-body">
              {data.credential_resolved
                ? "Configurada en el servidor"
                : "Sin configurar"}
            </p>
          </Field>
        ) : null}
        {owner ? (
          <div>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => setDisconnecting(true)}
            >
              Desconectar
            </Button>
          </div>
        ) : null}
      </Block>

      <Block title="Mapeo">
        <Field
          label="Mapeo en uso"
          help="El mapeo traduce el documento de Botica a los nombres de campo de ese sistema. Cambiarlo vuelve a construir los envíos que estén pendientes."
        >
          <Select
            value={draft.mapping || (spec?.mappings[0] ?? "")}
            onValueChange={(next) =>
              setDraft((one) => ({ ...one, mapping: next }))
            }
            options={(spec?.mappings ?? []).map((one) => ({
              value: one,
              label: one,
            }))}
          />
        </Field>
        <div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setExample(true)}
          >
            Ver documento de ejemplo
          </Button>
        </div>
      </Block>

      <Block title="Entrega">
        {/* **The mode follows the target** and is a read-out rather than a
            control: a file target delivers by export and an API target
            delivers per sale, so a select here would be a way for the screen
            and the delivery job to disagree. */}
        <Field
          label="Modo"
          help="Lo decide el sistema de facturación elegido, no esta pantalla."
        >
          <p className="text-14 text-ink-body">
            {DELIVERY_MODES.find((one) => one.value === data.delivery.mode)
              ?.label ?? data.delivery.mode}
          </p>
        </Field>
        {/* The destination and the file form belong to a file target and to
            nothing else (S5, the `invoicing` group): shown against an API
            target they would be two controls that change nothing. */}
        {data.delivery.mode === "batched" ? (
          <>
            <Field label="Carpeta de destino">
              <Input
                value={draft.delivery.prefix}
                onChange={(event) =>
                  setDraft((one) => ({
                    ...one,
                    delivery: {
                      ...one.delivery,
                      prefix: event.currentTarget.value,
                    },
                  }))
                }
              />
            </Field>
            <Field label="Formato del archivo">
              <Select
                value={draft.delivery.format}
                onValueChange={(next) =>
                  setDraft((one) => ({
                    ...one,
                    delivery: {
                      ...one.delivery,
                      format: next as InvoicingSettings["delivery"]["format"],
                    },
                  }))
                }
                options={FILE_FORMATS}
              />
            </Field>
          </>
        ) : null}
        {exports.data && exports.data.length > 0 ? (
          <ul className="flex flex-col gap-1.5">
            {exports.data.slice(0, 5).map((one) => (
              <li key={one.period} className="flex justify-between text-12">
                <span className="text-ink-body">{one.period}</span>
                <span className="tabular-nums text-ink">
                  {count(one.document_count)} documentos
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </Block>

      <Block title="Reintentos">
        <Field
          label="Horas de reintento"
          help="Un problema de conexión se reintenta durante este tiempo antes de darse por fallido."
        >
          <Input
            inputMode="numeric"
            value={String(draft.retry.cap_hours)}
            onChange={(event) =>
              setDraft((one) => ({
                ...one,
                retry: {
                  ...one.retry,
                  cap_hours: Number(event.currentTarget.value) || 0,
                },
              }))
            }
          />
        </Field>
        <Field
          label="Minutos antes de volver a consultar"
          help="Cuánto se espera antes de preguntarle al sistema de facturación si ya tiene un documento entregado. Nunca se vuelve a enviar."
        >
          <Input
            inputMode="numeric"
            value={String(draft.retry.dwell_minutes)}
            onChange={(event) =>
              setDraft((one) => ({
                ...one,
                retry: {
                  ...one.retry,
                  dwell_minutes: Number(event.currentTarget.value) || 0,
                },
              }))
            }
          />
        </Field>
        <Field
          label="Horas de desfase de reloj admitidas"
          help="Una venta cuyo equipo tenía el reloj más desfasado que esto queda retenida en vez de enviarse con una fecha equivocada."
        >
          <Input
            inputMode="numeric"
            value={String(draft.retry.clock_skew_hours)}
            onChange={(event) =>
              setDraft((one) => ({
                ...one,
                retry: {
                  ...one.retry,
                  clock_skew_hours: Number(event.currentTarget.value) || 0,
                },
              }))
            }
          />
        </Field>
        <Field
          label="Avisos de envíos fallidos"
          help="Correos separados por coma. Vacío significa que no se envía el resumen; la lista de envíos sigue estando."
        >
          <Input
            value={draft.notifications}
            onChange={(event) =>
              setDraft((one) => ({
                ...one,
                notifications: event.currentTarget.value,
              }))
            }
          />
        </Field>
      </Block>

      {/* The 24-hour read-out, and `Ver envíos` -- one of the three ways the
          work list is reached, because it has no nav item (§B.8.1). */}
      <div className="flex items-center justify-between border-t border-hairline pt-4">
        {/* **`held` is the fallback, not silence.** The summary answers
            `configured: false` when the credential stops resolving, and this
            screen would then read `Sin envíos registrados.` beside a `Clave`
            field saying the key is missing and a `Desconectar` confirm naming
            the documents that are waiting — three statements about the same
            rows, one of them false. The row count is unconditional, so it is
            what this line says whenever the summary carries no counts. */}
        <p className="text-12 text-ink-label">
          {summary.data?.configured
            ? `${count(summary.data.unsent ?? 0)} por enviar · ${count(
                summary.data.failed ?? 0,
              )} con problemas${
                summary.data.oldest_unsent_at
                  ? ` · el más antiguo ${since(summary.data.oldest_unsent_at)}`
                  : ""
              }`
            : data.held > 0
              ? `${count(data.held)} envíos sin resolver. El envío está detenido hasta que la clave vuelva a resolverse.`
              : "Sin envíos registrados."}
        </p>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            settings.close();
            void navigate({ to: "/fiscal-documents" });
          }}
        >
          Ver envíos
        </Button>
      </div>

      <div className="flex items-center gap-3">
        <Button
          size="md"
          variant="primary"
          busy={save.isPending}
          onClick={commit}
        >
          Guardar cambios
        </Button>
      </div>

      {save.isError ? (
        <RegionError
          title="No pudimos guardar la configuración de facturación."
          detail={(save.error as Error).message}
        />
      ) : null}

      <ConfirmDialog
        open={disconnecting}
        title="Desconectar el sistema de facturación"
        body={
          data.held > 0
            ? `Quedan ${count(data.held)} envíos sin resolver. Al desconectar dejan de intentarse y siguen listados en ${WORK_LIST}. Las ventas nuevas no generarán ningún documento.`
            : "Las ventas nuevas dejarán de enviarse a ningún sistema de facturación. Se registran normalmente."
        }
        confirmLabel="Desconectar"
        busy={save.isPending}
        onCancel={() => setDisconnecting(false)}
        onConfirm={() =>
          save.mutate(
            { target: "", base_url: "" },
            {
              onSuccess: () => {
                setDisconnecting(false);
                toast("Se desconectó el sistema de facturación.");
              },
            },
          )
        }
      />

      <ExampleDocument open={example} onClose={() => setExample(false)} />
    </div>
  );
}

function Block({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4 border-t border-hairline pt-5 first:border-t-0 first:pt-0">
      <h4 className="text-16 font-medium text-ink">{title}</h4>
      {children}
    </section>
  );
}

/**
 * `Ver documento de ejemplo` — **the control that makes wiring a client's
 * system a conversation with evidence in it.** It renders the canonical payload
 * of the most recent sale, selectable, without sending anything: the first hour
 * of an integration is spent answering *"what exactly do you send?"*.
 */
function ExampleDocument({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const sales = useSales(
    { page: 1, page_size: 25, order: "desc", status: "closed" },
    open,
  );
  const saleId = sales.data?.rows[0]?.id ?? null;
  const document = useCanonicalDocument(open ? saleId : null);

  return (
    <Modal open={open} title="Documento de ejemplo" onClose={onClose}>
      <div className="mt-4 flex flex-col gap-3">
        <p className="text-12 text-ink-label">
          Así se ve una venta de esta droguería en el documento que Botica
          entrega. No se envía nada al abrir esta ventana.
        </p>
        {document.isError ? (
          <RegionError
            title="No pudimos construir el documento de ejemplo."
            detail={(document.error as Error).message}
          />
        ) : document.data ? (
          <pre className="surface-scroll max-h-[420px] select-all overflow-auto rounded-card bg-chrome p-3 font-mono text-11 leading-[16px] text-ink-body">
            {JSON.stringify(document.data, null, 2)}
          </pre>
        ) : sales.data && !saleId ? (
          <p className="text-14 text-ink-body">
            Todavía no hay ninguna venta cerrada de la cual construir un
            ejemplo.
          </p>
        ) : (
          <SkeletonBar className="h-[240px] w-full" />
        )}
      </div>
    </Modal>
  );
}

function SectionSkeleton() {
  return (
    <section className="flex flex-col gap-4" aria-hidden>
      <SkeletonBar className={`${BAR.heading} w-56`} />
      <SkeletonBar className={`${BAR.label} w-[420px]`} />
      <SkeletonBar className={`${BAR.control} w-[320px]`} />
      <SkeletonBar className={`${BAR.control} w-[320px]`} />
    </section>
  );
}
