"""S1 · the catalog.

The tables live in `core.models` beside S0's, because one Django app has one
models module and a catalog table is not a different kind of table. What lives
here is the behaviour that would otherwise double the size of `core/api.py`:

  `prices`      the resolution rule and the **one** price write path (A11)
  `search`      the four things the catalog's one search field matches
  `api`         the router `core.api` mounts
  `loader`      the internal load tool's engine, run by `load_catalog`
  `jobs`        the nightly INVIMA sweep
  `vocabulary`  the seed's name grammar, from one fixed random seed
  `demo`        the `catalog` fixture, registered with S0's seed command
"""
