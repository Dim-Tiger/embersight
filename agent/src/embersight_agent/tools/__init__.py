"""External-data tools used by the subagents.

Pass-1: each module exposes a stub `fetch()` (or similar) so the graph can
be exercised without network. Pass-2 fills in real adapters.

Naming guarantee: there is intentionally NO tool here whose name contains
any action verb that would imply EmberSight took an external action. The
Resource Recommendation and Evacuation Intelligence agents can only emit
proposals that pass through an `interrupt()`.

See ../../../../README.md "Repo guarantees" for the enforced banned-verb
list. Running the grep documented there from the repo root must return zero
matches under this directory.
"""
