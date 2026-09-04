"""The target interface, and the registry of what a tenant may name.

**Three operations, and no other module in the system knows a target's name, its
payload shape or its error vocabulary** (§8):

| `deliver(document)`         | send one canonical document                    |
| `query(document_key)`       | does the target hold it, and under what number |
| `fetch_representation(id)`  | optional -- the target's own PDF or link       |

**A proveedor tecnológico is one target among others**, not a case in the
design. Botica is not one and does not become one (A9): it hands a complete sale
to the system the client already runs and records whatever comes back.

**Adding a target is a mapping plus credentials.** The diff that adds a second
one must touch no file under the sale, the ticket, the sync path or the
canonical builder -- if it does, the payload was incomplete and the fix belongs
in the payload for everyone rather than in a branch for one client.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from core.fiscal import mappings, transports

#: The four outcomes a delivery can have, and the status each one lands on.
#: `UNKNOWN` is the one that matters: it is **not** a failure, and it is never
#: resolved by re-sending (§8).
HELD = "held"  # the target holds it            -> acknowledged
TAKEN = "taken"  # taken, confirmation later    -> sent
REFUSED = "refused"  # the target said no       -> failed
UNKNOWN = "unknown"  # timeout, drop, 5xx       -> stays pending, query first


@dataclass
class Outcome:
    """What one operation answered. `reason` is one Spanish sentence a person
    can act on -- never a bare HTTP status, never a vendor payload (§B.10.3)."""

    state: str
    external_number: str = ""
    cude: str = ""
    pdf_url: str = ""
    reason: str = ""
    response: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Context:
    """What a target is opened with. Everything it needs and nothing it does
    not: no request, no sale, no queryset."""

    tenant_id: str
    options: dict
    credential: str
    mapping: mappings.Mapping


@dataclass(frozen=True)
class Spec:
    """One target, as the settings screen and the delivery job see it.

    `batched` is what tells the delivery job there is nothing to send per
    document: the file target's documents wait for the period's export and are
    acknowledged when the file lands (*Delivery · the file export*).
    """

    id: str
    label: str
    needs_credential: bool
    needs_base_url: bool
    batched: bool
    mappings: tuple
    open: Callable

    @property
    def default_mapping(self) -> str:
        return self.mappings[0]


class HttpJsonTarget:
    """A JSON API behind one declarative mapping.

    This is the shape every client target takes, and the loopback is one of them
    over a transport that never leaves the instance -- so the envelope, the
    idempotency field, the vocabularies and the reply parser under test are the
    ones a real client's mapping uses.
    """

    def __init__(self, transport, mapping: mappings.Mapping):
        self.transport = transport
        self.mapping = mapping

    @property
    def supports_query(self) -> bool:
        return self.mapping.supports_query

    def deliver(self, document: dict) -> Outcome:
        body = mappings.render(self.mapping, document)
        try:
            reply = self.transport.send("documents", method="POST", body=body)
        except transports.TransportFailure as failure:
            # **Unknown, and the next attempt queries first.** The far end may
            # have committed and then failed to answer.
            return Outcome(state=UNKNOWN, reason=_transport_reason(failure))
        return self._read(reply)

    def query(self, document_key: str) -> Outcome:
        try:
            reply = self.transport.send(f"documents/{document_key}", method="GET")
        except transports.TransportFailure as failure:
            return Outcome(state=UNKNOWN, reason=_transport_reason(failure))
        if reply.status == 404:
            # **Empty is the only answer that leads to a second delivery.**
            return Outcome(state=REFUSED, reason="", response=reply.body)
        if reply.status >= 400:
            return Outcome(state=UNKNOWN, reason=_refusal(self.mapping, reply))
        return self._identifiers(reply, HELD)

    def fetch_representation(self, external_id: str) -> str:
        try:
            reply = self.transport.send(f"documents/{external_id}", method="GET")
        except transports.TransportFailure:
            return ""
        return str(reply.body.get(self.mapping.reply_pdf_url) or "")

    def _read(self, reply: transports.Reply) -> Outcome:
        if reply.status >= 400:
            return Outcome(
                state=REFUSED,
                reason=_refusal(self.mapping, reply),
                response=reply.body,
            )
        if reply.status == 202 or not reply.body.get(
            self.mapping.reply_external_number
        ):
            # Taken, confirmation later. Re-queried after the dwell -- **a
            # re-query, never a second delivery**.
            return Outcome(state=TAKEN, response=reply.body)
        return self._identifiers(reply, HELD)

    def _identifiers(self, reply: transports.Reply, state: str) -> Outcome:
        body = reply.body
        return Outcome(
            state=state,
            external_number=str(body.get(self.mapping.reply_external_number) or ""),
            cude=str(body.get(self.mapping.reply_cude) or ""),
            pdf_url=str(body.get(self.mapping.reply_pdf_url) or ""),
            response=body,
        )


class FileTarget:
    """A client whose system has no API gets the same payload on a schedule.

    **A transport, not a second design**: same canonical document, same
    `document_key`, same mapping, same validation, same states. It sends
    nothing per document -- `deliver` is never called for a batched target --
    and `query` answers from the period's file, which is what makes
    exactly-once survive the transport.
    """

    def __init__(self, context: Context):
        self.context = context
        self.mapping = context.mapping

    @property
    def supports_query(self) -> bool:
        return True

    def deliver(self, document: dict) -> Outcome:
        raise RuntimeError(
            "The file target delivers by export, not per document. A batched "
            "target's documents wait for `export_fiscal_documents`."
        )

    def query(self, document_key: str) -> Outcome:
        from core.fiscal import export

        if export.holds(self.context, document_key):
            return Outcome(state=HELD)
        return Outcome(state=REFUSED)

    def fetch_representation(self, external_id: str) -> str:
        return ""


def _refusal(mapping: mappings.Mapping, reply: transports.Reply) -> str:
    """The target's reason, parsed into one sentence.

    A raw body never reaches a person (§B.10.3): it is stored on
    `fiscal_documents.response` and read in Django admin.
    """
    stated = reply.body.get(mapping.reply_error) or reply.body.get("message")
    if isinstance(stated, str) and stated.strip():
        return f"El sistema de facturación rechazó el documento: {stated.strip()}"
    return f"El sistema de facturación respondió {reply.status}."


def _transport_reason(failure: Exception) -> str:
    del failure  # its text is a socket error and is not a sentence for a person
    return "No hay conexión con el sistema de facturación."


# ---------------------------------------------------------------------------
# The two targets that ship with this stage
# ---------------------------------------------------------------------------


def _open_loopback(context: Context):
    return HttpJsonTarget(
        transports.LoopbackTransport(context.tenant_id, context.credential),
        context.mapping,
    )


def _open_http(context: Context):
    return HttpJsonTarget(
        transports.HttpTransport(
            context.options.get("base_url", ""), context.credential
        ),
        context.mapping,
    )


def _open_file(context: Context):
    return FileTarget(context)


REGISTRY: dict[str, Spec] = {}


def register(spec: Spec) -> Spec:
    """Register one target. Called once per target, at import."""
    if spec.id in REGISTRY:
        raise RuntimeError(f"A target named {spec.id!r} is already registered.")
    REGISTRY[spec.id] = spec
    return spec


def registry() -> dict:
    return REGISTRY


def get(target_id: str) -> Spec:
    spec = REGISTRY.get(target_id)
    if spec is None:
        raise LookupError(
            f"No target named {target_id!r}. The targets are declared in "
            "core.fiscal.targets and nowhere else."
        )
    return spec


register(
    Spec(
        id="loopback",
        label="Sistema de prueba (local)",
        needs_credential=False,
        needs_base_url=False,
        batched=False,
        mappings=(mappings.LOOPBACK.id, mappings.LOOPBACK_BLIND.id),
        open=_open_loopback,
    )
)

register(
    Spec(
        id="file",
        label="Exportación de archivos",
        needs_credential=False,
        needs_base_url=False,
        batched=True,
        mappings=(mappings.FILE.id,),
        open=_open_file,
    )
)

#: The shape a client's system takes: a JSON API at an address the pharmacy
#: gives us, behind a credential the instance holds and a mapping written
#: against that system's own field names. **No vendor is contracted at v1**
#: (§9, §11.1), so the mapping it defaults to is the canonical one -- the first
#: client's is registered beside it in `core.fiscal.mappings` and named here,
#: which is the whole of adding a target.
register(
    Spec(
        id="http_json",
        label="API del sistema de facturación",
        needs_credential=True,
        needs_base_url=True,
        batched=False,
        mappings=(mappings.LOOPBACK.id,),
        open=_open_http,
    )
)


def open_target(tenant, options: dict):
    """The target a tenant has configured, opened with its credential.

    Returns `(spec, target)`, or `(None, None)` where nothing is configured --
    which is the default and is not an error (§8).
    """
    from core.fiscal import secrets

    target_id = (options or {}).get("target") or ""
    if not target_id or target_id not in REGISTRY:
        return None, None
    spec = REGISTRY[target_id]
    if not secrets.resolves(tenant, spec):
        return None, None
    mapping_id = (options.get("mapping") or "") or spec.default_mapping
    if mapping_id not in spec.mappings:
        mapping_id = spec.default_mapping
    context = Context(
        tenant_id=str(getattr(tenant, "id", tenant)),
        options=options,
        credential=secrets.read(tenant),
        mapping=mappings.get(mapping_id),
    )
    return spec, spec.open(context)
