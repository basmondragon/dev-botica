"""S8 -- the assistant.

Importing this package registers its two push writers with S2's endpoint, so an
offer written during a blackout and an acceptance queued behind it go through
this stage's own service and not around it.
"""

from core.assistant import sync as _sync

_sync.register()
