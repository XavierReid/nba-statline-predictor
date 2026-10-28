"""As-of access layer and metamorphic canary for preseason-rating backtests.

See RATING_BACKTEST_PROTOCOL.md (Amendment 2, A2.5/A2.7 group A). The rules this module makes
mechanical rather than conventional:

* Predictors receive a **reader**, never a database session. `AsOfReader(source, T)` serves only data
  that was knowable before season T's first game and raises `LeakageError` for anything else.
* Every read is logged with its true-time (`data_through`), availability time (`knowable_from`) and
  fidelity class, so a result can say which retrospective proxies it depends on.
* The **canary** runs a predictor twice, on the real source and on a source whose season->=T *outcomes*
  (season stats, game-log numbers) have been replaced by deterministic garbage, and requires
  bit-identical output. Identity columns, draft facts and the roster/listing proxies are inputs that
  stand in for preseason knowledge, so they are deliberately not garbled.
"""
import copy
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

EXACT = "exact"
PROXY = "retrospective_proxy"
REVISION_RISK = "revision_risk"

# Season-stat columns that are identity, not outcomes; they survive garbling (they define who exists).
IDENTITY_COLUMNS = {"player_id", "team_id", "season"}
# Bio columns that are static listing facts. Season-outcome columns of the bio endpoint (GP, PTS, ...)
# are NEVER exposed.
BIO_STATIC_COLUMNS = {"PLAYER_ID", "AGE", "PLAYER_HEIGHT_INCHES", "PLAYER_WEIGHT", "COLLEGE", "COUNTRY",
                      "DRAFT_YEAR", "DRAFT_ROUND", "DRAFT_NUMBER"}
FORBIDDEN_INPUTS = {"players.position", "players.team_id", "directory.roster_status",
                    "rating_sets.observed(season>=T)"}
# Documented overrides where the games table does not mark the true end of a season (A2/F3).
SEASON_END_OVERRIDES = {"2019-20": date(2020, 10, 11)}


class LeakageError(Exception):
    """A read that would give a preseason prediction information it could not have had."""


def season_start_year(season: str) -> int:
    return int(season[:4])


def prev_season(season: str, n: int = 1) -> str:
    y = season_start_year(season) - n
    return f"{y}-{str(y + 1)[2:]}"


# ---------------------------------------------------------------------------- data sources

class DataSource:
    """Raw accessors. Implemented by `MemorySource` (tests) and `DBSource` (backfill database)."""

    def season_stats(self, season: str) -> List[dict]: raise NotImplementedError
    def team_stats(self, season: str) -> List[dict]: raise NotImplementedError
    def game_logs(self, season: str) -> List[dict]: raise NotImplementedError   # player_id, team_id, game_no, minutes
    def bio(self, season: str) -> List[dict]: raise NotImplementedError          # raw bio rows (may include outcomes)
    def draft_class(self, year: int) -> List[dict]: raise NotImplementedError
    def first_game(self, season: str) -> date: raise NotImplementedError
    def last_game(self, season: str) -> date: raise NotImplementedError


class MemorySource(DataSource):
    """In-memory source for unit tests: dicts keyed by season/year."""

    def __init__(self, stats=None, team=None, logs=None, bio=None, draft=None, calendar=None):
        self._stats, self._team, self._logs = stats or {}, team or {}, logs or {}
        self._bio, self._draft, self._cal = bio or {}, draft or {}, calendar or {}

    def season_stats(self, season): return copy.deepcopy(self._stats.get(season, []))
    def team_stats(self, season): return copy.deepcopy(self._team.get(season, []))
    def game_logs(self, season): return copy.deepcopy(self._logs.get(season, []))
    def bio(self, season): return copy.deepcopy(self._bio.get(season, []))
    def draft_class(self, year): return copy.deepcopy(self._draft.get(year, []))
    def first_game(self, season): return self._cal[season][0]
    def last_game(self, season): return self._cal[season][1]


class GarbledSource(DataSource):
    """Wraps a source; for seasons >= T replaces OUTCOME numbers with deterministic garbage.

    Identity columns, draft classes, bio listing facts and the calendar are left alone: they are inputs
    standing in for preseason knowledge (roster, listing, draft), not outcomes of season T.
    """

    def __init__(self, inner: DataSource, target_season: str, seed: int = 20261023):
        self.inner, self.t, self.seed = inner, target_season, seed

    def _late(self, season: str) -> bool:
        return season_start_year(season) >= season_start_year(self.t)

    def _garble(self, rows: List[dict], season: str, keep: set) -> List[dict]:
        rng = random.Random(f"{self.seed}:{season}")
        out = []
        for r in rows:
            g = dict(r)
            for k, v in r.items():
                if k in keep or isinstance(v, (str, date)) or v is None:
                    continue
                g[k] = round(rng.uniform(0, 100), 3) if isinstance(v, float) else rng.randint(0, 100)
            out.append(g)
        return out

    def season_stats(self, season):
        rows = self.inner.season_stats(season)
        return self._garble(rows, season, IDENTITY_COLUMNS) if self._late(season) else rows

    def team_stats(self, season):
        rows = self.inner.team_stats(season)
        return self._garble(rows, season, IDENTITY_COLUMNS) if self._late(season) else rows

    def game_logs(self, season):
        rows = self.inner.game_logs(season)
        return self._garble(rows, season, IDENTITY_COLUMNS | {"game_no"}) if self._late(season) else rows

    def bio(self, season):
        rows = self.inner.bio(season)
        if not self._late(season):
            return rows
        # outcome columns of the bio endpoint (GP, PTS, ...) are garbled; static listing facts are kept
        keep = BIO_STATIC_COLUMNS
        return self._garble(rows, season, keep)

    def draft_class(self, year): return self.inner.draft_class(year)
    def first_game(self, season): return self.inner.first_game(season)
    def last_game(self, season): return self.inner.last_game(season)


# ------------------------------------------------------------------------------ the reader

@dataclass
class ReadRecord:
    kind: str
    season: Optional[str]
    data_through: str
    knowable_from: str
    fidelity: str


class AsOfReader:
    """The only thing a predictor may touch. Cutoff = the day before season T's first game."""

    def __init__(self, source: DataSource, target_season: str, guard: bool = True):
        self._src, self.target = source, target_season
        self.cutoff = source.first_game(target_season) - timedelta(days=1)
        self._guard = guard
        self.reads: List[ReadRecord] = []

    # -- bookkeeping ---------------------------------------------------------------
    def _season_end(self, season: str) -> date:
        return SEASON_END_OVERRIDES.get(season) or self._src.last_game(season)

    def _log(self, kind, season, data_through, knowable_from, fidelity):
        self.reads.append(ReadRecord(kind, season, str(data_through), str(knowable_from), fidelity))

    def _require_prior(self, kind: str, season: str, fidelity: str):
        if self._guard and season_start_year(season) >= season_start_year(self.target):
            raise LeakageError(f"{kind}({season}) is season >= target {self.target}")
        end = self._season_end(season)
        knowable = end + timedelta(days=1)
        if self._guard and knowable > self.cutoff:
            raise LeakageError(f"{kind}({season}) knowable from {knowable}, after cutoff {self.cutoff}")
        self._log(kind, season, end, knowable, fidelity)

    # -- permitted reads -----------------------------------------------------------
    def season_stats(self, season: str, *, include_tracking_defense: bool = True) -> List[dict]:
        self._require_prior("season_stats", season, REVISION_RISK if include_tracking_defense else EXACT)
        rows = self._src.season_stats(season)
        if not include_tracking_defense:
            rows = [{k: v for k, v in r.items() if not k.startswith(("d_", "ra_", "paint_", "mid_", "corner3_"))} for r in rows]
        return rows

    def team_stats(self, season: str) -> List[dict]:
        self._require_prior("team_stats", season, EXACT)
        return self._src.team_stats(season)

    def prior_game_logs(self, season: str) -> List[dict]:
        self._require_prior("game_logs", season, EXACT)
        return self._src.game_logs(season)

    def draft_facts(self) -> Dict[int, dict]:
        """Draft slot facts for classes up to the target season's draft (held the June before T)."""
        out, y = {}, season_start_year(self.target)
        for year in range(y - 20, y + 1):                                  # draft of year T_start precedes T
            for r in self._src.draft_class(year):
                out[r["PERSON_ID"]] = {"draft_year": year, "round": r.get("ROUND_NUMBER"),
                                       "pick": r.get("OVERALL_PICK"), "organization": r.get("ORGANIZATION")}
        self._log("draft_facts", None, date(y, 6, 30), date(y, 6, 30), EXACT)
        return out

    def listing(self, season: Optional[str] = None) -> Dict[int, dict]:
        """Static listing facts (age, height, weight, college, country, draft) for players in the bio
        endpoint of `season` (default: the target season, i.e. rookies' preseason listing). Outcome
        columns of the endpoint are never returned. Retrospective proxy: fetched after the fact."""
        season = season or self.target
        if self._guard and season_start_year(season) > season_start_year(self.target):
            raise LeakageError(f"listing({season}) is after target {self.target}")
        rows = self._src.bio(season)
        self._log("listing", season, self.cutoff, self.cutoff, PROXY)
        return {r["PLAYER_ID"]: {k: v for k, v in r.items() if k in BIO_STATIC_COLUMNS} for r in rows}

    def preseason_roster_proxy(self, variant: str = "early", early_games: int = 10) -> Dict[int, int]:
        """player_id -> team_id from first appearance in season T (A2.3). Reveals only identity pairs,
        never minutes or statistics. Retrospective proxy: includes players who only played, and may
        include late arrivals under variant 'all'."""
        if variant not in ("early", "all"):
            raise ValueError(variant)
        first: Dict[int, tuple] = {}
        for r in sorted(self._src.game_logs(self.target), key=lambda r: (r["game_no"], r["player_id"])):
            first.setdefault(r["player_id"], (r["game_no"], r["team_id"]))
        out = {p: t for p, (g, t) in first.items() if variant == "all" or g <= early_games}
        self._log("roster_proxy:" + variant, self.target, self.cutoff, self.cutoff, PROXY)
        return out

    def read_forbidden(self, name: str):
        raise LeakageError(f"{name} is a forbidden input (A2.5): current-snapshot or later information")

    # -- audit ---------------------------------------------------------------------
    def manifest(self) -> dict:
        body = [r.__dict__ for r in self.reads]
        sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        return {"target": self.target, "cutoff": str(self.cutoff), "inputs": body, "sha256": sha,
                "non_exact_classes": sorted({r.fidelity for r in self.reads if r.fidelity != EXACT})}

    def audit(self) -> List[str]:
        """Manifest audit (gate A3): every input knowable at or before the cutoff."""
        return [f"{r.kind}({r.season}) knowable {r.knowable_from} > cutoff {self.cutoff}"
                for r in self.reads if r.knowable_from > str(self.cutoff)]


# ------------------------------------------------------------------------------- canary

Predictor = Callable[[AsOfReader], Dict[int, dict]]


@dataclass
class CanaryResult:
    target: str
    identical: bool
    error: Optional[str] = None
    differing_players: List[int] = field(default_factory=list)


def _canonical(pred: Dict[int, dict]) -> str:
    return json.dumps(pred, sort_keys=True, default=str)


def run_canary(predictor: Predictor, source: DataSource, target_season: str) -> CanaryResult:
    """Gate A1. Real data vs a source whose season->=T outcomes are garbage: outputs must be
    bit-identical. A predictor that touches forbidden or future data either raises under the guard
    (reported) or differs under garbling (reported)."""
    try:
        real = predictor(AsOfReader(source, target_season, guard=True))
    except LeakageError as e:
        return CanaryResult(target_season, False, error=f"guard: {e}")
    garbled = predictor(AsOfReader(GarbledSource(source, target_season), target_season, guard=False))
    if _canonical(real) == _canonical(garbled):
        return CanaryResult(target_season, True)
    bad = sorted(p for p in set(real) | set(garbled) if real.get(p) != garbled.get(p))
    return CanaryResult(target_season, False, error="outputs differ under garbling", differing_players=bad[:20])
