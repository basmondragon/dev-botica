"""The model gateway (architecture §10, ledger cross-stage services).

**One path to the vendor, and S8 owns it.** S6 and S8 run in parallel off S4, so
whichever lands first builds this module and the owner is S8 either way -- which
is why the shared kill switch and the per-tenant spend cap live in S8's
`assistant` settings group rather than in `purchasing`, where
`reason_text_enabled` gates S6's call and nothing else. **A second client would
be a second spend cap, which is no cap at all**, so no stage opens its own path
to OpenRouter.

What this module guarantees:

  * one HTTP path, with a caller-supplied timeout, to one configured base URL;
  * `Unavailable` on every failure -- a bad status, a timeout, a malformed
    body, an unconfigured deployment -- so a caller degrades rather than
    breaking. Quantities are arithmetic and never wait on a model (§10);
  * **the kill switch and the cap are read before the call**, from S8's group.
    `model_enabled` is the switch and it is off until somebody answers §11.3;
    the cap is `monthly_spend_cap_usd` against the calendar month's own
    `assistant_queries.cost_usd` sum, which is a read and not a stored counter
    somebody has to remember to reset;
  * the cost the vendor reported, returned to the caller, so the stage that owns
    the cost log writes one without this module growing a table.

**What is not counted, said plainly.** The sum the cap is enforced against is
over `assistant_queries` alone, so S6's one purchase-order call per order is
*subject* to the switch and the cap without being *in* the sum. The alternative
was a fifth table or a fifteenth settings key for an accounting figure, and the
ledger assigns S8 four tables and the settings register fourteen keys. S6's
volume is one call per order per week against the assistant's one per counter
query, so the sum it is missing from is one it could not move. **If that stops
being true** -- a later stage with real gateway volume -- the fix is a model-call
log table and a ledger amendment, and it is one query in this module.
"""

import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

#: The business clock the calendar month is read against (§1: one country, one
#: timezone). A cap that reset at midnight UTC would reset at seven in the
#: evening in Bogotá.
BUSINESS_TIMEZONE = ZoneInfo("America/Bogota")

logger = logging.getLogger(__name__)

#: S8's group, read here and **never written here** (rule 5: one owner per key
#: group).
ASSISTANT_GROUP = "assistant"

#: How long a reason-text call may take before the order renders its
#: deterministic strings instead. A buyer opening Compras at 06:00 is not
#: waiting on a language model. **The assistant passes its own**, because a
#: cashier holding a customer is not waiting twenty seconds for prose the card
#: already has a local version of.
TIMEOUT_SECONDS = 20

#: What a call costs, where the vendor did not price it itself. OpenRouter
#: returns `usage.cost` on most routes and this is the fallback, per million
#: tokens -- an estimate stamped on the row rather than a blank, because a cap
#: enforced against a column that is null on half its rows is not a cap.
INPUT_USD_PER_MTOK = 3.0
OUTPUT_USD_PER_MTOK = 15.0


class Unavailable(Exception):
    """The gateway could not answer. Every caller degrades; none of them fails."""


def is_configured() -> bool:
    return bool(
        getattr(settings, "BOTICA_GATEWAY_API_KEY", "")
        and getattr(settings, "BOTICA_GATEWAY_BASE_URL", "")
    )


def enabled_for(tenant) -> bool:
    """Whether a call may be made for this tenant.

    Two gates, and both are S8's: `model_enabled`, the kill switch, and
    `monthly_spend_cap_usd` against the month's own spend. **`enabled` is not
    consulted here** -- that switch removes the assistant column from Mostrador
    and says nothing about whether a vendor may be called, which is why S8's
    group carries two booleans rather than one.
    """
    if not is_configured():
        return False
    from core.assistant import settings as assistant_settings

    group = assistant_settings.read(tenant)
    if not group.get("model_enabled"):
        return False
    # **A cap of zero is a cap of zero.** It is the second way a tenant turns
    # the vendor off, and reading it as *no ceiling* would invert the one
    # setting whose whole job is to stop spending -- the failure being that a
    # tenant which set the cap to nothing gets an uncapped month.
    cap = assistant_settings.spend_cap(group)
    return spend_this_month(getattr(tenant, "id", tenant)) < cap


def spend_this_month(tenant_id) -> Decimal:
    """What this tenant has spent with the vendor so far this calendar month.

    One indexed aggregate on `(tenant_id, recorded_at)` over at most a few tens
    of thousands of rows, run before the call rather than by a job: **a cap a
    job enforces is a cap that is a day late.** It is off the sale's critical
    path by construction, because nothing about a sale waits on the endpoint
    that runs it (§4).
    """
    from core.models import AssistantQuery

    opened = (
        timezone.now()
        .astimezone(BUSINESS_TIMEZONE)
        .replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    )
    total = AssistantQuery.objects.filter(
        tenant_id=tenant_id, recorded_at__gte=opened
    ).aggregate(spent=Sum("cost_usd"))["spent"]
    return Decimal(total or 0)


def price(usage) -> Decimal:
    """What one call cost, in dollars, to six places.

    The vendor's own figure where it gave one; the configured rates otherwise.
    Never `None`: a cap enforced against a column that is null on half its rows
    is not a cap.
    """
    reported = (usage or {}).get("cost")
    if reported is not None:
        try:
            return Decimal(str(reported)).quantize(Decimal("0.000001"))
        except (ArithmeticError, ValueError):
            pass
    prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
    completion_tokens = int((usage or {}).get("completion_tokens") or 0)
    estimate = (
        Decimal(prompt_tokens) * Decimal(str(INPUT_USD_PER_MTOK))
        + Decimal(completion_tokens) * Decimal(str(OUTPUT_USD_PER_MTOK))
    ) / Decimal(1_000_000)
    return estimate.quantize(Decimal("0.000001"))


def complete(
    *, tenant, prompt, system="", max_tokens=1200, model=None, timeout=None
) -> dict:
    """One completion. Returns `{"text": ..., "usage": {...}, "model": ...}`.

    Raises `Unavailable` for every failure there is. A caller that wanted to
    tell a timeout from a refusal would be a caller deciding whether to show a
    person a vendor's error, and §B.10.3 already says it must not.
    """
    if not enabled_for(tenant):
        raise Unavailable("the gateway is off for this tenant")

    body = json.dumps(
        {
            "model": model or settings.BOTICA_GATEWAY_MODEL,
            "max_tokens": max_tokens,
            "messages": (
                ([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": prompt}]
            ),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.BOTICA_GATEWAY_BASE_URL.rstrip('/')}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.BOTICA_GATEWAY_API_KEY}",
        },
    )
    try:
        seconds = TIMEOUT_SECONDS if timeout is None else timeout
        with urllib.request.urlopen(request, timeout=seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
        raise Unavailable(str(error)) from error

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise Unavailable("the gateway answered in a shape we do not read") from error

    usage = payload.get("usage") or {}
    logger.info(
        "gateway call: model=%s prompt=%s completion=%s",
        payload.get("model"),
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )
    return {
        "text": text,
        "usage": usage,
        "model": payload.get("model"),
        "cost_usd": price(usage),
    }
