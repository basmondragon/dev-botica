"""The audit write path (ledger cross-stage services).

S0 ships the path and writes through it for its own elevated-role mutations;
every later stage appends through the same path on its own. Nothing appends to
`audit_log` by hand, and the database holds no UPDATE or DELETE grant on it for
the runtime role, so append-only is structural rather than editorial.
"""

from django.forms.models import model_to_dict

from core.models import AuditAction, AuditLog

ACTIONS = frozenset(AuditAction.values)


def snapshot(instance, fields):
    """The before/after shape: JSON-safe, only the fields that carry meaning."""
    if instance is None:
        return None
    data = model_to_dict(instance, fields=fields)
    return {
        key: (
            value
            if isinstance(value, (str, int, float, bool, dict, list, type(None)))
            else str(value)
        )
        for key, value in data.items()
    }


def record(
    *,
    actor,
    tenant_id,
    action,
    entity_type,
    entity_id=None,
    before=None,
    after=None,
    request_id="",
    row_id=None,
    at=None,
):
    """Append one row. Returns it, so a caller can assert on it in a test.

    `before` is null on a create and `after` is null on a delete; both being null
    is a defect, not an economy.
    """
    if action not in ACTIONS:
        raise ValueError(
            f"{action!r} is not one of the audit verbs. A stage needing an "
            "eleventh adds it to core.models.AuditAction, not to its own module."
        )
    if before is None and after is None:
        raise ValueError(
            f"An audit row for {action} {entity_type} carries neither a before "
            "nor an after. A row that records no change is not a record."
        )
    fields = dict(
        tenant_id=tenant_id,
        actor_user_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", "") or "",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        request_id=request_id or "",
    )
    if at is not None:
        fields["created_at"] = at
    # `row_id` exists for the demo seed: every seeded id is derived from a
    # natural key, so a rebuilt seed keeps the ids it had and a screenshot, a
    # saved link and a bug report all still point at the same row. Present means
    # done -- there is no update path here, and the database holds no UPDATE
    # grant that would let one exist.
    if row_id is not None:
        existing = AuditLog.objects.filter(id=row_id).first()
        if existing is not None:
            return existing
        fields["id"] = row_id
    return AuditLog.objects.create(**fields)
