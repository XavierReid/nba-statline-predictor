import json
from types import SimpleNamespace

import pytest

from validation.ratings.lockguard import LockedSetError, check


def main_for(tmp_path, name="validation.ratings.fake", body="x = 1"):
    f = tmp_path / "fake.py"
    f.write_text(body)
    return f, SimpleNamespace(__spec__=SimpleNamespace(name=name), __file__=str(f))


def approvals(tmp_path, name, sha):
    p = tmp_path / "ap.json"
    p.write_text(json.dumps({name: sha}))
    return str(p)


def test_dev_seasons_are_never_blocked(tmp_path):
    check("2021-22", str(tmp_path / "none"), str(tmp_path / "none2"), main=None)


def test_locked_season_blocked_by_default(tmp_path):
    with pytest.raises(LockedSetError):
        check("2022-23", str(tmp_path / "FREEZE"), str(tmp_path / "ap.json"), main=main_for(tmp_path)[1])


def test_freeze_file_opens_the_lock(tmp_path):
    (tmp_path / "FREEZE").write_text("frozen at abc123")
    check("2025-26", str(tmp_path / "FREEZE"), str(tmp_path / "ap.json"), main=main_for(tmp_path)[1])


def test_approved_unmodified_script_may_read(tmp_path):
    import hashlib
    f, m = main_for(tmp_path)
    ap = approvals(tmp_path, "validation.ratings.fake", hashlib.sha256(f.read_bytes()).hexdigest())
    check("2023-24", str(tmp_path / "FREEZE"), ap, main=m)


def test_editing_an_approved_script_revokes_approval(tmp_path):
    import hashlib
    f, m = main_for(tmp_path)
    ap = approvals(tmp_path, "validation.ratings.fake", hashlib.sha256(f.read_bytes()).hexdigest())
    f.write_text("x = 2  # changed after approval")
    with pytest.raises(LockedSetError):
        check("2023-24", str(tmp_path / "FREEZE"), ap, main=m)
