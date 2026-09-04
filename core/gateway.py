"""The model gateway (architecture §10, ledger cross-stage services).

**One path to the vendor, and S8 owns it.** S6 and S8 run in parallel off S4, so
whichever lands first builds this module and the owner is S8 either way -- which
is why the shared kill switch and the per-tenant spend cap live in S8's
`assistant` settings group rather than in `purchasing`, where
`reason_text_enabled` gates S6's call and nothing else. **A second client would
be a second spend cap, which is no cap at all**, so no stage opens its own path
to OpenRouter.

What this module guarantees:

  * one HTTP path, with a timeout, to one configured base URL;
  * `Unavailable` on every failure -- a bad status, a timeout, a malformed
    body, an unconfigured deployment -- so a caller degrades rather than
    breaking. Quantities are arithmetic and never wait on a model (§10);
  * **the kill switch and the cap are read before the call**, from S8's group
    where it has been written, and a tenant that has never opened the assistant
    is not thereby denied a reason line -- S6 does not read S8's group as a
    precondition;
  * the usage the vendor reported, returned to the caller, so the stage that
    owns the cost log can write one without this module growing a table.
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from core import tenant_settings

logger = logging.getLogger(__name__)

#: S8's group, read here and **never written here** (rule 5: one owner per key
#: group). Absent means the assistant was never configured, which is not a
#: refusal -- it is a tenant that has not opened that screen.
ASSISTANT_GROUP = "assistant"

#: How long a reason-text call may take before the order renders its
#: deterministic strings instead. A buyer opening Compras at 06:00 is not
#: waiting on a language model.
TIMEOUT_SECONDS = 20


class Unavailable(Exception):
    """The gateway could not answer. Every caller degrades; none of them fails."""


def is_configured() -> bool:
    return bool(
        getattr(settings, "BOTICA_GATEWAY_API_KEY", "")
        and getattr(settings, "BOTICA_GATEWAY_BASE_URL", "")
    )


def enabled_for(tenant) -> bool:
    """Whether a call may be made for this tenant.

    Two switches, and both are S8's: `enabled`, the kill switch, and
    `monthly_spend_cap_usd` against `spend_this_month_usd`, the cap. **An
    unwritten group is not an off switch** -- S6 and S8 land in parallel and a
    tenant that never enabled the assistant would otherwise get no reason text
    at all.
    """
    if not is_configured():
        return False
    group = tenant_settings.read_group(tenant, ASSISTANT_GROUP)
    if not group:
        return True
    if group.get("enabled") is False:
        return False
    cap = group.get("monthly_spend_cap_usd")
    spent = group.get("spend_this_month_usd")
    if cap is not None and spent is not None and float(spent) >= float(cap):
        return False
    return True


def complete(*, tenant, prompt, system="", max_tokens=1200, model=None) -> dict:
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
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
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
    return {"text": text, "usage": usage, "model": payload.get("model")}
