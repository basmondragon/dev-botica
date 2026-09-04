"""The one object-storage seam this stage touches (architecture §10).

**S5 writes files and expects the bucket to exist.** Provisioning it, its
lifecycle and its retention are a deploy concern and S10's, so everything here
goes through Django's configured `default` storage: on a developer's machine
that is the filesystem under `MEDIA_ROOT`, and in production the deploy swaps
one `STORAGES` entry for an S3-compatible backend and nothing in this stage
changes.

Two things use it: the **file export target**, which renders a period's
documents into one file for a client whose system has no API, and the
**loopback target's** own store, which is what makes it inspectable across the
web and worker processes rather than only inside one test.

**A write here replaces rather than appends.** Django's `Storage.save` picks a
free name when one is taken, which would turn a re-run of a period's export into
`2026-09-01_a8Xk.json` beside the original -- and *Delivery · the file export*
requires the opposite: re-running a period overwrites the same file with the
same content, so a re-run is a no-op at the far end.
"""

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


def put(name: str, data: bytes) -> str:
    """Write one file, replacing whatever stood at that name."""
    if default_storage.exists(name):
        default_storage.delete(name)
    return default_storage.save(name, ContentFile(data))


def get(name: str) -> bytes | None:
    """One file's bytes, or `None` where nothing stands at that name."""
    if not default_storage.exists(name):
        return None
    with default_storage.open(name, "rb") as handle:
        return handle.read()


def remove(name: str) -> None:
    if default_storage.exists(name):
        default_storage.delete(name)


def names(prefix: str) -> list[str]:
    """The files directly under one prefix, sorted.

    `listdir` raises on a prefix that has never been written, which for a
    listing endpoint is not an error: a tenant that has run no export has no
    exports, and an empty list says so.
    """
    try:
        _directories, files = default_storage.listdir(prefix)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return []
    return sorted(files)


def link(name: str) -> str:
    """A link to one file, where the backend can make one.

    On S3 this is a signed URL; on the filesystem it is `MEDIA_URL` plus the
    name, which nothing serves in development -- and that is correct rather than
    broken, because serving object storage is the deploy's job (§10). A backend
    that refuses to make one answers `''` and the surface renders no link rather
    than a dead one.
    """
    try:
        return default_storage.url(name)
    except (NotImplementedError, ValueError):
        return ""
