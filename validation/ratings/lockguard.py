"""Hard lock on the held-out test seasons (2022-23 onward) for rating validation.

Reading a locked season is refused unless (a) the FREEZE file exists (only Xavier creates it, after the method
and constants are committed), or (b) the running script is on the approved list AND its file is byte-identical
to the version that was approved. Editing an approved script therefore revokes its approval until re-approved.
"""
import hashlib
import json
import os
import sys

LOCKED_FROM = "2022-23"
HERE = os.path.dirname(os.path.abspath(__file__))
FREEZE_FILE = os.path.join(HERE, "FREEZE")
APPROVALS_FILE = os.path.join(HERE, "locked_approvals.json")   # {"module.name": "sha256 of its file"}


class LockedSetError(RuntimeError):
    pass


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _running_script(main=None):
    main = main or sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    return (getattr(spec, "name", None), getattr(main, "__file__", None))


def check(season, freeze_file=None, approvals_file=None, main=None):
    """Raise LockedSetError if `season` is a locked season and the caller has no right to read it."""
    if season < LOCKED_FROM:
        return
    if os.path.exists(freeze_file or FREEZE_FILE):
        return
    name, path = _running_script(main)
    try:
        approved = json.load(open(approvals_file or APPROVALS_FILE))
    except FileNotFoundError:
        approved = {}
    if name and path and approved.get(name) == _sha(path):
        return
    raise LockedSetError(
        "season %s is in the locked test set; no FREEZE file and %r is not an approved, unmodified script" % (season, name))
