"""The `pricing` fixture, registered with **S0's** `seed_demo_tenant`.

S7 ships no seed command. It registers one fixture, declares S4's sales as its
dependency, and **writes no estimate and no proposal by hand**: it sets the
tenant's own margin goal, loads caps the way a regente would, runs
`pricing.run` exactly as the cron does, and then resolves a subset **through
S1's price editor**.

*That is the whole design of this file.* A fixture that stamped
`price_proposals` rows directly would keep rendering a convincing screen after
the engine had broken, which is the one thing a completion test must not do --
and it would model a write path that does not exist, because `taken`,
`modified` and `dismissed` are S1's writes and nowhere else's (A11).

**The two runs are deliberate.** The first produces the suggestions a person
then acts on; the resolutions date back about ten days; the second run is the
following Monday's, and it is what puts the resolved references into
`cooldown`, supersedes what nobody looked at, and leaves the screen in the state
a tenant is actually in on the second week -- resolved rows beside live ones,
which is the mix `Adopción` and the `Sugerido` column both exist to show.

**No cap is invented as fact.** Every seeded `regulated_max_price` carries a
plainly fictional source reference, because a real CNPMDM ceiling is a legal
figure and a demo that shipped one somebody could mistake for the real number is
worse than a demo with no caps at all (§11.4).

**What this fixture cannot build, stated here rather than left to be
discovered.** The stage document asks `default` for *around 30 references*
carrying an elasticity-backed suggestion. It builds the estimates -- real fits
over sales S4 wrote, on references whose price genuinely moved and whose demand
genuinely responded -- and it builds **no elasticity proposal at all**, because
two constants meet and the fixture owns neither.

The materiality floor is `$50.000` a month and is deliberately not a setting: a
dial that controls how many proposals appear is a dial somebody turns until the
screen looks productive. At a 3% step, clearing it needs a single reference
turning over about `$1,7 M` a month. This seed rings roughly ten thousand
tickets across six sedes -- a size S4's own fixture chose on purpose, so that a
seed builds in seconds rather than minutes -- and its very best reference turns
over about `$2 M` a month while the rest are one to two orders of magnitude
below that. A reference that busy is also one whose shelf budget empties early,
so it is rarely the one that also carries twenty weeks of price variation.

So the screen this fixture produces reads `0 de 260 propuestas con elasticidad`
on its provenance line, and that is **the honest answer for a tenant this
size**, in the words the stage document itself uses for it: *the honest question
at a pilot review is how much of this screen is evidence yet, the `Base` chip
answers it in one click, and the answer being "not much" is the expected reading
rather than a defect.* What would change it is a bigger seed -- `REVENUE_UNIT`
in S4's fixture is the one number that sets its absolute size -- and that is a
trade against every seed run in the suite, which is S4's call and not this
stage's to make.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from core.catalog import prices as price_service
from core.demo.registry import register
from core.models import (
    AuditLog,
    CapStatus,
    ElasticityEstimate,
    Item,
    PriceProposal,
    PriceProposalStatus,
    SupplierItem,
    Tenant,
    User,
)
from core.pricing import caps as cap_service, engine
from core.pricing import settings as pricing_settings

logger = logging.getLogger(__name__)

#: The seed's own settings, **and `margin_goal_pct` is set explicitly because no
#: default ships**. `22,0` is the handoff's illustration and this seed's value,
#: never a shipped default: a number we chose would arrive on an owner's screen
#: wearing the same badge as their own goal.
SETTINGS = {
    "margin_goal_pct": 22.0,
    "max_single_step_pct": 3.0,
    "rounding_unit": 50,
    "min_days_between_changes": 30,
    #: **The seed never demonstrates the product with the guardrail switched
    #: off, on any profile.**
    "allow_raise_without_cap": False,
}

#: Roughly how many references carry a loaded ceiling -- the state a real tenant
#: reaches after a regente spends half an hour on the references they actually
#: sell under control.
CAPPED_REFERENCES = 60
#: How many are stated to be outside price control. It has to cover the
#: references the margin rule is meant to suggest on, because **the default
#: forbids raising a reference nobody has ruled on** and a seed that contradicted
#: its own engine would teach a build agent the wrong rule.
NOT_REGULATED_REFERENCES = 240

#: Two above their cap, so `Propuestas sobre el tope` is non-zero, the critical
#: badge renders and the absent-action rule is visible without anyone
#: constructing it by hand.
ABOVE_CAP_REFERENCES = 2
#: A handful bound by their cap, so `cap_bound_raise` renders.
CAP_BOUND_REFERENCES = 6
#: And a couple sitting exactly on it, so `cap_at_current` does too.
CAP_AT_CURRENT_REFERENCES = 2

#: The resolutions, in the three states a person's decision reaches. `modified`
#: differs from `suggested_price` **in both directions**, so the median signed
#: gap is not trivially one-sided.
TAKEN = 20
MODIFIED = 12
DISMISSED = 8
#: How far back the resolutions are dated. Inside `min_days_between_changes`, so
#: the second run puts those references into `cooldown` with the reason
#: rendered -- which is the state a real tenant is in a week after acting.
RESOLVED_DAYS_BACK = 10

#: The stale rows: two whose price a person moved after the run, and one whose
#: cost basis moved more than the 5% the grid tolerates.
STALE_PRICE_REFERENCES = 2
COST_MOVE_SHARE = Decimal("1.08")

#: What each profile builds. **`cold` is a profile, not a mutilated `default`**:
#: it runs the same engine over a tenant with catalog, stock, prices and caps and
#: no `sales` rows at all, which is how the sales-free path is reached.
PROFILES = {
    "default": {"caps": True, "resolve": True, "stale": True},
    "young": {"caps": True, "resolve": False, "stale": False},
    "cold": {"caps": True, "resolve": False, "stale": False},
    "scale": {"caps": True, "resolve": False, "stale": False},
    #: One network, one sede, one owner -- **and a handful of proposals and
    #: estimates anyway**, so an isolation check reading zero rows from the
    #: other tenant is reading zero from a table that has rows.
    "minimal": {"caps": True, "resolve": False, "stale": False},
}

#: The audit rows this fixture's own services write.
AUDITED_ENTITIES = (
    "items.regulated_max_price",
    "item_prices",
    "price_proposals",
    "settings.pricing",
)


def build(context):
    """Set the goal, load the caps, run the engine, then act like a person."""
    shape = PROFILES[context.profile]
    tenant = Tenant.objects.get(id=context.tenant_id)
    context.note(f"  precios         meta de margen {SETTINGS['margin_goal_pct']}%")

    # **A rerun does the work once and reports it twice.**
    #
    # Every row below carries an id the ORM minted rather than one derived from
    # a natural key -- the estimates and proposals `engine.run` writes, and the
    # `manual` rows S1's editor writes underneath `_resolve` and `_make_stale`
    # -- and `set_cap` restamps `items.updated_at` on every reference it
    # touches. So a second pass would not land on the rows the first one wrote.
    # It would put a second set of repricings beside them and move four thousand
    # item stamps, and S0's promise that a rerun over the seed's own rows changes
    # nothing would be false for this stage alone.
    #
    # Deriving the ids instead would mean a creation path existing only for the
    # seed, which is the thing `owned_ids` below states this fixture will not
    # build. Not running twice is the same answer given once more.
    #
    # `cap_status` is the marker: S1 leaves it empty on every reference it
    # writes, and `_load_caps` is the only thing that fills it -- on every
    # profile, since all five load caps.
    already = (
        Item.objects.filter(tenant_id=context.tenant_id).exclude(cap_status="").exists()
    )

    if not already:
        pricing_settings.write(tenant, dict(SETTINGS))
        actor = _regente(context)
        if shape["caps"]:
            loaded = _load_caps(context, actor)
            context.wrote("items", 0)
            context.note(f"  precios         {loaded} topes regulados cargados")

        engine.run(context.tenant_id)

        if shape["resolve"]:
            _resolve(context, actor)
            # **The second run is the following Monday's**, and it is what puts
            # the resolved references into `cooldown`, supersedes what nobody
            # looked at, and leaves the screen in the state a tenant is actually
            # in on the second week -- resolved rows beside live ones.
            engine.run(context.tenant_id)
            if shape["stale"]:
                # **After** the run, and only after: a suggestion is stale
                # because somebody moved the price the run computed against, so
                # an edit made before it would simply be the price the run read.
                _make_stale(context, actor)

    context.wrote(
        "elasticity_estimates",
        ElasticityEstimate.objects.filter(tenant_id=context.tenant_id).count(),
    )
    context.wrote(
        "price_proposals",
        PriceProposal.objects.filter(tenant_id=context.tenant_id).count(),
    )
    live = PriceProposal.objects.filter(
        tenant_id=context.tenant_id, status=PriceProposalStatus.PROPOSED
    )
    context.note(
        f"  precios         {live.filter(basis='margin_rule').count()} propuestas "
        f"por margen · {live.filter(basis='elasticity').count()} por elasticidad"
    )


#: Kept: `item_plan` builds four thousand rows and both callers are inside a
#: seed's hot path.
_DRAWN: dict[str, set] = {}


def drawn_references(profile) -> set:
    """The references whose figure a screen draws, which **no resolution may
    move**.

    S1's fixture marks them `fixed_price` and keeps its own repricings, sede
    overrides and future-dated rows off them, for one reason: the Mostrador
    ticket totals `$15.600` and four of those four figures are on it. A
    suggestion this fixture *takes* is a price change like any other, so the
    same rule binds here -- and it binds harder, because the proposal that moved
    it would look like the engine working.
    """
    from core.catalog.demo import item_plan

    if profile not in _DRAWN:
        _DRAWN[profile] = {
            (row["name"], row["presentation"])
            for row in item_plan(profile)
            if row["fixed_price"]
        }
    return _DRAWN[profile]


def _regente(context):
    """Whoever holds price rights on this tenant -- the administrator where
    there is one, the single `owner` under `minimal`."""
    people = list(User.objects.filter(tenant_id=context.tenant_id).order_by("email"))
    for role in ("admin", "owner"):
        for person in people:
            if person.role == role:
                return person
    return people[0] if people else None


# ---------------------------------------------------------------------------
# The caps
# ---------------------------------------------------------------------------


def _load_caps(context, actor) -> int:
    """Caps in all three states, in a realistic proportion.

    The references the margin rule is meant to act on are the ones a regente
    would have ruled on: they are `not_regulated` or `capped`, never `unknown`,
    because the default forbids raising a reference nobody has ruled on.
    """
    today = timezone.localdate()
    prices = engine.current_prices(context.tenant_id, today)
    costs = engine.cost_bases(context.tenant_id)
    goal = Decimal(str(SETTINGS["margin_goal_pct"]))

    ranked = []
    for item in Item.objects.filter(tenant_id=context.tenant_id).order_by("name"):
        price = prices.get(item.id)
        cost = costs.get(item.id, (None, None))[0]
        if price is None or cost is None:
            continue
        margin = engine.margin_pct(price, cost, item.vat_class)
        ranked.append((item, price, margin))

    # **Widest gap first**, because the states this fixture has to make visible
    # are all downstream of one: a reference sitting a tenth of a point below
    # the goal is `margin_gap_immaterial` before the cap logic is ever reached,
    # so picking the cap-bound and above-cap references off the top of an
    # alphabetical list would seed six caps nobody would ever see bind.
    below = sorted(
        (row for row in ranked if row[2] is not None and row[2] < goal),
        key=lambda row: (row[2], row[0].name),
    )
    at_or_above = [row for row in ranked if row[2] is not None and row[2] >= goal]

    # The compliance findings and the bound raises come off the top of the
    # below-goal list, so both are references the engine would otherwise have
    # suggested a rise on -- which is the only place either state is visible.
    written = 0
    cursor = 0

    def take(rows, count):
        nonlocal cursor
        chunk = rows[cursor : cursor + count]
        cursor += count
        return chunk

    for item, price, _margin in take(below, ABOVE_CAP_REFERENCES):
        # Below its own shelf price: the till is charging above the legal
        # maximum today, which is a compliance finding and not an opportunity.
        _cap(context, actor, item, price * Decimal("0.94"), "Circular CNPMDM 00-DEMO-1")
        written += 1
    for item, price, _margin in take(below, CAP_BOUND_REFERENCES):
        # Inside the step cap, so the suggestion lands **at** the ceiling.
        _cap(
            context, actor, item, price * Decimal("1.015"), "Circular CNPMDM 00-DEMO-2"
        )
        written += 1
    for item, price, _margin in take(below, CAP_AT_CURRENT_REFERENCES):
        _cap(context, actor, item, price, "Circular CNPMDM 00-DEMO-3")
        written += 1
    remaining_caps = CAPPED_REFERENCES - written
    for item, price, _margin in take(below, remaining_caps):
        _cap(context, actor, item, price * Decimal("1.5"), "Circular CNPMDM 00-DEMO-4")
        written += 1

    # The rest of the below-goal references a regente has ruled on are stated to
    # be outside price control, which is what lets the margin rule act on them.
    for item, _price, _margin in take(below, NOT_REGULATED_REFERENCES):
        _not_regulated(context, actor, item)
    # And every reference the estimator can actually fit, so an elasticity
    # suggestion is not silently blocked by a cap nobody has loaded.
    for item, _price, _margin in at_or_above[:NOT_REGULATED_REFERENCES]:
        _not_regulated(context, actor, item)
    return written


def _cap(context, actor, item, price, source):
    cap_service.set_cap(
        item=item,
        actor=actor,
        tenant_id=context.tenant_id,
        price=Decimal(price).quantize(Decimal("1")),
        status=CapStatus.CAPPED,
        source=source,
        request_id=f"seed:{context.profile}",
    )


def _not_regulated(context, actor, item):
    cap_service.set_cap(
        item=item,
        actor=actor,
        tenant_id=context.tenant_id,
        price=None,
        status=CapStatus.NOT_REGULATED,
        source="Listado interno de referencias sin control de precio (demo)",
        request_id=f"seed:{context.profile}",
    )


# ---------------------------------------------------------------------------
# What a person did with the suggestions
# ---------------------------------------------------------------------------


def _resolve(context, actor):
    """Take, modify and dismiss a subset -- **through S1's price editor**.

    A fixture that stamped the three resolved states directly would be modelling
    a write path that does not exist. So each one goes through `set_price`, which
    writes the `manual` row carrying `proposal_id` and the person's name, and
    then through `take_proposal`, which is the same call the endpoint makes.
    """
    when = timezone.localdate() - timedelta(days=RESOLVED_DAYS_BACK)
    at = timezone.now() - timedelta(days=RESOLVED_DAYS_BACK)
    drawn = drawn_references(context.profile)
    live = [
        one
        for one in PriceProposal.objects.filter(
            tenant_id=context.tenant_id, status=PriceProposalStatus.PROPOSED
        )
        .select_related("item")
        .order_by("item__name")
        if (one.item.name, one.item.presentation) not in drawn
    ]
    taken, modified, dismissed = 0, 0, 0
    # Alternating up and down, so the median signed gap is not one-sided.
    directions = (Decimal("1.03"), Decimal("0.97"))
    for proposal in live:
        if taken < TAKEN:
            _save(context, proposal, actor, proposal.suggested_price, when, at)
            taken += 1
        elif modified < MODIFIED:
            moved = (
                Decimal(proposal.suggested_price) * directions[modified % 2]
            ).quantize(Decimal("1"))
            cap = proposal.item.regulated_max_price
            if cap is not None and moved > cap:
                moved = Decimal(cap)
            if moved == Decimal(proposal.suggested_price):
                continue
            _save(context, proposal, actor, moved, when, at)
            modified += 1
        elif dismissed < DISMISSED:
            price_service.dismiss_proposal(
                proposal=proposal,
                actor=actor,
                tenant_id=context.tenant_id,
                request_id=f"seed:{context.profile}",
            )
            PriceProposal.objects.filter(id=proposal.id).update(resolved_at=at)
            dismissed += 1
        else:
            break
    logger.info(
        "seeded resolutions: %s taken, %s modified, %s dismissed",
        taken,
        modified,
        dismissed,
    )


def _save(context, proposal, actor, price, when, at):
    price_service.set_price(
        item=proposal.item,
        actor=actor,
        tenant_id=context.tenant_id,
        price=price,
        effective_from=when,
        proposal_id=proposal.id,
        request_id=f"seed:{context.profile}",
    )
    price_service.take_proposal(proposal=proposal, actor=actor, price=price)
    # The decision was made ten days ago, and `Tomada el 22/08` is a line the
    # grid renders. `take_proposal` stamps the clock the endpoint would; the
    # seed is the one caller that knows the clock is not now.
    PriceProposal.objects.filter(id=proposal.id).update(resolved_at=at)


def _make_stale(context, actor):
    """Two references repriced by hand after the run, and one whose cost moved.

    So the greyed figures, the sentence naming what moved and the no-pre-fill
    action are all visible without waiting a week for them.

    **The cost move is the one place this fixture reaches into a row another
    stage owns**, and it is stated rather than hidden: nothing in the product
    moves a cost basis without also moving stock, so a supplier's list price is
    edited directly on a reference that has none. It changes no row count, and
    the alternative -- a receipt, its lines and its ledger moves -- would seed
    S6's document trail to demonstrate a colour on S7's screen.
    """
    drawn = drawn_references(context.profile)
    live = [
        one
        for one in PriceProposal.objects.filter(
            tenant_id=context.tenant_id, status=PriceProposalStatus.PROPOSED
        )
        .select_related("item")
        .order_by("-computed_at", "item__name")
        if (one.item.name, one.item.presentation) not in drawn
    ]
    for proposal in live[:STALE_PRICE_REFERENCES]:
        moved = (Decimal(proposal.current_price) * Decimal("1.02")).quantize(
            Decimal("1")
        )
        cap = proposal.item.regulated_max_price
        if cap is not None and moved > cap:
            moved = Decimal(cap)
        if moved == Decimal(proposal.current_price):
            continue
        price_service.set_price(
            item=proposal.item,
            actor=actor,
            tenant_id=context.tenant_id,
            price=moved,
            request_id=f"seed:{context.profile}",
        )
    for proposal in live[STALE_PRICE_REFERENCES:]:
        if proposal.cost_source != "supplier":
            continue
        SupplierItem.objects.filter(
            tenant_id=context.tenant_id, item_id=proposal.item_id
        ).update(cost=Decimal(proposal.cost_basis) * COST_MOVE_SHARE)
        break


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def owned_ids(context):
    """Exactly the rows this fixture writes in its guard tables.

    **All four are read back, and that is a stated weakness rather than a hidden
    one** -- the same one S6's fixture states. Every id here is minted by the
    ORM inside the product's own service functions: `pricing.run` writes its
    estimates and proposals, S1's editor writes the `manual` price rows, and
    both write their own `audit_log` rows. Threading a derived id through them
    would be a second creation path existing only for the seed, which is the
    thing the ledger's cross-stage rule exists to prevent.

    What still holds is the guard's real protection: the command is confined to
    tenants whose slug begins `demo-`, which no provisioned network can acquire.
    What is given up is narrower: a suggestion somebody resolved through the
    product's own screens on a demo tenant no longer refuses the next seed run.
    """
    return {
        "elasticity_estimates": set(
            ElasticityEstimate.objects.filter(tenant_id=context.tenant_id).values_list(
                "id", flat=True
            )
        ),
        "price_proposals": set(
            PriceProposal.objects.filter(tenant_id=context.tenant_id).values_list(
                "id", flat=True
            )
        ),
        "audit_log": set(
            AuditLog.objects.filter(
                tenant_id=context.tenant_id, entity_type__in=AUDITED_ENTITIES
            ).values_list("id", flat=True)
        ),
    }


register(
    "pricing",
    tables=(
        "elasticity_estimates",
        "price_proposals",
        # One table this fixture adds to through another stage's service: every
        # elevated-role call in this stage appends to the audit trail, and
        # `audit_log` is guarded by S0's own fixture.
        #
        # **`item_prices` is deliberately not declared**, and it is worth saying
        # why rather than leaving a reader to infer it. That table is not under
        # the guard at all: S1's fixture writes four thousand rows into it and
        # declares only `items`, `customers` and `imports`. Declaring it here
        # would pull it into `all_guard_tables()` and refuse S1's own fixture on
        # the very next run -- so bringing `item_prices` under the guard is S1's
        # change to make, with S1's derived ids, and not a side effect of this
        # stage needing thirty-two `manual` rows.
        "audit_log",
    ),
    requires=("counter", "purchasing"),
    build=build,
    owned_ids=owned_ids,
)
