"""S4 · the counter.

Importing this package registers the six push writers with S2's endpoint, so a
sale rung up on a till that was offline goes through S3's ledger service and not
around it -- the same shape S3 took for its two.
"""

from core.counter import sync as _sync

_sync.register()
