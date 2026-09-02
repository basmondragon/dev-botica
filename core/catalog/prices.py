"""Price resolution, and the one write path that exists (A11).

**A price changes in exactly one place**: `set_price`, called by
`POST /api/items/{id}/prices`, writing a row at `source = manual` carrying the
caller's id. The only other writer of `item_prices` in the whole product is this
stage's load tool, which writes `imported` rows at onboarding under an operator
running a command with an explicit tenant -- a second *person*, not a second
kind of writer. There is no third, and neither of the two is a model.

That is what makes A11 a structural property rather than a policy: human
approval implemented as a setting is a setting somebody can turn off; a model
with no write path has no such setting to find. So the audit row, the cap check
and the proposal stamp live together here, and a stage that finds it needs its
own price write has found a change to A11 rather than an implementation detail.
"""

from datetime import date
from decimal import Decimal

from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from ninja.errors import HttpError

from core import audit
from core.models import AuditAction, Item, ItemPrice, PriceSource


#: Every price is stored to the centavo, because a box of 30 at $12.000 divides
#: to $400,00 and a box of 30 at $12.500 does not. Every *displayed* and
#: *charged* figure is a whole peso (§A.11), and the rounding happens once, at
#: the line total, half-up.
CENTAVO = Decimal("0.01")


class PriceRefused(HttpError):
    """A refusal the field's own error slot renders (§B.5.7).

    422 rather than 409: the figure in the body is the thing that is wrong, and
    the recovery is to type a different one.
    """

    def __init__(self, message):
        super().__init__(422, message)


def today(tenant_tz=None):
    """The pharmacy's own day. Every window in this module is compared against
    it rather than against a timestamp, because a price list is dated in days."""
    del tenant_tz  # one timezone per instance at v1 (S0's `/api/settings/tenant`)
    return timezone.localdate()


def in_force(item_id, *, location_id=None, on=None):
    """The price in force for one item, at one scope, on one day.

    **The rule**: the row whose window contains the day, preferring a
    `location_id` match over the network-wide row; within one scope, the latest
    `effective_from` wins. There is no activation job -- a future-dated row
    becomes current because this runs at read time.
    """
    day = on or today()
    rows = ItemPrice.objects.filter(item_id=item_id, effective_from__lte=day).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gt=day)
    )
    if location_id is not None:
        scoped = (
            rows.filter(location_id=location_id).order_by("-effective_from").first()
        )
        if scoped is not None:
            return scoped
    return rows.filter(location__isnull=True).order_by("-effective_from", "-id").first()


def network_price_subquery(day):
    """The same rule as a subquery, so the grid resolves 25 prices in one query
    rather than 25. Network-wide only: the catalog grid is the network's, and a
    sede override is shown in the item panel where its scope can be named."""
    return Subquery(
        ItemPrice.objects.filter(
            item_id=OuterRef("pk"),
            location__isnull=True,
            effective_from__lte=day,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=day))
        .order_by("-effective_from", "-id")
        .values("price")[:1]
    )


def box_price(item, unit_price):
    """The derived box figure the editor shows beside a fraccionable item.

    `item_prices.price` is always per base unit; a box price is
    `price × units_per_pack`, **shown and never stored as a second row**. There
    is exactly one price per item per scope per moment.
    """
    if unit_price is None:
        return None
    return Decimal(unit_price) * item.units_per_pack


def check_against_cap(item, price):
    """The last gate before a price reaches a till (§11.4, A11).

    A price above a **known** `regulated_max_price` is refused, naming the cap,
    whatever produced the number -- typed from scratch, read off a supplier's
    list, or pre-filled from a suggestion. S7 checks its own proposals when it
    computes them, but a proposal is not a price and a person may type any
    figure into that field, so the check runs again here against the value
    actually being saved.

    **A null cap means *unknown*, never *uncapped*.** The editor says so beside
    the field rather than passing the save in silence, and it does not refuse on
    an unknown cap: at S1 every cap is null, and refusing on unknown would make
    the entire catalog unpriceable.
    """
    cap = item.regulated_max_price
    if cap is not None and Decimal(price) > cap:
        raise PriceRefused(f"El precio supera el tope regulado de {_pesos(cap)}.")


def _pesos(amount):
    """`$15.600` -- the §A.11 form, for a message the field's error slot shows.

    Server-side rather than client-side because the refusal has to name the cap
    even when it reaches an operator through a command or a log line.
    """
    whole = int(Decimal(amount).quantize(Decimal("1")))
    return "$" + f"{whole:,}".replace(",", ".")


def set_price(
    *,
    item,
    actor,
    tenant_id,
    price,
    effective_from=None,
    location=None,
    proposal_id=None,
    request_id="",
):
    """Write a price. **The only interactive path that does** (A11).

    Creates a row and closes the row the new one supersedes, in the same scope,
    in the same transaction. `source` is `manual` always and `set_by_user_id` is
    the caller -- neither is accepted from a request body, because a field a
    client can set is a field a client can lie about, and the second question of
    every price dispute is who typed it.
    """
    start = effective_from or today()
    # To the centavo, because the column is `numeric(12,2)` and a figure that
    # only takes its final shape on the way back out of the database makes the
    # audit row's `before` and `after` two different spellings of one number.
    price = Decimal(price).quantize(CENTAVO)
    if price < 0:
        raise PriceRefused("El precio no puede ser negativo.")
    check_against_cap(item, price)

    scope = Q(location=location) if location is not None else Q(location__isnull=True)
    rows = ItemPrice.objects.filter(item=item).filter(scope)
    earliest = rows.order_by("effective_from").values_list("effective_from", flat=True)
    if earliest and start < earliest[0]:
        # Backdating behind everything the scope holds would close a row before
        # it opened. Refusing names the date; accepting would leave two rows
        # whose order only the resolution rule could explain.
        raise PriceRefused(
            f"Ya hay un precio con fecha {earliest[0]:%d/%m/%Y}. Un precio "
            "nuevo empieza en esa fecha o después."
        )

    # The row this one supersedes is **the one whose window contains `start`**,
    # not simply the open one -- so a price already dated ahead does not make the
    # item unrepricable today. It survives untouched, and the new row closes on
    # its date rather than opening a second window over it.
    superseded = (
        rows.filter(effective_from__lte=start)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=start))
        .order_by("-effective_from", "-id")
        .first()
    )
    pending = (
        rows.filter(effective_from__gt=start)
        .order_by("effective_from")
        .values_list("effective_from", flat=True)
        .first()
    )

    before = None
    if superseded is not None:
        before = {
            "price": str(superseded.price),
            "effective_from": superseded.effective_from.isoformat(),
            "source": superseded.source,
        }
        superseded.effective_to = start
        superseded.save(update_fields=["effective_to", "updated_at"])

    row = ItemPrice.objects.create(
        tenant_id=tenant_id,
        item=item,
        location=location,
        price=price,
        effective_from=start,
        # Closed on the day the next row already dated in this scope opens, so
        # the partial unique index still sees exactly one open row.
        effective_to=pending,
        source=PriceSource.MANUAL,
        proposal_id=proposal_id,
        set_by_user=actor if getattr(actor, "id", None) else None,
        # Stamped, not joined: S0's referential rule is that a stage referencing
        # `users` does so ON DELETE SET NULL **and stamps the identity it needs
        # at write time**, and this is the screen that needs one.
        set_by_name=getattr(actor, "name", "") or "",
    )

    audit.record(
        actor=actor,
        tenant_id=tenant_id,
        action=AuditAction.CREATE,
        entity_type="item_prices",
        entity_id=row.id,
        before=before,
        after={
            "item": str(item.id),
            "location": str(location.id) if location else None,
            "price": str(row.price),
            "effective_from": row.effective_from.isoformat(),
            "source": row.source,
            "proposal_id": str(proposal_id) if proposal_id else None,
        },
        request_id=request_id,
    )
    return row


def withdraw_price(*, row, actor, tenant_id, request_id=""):
    """Take one price row out of play, and say which of the two things it did.

    A row **never in force** -- dated ahead and not yet reached -- is removed
    outright: nothing was ever charged at it. A row that *has* been in force is
    not deletable, because it is what a past sale was made at; it is **closed**
    instead, and a closed sede override returns that sede to the network price
    by the resolution rule with no further edit.

    The one refusal is closing the open **network-wide** row, which would leave
    the item with no price at any sede. An item that should not be sold is
    deactivated; it is not left priceless.
    """
    day = today()
    if row.effective_from > day:
        # Read before the delete: `Model.delete()` sets the instance's pk to
        # None, so an audit row that took `row.id` afterwards named no entity at
        # all -- and a trail that cannot say which row was removed is half a
        # trail.
        row_id = row.id
        before = {
            "item": str(row.item_id),
            "price": str(row.price),
            "effective_from": row.effective_from.isoformat(),
            "location": str(row.location_id) if row.location_id else None,
        }
        # Re-open whatever this row closed. A future-dated row closes the row in
        # force at the moment it is created; deleting it without undoing that
        # would leave the item with no open price in that scope at all, which is
        # a silent hole in every till's price list.
        scope = (
            Q(location_id=row.location_id)
            if row.location_id
            else Q(location__isnull=True)
        )
        superseded = (
            ItemPrice.objects.filter(
                item_id=row.item_id, effective_to=row.effective_from
            )
            .filter(scope)
            .exclude(id=row.id)
            .order_by("-effective_from")
            .first()
        )
        row.delete()
        if superseded is not None:
            superseded.effective_to = None
            superseded.save(update_fields=["effective_to", "updated_at"])
        audit.record(
            actor=actor,
            tenant_id=tenant_id,
            action=AuditAction.DELETE,
            entity_type="item_prices",
            entity_id=row_id,
            before=before,
            request_id=request_id,
        )
        return "deleted"

    if row.effective_to is not None:
        raise HttpError(409, "Este precio ya está cerrado.")
    if row.location_id is None:
        raise PriceRefused(
            "Un producto no puede quedarse sin precio de red. Fije un precio "
            "nuevo, o desactive el producto."
        )

    before = {
        "effective_to": None,
        "price": str(row.price),
        "location": str(row.location_id),
    }
    row.effective_to = day
    row.save(update_fields=["effective_to", "updated_at"])
    audit.record(
        actor=actor,
        tenant_id=tenant_id,
        action=AuditAction.UPDATE,
        entity_type="item_prices",
        entity_id=row.id,
        before=before,
        after={"effective_to": day.isoformat()},
        request_id=request_id,
    )
    return "closed"


def import_price(
    *, tenant_id, item, price, effective_from, location=None, effective_to=None
):
    """The load tool's writer: `source = imported`, `proposal_id`,
    `set_by_user_id` and `set_by_name` all empty, because no person typed it and
    no model produced it. There is no third source it could write (A11).

    Idempotent on item, scope and `effective_from`, which is the loader's
    natural key for this entity: the same file applied twice is a no-op.
    """
    existing = (
        ItemPrice.objects.filter(item=item, effective_from=effective_from)
        .filter(
            Q(location=location) if location is not None else Q(location__isnull=True)
        )
        .first()
    )
    if existing is not None:
        changed = []
        if existing.price != price:
            existing.price = price
            changed.append("price")
        if existing.effective_to != effective_to:
            existing.effective_to = effective_to
            changed.append("effective_to")
        if changed:
            existing.save(update_fields=[*changed, "updated_at"])
        return existing, bool(changed)

    if effective_to is None:
        # One open row per scope, always. An import that lands a newer open row
        # closes whatever it supersedes rather than colliding with it.
        for open_row in (
            ItemPrice.objects.filter(item=item, effective_to__isnull=True)
            .filter(
                Q(location=location)
                if location is not None
                else Q(location__isnull=True)
            )
            .order_by("-effective_from")
        ):
            if open_row.effective_from > effective_from:
                raise ValueError(
                    "el precio abierto de este producto empieza después de esta fecha"
                )
            open_row.effective_to = effective_from
            open_row.save(update_fields=["effective_to", "updated_at"])

    return (
        ItemPrice.objects.create(
            tenant_id=tenant_id,
            item=item,
            location=location,
            price=price,
            effective_from=effective_from,
            effective_to=effective_to,
            source=PriceSource.IMPORTED,
        ),
        True,
    )


def history(item: Item, *, day: date | None = None):
    """Every price row for one item, newest first, with the row in force per
    scope marked. What the panel's Precio section reads."""
    day = day or today()
    rows = list(
        ItemPrice.objects.filter(item=item)
        .select_related("location")
        .order_by("-effective_from", "-created_at")
    )
    current = {
        row.location_id: row
        for row in sorted(rows, key=lambda one: one.effective_from)
        if row.effective_from <= day
        and (row.effective_to is None or row.effective_to > day)
    }
    return rows, current
