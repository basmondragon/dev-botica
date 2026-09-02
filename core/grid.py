"""The server half of the grid contract (architecture §9, S0 API surface).

§9 fixes the client half -- `manualPagination`, `manualSorting`,
`manualFiltering`, `rowCount` from the API. S0 fixes the server half once, so
eleven stages do not each invent a pagination envelope.

Every server-paginated list endpoint accepts `page` (1-based), `page_size`,
`sort` and `order`, and answers `{ rows, row_count, page, page_size }`.
"""

from typing import Generic, TypeVar

from ninja import Schema
from ninja.errors import HttpError

Row = TypeVar("Row")

DEFAULT_PAGE_SIZE = 25
PAGE_SIZES = (25, 50, 100)


class Page(Schema, Generic[Row]):
    """The envelope. `row_count` is the count **after filters and before
    pagination**, because it is the denominator of `1-15 de 4.284` and of the
    page group's width reservation (§B.4.5). It is never estimated and never
    omitted."""

    rows: list[Row]
    row_count: int
    page: int
    page_size: int


def paginate(queryset, *, page, page_size, sort, order, sortable):
    """Apply sort and page, and answer the envelope's four parts.

    An unknown sort key is a 422 rather than a silently ignored parameter: a sort
    the server dropped looks exactly like a sort the data does not distinguish.
    """
    if sort is not None:
        if sort not in sortable:
            raise HttpError(
                422,
                f"No se puede ordenar por «{sort}». Las columnas ordenables son: "
                + ", ".join(sorted(sortable))
                + ".",
            )
        columns = sortable[sort]
        prefix = "-" if order == "desc" else ""
        queryset = queryset.order_by(*[f"{prefix}{column}" for column in columns])

    page = max(1, int(page or 1))
    page_size = int(page_size or DEFAULT_PAGE_SIZE)
    if page_size not in PAGE_SIZES:
        raise HttpError(
            422,
            "El tamaño de página debe ser "
            + ", ".join(str(size) for size in PAGE_SIZES)
            + ".",
        )

    row_count = queryset.count()
    start = (page - 1) * page_size
    return list(queryset[start : start + page_size]), row_count, page, page_size
