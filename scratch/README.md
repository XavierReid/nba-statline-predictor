# scratch/

This is where Phase 1 work happens — throwaway scripts that prove a concept
before it graduates into the scaffold's main `app/` modules.

## Why this folder exists

The main `app/` structure is production-grade — modules, dependency injection,
ORM, migrations, the works. That's the *target*, not the starting point.

For each feature, the build progression is:

1. **Phase 1 (this folder):** Simplest possible script that does the thing.
   No DB, no abstractions, no error handling. Maybe 5-20 lines. Goal: prove
   the API call works, prove you understand the data shape, prove the core
   logic.
2. **Phase 2:** Take what worked here, move it into the appropriate `app/`
   module (e.g., `app/ingestion/nba_client.py`), wire it into the scaffold
   structure with DB session, basic models, etc.
3. **Phase 3:** Add production-grade hardening — error handling, retries,
   logging, idempotency checks, tests.

This folder is for Phase 1 only.

## Naming convention

`NN_short_description.py` — e.g., `01_fetch_teams.py`, `02_fetch_players.py`.
The number reflects the rough order things were tried, so the folder reads as
a learning history.

## Lifecycle

Scripts here are meant to be either:

- **Deleted** once their logic has graduated into the scaffold (most common).
- **Kept around** as documentation of the learning progression (occasional —
  e.g., a script that compares two data sources, or a one-off backfill).

Don't import from `scratch/` in the main `app/`. If you find yourself wanting
to, that's a signal that script should graduate.

## What's NOT in scope here

- No production code patterns (no SQLAlchemy session management, no FastAPI
  routes, no Pydantic models). Those belong in `app/`.
- No unit tests. Phase 1 scripts are proven by running them and inspecting
  output. Tests come in Phase 3, after the code is in `app/`.
- No commits of broken or half-finished scratch scripts. If it doesn't run
  end-to-end, don't commit it.
