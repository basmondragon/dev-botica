import { useEffect, useState } from "react";
import { Button } from "@/ui/button";
import { Checkbox, Field, Input } from "@/ui/field";
import { DOT } from "@/ui/format";
import { Modal } from "@/ui/panel";
import { Select } from "@/ui/select";
import { RegionError } from "@/ui/states";
import { useToast } from "@/ui/toast";
import { DOCUMENT_TYPES } from "@/catalog/vocabulary";
import { useSync } from "./context";
import { findCustomers, registerCustomer } from "./local";
import type { CustomerDoc } from "./registry";

/**
 * **Registrar cliente** — the one client-originated write that exists at S2,
 * and the surface the whole push path is proven on.
 *
 * It is here rather than in the office's Clientes section because that is the
 * point: `customers` is created **at the counter, and offline** (ledger rule
 * 8's second paragraph). The canonical sale document S5 hands to the client's
 * invoicing system identifies the acquirer by document type, number and name,
 * captured at the counter and offline included — so a counter that cannot name
 * a customer during a blackout hands over an incomplete document, and a field
 * the receiving system needs and we did not send is a field a cashier re-types.
 *
 * §B.10.1 · **there is no loading state at all.** The row is written to the
 * local store and to the outbox immediately; the status line moves to
 * `Sin conexión · 1 por enviar` and the customer is findable at once.
 */
export function RegisterCustomer({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const sync = useSync();
  const toast = useToast();
  const [documentType, setDocumentType] = useState("CC");
  const [document, setDocument] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [consent, setConsent] = useState(false);
  const [known, setKnown] = useState<CustomerDoc | null>(null);
  const [failure, setFailure] = useState("");

  // Looked up locally on every keystroke, with no network in the path — which
  // is what stops a cashier creating a second row for someone the till already
  // knows, and is the half of criterion 12 the client can do on its own.
  useEffect(() => {
    const term = document.trim();
    let stale = false;
    const lookup =
      !open || !sync.database || term.length < 4
        ? Promise.resolve<CustomerDoc[]>([])
        : findCustomers(sync.database, term, 1);
    void lookup.then((rows) => {
      if (!stale) setKnown(rows[0] ?? null);
    });
    return () => {
      stale = true;
    };
  }, [open, document, sync.database]);

  function reset() {
    setDocumentType("CC");
    setDocument("");
    setName("");
    setPhone("");
    setConsent(false);
    setKnown(null);
    setFailure("");
  }

  async function save() {
    if (!sync.database) return;
    try {
      await registerCustomer(sync.database, {
        document_type: documentType,
        document: document.trim(),
        name: name.trim(),
        phone: phone.trim(),
        data_consent: consent,
      });
    } catch {
      // A local write that threw on a till is a quota error far more often than
      // anything else, and it is the one thing a cashier can be told about.
      setFailure(
        "Este equipo no pudo guardar el cliente. Cierre otras pestañas de Botica e intente de nuevo.",
      );
      return;
    }
    toast(`${name.trim()} quedó registrado en este equipo.`);
    reset();
    onClose();
  }

  return (
    <Modal open={open} title="Registrar cliente" onClose={onClose}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <p className="text-12 text-ink-label">
          Se guarda en este equipo de inmediato y se envía cuando haya conexión.
          El mostrador no espera al servidor.
        </p>

        <div className="grid grid-cols-[120px_1fr] gap-3">
          <Field label="Tipo" htmlFor="customer-document-type">
            <Select
              id="customer-document-type"
              value={documentType}
              options={DOCUMENT_TYPES.map((one) => ({
                value: one.value,
                label: one.value,
              }))}
              onValueChange={setDocumentType}
            />
          </Field>
          <Field label="Documento" htmlFor="customer-document">
            <Input
              id="customer-document"
              value={document}
              inputMode="numeric"
              autoFocus
              onChange={(event) => setDocument(event.target.value)}
            />
          </Field>
        </div>

        {known ? (
          <p className="text-12 text-ink-body">
            Este equipo ya conoce a {known.name} {DOT} {known.document_type}{" "}
            {known.document}. Registrarlo de nuevo no crea un segundo cliente.
          </p>
        ) : null}

        <Field label="Nombre" htmlFor="customer-name">
          <Input
            id="customer-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>

        <Field label="Teléfono" optional htmlFor="customer-phone">
          <Input
            id="customer-phone"
            value={phone}
            inputMode="tel"
            onChange={(event) => setPhone(event.target.value)}
          />
        </Field>

        <Checkbox
          checked={consent}
          onChange={setConsent}
          label="Autoriza el tratamiento de sus datos"
        />

        {failure ? (
          <RegionError
            title="No pudimos guardar este cliente."
            detail={failure}
            onRetry={() => void save()}
          />
        ) : null}

        <div className="flex justify-end gap-3">
          <Button variant="secondary" size="md" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            type="submit"
            variant="primary"
            size="md"
            disabled={!document.trim() || !name.trim()}
          >
            Registrar cliente
          </Button>
        </div>
      </form>
    </Modal>
  );
}
