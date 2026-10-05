import datetime
import time

import pytest

from pf import (CLIENT, assert_error, ago, correct, fixture, full_statement, get, iso, login, me_at, now_utc,
                parse_ts, pay, payment_ids_by_note, reset, seeded_payment, simultaneously, statement, user, world2,
                post, key)


def corrected_world():
    """A seeded payment at T1 later corrected (recorded now) to a smaller amount at a later effective time."""
    T1, T2 = ago(days=3), ago(days=2)
    t = world2(balances={"ada": 9000, "bob": 3500, "op": 0}, payments=[
        seeded_payment("p_k1", "ada", "bob", 1000, T1, note="orig")])
    pid = payment_ids_by_note(t["ada"])["orig"]
    c = correct(t["ada"], pid, 600, iso(T2), reason="fix").json()
    return t, (T1, T2), pid, c


def test_known_at_selects_revision():
    """
    Spec: "`GET /me` and `GET /statement` accept optional `known_at`, an RFC 3339 instant with offset."
    Spec: "For each payment, select its latest revision recorded **at or before** `known_at`; if none was yet recorded, that payment contributes nothing."
    Spec: "Omission means everything known when the read begins."
    Spec: "Then apply selected revisions according to their **effective** times."
    Spec: "`as_of` retains its inclusive meaning; a statement retains its half-open window."
    Spec: "Both query instants may be in the future."
    Spec: "The service must distinguish **when money took effect** from **when it learned that fact**."
    """
    t, (T1, T2), pid, c = corrected_world()
    sec = datetime.timedelta(seconds=1)
    rec = parse_ts(c["recorded_at"])
    future = iso(now_utc() + datetime.timedelta(days=5))
    assert me_at(t["ada"])["balance"] == 9400
    assert me_at(t["ada"], known_at=iso(T1 - sec))["balance"] == 10000
    assert me_at(t["ada"], known_at=iso(T1))["balance"] == 9000
    assert me_at(t["ada"], known_at=iso(rec - sec))["balance"] == 9000
    assert me_at(t["ada"], known_at=future)["balance"] == 9400
    assert me_at(t["ada"], as_of=iso(T1), known_at=iso(rec - sec))["balance"] == 9000
    assert me_at(t["ada"], as_of=iso(T1))["balance"] == 10000
    assert me_at(t["ada"], as_of=iso(T2))["balance"] == 9400
    assert me_at(t["ada"], as_of=future, known_at=future)["balance"] == 9400
    m = me_at(t["bob"], as_of=iso(T2 - sec), known_at=future)
    assert m["balance"] == 2500


def test_known_at_echo():
    """
    Spec: "Echo supplied `known_at` exactly."
    """
    t, (T1, T2), pid, c = corrected_world()
    given = T2.strftime("%Y-%m-%dT%H:%M:%SZ")
    m = me_at(t["ada"], known_at=given)
    assert m["known_at"] == given
    m = me_at(t["ada"], known_at=iso(T2), as_of=iso(T1))
    assert m["known_at"] == iso(T2) and m["as_of"] == iso(T1)


def test_statement_with_known_at_and_revision_fields():
    """
    Spec: "Statement ordering is now by selected `effective_at`, then payment id."
    Spec: "Each entry retains `payment`, `delta` and `balance_after`, and adds the selected `revision`, `effective_at` and `recorded_at`."
    Spec: "`payment.amount` is the selected amount for this statement."
    Spec: "No correction is counted alongside the revision it replaces."
    """
    t, (T1, T2), pid, c = corrected_world()
    p = pay(t["bob"], "ada", 50, note="mid").json()
    s = statement(t["ada"])
    assert s["opening_balance"] == 10000 and s["closing_balance"] == 9450
    assert len(s["entries"]) == 2
    e = s["entries"][0]
    assert e["payment"]["payment_id"] == pid and e["payment"]["amount"] == 600
    assert (e["revision"], e["delta"], e["balance_after"]) == (2, -600, 9400)
    assert parse_ts(e["effective_at"]) == T2 and parse_ts(e["recorded_at"]) == parse_ts(c["recorded_at"])
    assert s["entries"][1]["payment"]["payment_id"] == p["payment_id"] and s["entries"][1]["balance_after"] == 9450
    old = statement(t["ada"], known_at=iso(parse_ts(c["recorded_at"]) - datetime.timedelta(seconds=1)))
    assert len(old["entries"]) == 1
    e = old["entries"][0]
    assert (e["revision"], e["delta"], e["payment"]["amount"]) == (1, -1000, 1000)
    assert parse_ts(e["effective_at"]) == T1 and parse_ts(e["recorded_at"]) == T1
    assert old["closing_balance"] == 9000
    w = statement(t["ada"], **{"from": iso(T1), "to": iso(T2)})
    assert w["entries"] == [] and w["opening_balance"] == w["closing_balance"] == 10000
    w = statement(t["ada"], **{"from": iso(T1), "to": iso(T2)}, known_at=iso(T2))
    assert [x["payment"]["payment_id"] for x in w["entries"]] == [pid] and w["closing_balance"] == 9000


def test_zero_revision_entry():
    """
    Spec: "Zero-amount revisions still appear as entries with zero delta."
    """
    T1 = ago(days=2)
    t = world2(balances={"ada": 9000, "bob": 3500, "op": 0},
               payments=[seeded_payment("p_z", "ada", "bob", 1000, T1, note="z")])
    pid = payment_ids_by_note(t["ada"])["z"]
    assert correct(t["ada"], pid, 0, iso(T1), reason="void it").status_code == 201
    s = statement(t["ada"])
    assert len(s["entries"]) == 1
    e = s["entries"][0]
    assert (e["delta"], e["payment"]["amount"], e["revision"], e["balance_after"]) == (0, 0, 2, 10000)
    assert s["opening_balance"] == s["closing_balance"] == 10000


def test_no_corrections_no_known_at_unchanged():
    """
    Spec: "With no corrections and no `known_at`, previous behavior is unchanged."
    """
    T1 = ago(days=2)
    t = world2(balances={"ada": 9000, "bob": 3500, "op": 0},
               payments=[seeded_payment("p_u", "ada", "bob", 1000, T1, note="u")])
    s = statement(t["ada"])
    assert [(e["delta"], e["balance_after"], e["revision"]) for e in s["entries"]] == [(-1000, 9000, 1)]
    assert me_at(t["ada"])["balance"] == 9000


# ---------- snapshots ----------

def test_snapshot_freezes_result():
    """
    Spec: "Every first `GET /statement` response additionally returns an opaque `snapshot` token."
    Spec: "It freezes the caller's selected revisions, window, balances, entries and default `to` at that read."
    Spec: "`GET /statement?snapshot=<token>&limit=...&offset=...` pages that exact result, even after payments or corrections."
    Spec: "Paging changes neither balances nor entries; the final partial page and offsets beyond the end must report `has_more` correctly."
    Spec: "Old snapshots remain unchanged after any lifecycle action or correction."
    """
    T1, T2 = ago(days=3), ago(days=2)
    t = world2(balances={"ada": 9000, "bob": 3500, "op": 0}, payments=[
        seeded_payment(f"p_s{i}", "ada", "bob", 100, T1 + datetime.timedelta(minutes=i), note=f"n{i}")
        for i in range(10)])
    first = statement(t["ada"], limit=4)
    tok = first["snapshot"]
    assert len(first["entries"]) == 4 and first["has_more"] is True
    full = statement(t["ada"], limit=200)["entries"]
    pid = payment_ids_by_note(t["ada"])["n0"]
    assert correct(t["ada"], pid, 50, iso(T1)).status_code == 201
    pay(t["ada"], "bob", 7, note="after")
    pay(t["bob"], "ada", 9, note="after2")
    pages = []
    for off in (0, 4, 8):
        page = statement(t["ada"], snapshot=tok, limit=4, offset=off)
        assert page["opening_balance"] == first["opening_balance"] == 10000
        assert page["closing_balance"] == first["closing_balance"] == 9000
        pages.append(page)
    assert [len(p["entries"]) for p in pages] == [4, 4, 2]
    assert [p["has_more"] for p in pages] == [True, True, False]
    assert pages[0]["entries"] == first["entries"]
    assert [e for p in pages for e in p["entries"]] == full
    beyond = statement(t["ada"], snapshot=tok, limit=4, offset=10)
    assert beyond["entries"] == [] and beyond["has_more"] is False
    beyond = statement(t["ada"], snapshot=tok, limit=4, offset=50)
    assert beyond["entries"] == [] and beyond["has_more"] is False
    assert statement(t["ada"], snapshot=tok, limit=10, offset=0)["has_more"] is False
    fresh = statement(t["ada"], limit=200)
    assert fresh["closing_balance"] == 9000 + 50 - 7 + 9 and len(fresh["entries"]) == 12


def test_snapshot_freezes_default_to():
    """
    Spec: "It freezes the caller's selected revisions, window, balances, entries and default `to` at that read."
    """
    t = world2()
    pay(t["ada"], "bob", 10, note="a")
    first = statement(t["ada"])
    time.sleep(1.1)
    pay(t["ada"], "bob", 20, note="b")
    page = statement(t["ada"], snapshot=first["snapshot"])
    assert [e["payment"]["note"] for e in page["entries"]] == ["a"]
    assert page["closing_balance"] == first["closing_balance"] == 9990


@pytest.mark.parametrize("extra", [{"from": "2026-01-01T00:00:00+00:00"}, {"to": "2030-01-01T00:00:00+00:00"},
                                   {"known_at": "2026-01-01T00:00:00+00:00"}])
def test_snapshot_rejects_window_params(extra):
    """
    Spec: "Only limit and offset may accompany a snapshot; supplying `from`, `to` or `known_at` with it gives 422 `validation_failed`."
    Spec: "Unrecognized query parameters remain ignored under stage 1's general rule."
    """
    t = world2()
    pay(t["ada"], "bob", 10)
    tok = statement(t["ada"])["snapshot"]
    assert_error(get("/statement", t["ada"], dict(extra, snapshot=tok)), 422, "validation_failed")
    assert get("/statement", t["ada"], {"snapshot": tok, "colour": "blue"}).status_code == 200


def test_snapshot_not_found_cases():
    """
    Spec: "Unknown token, another user's token, or a token from before reset gives 404 `not_found`."
    Spec: "Tokens last until reset."
    Spec: "No storage survival across container restarts is required."
    """
    t = world2()
    pay(t["ada"], "bob", 10)
    tok = statement(t["ada"])["snapshot"]
    assert_error(get("/statement", t["ada"], {"snapshot": "no-such-token"}), 404, "not_found")
    assert_error(get("/statement", t["bob"], {"snapshot": tok}), 404, "not_found")
    assert get("/statement", t["ada"], {"snapshot": tok}).status_code == 200
    t2 = world2()
    assert_error(get("/statement", t2["ada"], {"snapshot": tok}), 404, "not_found")


def test_snapshot_under_concurrent_writes():
    """
    Spec: "Existing snapshots remain unchanged during concurrent payments or corrections."
    """
    t = world2()
    for i in range(6):
        pay(t["ada"], "bob", 10 + i, note=f"x{i}")
    first = statement(t["ada"], limit=200)
    tok = first["snapshot"]
    calls = [lambda c, i=i: post("/payments", t["ada"], {"to_handle": "bob", "amount": 1}, key(), client=c)
             for i in range(10)]
    calls += [lambda c: c.get("/statement", params={"snapshot": tok, "limit": 3, "offset": 3},
                              headers={"Authorization": f"Bearer {t['ada']}"}) for _ in range(10)]
    results = simultaneously(calls)
    for r in results[10:]:
        assert r.status_code == 200
        body = r.json()
        assert body["entries"] == first["entries"][3:6]
        assert body["opening_balance"] == first["opening_balance"]
        assert body["closing_balance"] == first["closing_balance"]
