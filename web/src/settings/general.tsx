import { useState } from "react";
import type { Me } from "@/api/queries";
import { ApiError } from "@/api/client";
import {
  useSaveTenantSettings,
  useTenantSettings,
  type TenantSettings,
} from "@/api/queries";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { RegionError, SkeletonBar } from "@/ui/states";
import { useToast } from "@/ui/toast";
import { SectionHeading } from "./section";

interface Draft {
  name: string;
  nit: string;
  legal_name: string;
  timezone: string;
}

/**
 * §B.8.4·4 · **Ajustes · General** -- the network's identity, and the one
 * `settings` key group S0 writes.
 *
 * The `slug` is set at provisioning and is not editable: a slug that changes
 * breaks every link anyone saved. `status` is readable and absent from the
 * write, because a network able to suspend itself would have no way back.
 * Currency and number format are read-only with the reason stated once.
 */
export function GeneralSection({ me }: { me: Me }) {
  const settings = useTenantSettings();

  if (settings.isPending) return <GeneralSkeleton />;

  if (settings.isError) {
    return (
      <RegionError
        title="No pudimos cargar los datos de la droguería."
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

  // The form is keyed on what the server sent, so a reload re-seeds the draft
  // by remounting rather than by copying server data into local state in an
  // effect -- which would render twice and fight a field the user is typing in.
  return (
    <GeneralForm
      me={me}
      data={settings.data!}
      key={`${settings.dataUpdatedAt}`}
    />
  );
}

function GeneralForm({ me, data }: { me: Me; data: TenantSettings }) {
  const save = useSaveTenantSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft>({
    name: data.name,
    nit: data.nit,
    legal_name: data.legal_name,
    timezone: data.timezone,
  });
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitted, setSubmitted] = useState(false);

  // §B.5.7 · validation fires on blur and on submit, never on keystroke.
  const shows = (field: keyof Draft) => submitted || touched[field];
  const missing = (field: keyof Draft) =>
    shows(field) && !draft[field].trim()
      ? "Este campo es obligatorio."
      : undefined;
  const invalid = ["name", "legal_name", "timezone"].some(
    (field) => !draft[field as keyof Draft].trim(),
  );

  return (
    <section className="min-h-0 flex-1 overflow-y-auto">
      <SectionHeading
        title="General"
        description={`${data.name} · ${data.slug}`}
      />

      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          setSubmitted(true);
          if (invalid) return;
          save.mutate(draft, {
            onSuccess: () => toast("Se guardaron los datos de la droguería."),
            onError: (error) =>
              toast(
                error instanceof ApiError
                  ? error.message
                  : "No pudimos guardar los datos de la droguería.",
              ),
          });
        }}
        className="flex max-w-[560px] flex-col gap-4"
      >
        <Field
          label="Nombre de la droguería"
          htmlFor="tenant-name"
          error={missing("name")}
          required
        >
          <Input
            id="tenant-name"
            value={draft.name}
            invalid={!!missing("name")}
            onBlur={() => setTouched((one) => ({ ...one, name: true }))}
            onChange={(event) =>
              setDraft((one) => ({ ...one, name: event.currentTarget.value }))
            }
          />
        </Field>

        <Field label="NIT" htmlFor="tenant-nit" optional>
          <Input
            id="tenant-nit"
            value={draft.nit}
            onChange={(event) =>
              setDraft((one) => ({ ...one, nit: event.currentTarget.value }))
            }
          />
        </Field>

        <Field
          label="Razón social"
          htmlFor="tenant-legal-name"
          error={missing("legal_name")}
          required
        >
          <Input
            id="tenant-legal-name"
            value={draft.legal_name}
            invalid={!!missing("legal_name")}
            onBlur={() => setTouched((one) => ({ ...one, legal_name: true }))}
            onChange={(event) =>
              setDraft((one) => ({
                ...one,
                legal_name: event.currentTarget.value,
              }))
            }
          />
        </Field>

        <Field
          label="Zona horaria"
          htmlFor="tenant-timezone"
          error={missing("timezone")}
          required
        >
          <Input
            id="tenant-timezone"
            value={draft.timezone}
            invalid={!!missing("timezone")}
            onBlur={() => setTouched((one) => ({ ...one, timezone: true }))}
            onChange={(event) =>
              setDraft((one) => ({
                ...one,
                timezone: event.currentTarget.value,
              }))
            }
          />
        </Field>

        <div className="mt-3 border-t border-hairline pt-7">
          <Field label="Identificador" htmlFor="tenant-slug">
            <Input id="tenant-slug" value={data.slug} readOnly />
          </Field>
          <div className="mt-4 grid grid-cols-2 gap-4">
            <Field label="Moneda" htmlFor="tenant-currency">
              <Input id="tenant-currency" value={data.currency} readOnly />
            </Field>
            <Field label="Formato de números" htmlFor="tenant-number-format">
              <Input
                id="tenant-number-format"
                value={data.number_format}
                readOnly
              />
            </Field>
          </div>
          <p className="mt-3 text-12 text-ink-label">
            Botica opera en Colombia. La moneda y el formato de números no son
            configurables en esta versión.
          </p>
        </div>

        <div className="mt-3 border-t border-hairline pt-7">
          <p className="text-12 text-ink-label">
            Versión en uso:{" "}
            <span className="font-mono text-10 uppercase tracking-eyebrow text-ink">
              Botica {data.app_version}
            </span>
          </p>
          <p className="mt-2 text-12 text-ink-label">
            Sesión iniciada como {me.name}.
          </p>
        </div>

        {/* §B.6.2 · exactly one primary, right-aligned at gap:8px. */}
        <div className="mt-3 flex items-center justify-end gap-2">
          <Button
            variant="secondary"
            disabled={save.isPending}
            onClick={() => {
              setDraft({
                name: data.name,
                nit: data.nit,
                legal_name: data.legal_name,
                timezone: data.timezone,
              });
              setSubmitted(false);
              setTouched({});
            }}
          >
            Cancelar
          </Button>
          <Button
            type="submit"
            variant="primary"
            busy={save.isPending}
            busyLabel="Guardando…"
          >
            Guardar
          </Button>
        </div>
      </form>
    </section>
  );
}

/** §B.10.1 · a skeleton of the real field stack. */
function GeneralSkeleton() {
  return (
    <section className="flex max-w-[560px] flex-col gap-4">
      <SkeletonBar className="h-5 w-32" />
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index}>
          <SkeletonBar className="h-3 w-24" />
          <SkeletonBar className="mt-2 h-[34px] w-full" />
        </div>
      ))}
    </section>
  );
}
