"""One field, one query param, four things matched.

`name`, the laboratorio's `name`, any `item_barcodes.code` and
`invima_registration`. A barcode and a registration number are matched
**exactly** -- they are identifiers somebody scanned or pasted, and a partial
match on either is a wrong row. A name and a laboratorio are matched as a prefix
and as a trigram, so `losar` finds `Losartán 50 mg × 30` and `acetaminofen`
finds `Acetaminofén`.

The binding number is §4's: the inventory grid page, on any filter combination,
in **under 400ms p95 server time**, server-paginated. At the handoff's 4.284
rows that is comfortable across a three-table join, and the trigram indexes in
migration 0006 are what make it so. If a pilot's catalog and that budget
disagree, the answer is a maintained search column on `items` -- a migration on
a table this stage owns, changing no endpoint contract and no screen. It is
named here so nobody reaches for a search service, which would be a fifth
container against an architecture whose defining choice is that Postgres does
everything (§4).
"""

import unicodedata

from django.db.models import Exists, OuterRef, Q

from core.models import ItemBarcode


def fold(term):
    """Strip accents and lowercase, so `acetaminofen` finds `Acetaminofén`.

    This is the **needle**'s half of the fold. The haystack's half is
    `items.search_name` and `manufacturers.search_name`, two generated columns
    the database maintains over the same expression -- folding only the needle
    would find nothing, because the accent is in the stored value.
    """
    return "".join(
        character
        for character in unicodedata.normalize("NFD", term or "")
        if unicodedata.category(character) != "Mn"
    ).lower()


def matching(queryset, term, tenant_id):
    """Narrow an `items` queryset by the catalog's one search field."""
    term = (term or "").strip()
    if not term:
        return queryset

    folded = fold(term)
    has_code = Exists(
        ItemBarcode.objects.filter(
            tenant_id=tenant_id, item_id=OuterRef("pk"), code=term
        )
    )
    # Four things, and `contains` rather than `icontains`: the two `search_name`
    # columns are already folded and lowercased, so a case-insensitive operator
    # would wrap them in `UPPER()` and step off the trigram index for nothing.
    predicate = (
        Q(search_name__contains=folded)
        | Q(manufacturer__search_name__contains=folded)
        | Q(invima_registration__iexact=term)
        | has_code
    )
    # `.distinct()` is deliberately absent: the barcode arm is an EXISTS rather
    # than a join, so an item carrying three codes is still one row. A DISTINCT
    # here would cost a sort on every keystroke to fix a duplication that the
    # subquery already prevents.
    return queryset.filter(predicate)
