from datetime import datetime, timedelta

from radar.db import Database
from radar.providers import broker
from radar.tradable import filter_tradable

NOW = datetime(2026, 9, 24, 10, 0)
CFG = {"only_tradable": True, "recheck_days": 7, "always_allow": ["MINE"], "always_block": ["NOPE"]}


def fake_checker(calls):
    table = {"AMC": True, "GCTK": False, "ERR": None}

    def check(ticker, name, isin):
        calls.append(ticker)
        return broker.Availability(table.get(ticker, True), f"test {ticker}")
    return check


def test_filters_and_caches():
    db, calls = Database(":memory:"), []
    kept, avail = filter_tradable(db, ["AMC", "GCTK", "ERR", "MINE", "NOPE"], {}, NOW, CFG, 10,
                                  fake_checker(calls))
    assert kept == ["AMC", "ERR", "MINE"]          # unknown (ERR) kept, never silently dropped
    assert avail["GCTK"].available is False and avail["MINE"].available is True
    assert sorted(calls) == ["AMC", "ERR", "GCTK"]

    calls.clear()
    filter_tradable(db, ["AMC", "GCTK", "ERR"], {}, NOW + timedelta(days=1), CFG, 10, fake_checker(calls))
    assert calls == ["ERR"]                         # failed lookup retried, others cached

    calls.clear()
    filter_tradable(db, ["AMC"], {}, NOW + timedelta(days=8), CFG, 10, fake_checker(calls))
    assert calls == ["AMC"]                         # recheck after recheck_days


def test_limit_applied_after_filter():
    kept, _ = filter_tradable(Database(":memory:"), ["GCTK", "A", "B", "C"], {}, NOW, CFG, 2,
                              fake_checker([]))
    assert kept == ["A", "B"]


def test_disabled_filter_keeps_everything():
    kept, avail = filter_tradable(Database(":memory:"), ["GCTK", "A"], {}, NOW, {"only_tradable": False}, 5)
    assert kept == ["GCTK", "A"] and avail == {}


def test_check_ls_by_isin_and_name(monkeypatch):
    results = {
        "US00165C3025": [{"isin": "US00165C3025", "displayname": "AMC ENTERTAINM.HLD.A NEW", "categorySymbol": "STK"}],
        "JAGX": [{"isin": "CA47009M8896", "displayname": "JAGUAR MINING INC. NEW", "categorySymbol": "STK"}],
        "AMC": [],
    }
    monkeypatch.setattr(broker, "ls_search", lambda q: results.get(q, []))
    assert broker.check_ls("AMC", "AMC Entertainment Holdings", "US00165C3025").available is True
    results["AMC"] = [{"isin": "US00165C3025", "displayname": "AMC ENTERTAINM.HLD.A NEW", "categorySymbol": "STK"}]
    assert broker.check_ls("AMC", "AMC Entertainment Holdings", None).available is True
    # Name mismatch (Jaguar Health vs Jaguar Mining shares the first word!) -> guarded by ISIN first
    assert broker.check_ls("GCTK", "GlucoTrack Inc", "US45824Q8877").available is False

    def boom(q):
        raise ConnectionError("down")
    monkeypatch.setattr(broker, "ls_search", boom)
    assert broker.check_ls("X", "X", None).available is None


def test_one_shared_word_is_not_a_match(monkeypatch):
    """ALP (Alpha Compute Corp) was matched to ALPHA SYSTEMS (Japan)."""
    hits = [{"isin": "JP3126330004", "displayname": "ALPHA SYSTEMS I", "categorySymbol": "STK"},
            {"isin": "US02081G2012", "displayname": "ALPHATEC HOLDINGS INC.", "categorySymbol": "STK"}]
    monkeypatch.setattr(broker, "ls_search", lambda q: hits if q == "ALP" else [])
    assert broker.check_ls("ALP", "Alpha Compute Corp", None).available is False
    assert broker.check_ls("ALP", "Alpha Compute Corp", "VGG7185A1443").available is False


def test_name_matching_rules():
    from radar.providers.broker import _same_company
    assert _same_company("AMC Entertainment Holdings", "AMC ENTERTAINM.HLD.A NEW")
    assert _same_company("AIxCrypto Holdings, Inc.", "AIXCRYPTO HLDGS.  DL-,001")
    assert not _same_company("Alpha Compute Corp", "ALPHA SYSTEMS I")
    assert not _same_company("Capstone Holding Corp.", "CAPSTONE COPPER CORP.")


def test_old_positive_checks_invalidated_once():
    db, calls = Database(":memory:"), []
    db.set_broker_availability("ALP", True, "LS Exchange (ALPHA SYSTEMS I, JP3126330004)", NOW.isoformat())
    db.set_broker_availability("GCTK", False, "no encontrado", NOW.isoformat())
    filter_tradable(db, ["ALP", "GCTK"], {}, NOW, CFG, 10, fake_checker(calls))
    assert calls == ["ALP"]          # positive from old rules rechecked, negative kept
    calls.clear()
    filter_tradable(db, ["ALP"], {}, NOW, CFG, 10, fake_checker(calls))
    assert calls == []               # only once
