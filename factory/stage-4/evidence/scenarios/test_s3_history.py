import datetime

import pytest

from pf import (CLIENT, activity, assert_error, fixture, full_statement, get, iso, key, login, me, me_at, ago,
                now_utc, parse_ts, pay, payment_ids_by_note, reset, seeded_payment, snapshot, statement, user,
                world2)

BAL = {"ada": 10000, "bob": 2500, "cy": 300, "dan": 5000, "op": 0}
OPENING = {"ada": 9600, "bob": 3150, "cy": 0, "dan": 5050, "op": 0}


def history():
    """Seeded history at three past instants; returns (tokens, instants, payment ids by note)."""
    T1, T2, T3 = ago(days=3), ago(days=2), ago(days=1)
    t = world2(balances=BAL, payments=[
        seeded_payment("p_h1", "ada", "bob", 500, T1, note="one"),
        seeded_payment("p_h2", "bob", "ada", 1200, T2, note="two"),
        seeded_payment("p_h3", "ada", "cy", 300, T3, note="three"),
        seeded_payment("p_h4", "dan", "bob", 50, T2, note="other"),
    ])
    return t, (T1, T2, T3), payment_ids_by_note(t["bob"]) | payment_ids_by_note(t["ada"])


# ---------- payment timestamps ----------

def test_seeded_created_at_kept():
    """
    Spec: "Every payment's `created_at` is an RFC 3339 instant with an offset identifying when it moved money."
    Spec: "Every endpoint returning a payment includes it."
    Spec: "Seeded payments may supply `created_at`; omission uses reset time, before subsequent API-created payments."
    Spec: "A fixture's `balance` remains the balance after all seeded payments. Loading those payments must not change that balance."
    Spec: "A seeded payment's supplied `created_at` is also its original recorded/effective time; omission uses reset time."
    """
    T1 = ago(days=3)
    before_reset = now_utc() - datetime.timedelta(seconds=2)
    t = world2(balances=BAL, payments=[seeded_payment("p_a", "ada", "bob", 500, T1, note="dated"),
                                       seeded_payment("p_b", "ada", "cy", 300, None, note="undated")])
    assert {h: me(tok)["balance"] for h, tok in t.items()} == BAL
    feed = {p["note"]: p for p in activity(t["ada"])}
    assert parse_ts(feed["dated"]["created_at"]) == T1
    undated = parse_ts(feed["undated"]["created_at"])
    assert before_reset <= undated <= now_utc() + datetime.timedelta(seconds=2)
    from pf import revisions
    for note in ("dated", "undated"):
        r1 = revisions(t["ada"], feed[note]["payment_id"])[0]
        assert parse_ts(r1["effective_at"]) == parse_ts(r1["recorded_at"]) == parse_ts(feed[note]["created_at"])
    p = pay(t["ada"], "bob", 1).json()
    assert parse_ts(p["created_at"]) >= undated
    order = [x["note"] for x in activity(t["ada"])]
    assert order.index("undated") < order.index("dated")


def test_seeded_future_created_at_rejected():
    """
    Spec: "A seeded `created_at` in the future gives `422 validation_failed` from `POST /_test/reset`, with no state change."
    """
    t = world2()
    pay(t["ada"], "bob", 10, note="before")
    before = snapshot(t)
    body = fixture(users=[user("ada", 100), user("bob", 0)],
                   payments=[seeded_payment("p_f", "ada", "bob", 1, now_utc() + datetime.timedelta(hours=2))])
    assert_error(CLIENT.post("/_test/reset", json=body), 422, "validation_failed")
    assert snapshot(t) == before


def test_activity_order_by_created_at():
    """
    Spec: "`GET /activity` retains its existing ordering by this field."
    """
    t, (T1, T2, T3), ids = history()
    notes = [p["note"] for p in activity(t["ada"])]
    assert notes.index("three") < notes.index("two") < notes.index("one")


# ---------- GET /me as_of ----------

def test_me_as_of_points_in_time():
    """
    Spec: "GET /me?as_of=2026-09-24T13:20:00%2B00:00"
    Spec: "With it, `balance` is the caller's balance as it stood at that instant: the balance after every payment of theirs with `created_at` at or before `as_of`, and before every payment after it."
    Spec: "A payment made at exactly `as_of` counts as having happened."
    Spec: "An `as_of` at or after the latest payment returns the current balance."
    Spec: "An `as_of` before the earliest payment returns the opening balance — what the wallet held before anything moved."
    Spec: "Users can request historical balances and paginated statements."
    """
    t, (T1, T2, T3), _ = history()
    sec = datetime.timedelta(seconds=1)
    cases = [(T1 - sec, 9600), (T1, 9100), (T1 + sec, 9100), (T2 - sec, 9100), (T2, 10300), (T3, 10000),
             (now_utc(), 10000), (now_utc() + datetime.timedelta(days=30), 10000)]
    for instant, expected in cases:
        m = me_at(t["ada"], as_of=iso(instant))
        assert m["balance"] == expected, (instant, m)
        assert m["total"] == m["balance"] and m["available"] == m["total"] - m["held"]
    assert me_at(t["bob"], as_of=iso(T1 - sec))["balance"] == 3150
    assert me_at(t["bob"], as_of=iso(T2))["balance"] == 3150 + 500 - 1200 + 50


def test_me_as_of_echo_and_offsets():
    """
    Spec: "The response carries `as_of` back, exactly as given."
    Spec: "`as_of` is optional and is an RFC 3339 instant with an offset."
    """
    t, (T1, T2, T3), _ = history()
    plus2 = T1.astimezone(datetime.timezone(datetime.timedelta(hours=2))).isoformat()
    z = T1.strftime("%Y-%m-%dT%H:%M:%SZ")
    for given in (iso(T1), plus2, z):
        m = me_at(t["ada"], as_of=given)
        assert m["as_of"] == given
        assert m["balance"] == 9100


def test_me_without_temporal_params_current():
    """
    Spec: "Without temporal query parameters the response retains the existing money fields and reports current corrected values."
    """
    t, _, _ = history()
    m = me(t["ada"])
    assert (m["balance"], m["total"], m["available"], m["held"]) == (10000, 10000, 10000, 0)


@pytest.mark.parametrize("bad", ["2026-09-24T13:20:00", "2026-09-24", "", "yesterday", "2026-13-40T00:00:00+00:00",
                                 "1727180400"])
def test_me_as_of_invalid(bad):
    """
    Spec: "Anything else — a naive local time, a bare date, an empty value — is 422 `validation_failed`."
    Spec: "Invalid/empty instants are 422."
    """
    t, _, _ = history()
    assert_error(get("/me", t["ada"], {"as_of": bad}), 422, "validation_failed")
    assert_error(get("/me", t["ada"], {"known_at": bad}), 422, "validation_failed")


# ---------- GET /statement ----------

def test_statement_full_default_window():
    """
    Spec: "GET /statement?from=<instant>&to=<instant>&limit=50&offset=0"
    Spec: "Both `from` and `to` are optional; `from` defaults to the opening of the wallet and `to` to now."
    Spec: "Returns the payments the caller sent or received in the half-open window `[from, to)`, **oldest first**, each with the caller's balance immediately after it:"
    Spec: "{ "opening_balance": 10000,"
    Spec: "Entries are ordered by `created_at` ascending, then payment `id` ascending for ties."
    Spec: "`opening_balance` plus all `delta` values in the full window must equal `closing_balance`."
    Spec: "A sent payment has a negative `delta`; a received payment has a positive `delta`."
    Spec: "This abbreviated example omits the revision fields and `snapshot` token described below."
    """
    t, (T1, T2, T3), ids = history()
    s = statement(t["ada"])
    assert s["opening_balance"] == 9600 and s["closing_balance"] == 10000 and s["has_more"] is False
    assert isinstance(s["snapshot"], str) and s["snapshot"]
    got = [(e["payment"]["note"], e["delta"], e["balance_after"]) for e in s["entries"]]
    assert got == [("one", -500, 9100), ("two", 1200, 10300), ("three", -300, 10000)]
    for e in s["entries"]:
        assert e["payment"]["payment_id"] in ids.values()
        assert e["revision"] == 1
        assert parse_ts(e["effective_at"]) == parse_ts(e["payment"]["created_at"])
        assert parse_ts(e["recorded_at"]) == parse_ts(e["payment"]["created_at"])
    assert s["opening_balance"] + sum(e["delta"] for e in s["entries"]) == s["closing_balance"]


def test_statement_only_own_payments():
    """
    Spec: "Only payments sent or received by the caller appear in their statement, including when other payments are public."
    Spec: "The activity-feed visibility rules do not apply to statements."
    """
    t, _, ids = history()
    assert "other" in {p["note"] for p in activity(t["ada"])}
    assert "other" not in {e["payment"]["note"] for e in statement(t["ada"])["entries"]}
    s = statement(t["cy"])
    assert [e["payment"]["note"] for e in s["entries"]] == ["three"]
    priv = pay(t["dan"], "cy", 7, note="private one", visibility="private").json()
    s = statement(t["cy"])
    assert [e["payment"]["note"] for e in s["entries"]] == ["three", "private one"]
    assert s["entries"][-1]["payment"]["payment_id"] == priv["payment_id"]
    assert s["closing_balance"] == 307


def test_statement_half_open_window():
    """
    Spec: "`opening_balance` is the balance immediately before `from`. `closing_balance` is the balance immediately before `to`."
    """
    t, (T1, T2, T3), _ = history()
    s = statement(t["ada"], **{"from": iso(T1), "to": iso(T3)})
    assert [e["payment"]["note"] for e in s["entries"]] == ["one", "two"]
    assert s["opening_balance"] == 9600 and s["closing_balance"] == 10300
    s = statement(t["ada"], **{"from": iso(T1 + datetime.timedelta(seconds=1)), "to": iso(T3 + datetime.timedelta(seconds=1))})
    assert [e["payment"]["note"] for e in s["entries"]] == ["two", "three"]
    assert s["opening_balance"] == 9100 and s["closing_balance"] == 10000
    s = statement(t["ada"], to=iso(T1))
    assert s["entries"] == [] and s["opening_balance"] == 9600 and s["closing_balance"] == 9600
    s = statement(t["ada"], **{"from": iso(now_utc() + datetime.timedelta(days=1)),
                               "to": iso(now_utc() + datetime.timedelta(days=2))})
    assert s["entries"] == [] and s["opening_balance"] == 10000 and s["closing_balance"] == 10000


def test_statement_pagination_stable_balances():
    """
    Spec: "Pagination must not change an entry's `balance_after` or the window's opening and closing balances. These values describe the full window regardless of `limit` and `offset`."
    Spec: "`limit` and `offset` behave exactly as in `GET /requests`."
    """
    t, _, _ = history()
    full = statement(t["ada"])
    for off in range(4):
        page = statement(t["ada"], limit=1, offset=off)
        assert page["opening_balance"] == 9600 and page["closing_balance"] == 10000
        if off < 3:
            assert len(page["entries"]) == 1
            e = page["entries"][0]
            assert (e["payment"]["payment_id"], e["delta"], e["balance_after"]) == \
                (full["entries"][off]["payment"]["payment_id"], full["entries"][off]["delta"],
                 full["entries"][off]["balance_after"])
            assert page["has_more"] is (off < 2)
        else:
            assert page["entries"] == [] and page["has_more"] is False
    page = statement(t["ada"], limit=2, offset=1)
    assert [e["balance_after"] for e in page["entries"]] == [10300, 10000] and page["has_more"] is False


def test_statement_ties_ordered_by_id():
    """
    Spec: "Entries are ordered by `created_at` ascending, then payment `id` ascending for ties."
    """
    T = ago(days=1)
    t = world2(balances={"ada": 1000, "bob": 1000, "op": 0}, payments=[
        seeded_payment("p_t3", "ada", "bob", 3, T, note="c"), seeded_payment("p_t1", "ada", "bob", 1, T, note="a"),
        seeded_payment("p_t2", "bob", "ada", 2, T, note="b")])
    s = statement(t["ada"])
    assert s["opening_balance"] == 1002
    ids = [e["payment"]["payment_id"] for e in s["entries"]]
    if len({len(i) for i in ids}) == 1:
        assert ids == sorted(ids)
    assert s["opening_balance"] + sum(e["delta"] for e in s["entries"]) == s["closing_balance"] == 1000
    assert [e["balance_after"] for e in s["entries"]][-1] == 1000


@pytest.mark.parametrize("params", [{"from": "2026-09-24"}, {"to": "2026-09-24T10:00:00"}, {"from": ""},
                                    {"known_at": "noon"}, {"limit": "0"}, {"limit": "201"}, {"offset": "-1"},
                                    {"limit": "4.0"}])
def test_statement_bad_query(params):
    """
    Spec: "`limit` and `offset` behave exactly as in `GET /requests`."
    Spec: "Invalid/empty instants are 422."
    """
    t, _, _ = history()
    assert_error(get("/statement", t["ada"], params), 422, "validation_failed")


def test_statement_requires_token():
    """
    Spec: "No token is 401."
    """
    history()
    assert_error(CLIENT.get("/statement"), 401, "unauthenticated")
    assert_error(CLIENT.get("/me", params={"as_of": iso(now_utc())}), 401, "unauthenticated")


def test_new_account_opens_at_zero():
    """
    Spec: "New accounts open at zero."
    """
    t, _, _ = history()
    s = CLIENT.post("/auth/signup", json={"email": "fresh@example.org", "password": "correct horse",
                                          "display_name": "F"})
    tok = s.json()["token"]
    pay(t["ada"], "fresh", 250, note="gift")
    st = statement(tok)
    assert st["opening_balance"] == 0 and st["closing_balance"] == 250
    assert [e["delta"] for e in st["entries"]] == [250]
    assert me_at(tok, as_of=iso(ago(days=10)))["balance"] == 0


def test_historical_sum_equals_seeded_total():
    """
    Spec: "The sum of balances must equal the seeded total in every historical view."
    """
    t, (T1, T2, T3), _ = history()
    sec = datetime.timedelta(seconds=1)
    for instant in (T1 - sec, T1, T2, T3, now_utc()):
        assert sum(me_at(tok, as_of=iso(instant))["balance"] for tok in t.values()) == sum(BAL.values())
