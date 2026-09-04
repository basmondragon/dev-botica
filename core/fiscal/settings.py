"""The `invoicing` key group of `tenants.settings` (ledger rule 5).

S5 owns exactly one group and writes it through S0's per-group helper, which
issues a single `jsonb_set` and leaves every other group as it stands. A
read-modify-write of the whole column would take out S0's `tenant` group, S2's
`sync` group and S3's `inventory` group, and would only be noticed on the screen
they belong to, weeks later.

**Empty is the default and means the handoff is off** (ledger, §8). Every
predicate in this stage reads `target` being absent as *no invoicing system is
connected*, which is a supported configuration and not a broken one.

**The credential is never here.** It lives in the instance's secrets store,
keyed per tenant (`core.fiscal.secrets`), because a credential in a JSONB column
is a credential every `admin` query can read -- and every `audit_log` row that
records a settings write would carry it too.
"""

from core import tenant_settings

GROUP = "invoicing"

#: The retry policy's three numbers, in the units the settings screen shows.
DEFAULT_RETRY: dict[str, object] = {
    #: How long the ladder keeps trying before a document is called `failed`.
    #: A transport failure is never `failed` before this.
    "cap_hours": 24,
    #: How long a document may sit in `sent` before the sweep **re-queries** it.
    #: Never a re-send: the target may already hold it.
    "dwell_minutes": 30,
    #: How far the till's clock may sit from the server's before a document is
    #: **held rather than delivered**. A document dated two days wrong at the
    #: far end is a correction someone makes by hand, and it is cheaper to hold
    #: one than to unwind one.
    "clock_skew_hours": 24,
}

DEFAULT_DELIVERY: dict[str, object] = {
    #: `per_sale` sends each document as it is built; `batched` is the file
    #: target's shape and renders a period into one file. **Derived from the
    #: target on every write** rather than chosen beside it -- see `write`.
    "mode": "per_sale",
    #: The file target's destination prefix inside object storage.
    "prefix": "fiscal-exports",
    #: `csv` at line grain where the mapping declares columns, `json` otherwise.
    "format": "json",
}

#: What an unwritten group reads as. **`target` empty is the whole of "off"**:
#: `handoff_enabled` reads it, the sweep exits on it, and the summary answers
#: `{"configured": false}` because of it.
DEFAULTS: dict[str, object] = {
    "target": "",
    "environment": "test",
    "base_url": "",
    #: Stamped when a target is first saved, and **never re-stamped**. This is
    #: the no-backfill rule expressed as data rather than as a convention
    #: somebody has to remember: nothing closed before it is ever queued, and
    #: the orphan check is bounded by the same timestamp.
    "configured_at": "",
    "mapping": "",
    "delivery": DEFAULT_DELIVERY,
    "retry": DEFAULT_RETRY,
    #: Who receives the daily failed-delivery digest. **Empty means no digest
    #: and the work list still renders** -- the list is the record and the email
    #: is a pointer to it.
    "notifications": [],
}

#: The one key an `admin` may not touch. §2 withholds billing and API-key
#: settings from `admin`, and naming a target is what decides which credential
#: is read out of the secrets store.
OWNER_ONLY = ("target", "environment", "base_url")

BOUNDS: dict[str, tuple[int, int]] = {
    "cap_hours": (1, 720),
    "dwell_minutes": (1, 1440),
    "clock_skew_hours": (1, 720),
}

ENVIRONMENTS = ("test", "production")
DELIVERY_MODES = ("per_sale", "batched")
FILE_FORMATS = ("csv", "json")


class Invalid(ValueError):
    """A settings write the group refuses, named in Spanish for the field."""


def read(tenant) -> dict:
    """The group, with every unwritten key standing at its default."""
    stored = tenant_settings.read_group(tenant, GROUP)
    merged = {**DEFAULTS, **stored}
    merged["delivery"] = {**DEFAULT_DELIVERY, **(stored.get("delivery") or {})}
    merged["retry"] = {**DEFAULT_RETRY, **(stored.get("retry") or {})}
    merged["notifications"] = list(stored.get("notifications") or [])
    return merged


def write(tenant, values: dict) -> dict:
    """Write the group through S0's helper. Returns what was written.

    **`configured_at` is stamped once**, when a target is first named, and is
    never moved by a later edit. Disconnecting clears the target and leaves the
    stamp: a reconnection resumes from the original boundary rather than
    silently orphaning every sale closed in between.
    """
    from django.utils import timezone

    current = read(tenant)
    merged = {**current, **values}
    if "delivery" in values:
        merged["delivery"] = {**current["delivery"], **(values["delivery"] or {})}
    if "retry" in values:
        merged["retry"] = {**current["retry"], **(values["retry"] or {})}
    # **The mode follows the target.** A file target delivers by export and an
    # API target delivers per sale; storing the two independently would let a
    # screen say `Por lotes` about a target that sends one request per ticket,
    # and the delivery job would believe the target while the screen believed
    # the setting.
    if merged["target"]:
        from core.fiscal import targets

        spec = targets.registry().get(merged["target"])
        if spec is not None:
            merged["delivery"] = {
                **merged["delivery"],
                "mode": "batched" if spec.batched else "per_sale",
            }
    check(merged)
    if merged["target"] and not merged["configured_at"]:
        merged["configured_at"] = timezone.now().isoformat()
    tenant_settings.write_group(tenant, GROUP, merged)
    return merged


def check(values: dict) -> None:
    """The bounds and the vocabularies, before anything is written."""
    from core.fiscal import targets

    target = values.get("target") or ""
    if target and target not in targets.registry():
        raise Invalid(
            f"«{target}» no es un sistema de facturación conocido. Los "
            "disponibles son: " + ", ".join(sorted(targets.registry())) + "."
        )
    if values.get("environment") not in ENVIRONMENTS:
        raise Invalid("El entorno es «test» o «production».")

    if target:
        spec = targets.get(target)
        if spec.needs_base_url and not (values.get("base_url") or "").strip():
            raise Invalid(
                "Este sistema de facturación necesita la dirección de su API."
            )
        mapping = values.get("mapping") or ""
        if mapping and mapping not in spec.mappings:
            raise Invalid(
                f"«{mapping}» no es un mapeo de este sistema. Los suyos son: "
                + ", ".join(spec.mappings)
                + "."
            )

    delivery = values.get("delivery") or {}
    if delivery.get("mode") not in DELIVERY_MODES:
        raise Invalid("La entrega es «per_sale» o «batched».")
    if delivery.get("format") not in FILE_FORMATS:
        raise Invalid("El archivo de exportación es «csv» o «json».")
    if not str(delivery.get("prefix") or "").strip():
        raise Invalid("La exportación necesita una carpeta de destino.")

    retry = values.get("retry") or {}
    for key, (low, high) in BOUNDS.items():
        given = retry.get(key)
        if not isinstance(given, int) or isinstance(given, bool):
            raise Invalid(f"«{key}» debe ser un número entero.")
        if not low <= given <= high:
            raise Invalid(f"«{key}» debe estar entre {low} y {high}.")

    recipients = values.get("notifications")
    if not isinstance(recipients, list) or any(
        not isinstance(one, str) for one in recipients
    ):
        raise Invalid("Los destinatarios del resumen son una lista de correos.")
