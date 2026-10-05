"""The project layer.

A *download project* is a first-class logical container of download tasks — not
merely a folder.  It owns a name, a destination, its own concurrency ceiling, an
optional daily time window, an optional completion action, and a set of tasks.

Module map
----------
``models``      what a project **is**: identity, validation, persisted fields
``views``       what a project **shows**: counts, progress, derived state
``schedule``    the time-window value object and its rules (no I/O)
``validation``  filesystem checks for a project's destination (no SQL)
``admission``   the concurrency/schedule policy (no I/O, no SQL)
``repository``  SQL for the ``projects`` table only
``deletion``    the only code that removes a user's files from disk
``service``     orchestration: CRUD, delete policies, referential integrity

Dependency direction is strictly one way::

    service → {repository, validation, views, deletion, models, admission}
    views   → models
    service → core.db / core.store
    ui/common.py → admission            (policy only, never the service)

``views`` importing ``models`` (and never the reverse) is what keeps the domain
free of any notion of presentation; ``deletion`` is separate because it is the
one place in the package where a mistake is unrecoverable.

This package deliberately has an **empty** ``__init__``: re-exporting the service
here would make ``import projects`` pull in SQLite, the store and the whole
domain, which is exactly the kind of hidden dependency the layering is meant to
prevent.  Import the submodule you actually need.

See ``docs/PROJECTS.md`` for the decision record.
"""

from __future__ import annotations
