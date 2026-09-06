"""The `assistant` fixture, registered with **S0's** `seed_demo_tenant`.

S8 ships no seed command. It registers one fixture, declares S4's sales as its
dependency, and **mines its rules rather than inserting them**: the fixture runs
`assistant.cross_sell_refresh` over the seeded sales, so `basis` comes out
`counter` and the bands come out of the seeded volume instead of being chosen.
A seeded rule table hides the one failure this stage most needs the seed to
catch -- a miner that runs and produces nothing above the floor -- and lets a
demo pass on a job that has never executed.

**It writes no sale row and arranges no sede's shape.** S4 seeds Usme with three
weeks against the other five sedes' 180 days; this fixture mines over what S4
left, and Chapinero having three cards while Usme has two is a consequence of
that fixture rather than of anything arranged here.

**No gateway call is made.** The fixture writes `mode`, `model`, `cost_usd` and
`latency_ms` itself, because a seed that calls a model costs money on every run
and cannot run on a plane.

**The transcripts are synthetic and describe nobody.** Symptom text is health
data (§11.3), so they are a fixed set of generic Spanish sentences carrying no
name, no identifier and no age beyond a population chip.

## What this fixture cannot build, stated here rather than left to be discovered

Four of the stage document's targets for `default` are not reachable against
S4's own fixture, and each is one measurement rather than an opinion. S4 rings
**10.503 closed tickets, of which 3.431 carry two or more distinct items**,
drawn across **772 distinct references** out of a catalog of 4.284.

**1 · The support floor, and therefore the bands.** At that spread the strongest
ordered pair in the whole network recurs **nine** times, so
`cross_sell_min_support` at its production default of **25 clears nothing** and
a seed shipping the default would ship an empty `cross_sell_rules` on
`default` -- which is `cold`'s state, not this profile's, and would leave every
screen this stage draws for a warm sede unexercised. The fixture writes a floor
this window supports and prints it on the console. `confidence_band` is derived
at write time from the pair's own `support` and the window's own denominator:
`medium` wants a pair in **100** tickets and `high` wants **500** over a window
holding **10.000**, and this seed's whole window holds 3.431. So **every seeded
band is `low`**, the *Learning* regime is what `default` shows, and the
figureless form of `bought_together_location` is what renders. Nothing here
writes a band -- the miner does, from two numbers, and both being small on a
seed this size is the honest reading rather than a defect to paper over. The
check that reads both forms of the reason line moves one seeded rule's band by
hand and reloads, which is how the stage document already writes it.

**2 · Usme holds two rules rather than none.** At the production floor a sede
with three weeks of trading clears nothing; at this seed's floor it clears two
pairs. What the cold sede is *for* still holds and is what a demo shows:
**Usme's card C draws two cards and leaves the third slot empty**, because
neither its own two rules nor the network's cover any anchor the transcript
seeds, while Chapinero's draws three. The state where the table is empty
tenant-wide is the **`cold`** profile, which is how §1 says to reach it -- by
asking for a profile and never by deleting rows.

**3 · The combination list draws the pairs S4's tickets contain.** The stage
document asks for *suero oral + antidiarreico*, *analgésico + protector
gástrico* and *antigripal + vitamina C* at the head of *Combinaciones más
aceptadas*. Translated into this catalog's own taxonomy -- which is the grain
the definition in *Hands off* counts at -- the first of those is
`Bebidas y sueros + Digestivo`, and it occurs **zero** times across all 10.503
tickets: no ticket in the seed carries a rehydration product and a digestive
one together. `Analgésicos + Digestivo` occurs 28 times and
`Respiratorio + Metabólico` five. No choice of *which line to credit* can put
pairs that thin above the ones S4's draw makes common, so this fixture credits a
target pair wherever it exists and otherwise credits the ticket's own line
order. The list is populated, stable, deterministic and computed by the one
definition S9 reads -- and its top three are `Antibióticos + Analgésicos`,
`Cardiovascular + Analgésicos` and `Antibióticos + Metabólico`.

**4 · The acceptance counts follow the rate, not the handoff's absolutes.** An
acceptance is a **line on a ticket**, and once the anchor line and §7's three
exclusions are taken out, S4's tickets leave **2.207** creditable lines in the
whole network. The handoff's `3.412` is more acceptances than there are lines to
credit. So the fixture holds the rate the Panel draws -- `58,6%`, exactly -- and
takes 1.900 of the 2.207, deriving the offer count from it.

*What would change all four* is a bigger and more concentrated seed:
`REVENUE_UNIT` in S4's fixture is the one number that sets its absolute size,
and the composition of a ticket is that fixture's draw. Both are S4's call and a
trade against every seed run in the suite, which is why this file measures what
it was given and says so rather than writing rows S4 did not earn.
"""

from collections import defaultdict
from decimal import Decimal

from core.assistant import (
    extract,
    mining,
    pipeline,
    reasons,
    settings as assistant_settings,
)
from core.assistant.vocabulary import fold
from core.demo.registry import register
from core.inventory.demo import stable_int
from core.models import (
    AssistantMode,
    AssistantQuery,
    AssistantSuggestion,
    Category,
    CrossSellConfidence,
    CrossSellRule,
    Item,
    ItemWarning,
    ItemWarningSource,
    ItemWarningType,
    Location,
    LocationStatus,
    Sale,
    SaleLine,
    SaleStatus,
    StockOnHand,
    SuggestionType,
    Tenant,
    WarningSeverity,
)

# ---------------------------------------------------------------------------
# The shape
# ---------------------------------------------------------------------------

#: **The handoff's rate, on this seed's own volume.** `58,6%` is the number on
#: the Panel's donut and the number check 9 reads three ways, and the fixture
#: hits it exactly. The two counts under it are not the handoff's `3.412 de
#: 5.824`: an acceptance is a line on a ticket, and S4's tickets leave
#: **2.207** creditable lines in the whole network once the anchor line and
#: §7's three exclusions are taken out. The seed takes 1.900 of them and derives
#: the rest from the rate.
QUERIES = 2400
OFFERED = 3242
ACCEPTED = 1900

#: `young` is twelve days and `minimal` is a working tenant holding nothing
#: worth leaking, so both are sized down from the same construction.
YOUNG_QUERIES = 180
YOUNG_OFFERED = 246
YOUNG_ACCEPTED = 144

#: The per-sede acceptance spread, applied in the sedes' own revenue order.
#: **Six different numbers**, because a per-sede table where every row reads
#: 58,6% is a Panel that reads as a mock of itself.
SEDE_RATES = (0.622, 0.601, 0.586, 0.571, 0.552, 0.534)

#: One query in six ran without a model, which is what puts both values of
#: `mode` on `Registro del asistente`.
LOCAL_SHARE = 6

#: One model answer in fifty was discarded by the output check -- above the
#: 2% alert default often enough to be visible and not so often that the
#: seeded tenant opens on its own alarm.
REJECTED_EVERY = 55

#: What a call cost and how long it took. Fixed bands rather than a draw, so the
#: spend bar and the latency column are the same on every run.
COST_STEPS = ("0.002100", "0.002600", "0.003100", "0.003800")
LATENCY_STEPS = (620, 780, 940, 1180, 1460)

#: The mining floor this seed's own window can clear. **The production defaults
#: are 25 and 0,15**; see the module docstring for the measurement behind these.
SEED_MIN_SUPPORT = 5
SEED_MIN_CONFIDENCE = 0.05
#: The whole seeded history, so Ajustes' provenance line names a real window and
#: the miner is not handed half the tickets S4 wrote.
SEED_WINDOW_DAYS = 190

#: `symptom_key -> [category name]`, against S1's own tree. **This is the one
#: precondition of the cold-start floor** and it is configuration rather than
#: data, so it is seeded under every profile: an assistant that cannot filter is
#: not a smaller assistant, it is an unsafe one.
#:
#: Seven keys are deliberately left unmapped -- `blood_in_stool` is a red flag
#: and not a thing to sell, and the rest are complaints this catalog has no
#: shelf for. They are what Ajustes lists as *síntomas sin categoría*, and a map
#: with no gaps would leave that reading unexercised.
SYMPTOM_CATEGORIES: dict[str, tuple[str, ...]] = {
    "diarrhea": ("Bebidas y sueros", "Digestivo"),
    "dehydration": ("Bebidas y sueros",),
    "vomiting": ("Digestivo",),
    "nausea": ("Digestivo",),
    "abdominal_pain": ("Digestivo", "Analgésicos"),
    "heartburn": ("Digestivo",),
    "constipation": ("Digestivo",),
    "fever": ("Analgésicos",),
    "headache": ("Analgésicos",),
    "muscle_pain": ("Analgésicos",),
    "back_pain": ("Analgésicos",),
    "menstrual_pain": ("Analgésicos",),
    "sore_throat": ("Respiratorio",),
    "cough": ("Respiratorio",),
    "nasal_congestion": ("Respiratorio",),
    "runny_nose": ("Respiratorio",),
    "allergy": ("Antialérgicos",),
    "skin_rash": ("Antialérgicos", "Cuidado personal"),
    "itching": ("Antialérgicos", "Cuidado personal"),
    "fatigue": ("Metabólico",),
    "wound": ("Dispositivos médicos", "Cuidado personal"),
    "eye_irritation": ("Dispositivos médicos",),
}

#: What a cashier types, and nothing a person could be identified by. Each is
#: paired with the sede-agnostic pipeline run that produces its cards.
TRANSCRIPTS: tuple[str, ...] = (
    "Lleva dos días con diarrea y algo de fiebre. Adulto, toma losartán.",
    "Tiene tos y dolor de garganta desde ayer. Adulto.",
    "Dolor de cabeza fuerte desde anoche. Adulto, sin fiebre.",
    "Acidez y dolor de estómago después de comer. Adulto.",
    "Brote en la piel y picazón. Niño.",
    "Congestión nasal y estornudos hace tres días. Adulto.",
    "Cansancio y debilidad. Adulto mayor.",
    "Cólicos menstruales fuertes desde ayer.",
)

#: **The handoff's own loperamida row**, with its real trigger. Criteria 5 and 6
#: pass from the seed rather than from a test fixture.
LOPERAMIDA_TEXT = "no ofrecer si la fiebre pasa de 38,5 °C o si hay sangre"
LOPERAMIDA_TRIGGERS = [
    {"symptom": "fever", "operator": ">", "value": 38.5, "unit": "celsius"},
    {"symptom": "blood_in_stool"},
]

#: The rest of the safety layer, keyed by folded active ingredient.
#: `(type, severity, text, triggers)`.
#:
#: **The age clauses are what keep the drawn screen readable.** A population
#: clause naming an age is decided *false* by an adult query (S8, *3 · Filter*),
#: so an analgesic carrying *no dar a menores de 12 años* ranks normally for the
#: adult in the handoff's transcript and becomes a **Con condición** card for a
#: query that says *niño* -- which is both the right behaviour and the reason
#: card C at Chapinero draws the loperamida row rather than four caution cards.
WARNINGS: tuple[tuple[str, str, str, str, list], ...] = (
    (
        "ibuprofeno",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.BLOCKING,
        "no ofrecer en embarazo",
        [{"population": "pregnant"}],
    ),
    (
        "naproxeno",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.BLOCKING,
        "no ofrecer en embarazo",
        [{"population": "pregnant"}],
    ),
    (
        "diclofenaco",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.BLOCKING,
        "no ofrecer en embarazo ni a menores",
        [{"population": "pregnant"}, {"population": "child"}],
    ),
    (
        "ibuprofeno",
        ItemWarningType.INTERACTION,
        WarningSeverity.ADVISORY,
        "puede subir la tensión si toma losartán o enalapril; recomiende consultar",
        [
            {"interacts_with_ingredient": "losartan"},
            {"interacts_with_ingredient": "enalapril"},
        ],
    ),
    (
        "naproxeno",
        ItemWarningType.INTERACTION,
        WarningSeverity.ADVISORY,
        "con warfarina aumenta el riesgo de sangrado",
        [{"interacts_with_ingredient": "warfarina"}],
    ),
    (
        "acido acetilsalicilico",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.BLOCKING,
        "no ofrecer a menores de 16 años",
        [{"population": "child"}, {"population": "infant"}],
    ),
    (
        "dipirona",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.BLOCKING,
        "no ofrecer a menores",
        [{"population": "child"}, {"population": "infant"}],
    ),
    (
        "acetaminofen",
        ItemWarningType.INTERACTION,
        WarningSeverity.ADVISORY,
        "con warfarina, no pasar de dos días seguidos sin consultar",
        [{"interacts_with_ingredient": "warfarina"}],
    ),
    (
        "loratadina",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.BLOCKING,
        "no ofrecer a menores de dos años",
        [{"population": "infant"}],
    ),
    (
        "desloratadina",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.BLOCKING,
        "no ofrecer a menores de dos años",
        [{"population": "infant"}],
    ),
    (
        "cetirizina",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.ADVISORY,
        "en el adulto mayor puede dar somnolencia",
        [{"population": "elderly"}],
    ),
    (
        "omeprazol",
        ItemWarningType.INTERACTION,
        WarningSeverity.ADVISORY,
        "con clopidogrel puede restarle efecto; recomiende consultar",
        [{"interacts_with_ingredient": "clopidogrel"}],
    ),
    (
        "bisacodilo",
        ItemWarningType.DO_NOT_SUGGEST_IF,
        WarningSeverity.BLOCKING,
        "no ofrecer si hay dolor abdominal de más de tres días",
        [{"duration_days": {"operator": ">=", "value": 3}}],
    ),
    (
        "sales de rehidratacion oral",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.ADVISORY,
        "en el diabético, revisar el contenido de azúcar",
        [{"population": "diabetic"}],
    ),
    (
        "pseudoefedrina",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.BLOCKING,
        "no ofrecer a hipertensos",
        [{"population": "hypertensive"}],
    ),
    (
        "ambroxol",
        ItemWarningType.CONTRAINDICATION,
        WarningSeverity.ADVISORY,
        "en menores de dos años, remitir a consulta",
        [{"population": "infant"}],
    ),
    (
        "dextrometorfano",
        ItemWarningType.DO_NOT_SUGGEST_IF,
        WarningSeverity.BLOCKING,
        "no ofrecer si la tos trae flema",
        [{"symptom": "cough", "operator": "==", "value": 1, "unit": "days"}],
    ),
    (
        "metformina",
        ItemWarningType.INTERACTION,
        WarningSeverity.ADVISORY,
        "con alcohol o deshidratación, remitir a consulta",
        [{"symptom": "dehydration"}],
    ),
    (
        "hidroclorotiazida",
        ItemWarningType.DO_NOT_SUGGEST_IF,
        WarningSeverity.BLOCKING,
        "no ofrecer si hay vómito o diarrea",
        [{"symptom": "vomiting"}, {"symptom": "diarrhea"}],
    ),
    (
        "ciprofloxacina",
        ItemWarningType.DO_NOT_SUGGEST_IF,
        WarningSeverity.BLOCKING,
        "no ofrecer si hay ardor al orinar sin fórmula médica",
        [{"symptom": "burning_urination"}],
    ),
)

#: The most cards one seeded query shows, which is `suggestion_card_count`.
MAX_CARDS = 3

#: How many of the transcripts a profile draws on. **`scale` uses three**, and
#: the reason is arithmetic: one real pipeline run per sede per transcript is
#: what makes a seeded card the pipeline's own, and twenty sedes times eight
#: transcripts is a hundred and sixty runs over a four-thousand-item catalog for
#: a profile that exists to measure a per-till disk ceiling and a registry
#: predicate. The *shape* is the same across twenty sedes, which is what that
#: profile is for; the variety of the questions is not.
SCALE_TRANSCRIPTS = 3


def _transcripts(profile) -> tuple[str, ...]:
    return TRANSCRIPTS[:SCALE_TRANSCRIPTS] if profile == "scale" else TRANSCRIPTS


#: How many items each ingredient rule is written onto. Two is enough for the
#: editor to show repetition without turning forty rows into four hundred.
WARNINGS_PER_INGREDIENT = 2

#: The three ordered category pairs the handoff's *Combinaciones más aceptadas*
#: draws, translated into **this catalog's own taxonomy** -- which is what the
#: definition in *Hands off* counts at, `items.category_id` rendered as that
#: row's `categories.name` and never rolled up to a parent.
#:
#:   suero oral + antidiarreico      -> Bebidas y sueros + Digestivo
#:   analgésico + protector gástrico -> Analgésicos + Digestivo
#:   antigripal + vitamina C         -> Respiratorio + Metabólico
#:
#: The drug classes the handoff labels are not categories in S1's tree; these
#: are the categories the same products sit in, and the pair is what the tile
#: counts.
TARGET_PAIRS: tuple[tuple[str, str], ...] = (
    ("Bebidas y sueros", "Digestivo"),
    ("Analgésicos", "Digestivo"),
    ("Respiratorio", "Metabólico"),
)


def _shape(profile) -> dict:
    """What each profile builds. Every profile builds the map and the safety
    layer; the counts are what differ."""
    if profile == "cold":
        # The map and the warnings, and no sales to mine -- so the miner runs
        # and writes nothing. **This is the parametric floor of *Cold start*,
        # reached by asking for it.**
        return {"queries": 0, "offered": 0, "accepted": 0, "mine": True}
    if profile == "minimal":
        return {"queries": 0, "offered": 0, "accepted": 0, "mine": False}
    if profile == "young":
        return {
            "queries": YOUNG_QUERIES,
            "offered": YOUNG_OFFERED,
            "accepted": YOUNG_ACCEPTED,
            "mine": True,
        }
    return {"queries": QUERIES, "offered": OFFERED, "accepted": ACCEPTED, "mine": True}


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def build(context):
    """Write the map and the warnings, mine the rules, then offer and accept."""
    shape = _shape(context.profile)
    tenant = Tenant.objects.get(id=context.tenant_id)
    _write_settings(context, tenant)
    written = _write_warnings(context)
    context.wrote("item_warnings", written)
    context.note(f"  asistente       {written} advertencias de producto")

    if shape["mine"]:
        report = mining.refresh(tenant)
        context.wrote(
            "cross_sell_rules",
            CrossSellRule.objects.filter(tenant_id=context.tenant_id).count(),
        )
        bands = _band_counts(context.tenant_id)
        context.note(
            f"  asistente       {report.written} reglas mineadas sobre "
            f"{report.tickets} tickets · bandas {bands}"
        )

    if shape["queries"]:
        offered, accepted, credited = _write_queries(context, tenant, shape)
        context.wrote("assistant_queries", shape["queries"])
        context.wrote("assistant_suggestions", offered)
        context.note(
            f"  asistente       {accepted} de {offered} sugerencias aceptadas · "
            f"{credited} líneas marcadas"
        )


def _write_settings(context, tenant):
    """The group, with the map against this tenant's own categories.

    The mining floors are the seed's and not the product's, and the console says
    so on every run: a reader who saw rules appear at a floor of 25 would draw
    exactly the wrong conclusion about how much trading this took.
    """
    categories = {
        row.name: str(row.id)
        for row in Category.objects.filter(tenant_id=context.tenant_id)
    }
    mapping = {
        key: [categories[name] for name in names if name in categories]
        for key, names in SYMPTOM_CATEGORIES.items()
    }
    assistant_settings.write(
        tenant,
        {
            "enabled": True,
            # §11.3 is unanswered, so the seed leaves the model off and every
            # seeded row that reads `mode = 'model'` is the fixture's own
            # writing rather than a call anybody made.
            "model_enabled": False,
            # **On**, so `assistant.transcript_purge` has something to purge and
            # `Registro del asistente` renders rows rather than a column of `—`.
            "retain_transcripts": True,
            "symptom_category_map": {key: ids for key, ids in mapping.items() if ids},
            "cross_sell_min_support": SEED_MIN_SUPPORT,
            "cross_sell_min_confidence": SEED_MIN_CONFIDENCE,
            "cross_sell_window_days": SEED_WINDOW_DAYS,
        },
    )
    context.note(
        f"  asistente       piso de soporte {SEED_MIN_SUPPORT} sobre "
        f"{SEED_WINDOW_DAYS} días (el producto trae 25 sobre 90)"
    )


def _write_warnings(context) -> int:
    """The safety layer, written onto the items whose ingredient it names."""
    rows = []
    for ingredient, kind, severity, text, triggers in WARNINGS:
        for index, item in enumerate(_items_for(context.tenant_id, ingredient)):
            rows.append(
                _warning(
                    context,
                    key=f"{ingredient}:{kind}:{index}",
                    item=item,
                    kind=kind,
                    severity=severity,
                    text=text,
                    triggers=triggers,
                )
            )
    # **The handoff's own row, twice over, and the second one is why.** S3's
    # fixture stocks no loperamida box anywhere in the network, so a rule
    # written only onto loperamida is a rule the counter can never render: the
    # item is filtered for want of stock before any warning is read. The same
    # sentence and the same trigger therefore also go onto the antispasmodic
    # this catalog actually shelves, which is the reference a **Con condición**
    # card draws at Chapinero. The loperamida rows still exist, still validate,
    # and are what Ajustes shows -- they are simply not what a sede with none of
    # it can offer.
    for index, item in enumerate(
        _items_for(context.tenant_id, "loperamida", stocked_first=False)
    ):
        rows.append(
            _warning(
                context,
                key=f"loperamida:handoff:{index}",
                item=item,
                kind=ItemWarningType.DO_NOT_SUGGEST_IF,
                severity=WarningSeverity.BLOCKING,
                text=LOPERAMIDA_TEXT,
                triggers=LOPERAMIDA_TRIGGERS,
            )
        )
    # **Exactly one stocked reference carries it**, because criterion 5 reads
    # *both figures fall by exactly one* when a temperature is added: a rule on
    # two stocked boxes would take two candidates out at once and the check
    # would fail on a fixture rather than on the filter.
    shelved = _items_for(context.tenant_id, "butilbromuro de hioscina")[:1]
    for index, item in enumerate(shelved):
        rows.append(
            _warning(
                context,
                key=f"shelved:handoff:{index}",
                item=item,
                kind=ItemWarningType.DO_NOT_SUGGEST_IF,
                severity=WarningSeverity.BLOCKING,
                text=LOPERAMIDA_TEXT,
                triggers=LOPERAMIDA_TRIGGERS,
            )
        )
    ItemWarning.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def _warning(context, *, key, item, kind, severity, text, triggers):
    return ItemWarning(
        id=context.uid("item_warnings", key),
        tenant_id=context.tenant_id,
        item=item,
        type=kind,
        text=text,
        severity=severity,
        # **Loaded with the catalog**, which is what the ledger says a warning's
        # origin is. Nothing in the seed is a regente's own typing.
        source=ItemWarningSource.CATALOG,
        triggers=triggers,
        active=True,
    )


def _items_for(tenant_id, ingredient, *, stocked_first=True):
    """The items one ingredient rule is written onto.

    **Stocked references first, by how much of them the network holds.** A
    safety layer written onto boxes no sede carries is a safety layer nothing
    ever evaluates: S3's fixture stocks a few hundred of the four thousand
    references, so a rule placed by name order lands on a box the assistant can
    never offer and the filter is exercised by nothing. Ties and the unstocked
    remainder fall back to name order, which keeps two runs identical.
    """
    folded = fold(ingredient)
    rows = list(
        Item.objects.filter(tenant_id=tenant_id, active=True)
        .filter(search_name__contains=folded)
        .order_by("name")
    )
    if not rows:
        rows = list(
            Item.objects.filter(tenant_id=tenant_id, active=True)
            .filter(active_ingredient__icontains=ingredient)
            .order_by("name")
        )
    if not rows or not stocked_first:
        return rows[:WARNINGS_PER_INGREDIENT]
    held: dict[str, int] = defaultdict(int)
    for row in (
        StockOnHand.objects.filter(
            tenant_id=tenant_id, item_id__in=[one.id for one in rows], quantity__gt=0
        )
        # `location__name, item__name` joins both tables in for a sort nothing
        # reads; the rows are folded into a dictionary and sorted below.
        .order_by()
        .values("item_id", "quantity")
    ):
        held[str(row["item_id"])] += int(row["quantity"])
    rows.sort(key=lambda one: (-held.get(str(one.id), 0), one.name))
    return rows[:WARNINGS_PER_INGREDIENT]


def _band_counts(tenant_id) -> str:
    counts: dict[str, int] = defaultdict(int)
    for band in CrossSellRule.objects.filter(tenant_id=tenant_id).values_list(
        "confidence_band", flat=True
    ):
        counts[band] += 1
    return (
        " · ".join(
            f"{CrossSellConfidence(band).label.lower()} {counts[band]}"
            for band in CrossSellConfidence.values
            if counts[band]
        )
        or "ninguna"
    )


# ---------------------------------------------------------------------------
# The offers
# ---------------------------------------------------------------------------


def _write_queries(context, tenant, shape):
    """One query per chosen ticket, its cards, and the lines they became.

    **Every accepted card is a line that is actually on that ticket**, which is
    what makes `sale_lines.from_suggestion` true of exactly the lines the
    suggestions became and what makes the price on the card and the price on the
    line the same figure rather than a close one.
    """
    values = assistant_settings.read(tenant)
    tickets = _tickets(context.tenant_id)
    if not tickets:
        return 0, 0, 0
    catalogue = _catalogue(context.tenant_id)
    stock = _stock(context.tenant_id)
    anchors = _rule_anchors(context.tenant_id)
    warnings = _warnings_by_item(context.tenant_id)
    scripts = _transcripts(context.profile)
    plans = _plans(context.tenant_id, values, catalogue, scripts)

    chosen = _choose(tickets, shape, catalogue, len(scripts))
    queries: list[AssistantQuery] = []
    suggestions: list[AssistantSuggestion] = []
    credited: list = []
    for order, plan in enumerate(chosen):
        _compose(
            context,
            order=order,
            plan=plan,
            catalogue=catalogue,
            stock=stock,
            anchors=anchors,
            warnings=warnings,
            plans=plans,
            queries=queries,
            suggestions=suggestions,
            credited=credited,
        )
    # `ignore_conflicts` because **a rerun over the seed's own rows is a
    # no-op**: every id is derived from a natural key, so the second run offers
    # the same rows and the database keeps the ones it has. That is what makes
    # `make seed` idempotent and what the determinism gate reads.
    AssistantQuery.objects.bulk_create(queries, batch_size=1000, ignore_conflicts=True)
    AssistantSuggestion.objects.bulk_create(
        suggestions, batch_size=1000, ignore_conflicts=True
    )
    for start in range(0, len(credited), 1000):
        SaleLine.objects.filter(id__in=credited[start : start + 1000]).update(
            from_suggestion=True
        )
    accepted = sum(1 for one in suggestions if bool(one.accepted))
    return len(suggestions), accepted, len(credited)


def _tickets(tenant_id):
    """Every closed counter ticket, with its lines in position order.

    Read in two queries: a seed that walked the ORM per ticket would spend
    minutes where this spends a second, and this runs in every test that needs a
    seeded tenant.
    """
    sales: dict[str, dict] = {
        str(row["id"]): {
            "id": str(row["id"]),
            "location_id": str(row["location_id"]),
            "total": row["total"],
            "recorded_at": row["recorded_at"],
            "occurred_at": row["occurred_at"],
            "user_id": row["sold_by_user_id"],
            "lines": [],
        }
        for row in Sale.objects.filter(
            tenant_id=tenant_id, status=SaleStatus.CLOSED, source="counter"
        ).values(
            "id",
            "location_id",
            "total",
            "recorded_at",
            "occurred_at",
            "sold_by_user_id",
        )
    }
    for row in (
        SaleLine.objects.filter(tenant_id=tenant_id, sale_id__in=list(sales))
        .order_by("sale_id", "position")
        .values("id", "sale_id", "position", "item_id", "unit_price")
    ):
        held = sales.get(str(row["sale_id"]))
        if held is not None:
            held["lines"].append(
                {
                    "id": row["id"],
                    "position": row["position"],
                    "item_id": str(row["item_id"]),
                    "unit_price": row["unit_price"],
                }
            )
    return [one for one in sales.values() if one["lines"]]


def _catalogue(tenant_id):
    return {
        str(row["id"]): {
            "name": row["name"],
            "category_id": str(row["category_id"]) if row["category_id"] else "",
            "category": row["category__name"] or "",
            "requires_prescription": row["requires_prescription"],
            "controlled": row["controlled"],
            "invima_status": row["invima_status"],
        }
        for row in Item.objects.filter(tenant_id=tenant_id).values(
            "id",
            "name",
            "category_id",
            "category__name",
            "requires_prescription",
            "controlled",
            "invima_status",
        )
    }


def _stock(tenant_id):
    held: dict[tuple[str, str], int] = defaultdict(int)
    for row in (
        StockOnHand.objects.filter(tenant_id=tenant_id)
        .order_by()  # As above: two joins and a sort, for a dictionary.
        .values("location_id", "item_id", "quantity")
    ):
        held[(str(row["location_id"]), str(row["item_id"]))] += int(row["quantity"])
    return held


def _rule_anchors(tenant_id):
    """`(location_id | '', item_a) -> {item_b: (band, item_a_name)}`."""
    found: dict[tuple[str, str], dict] = defaultdict(dict)
    for row in CrossSellRule.objects.filter(tenant_id=tenant_id).values(
        "location_id", "item_a_id", "item_a__name", "item_b_id", "confidence_band"
    ):
        key = (str(row["location_id"] or ""), str(row["item_a_id"]))
        found[key][str(row["item_b_id"])] = (
            row["confidence_band"],
            row["item_a__name"],
        )
    return found


def _warnings_by_item(tenant_id):
    held: dict[str, list] = defaultdict(list)
    for row in ItemWarning.objects.filter(tenant_id=tenant_id, active=True):
        held[str(row.item_id)].append(row)
    return held


def _plans(tenant_id, values, catalogue, transcripts):
    """One real pipeline run per sede per transcript, cached.

    **This is what makes a seeded card the pipeline's own.** Running it four
    thousand times would take minutes; running it forty-eight times and drawing
    from the answers gives every seeded card a real type, a real reason code and
    a real availability figure, on this sede's own shelf.
    """
    del catalogue
    # The card sets, keyed by `(sede, transcript)`, plus the cashiers'
    # names under `"names"` -- stamped on every query row, because §2
    # hard-deletes a `users` row and the log has to keep saying who asked.
    cache: dict = {}
    sedes = list(
        Location.objects.filter(
            tenant_id=tenant_id, status=LocationStatus.ACTIVE
        ).values_list("id", flat=True)
    )
    from core.models import User

    cache["names"] = {
        str(one.id): one.name for one in User.objects.filter(tenant_id=tenant_id)
    }
    for location_id in sedes:
        for index, transcript in enumerate(transcripts):
            outcome = pipeline.run(
                tenant_id=tenant_id,
                location_id=location_id,
                symptoms=extract.extract(transcript),
                settings=values,
            )
            cache[(str(location_id), index)] = outcome.cards
    return cache


def _choose(tickets, shape, catalogue, scripts):
    """Which tickets carry a query, how many cards each showed, and which of
    them were taken.

    **The suggested tickets are the larger ones**, which is what separates the
    two means the Panel compares -- and it is true of a counter as well as of
    this fixture: a suggestion taken is a line added.

    The totals are hit **exactly** rather than approximately, because check 9
    reads `3.412 de 5.824 · 58,6%` three ways and all three have to be the same
    figure. The per-sede rates are spread around that mean, because a per-sede
    table where every row reads 58,6% is a Panel that reads as a mock of itself.
    """
    by_sede: dict[str, list] = defaultdict(list)
    for ticket in tickets:
        by_sede[ticket["location_id"]].append(ticket)
    for rows in by_sede.values():
        # **Creditable lines first, then revenue.** A single-line ticket can
        # carry a card but never an acceptance -- its one line is the anchor --
        # so a selection made on revenue alone spends most of its quota on
        # tickets that cannot answer the question the tile asks. Multi-line
        # tickets are also the larger ones, so this orders the suggested side of
        # the ticket comparison exactly as revenue would have.
        rows.sort(
            key=lambda one: (-_capacity(one, catalogue), -one["total"], one["id"])
        )

    order = sorted(by_sede, key=lambda key: (-len(by_sede[key]), key))
    quotas = _quotas(order, by_sede, shape)

    chosen: list[dict] = []
    for position, location_id in enumerate(order):
        chosen.extend(
            _plan_sede(
                by_sede[location_id],
                quotas[location_id],
                position,
                catalogue,
                scripts,
            )
        )
    return chosen


def _quotas(order, by_sede, shape):
    """Per-sede queries, offers and acceptances, summing to the exact targets.

    The last sede takes the remainder, so rounding never costs the network a
    ticket -- and it is the smallest sede, where a handful either way is not a
    figure anybody reads.
    """
    total = sum(len(by_sede[key]) for key in order)
    quotas: dict[str, dict] = {}
    spent = {"queries": 0, "offered": 0, "accepted": 0}
    for index, location_id in enumerate(order):
        if index == len(order) - 1:
            quotas[location_id] = {key: shape[key] - spent[key] for key in spent}
            continue
        share = len(by_sede[location_id]) / total
        offered = int(shape["offered"] * share)
        quota = {
            "queries": int(shape["queries"] * share),
            "offered": offered,
            "accepted": int(offered * SEDE_RATES[min(index, len(SEDE_RATES) - 1)]),
        }
        quotas[location_id] = quota
        for key in spent:
            spent[key] += quota[key]
    return quotas


def _plan_sede(rows, quota, position, catalogue, scripts):
    """One sede's tickets, taken from the top of its own revenue order.

    Acceptances go on in two passes -- one per ticket, then a second on the
    tickets that have a line spare -- so the biggest tickets carry two and the
    rest carry one, which is the shape a counter produces and the shape the
    `Ticket con sugerencia` mean rests on.
    """
    wanted = max(0, min(quota["queries"], len(rows)))
    picked = rows[:wanted]
    if not picked:
        return []
    plans = [
        {
            "ticket": ticket,
            "transcript": (stable_int("script", ticket["id"]) + position) % scripts,
            "accepted": 0,
            "offers": 0,
            "capacity": _capacity(ticket, catalogue),
        }
        for ticket in picked
    ]
    left = max(0, quota["accepted"])
    for wanted_each in (1, 2):
        for plan in plans:
            if left <= 0:
                break
            if plan["capacity"] >= wanted_each and plan["accepted"] == wanted_each - 1:
                plan["accepted"] = wanted_each
                left -= 1
    for plan in plans:
        plan["offers"] = plan["accepted"]

    # The un-accepted cards: one per query per round, so every query that showed
    # anything shows at least one card and the extras land evenly rather than on
    # the first two hundred tickets.
    extra = max(0, quota["offered"] - sum(one["accepted"] for one in plans))
    while extra > 0:
        placed = 0
        for plan in plans:
            if extra <= 0:
                break
            if plan["offers"] < MAX_CARDS:
                plan["offers"] += 1
                extra -= 1
                placed += 1
        if placed == 0:
            break
    return plans


def _capacity(ticket, catalogue) -> int:
    """How many of a ticket's lines a suggestion could have become.

    The line at the lowest position is **the reference the customer came in
    for** and is never credited -- it is the anchor the combination list counts
    from -- and §7's three exclusions apply to a seeded card exactly as they
    apply to a real one.
    """
    return sum(
        1
        for line in ticket["lines"][1:]
        if not _never_suggested(catalogue.get(line["item_id"], {}))
    )


def _compose(
    context,
    *,
    order,
    plan,
    catalogue,
    stock,
    anchors,
    warnings,
    plans,
    queries,
    suggestions,
    credited,
):
    """One query row and the cards it showed."""
    ticket = plan["ticket"]
    location_id = ticket["location_id"]
    names = plans["names"]
    index = plan["transcript"]
    transcript = TRANSCRIPTS[index]
    symptoms = extract.extract(transcript)
    key = f"{ticket['id']}:{index}"
    query_id = context.uid("assistant_queries", key)
    seed = stable_int("assistant", ticket["id"])
    local = seed % LOCAL_SHARE == 0
    rejected = (not local) and order % REJECTED_EVERY == 0
    cards = plans.get((location_id, index), [])

    lines = ticket["lines"]
    anchor = lines[0]
    taken = _credit_lines(
        lines[1:],
        plan["accepted"],
        catalogue,
        catalogue.get(anchor["item_id"], {}).get("category", ""),
        warnings=warnings,
        symptoms=symptoms,
    )
    drawn: list[dict] = []
    for line in taken:
        card = _card_for(
            item_id=line["item_id"],
            price=line["unit_price"],
            location_id=location_id,
            anchor_item=anchor["item_id"],
            anchors=anchors,
            warnings=warnings,
            stock=stock,
            catalogue=catalogue,
            symptoms=symptoms,
            sale_line_id=line["id"],
        )
        if card is not None:
            drawn.append(card)
    on_ticket = {one["item_id"] for one in lines}
    for card in cards:
        if len(drawn) >= plan["offers"]:
            break
        if card["item_id"] in on_ticket:
            continue
        drawn.append({**card, "sale_line_id": None})

    primary, secondary = pipeline.local_prose(drawn, len(cards) or len(drawn))
    queries.append(
        AssistantQuery(
            id=query_id,
            client_uuid=query_id,
            tenant_id=context.tenant_id,
            location_id=location_id,
            sale_id=ticket["id"],
            user_id=ticket["user_id"],
            user_name=names.get(str(ticket["user_id"]), ""),
            occurred_at=ticket["occurred_at"],
            recorded_at=ticket["recorded_at"],
            transcript=transcript,
            symptoms=symptoms,
            recommendation=primary,
            recommendation_secondary=secondary,
            mode=AssistantMode.LOCAL if local else AssistantMode.MODEL,
            model="" if local else "anthropic/claude-sonnet-4.5",
            cost_usd=Decimal("0") if local else Decimal(COST_STEPS[seed % 4]),
            latency_ms=None if local else LATENCY_STEPS[seed % 5],
            excluded=[],
            output_check_passed=not rejected,
            output_check_flags=["condition_named"] if rejected else [],
            candidate_count=len(cards) or len(drawn),
            bundle_version="1.seed",
            ruleset_computed_at=None,
        )
    )
    for rank, card in enumerate(drawn, start=1):
        row_key = f"{key}:{rank}"
        row_id = context.uid("assistant_suggestions", row_key)
        accepted = card["sale_line_id"] is not None
        suggestions.append(
            AssistantSuggestion(
                id=row_id,
                client_uuid=row_id,
                tenant_id=context.tenant_id,
                query_id=query_id,
                location_id=location_id,
                item_id=card["item_id"],
                type=card["type"],
                reason=card["reason"],
                reason_code=card["reason_code"],
                price=card["price"],
                rank=rank,
                available_quantity=card["available_quantity"],
                warning_id=card.get("warning_id"),
                rule_confidence=card.get("rule_confidence"),
                accepted=accepted,
                accepted_at=ticket["recorded_at"] if accepted else None,
                sale_line_id=card["sale_line_id"],
                occurred_at=ticket["occurred_at"],
                recorded_at=ticket["recorded_at"],
            )
        )
        if accepted:
            credited.append(card["sale_line_id"])


def _credit_lines(
    candidates, wanted, catalogue, anchor_category, *, warnings, symptoms
):
    """Which of a ticket's lines the suggestions became.

    **A line the safety filter would have removed is never credited.** The
    pipeline drops a candidate carrying a satisfied `blocking` warning *before*
    anything is typed or ranked, so a fixture that credited one would seed a
    card the product itself would not have drawn -- and, on a
    `do_not_suggest_if`, would seed exactly the state check 1 calls **Wrong**: a
    card present carrying the warning as its text.

    **A line that completes one of the three target combinations is credited
    first**, in the order the handoff lists them, so those rows appear in
    *Combinaciones más aceptadas* wherever S4's tickets contain them at all.
    Where they do not -- see the module docstring -- the credit goes to the
    ticket's own line order, which is deterministic and is what makes two runs
    over one period return the same list.
    """
    if wanted <= 0 or not candidates:
        return []
    eligible = [
        line
        for line in candidates
        if not _never_suggested(catalogue.get(line["item_id"], {}))
        and not _blocked(line["item_id"], warnings, symptoms)
    ]
    if not eligible:
        return []

    def preference(line):
        category = catalogue.get(line["item_id"], {}).get("category", "")
        for index, (anchor, suggestion) in enumerate(TARGET_PAIRS):
            if anchor == anchor_category and suggestion == category:
                return index
        return len(TARGET_PAIRS)

    ordered = sorted(eligible, key=lambda line: (preference(line), line["position"]))
    return ordered[:wanted]


def _blocked(item_id, warnings, symptoms) -> bool:
    """The filter's fifth step, applied to the seed exactly as to a counter.

    Any `blocking` warning whose trigger the extraction **satisfies** removes
    the candidate. It runs before the type is derived, which is the order
    `pipeline.filter_candidates` runs it in and the order §7 is emphatic about.
    """
    from core.assistant import filters

    extraction = filters.Extraction(symptoms)
    return any(
        row.severity == WarningSeverity.BLOCKING
        and filters.evaluate(row.triggers, extraction) == filters.SATISFIED
        for row in warnings.get(item_id, [])
    )


def _never_suggested(item) -> bool:
    """§7 · what the assistant never proposes, applied to the seed as well.

    Check 2 counts these across **every** seeded query, so a fixture that
    credited a prescription line would make the check fail on data rather than
    on code -- which is the same defect one door along.
    """
    return bool(
        item.get("requires_prescription")
        or item.get("controlled")
        or item.get("invima_status") == "expired"
    )


def _card_for(
    *,
    item_id,
    price,
    location_id,
    anchor_item,
    anchors,
    warnings,
    stock,
    catalogue,
    symptoms,
    sale_line_id,
):
    """One card, typed and reasoned the way the pipeline would type it."""
    from core.assistant import filters

    extraction = filters.Extraction(symptoms)
    band = None
    anchor_name = ""
    rule = anchors.get((location_id, anchor_item), {}).get(item_id) or anchors.get(
        ("", anchor_item), {}
    ).get(item_id)
    network = (location_id, anchor_item) not in anchors or item_id not in anchors.get(
        (location_id, anchor_item), {}
    )
    if rule is not None:
        band, anchor_name = rule

    # **The same rule the pipeline applies**, called rather than restated: a
    # fixture that decided for itself which warning makes a card conditional
    # would seed rows the product itself would not have drawn, and the screen
    # would stop being evidence about the code.
    warning = None
    for row in warnings.get(item_id, []):
        outcome = filters.evaluate(row.triggers, extraction)
        if outcome == filters.SATISFIED and row.severity == WarningSeverity.BLOCKING:
            # Unreachable from `_credit_lines`, which drops the line before it
            # gets here. It is stated anyway, because a caller added later that
            # skipped that filter would otherwise seed the one card §7 forbids.
            return None
        if filters.makes_conditional(row.type, row.severity, outcome):
            warning = row
            break

    if warning is not None:
        kind = SuggestionType.CONDITIONAL
        code, reason = "warning_conditional", warning.text
    elif rule is not None:
        kind = SuggestionType.BOUGHT_TOGETHER
        if network:
            code = "bought_together_network"
            reason = reasons.line("bought_together_network", anchor=anchor_name)
        else:
            code = "bought_together_location"
            reason = reasons.line("bought_together_location_low", anchor=anchor_name)
    else:
        kind = SuggestionType.FIRST_CHOICE
        key = sorted(extraction.symptoms)[0] if extraction.symptoms else ""
        code = "symptom_primary"
        reason = reasons.line("symptom_primary", symptom_key=key)
    return {
        "item_id": item_id,
        "item_name": catalogue.get(item_id, {}).get("name", ""),
        "type": kind,
        "reason": reason,
        "reason_code": code,
        "price": price,
        "available_quantity": stock.get((location_id, item_id), 0),
        "warning_id": warning.id if warning is not None else None,
        "rule_confidence": band,
        "sale_line_id": sale_line_id,
    }


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def owned_ids(context):
    """Exactly the rows this fixture writes in its guard tables.

    `cross_sell_rules` is **not** among them, and that is the point of mining
    rather than inserting: the miner writes those rows and derives their ids, so
    this fixture cannot enumerate them. It is therefore not a guard table
    either -- see `register` below.
    """
    warnings = set()
    for ingredient, kind, _severity, _text, _triggers in WARNINGS:
        for index in range(WARNINGS_PER_INGREDIENT):
            warnings.add(context.uid("item_warnings", f"{ingredient}:{kind}:{index}"))
    for index in range(WARNINGS_PER_INGREDIENT):
        warnings.add(context.uid("item_warnings", f"loperamida:handoff:{index}"))
        warnings.add(context.uid("item_warnings", f"shelved:handoff:{index}"))
    queries = set(
        AssistantQuery.objects.filter(tenant_id=context.tenant_id).values_list(
            "id", flat=True
        )
    )
    suggestions = set(
        AssistantSuggestion.objects.filter(tenant_id=context.tenant_id).values_list(
            "id", flat=True
        )
    )
    return {
        "item_warnings": warnings,
        "assistant_queries": queries,
        "assistant_suggestions": suggestions,
    }


register(
    "assistant",
    tables=(
        "item_warnings",
        "assistant_queries",
        "assistant_suggestions",
        # **Declared because this fixture writes one column on it** -- the one
        # column S8 writes on S4's table. The row count does not move, so the
        # guard's count check is satisfied either way; declaring it is what says
        # in the registry that this stage touches that table at all.
        "sale_lines",
    ),
    requires=("counter",),
    build=build,
    owned_ids=owned_ids,
)
