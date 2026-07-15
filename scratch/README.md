# scratch/

Two kinds of files live here.

## Permanent calibration tooling

These are maintained, documented in RUNBOOK.md, and used every calibration cycle:

| Script | Purpose |
|---|---|
| `calibrate_simulator.py` | Headline metrics on a fixed matchup set (quick before/after checks) |
| `replay_schedule.py` | Gold standard: replays the real season schedule, per-team strength slopes, `--set KEY=VALUE` config sweeps |
| `cross_era_compare.py` | Scoring/box-score reconciliation across ingested eras |
| `q4_role_split.py` | Q4 NET(lead-trail) by entering-margin band (gap 3.2 harness) |
| `explore_ratings.py` | Player attribute/tendency explorer |
| `03_game_simulator.py` | CLI wrapper to simulate a single game from the terminal |

They stay in scratch/ (not app/) because they are operator tools, not application
code — they read the DB directly and print reports.

## True scratch

Anything else: throwaway probes and one-off explorations. Delete freely once a
concept graduates into `app/` or stops being useful.
