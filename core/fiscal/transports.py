"""The one seam between a target and the wire.

Two implementations and one interface, so that everything above this line -- the
canonical builder, the mapping renderer, the idempotency field, the reply
parser, the ladder -- is exercised identically whether a document is going to a
client's API or to the loopback a session verifies against. Only the socket
changes.

**`TransportFailure` is an *unknown* outcome and never a failure.** After a
timeout, a dropped connection or a 5xx with no body, the target may have
committed; the next attempt **queries first** and never re-sends blind (§8).
That asymmetry is the whole shape of this stage: a document that never arrived
is found by the work list and delivered on the next attempt, while a document
that arrived twice is signed, numbered and filed twice under the pharmacy's own
resolution.
"""

import hashlib
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urljoin

from core.fiscal import storage

logger = logging.getLogger(__name__)

#: Long enough for a slow invoicing API, short enough that a hung target does
#: not hold a worker slot for a minute. Nothing on the counter's critical path
#: waits on this -- the handoff is asynchronous, always (§8).
TIMEOUT_SECONDS = 20


class TransportFailure(Exception):
    """The outcome is **unknown**: the far end may or may not hold the document.

    Never rendered to a user as it stands; the target turns it into one Spanish
    sentence a person can act on (§B.10.3).
    """


@dataclass
class Reply:
    status: int
    body: dict = field(default_factory=dict)
    text: str = ""


class HttpTransport:
    """One HTTPS request, and nothing clever.

    `urllib` rather than a client library, because this stage adds no dependency
    for one POST and one GET: the part of the problem that is genuinely hard --
    idempotency, the ladder, the query-first rule -- is above this line, and a
    client library would not help with any of it.
    """

    def __init__(self, base_url: str, credential: str = "", *, timeout=None):
        self.base_url = base_url.rstrip("/") + "/"
        self.credential = credential
        self.timeout = timeout or TIMEOUT_SECONDS

    def send(self, path: str, *, method="POST", body=None) -> Reply:
        url = urljoin(self.base_url, path.lstrip("/"))
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        if self.credential:
            request.add_header("Authorization", f"Bearer {self.credential}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return _reply(response.status, response.read())
        except urllib.error.HTTPError as failure:
            payload = failure.read() if failure.fp is not None else b""
            reply = _reply(failure.code, payload)
            if reply.status >= 500:
                # A 5xx is an **unknown**, not a refusal: the far end may have
                # committed and then failed to answer. Telling a pharmacy their
                # invoicing system refused a document it may hold is worse than
                # telling them nothing yet.
                raise TransportFailure(
                    reply.text or f"HTTP {reply.status}"
                ) from failure
            return reply
        except (urllib.error.URLError, TimeoutError, OSError) as failure:
            raise TransportFailure(str(failure)) from failure


def _reply(status: int, payload: bytes) -> Reply:
    text = (payload or b"").decode("utf-8", "replace")[:2000]
    try:
        body = json.loads(text) if text.strip() else {}
    except ValueError:
        body = {}
    return Reply(status=status, body=body if isinstance(body, dict) else {}, text=text)


# ---------------------------------------------------------------------------
# The loopback
# ---------------------------------------------------------------------------

#: What the loopback can be told to do. Each one is a real failure mode of a
#: real invoicing API, and each one is a check in this stage's *Verification*.
ACCEPT = "accept"  # 200, holds it, returns identifiers  -> acknowledged
ACCEPT_LATER = "accept_later"  # 202, taken, confirms later      -> sent
REFUSE = "refuse"  # 422 with a reason               -> failed
HANG = "hang"  # nothing stored, no answer       -> unknown
COMMIT_THEN_DROP = "commit_then_drop"  # stored, then no answer  -> unknown

MODES = (ACCEPT, ACCEPT_LATER, REFUSE, HANG, COMMIT_THEN_DROP)

#: Where the loopback keeps what it holds and what it was sent. Object storage
#: rather than process memory, so the web process, the worker and a test session
#: all inspect the **same** target -- a recorder that lived in one process would
#: make check 6 unrunnable the moment delivery moved to a job.
ROOT = "fiscal-loopback"


def _root(tenant_id) -> str:
    return f"{ROOT}/{tenant_id}"


def configure(tenant_id, *, mode=ACCEPT, refusal="") -> None:
    """Tell the loopback how to behave. Never called by product code."""
    if mode not in MODES:
        raise ValueError(f"{mode!r} is not a loopback mode; the five are {MODES}.")
    storage.put(
        f"{_root(tenant_id)}/mode.json",
        json.dumps({"mode": mode, "refusal": refusal}).encode("utf-8"),
    )


def mode_of(tenant_id) -> tuple[str, str]:
    raw = storage.get(f"{_root(tenant_id)}/mode.json")
    if not raw:
        return ACCEPT, ""
    try:
        held = json.loads(raw)
    except ValueError:
        return ACCEPT, ""
    return held.get("mode", ACCEPT), held.get("refusal", "")


def held_documents(tenant_id) -> list[dict]:
    """What the target holds, read directly rather than through our own rows.

    A check that inferred the far end's contents from `fiscal_documents` would
    be a check of our own bookkeeping, which is precisely the thing under test.
    """
    prefix = f"{_root(tenant_id)}/documents"
    out = []
    for name in storage.names(prefix):
        raw = storage.get(f"{prefix}/{name}")
        if raw:
            out.append(json.loads(raw))
    return out


def request_log(tenant_id) -> list[dict]:
    """Every request the target received, in order, with its operation and key."""
    prefix = f"{_root(tenant_id)}/log"
    entries = []
    for name in storage.names(prefix):
        raw = storage.get(f"{prefix}/{name}")
        if raw:
            entries.append(json.loads(raw))
    return sorted(entries, key=lambda entry: entry["ordinal"])


def reset(tenant_id) -> None:
    for group in ("documents", "log"):
        prefix = f"{_root(tenant_id)}/{group}"
        for name in storage.names(prefix):
            storage.remove(f"{prefix}/{name}")
    storage.remove(f"{_root(tenant_id)}/mode.json")


class LoopbackTransport:
    """An invoicing system that never leaves the instance.

    It records every request it receives, holds what it accepts, and can be told
    to refuse, to hang, or to **commit and then drop the connection** -- which is
    the one failure this whole stage is shaped around, and the only way to
    verify the query-first rule without a client's production API.

    It speaks the same `send(path, method, body) -> Reply` the HTTP transport
    does, so the mapping, the idempotency field and the reply parser above it
    are the ones a real target uses.
    """

    def __init__(self, tenant_id, credential=""):
        self.tenant_id = str(tenant_id)
        self.credential = credential

    def send(self, path: str, *, method="POST", body=None) -> Reply:
        mode, refusal = mode_of(self.tenant_id)
        key = _key_of(path, body)

        if mode == HANG:
            # **The target is stopped**, so it receives nothing and records
            # nothing -- on either operation. That is what makes the "restore the
            # target and the queue drains with no manual step" check a real one:
            # while it is down there is no evidence anywhere that a request was
            # ever made, which is exactly the ambiguity the ladder exists for.
            raise TransportFailure("El sistema de facturación no respondió.")

        self._record(method, path, key, body, mode)

        if method == "GET":
            # The query operation, and it is deliberately **not** affected by
            # `commit_then_drop`: a target that has committed answers a status
            # query even when it dropped the connection on the write, and that
            # is exactly the property the query-first rule depends on.
            held = storage.get(f"{_root(self.tenant_id)}/documents/{key}.json")
            if not held:
                return Reply(status=404)
            return Reply(status=200, body=json.loads(held))

        if mode == REFUSE:
            return Reply(
                status=422,
                body={"error": refusal or "El documento fue rechazado."},
            )

        self._commit(key, body)
        if mode == COMMIT_THEN_DROP:
            raise TransportFailure(
                "La conexión se cerró después de enviar el documento."
            )
        if mode == ACCEPT_LATER:
            return Reply(status=202, body={})
        return Reply(status=200, body=self._identifiers(key))

    def _commit(self, key, body) -> None:
        """Idempotent at the far end, which is what a well-behaved target is.

        A second `deliver` under the same key overwrites the same record rather
        than creating a second document -- so a duplicate at this target is
        visible only in its **request log**, which is where check 6 looks for it.
        Making the store itself refuse the second write would hide the defect
        this target exists to expose.
        """
        name = f"{_root(self.tenant_id)}/documents/{key}.json"
        if storage.get(name):
            return
        storage.put(
            name,
            json.dumps({**self._identifiers(key), "body": body}).encode("utf-8"),
        )

    def _identifiers(self, key) -> dict:
        """What a target that issues its own number returns.

        Derived from the key by a **stable** digest, not by `hash()`, which is
        salted per process: the web process delivers and the worker re-queries,
        and a number that differed between them would make check 6 pass or fail
        on which process ran it.
        """
        digest = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 10_000
        return {
            "external_number": f"FE-{digest:04d}",
            "cude": f"cude-{key}",
            "pdf_url": f"https://loopback.invalid/{key}.pdf",
        }

    def _record(self, method, path, key, body, mode) -> None:
        prefix = f"{_root(self.tenant_id)}/log"
        ordinal = len(storage.names(prefix)) + 1
        storage.put(
            f"{prefix}/{ordinal:06d}.json",
            json.dumps(
                {
                    "ordinal": ordinal,
                    "operation": "query" if method == "GET" else "deliver",
                    "path": path,
                    "document_key": key,
                    "mode": mode,
                    "body": body,
                }
            ).encode("utf-8"),
        )


def _key_of(path: str, body) -> str:
    if isinstance(body, dict) and body.get("idempotency_key"):
        return str(body["idempotency_key"])
    return path.rstrip("/").rsplit("/", 1)[-1]
