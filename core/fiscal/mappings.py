"""The declarative mapping layer. **The canonical payload is ours; the field
names are theirs.**

A target is three things: a transport, a declarative mapping, and a credential
(§8). The mapping declares the envelope, the field names, and the value
vocabularies -- payment method, document type, `vat_class` and its rate -- plus
**which field carries `document_key`**, which is the one question asked of every
new target before anything else.

**Adding a target is a mapping plus credentials, and it is never a change to the
sale.** That is checkable: the diff that adds a second target must touch no file
under the sale, the ticket, the sync path or the canonical builder. If it does,
the payload was incomplete and the fix belongs in the payload for everyone, not
in a branch for one client.

**No mapping validates.** Validation is on the canonical payload, once, for
every target (`core.fiscal.document`) -- put it in a mapping and each new client
rediscovers the same missing NIT.
"""

from dataclasses import dataclass, field

#: Every canonical group and line field, in the order a reader of the payload
#: meets them. A mapping renames from this list and never invents a name that is
#: not in it: a key a mapping emits from nowhere is a key nobody can trace back
#: to the sale it came from.
GROUPS = ("document", "emitter", "acquirer", "lines", "totals", "payments")

LINE_FIELDS = (
    "position",
    "item_code",
    "item_id",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "discount",
    "vat_class",
    "tax_rate",
    "tax_amount",
    "line_total",
)


@dataclass(frozen=True)
class Mapping:
    """One receiving system's vocabulary.

    `version` is stored on every row it produces (`fiscal_documents.
    mapping_version`), because a delivery that succeeded a year ago and fails
    today is otherwise indistinguishable from a target that changed its API --
    and because changing the version is what re-renders every stuck document on
    its next attempt without a data migration over rows we cannot legally edit.
    """

    id: str
    version: str
    #: **Which field carries `document_key`** as the target's idempotency key or
    #: external reference. `None` means the target offers none -- and a target
    #: with neither this nor a `query` operation is delivered to **once per
    #: document, by policy**, because a blind retry against a system that cannot
    #: dedupe is how a pharmacy ends up with two signed fiscal documents for one
    #: sale (§8).
    idempotency_field: str | None = None
    #: Constants the target expects in every body -- a document kind, a schema
    #: version, an establishment code.
    envelope: dict = field(default_factory=dict)
    #: Canonical group name -> the name this target gives it. A group absent
    #: from the map keeps its canonical name.
    groups: dict = field(default_factory=dict)
    #: Canonical line field -> this target's name for it.
    line_fields: dict = field(default_factory=dict)
    payment_methods: dict = field(default_factory=dict)
    document_types: dict = field(default_factory=dict)
    vat_classes: dict = field(default_factory=dict)
    #: What an unidentified acquirer becomes at this target. Our
    #: `is_final_consumer` has to become something concrete, and the exact form
    #: belongs to the target rather than to the payload (§8).
    final_consumer: dict = field(default_factory=dict)
    #: Whether this target offers a status query. **A target with neither an
    #: idempotency field nor a `query` operation is delivered to once per
    #: document, by policy** (§8): a blind retry against a system that cannot
    #: dedupe is how a pharmacy ends up with two signed fiscal documents for one
    #: sale. Declared here because it is a property of that system's API and not
    #: of our transport.
    supports_query: bool = True
    #: For the file target: the columns of a CSV at **line grain**. `None`
    #: renders JSON instead.
    columns: tuple | None = None
    #: Where the target's own identifiers live in its reply.
    reply_external_number: str = "external_number"
    reply_cude: str = "cude"
    reply_pdf_url: str = "pdf_url"
    #: The reply field carrying a refusal in words, parsed into one sentence.
    reply_error: str = "error"


def render(mapping: Mapping, document: dict) -> dict:
    """The canonical document in one target's vocabulary.

    Mechanical and total: every group travels, renamed where the mapping says
    so, with the three vocabularies translated and the idempotency field set.
    Nothing is dropped -- a mapping that needs less sends more, which costs a
    receiving system nothing, while a mapping that needs more is a defect found
    on the first delivery and fixed in one of three stated places (§8).
    """
    body: dict = dict(mapping.envelope)
    for group in GROUPS:
        name = mapping.groups.get(group, group)
        body[name] = _group(mapping, group, document[group])
    if mapping.idempotency_field:
        body[mapping.idempotency_field] = document["document"]["document_key"]
    return body


def _group(mapping: Mapping, group: str, value):
    if group == "acquirer":
        return _acquirer(mapping, value)
    if group == "lines":
        return [_line(mapping, line) for line in value]
    if group == "payments":
        return [
            {
                **payment,
                "method": mapping.payment_methods.get(
                    payment["method"], payment["method"]
                ),
            }
            for payment in value
        ]
    if group == "totals":
        return {
            **value,
            "tax_by_class": [
                {
                    **row,
                    "vat_class": mapping.vat_classes.get(
                        row["vat_class"], row["vat_class"]
                    ),
                }
                for row in value["tax_by_class"]
            ],
        }
    return value


def _acquirer(mapping: Mapping, acquirer: dict) -> dict:
    if acquirer.get("is_final_consumer"):
        return {**acquirer, **mapping.final_consumer}
    return {
        **acquirer,
        "document_type": mapping.document_types.get(
            acquirer["document_type"], acquirer["document_type"]
        ),
    }


def _line(mapping: Mapping, line: dict) -> dict:
    renamed = {}
    for name in LINE_FIELDS:
        value = line[name]
        if name == "vat_class":
            value = mapping.vat_classes.get(value, value)
        renamed[mapping.line_fields.get(name, name)] = value
    return renamed


def csv_rows(mapping: Mapping, document: dict) -> list[list]:
    """One row per line, for a file target whose mapping declares columns.

    Line grain rather than document grain, because the accountant importing the
    file needs the tax per line for the same reason the payload carries it (§3).
    Every column is looked up across the document's four scalar groups, so a
    mapping adds a column by naming it rather than by writing an extractor.
    """
    if not mapping.columns:
        return []
    flat_header = {
        **document["document"],
        **{
            f"emitter_{key}": value
            for key, value in document["emitter"].items()
            if key != "location"
        },
        **{
            f"location_{key}": value
            for key, value in document["emitter"]["location"].items()
        },
        **{f"acquirer_{key}": value for key, value in document["acquirer"].items()},
        **{
            f"total_{key}": value
            for key, value in document["totals"].items()
            if key != "tax_by_class"
        },
    }
    rows = []
    for line in document["lines"]:
        flat = {**flat_header, **{f"line_{key}": value for key, value in line.items()}}
        rows.append([_cell(flat.get(column)) for column in mapping.columns])
    return rows


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, dict)):
        return ""
    return str(value)


# ---------------------------------------------------------------------------
# The two mappings that ship with this stage.
#
# Neither belongs to a client. §11.1 is *Gated on* and is not a blocker: the
# canonical document, the delivery, the exactly-once mechanism, the work list
# and the settings surface are all built regardless, and the first client
# mapping is written against the first client's system as one more entry here.
# ---------------------------------------------------------------------------

#: The loopback's mapping. Deliberately **not** the identity: it renames two
#: groups, translates all three vocabularies and names an idempotency field, so
#: that every check in this stage exercises the mapping layer rather than
#: walking around it. A mapping that renamed nothing would pass on a payload
#: whose renderer was broken.
LOOPBACK = Mapping(
    id="loopback",
    version="1",
    idempotency_field="idempotency_key",
    envelope={"schema": "botica.canonical.v1"},
    groups={"document": "header", "emitter": "issuer"},
    line_fields={"item_code": "code", "description": "name"},
    payment_methods={
        "cash": "CASH",
        "debit_card": "DEBIT",
        "credit_card": "CREDIT",
        "transfer": "TRANSFER",
        "other": "OTHER",
    },
    document_types={
        "CC": "13",
        "CE": "22",
        "NIT": "31",
        "TI": "12",
        "PA": "41",
        "PEP": "47",
        "PPT": "48",
    },
    vat_classes={
        "excluded": "EXC",
        "exempt": "EXE",
        "rate_5": "IVA5",
        "rate_19": "IVA19",
    },
    final_consumer={
        "document_type": "13",
        "document": "222222222222",
        "name": "Consumidor final",
    },
)

#: The file target's mapping: a CSV at line grain, with the columns an
#: accountant's import expects. **`document_key` is a column** -- the file has no
#: idempotency header to put it in, and exactly-once has to survive the
#: transport: a document already written into a period's file is never written
#: into another.
FILE = Mapping(
    id="file",
    version="1",
    idempotency_field=None,
    columns=(
        "document_key",
        "type",
        "sale_number",
        "occurred_at",
        "recorded_at",
        "emitter_nit",
        "location_code",
        "acquirer_document_type",
        "acquirer_document",
        "acquirer_name",
        "line_position",
        "line_code",
        "line_name",
        "line_quantity",
        "line_unit_price",
        "line_discount",
        "line_vat_class",
        "line_tax_rate",
        "line_tax_amount",
        "line_line_total",
        "total_total",
    ),
    document_types={},
    vat_classes={},
)

#: The badly-behaved target, and it exists because one is real. A system that
#: neither carries an idempotency reference nor answers a status query cannot be
#: retried safely, and the product has to have a defined behaviour for it rather
#: than discovering one at a pilot. Same envelope, same vocabularies; only the
#: two guarantees are withdrawn.
LOOPBACK_BLIND = Mapping(
    id="loopback_blind",
    version="1",
    idempotency_field=None,
    supports_query=False,
    envelope=LOOPBACK.envelope,
    groups=LOOPBACK.groups,
    line_fields=LOOPBACK.line_fields,
    payment_methods=LOOPBACK.payment_methods,
    document_types=LOOPBACK.document_types,
    vat_classes=LOOPBACK.vat_classes,
    final_consumer=LOOPBACK.final_consumer,
)

REGISTRY: dict[str, Mapping] = {
    LOOPBACK.id: LOOPBACK,
    LOOPBACK_BLIND.id: LOOPBACK_BLIND,
    FILE.id: FILE,
}


def get(mapping_id: str) -> Mapping:
    mapping = REGISTRY.get(mapping_id)
    if mapping is None:
        raise LookupError(
            f"No mapping named {mapping_id!r}. The mappings are declared in "
            "core.fiscal.mappings and nowhere else."
        )
    return mapping


def register(mapping: Mapping) -> Mapping:
    """Register one client's mapping. **This is the whole of adding a target.**"""
    if mapping.id in REGISTRY:
        raise RuntimeError(f"A mapping named {mapping.id!r} is already registered.")
    REGISTRY[mapping.id] = mapping
    return mapping
