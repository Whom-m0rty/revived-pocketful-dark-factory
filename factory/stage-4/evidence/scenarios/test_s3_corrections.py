import datetime

import pytest

from pf import (CLIENT, activity, assert_error, ago, balances, correct, get, iso, is_ts, key, me, me_at, now_utc,
                parse_ts, pay, payment_ids_by_note, post, revisions, seeded_payment, simultaneously, snapshot,
                statement, world2)


def fresh_payment(amount=500):
    t = world2()
    k = key()
    r = pay(t["ada"], "bob", amount, note="dinner", visibility="public", idem=k)
    assert r.status_code == 201
    return t, r.json(), k


def test_correction_decrease():
    """
    Spec: "`POST /payments/{payment_id}/corrections` requires an idempotency key and the original sender."
    Spec: "{"expected_revision": 1, "amount": 400,"
    Spec: "It appends an immutable revision, returning 201 with `payment_id`, `revision`, `amount`, `effective_at`, server-assigned `recorded_at`, and `reason`."
    Spec: "The difference from the previous amount moves between the **same two wallets** in the same atomic step."
    Spec: "Increasing the amount debits the original sender; decreasing it debits the original receiver."
    Spec: "Senders can correct eligible payments while preserving the original receipt."
    """
    t, p, _ = fresh_payment()
    eff = p["created_at"]
    r = correct(t["ada"], p["payment_id"], 400, eff, reason="corrected amount")
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["payment_id"] == p["payment_id"] and c["revision"] == 2 and c["amount"] == 400
    assert parse_ts(c["effective_at"]) == parse_ts(eff) and c["reason"] == "corrected amount"
    assert is_ts(c["recorded_at"]) and parse_ts(c["recorded_at"]) >= parse_ts(p["created_at"])
    b = balances(t)
    assert b["ada"] == 9600 and b["bob"] == 2900
    assert sum(b.values()) == 17500


def test_correction_increase_and_zero():
    """
    Spec: "Revision is a positive integer; amount is an integer 0..1000000000 (zero reverses the entire payment); reason is a string of 1..200 characters; effective time is an RFC 3339 instant not later than now."
    Spec: "Recorded times for one payment strictly increase."
    """
    t, p, _ = fresh_payment()
    r = correct(t["ada"], p["payment_id"], 700, p["created_at"])
    assert r.status_code == 201, r.text
    assert balances(t)["ada"] == 9300 and balances(t)["bob"] == 3200
    r2 = correct(t["ada"], p["payment_id"], 0, p["created_at"], expected_revision=2, reason="reverse")
    assert r2.status_code == 201, r2.text
    assert r2.json()["revision"] == 3 and r2.json()["amount"] == 0
    assert balances(t)["ada"] == 10000 and balances(t)["bob"] == 2500
    revs = revisions(t["ada"], p["payment_id"])
    stamps = [parse_ts(x["recorded_at"]) for x in revs]
    assert all(a < b for a, b in zip(stamps, stamps[1:])), stamps


def test_revisions_history():
    """
    Spec: "`GET /payments/{payment_id}/revisions` returns `{"revisions": [...]}` in revision order, including revision 1 (`reason: ""`)."
    Spec: "Every payment has a revision history."
    Spec: "Revision 1 has `amount` as originally paid and `effective_at = recorded_at = created_at`."
    Spec: "Correction changes neither parties nor visibility."
    """
    t, p, _ = fresh_payment()
    eff = iso(parse_ts(p["created_at"]) - datetime.timedelta(hours=5))
    c = correct(t["ada"], p["payment_id"], 450, eff, reason="earlier").json()
    for who in ("ada", "bob"):
        revs = revisions(t[who], p["payment_id"])
        assert [x["revision"] for x in revs] == [1, 2]
        r1, r2 = revs
        assert r1["amount"] == 500 and r1["reason"] == "" and r1["payment_id"] == p["payment_id"]
        assert parse_ts(r1["effective_at"]) == parse_ts(r1["recorded_at"]) == parse_ts(p["created_at"])
        assert r2 == c
    feed = {x["payment_id"]: x for x in activity(t["bob"])}
    assert feed[p["payment_id"]]["visibility"] == "public"
    assert feed[p["payment_id"]]["from_handle"] == "ada" and feed[p["payment_id"]]["to_handle"] == "bob"


def test_revisions_access():
    """
    Spec: "Only the two parties can read it; a third party gets 404 even for a public payment."
    Spec: "No token is 401."
    """
    t, p, _ = fresh_payment()
    assert_error(get(f"/payments/{p['payment_id']}/revisions", t["cy"]), 404, "not_found")
    assert_error(get(f"/payments/{p['payment_id']}/revisions", t["op"]), 404, "not_found")
    assert_error(get("/payments/p_nope/revisions", t["ada"]), 404, "not_found")
    assert_error(CLIENT.get(f"/payments/{p['payment_id']}/revisions"), 401, "unauthenticated")


def test_activity_and_receipts_unchanged():
    """
    Spec: "The original payment and every original idempotent response remain unchanged."
    Spec: "`GET /activity` continues to display the original payment; correction records are not new feed payments."
    """
    t, p, k = fresh_payment()
    assert correct(t["ada"], p["payment_id"], 100, p["created_at"]).status_code == 201
    for who in ("ada", "bob", "cy"):
        feed = activity(t[who])
        assert len(feed) == 1 and feed[0] == p
    again = pay(t["ada"], "bob", 500, note="dinner", visibility="public", idem=k)
    assert again.status_code == 200 and again.json() == p
    assert balances(t)["ada"] == 9900


def test_correction_permissions():
    """
    Spec: "An authenticated non-sender gets 403 `forbidden`; unknown payment gets 404."
    """
    t, p, _ = fresh_payment()
    before = snapshot(t)
    assert_error(correct(t["bob"], p["payment_id"], 400, p["created_at"]), 403, "forbidden")
    assert_error(correct(t["cy"], p["payment_id"], 400, p["created_at"]), 403, "forbidden")
    assert_error(correct(t["ada"], "p_unknown", 400, p["created_at"]), 404, "not_found")
    r = CLIENT.post(f"/payments/{p['payment_id']}/corrections", headers={"Idempotency-Key": key()},
                    json={"expected_revision": 1, "amount": 1, "effective_at": p["created_at"], "reason": "x"})
    assert_error(r, 401, "unauthenticated")
    assert snapshot(t) == before
    assert len(revisions(t["ada"], p["payment_id"])) == 1


def test_correction_requires_key():
    """
    Spec: "`POST /payments/{payment_id}/corrections` requires an idempotency key and the original sender."
    """
    t, p, _ = fresh_payment()
    body = {"expected_revision": 1, "amount": 400, "effective_at": p["created_at"], "reason": "x"}
    assert_error(post(f"/payments/{p['payment_id']}/corrections", t["ada"], body), 400, "missing_idempotency_key")
    assert_error(post(f"/payments/{p['payment_id']}/corrections", t["ada"], body, idem=""), 400,
                 "missing_idempotency_key")
    assert len(revisions(t["ada"], p["payment_id"])) == 1


@pytest.mark.parametrize("change", [
    {"expected_revision": None}, {"amount": None}, {"effective_at": None}, {"reason": None},
    {"expected_revision": 0}, {"expected_revision": -1}, {"expected_revision": 1.5},
    {"amount": -1}, {"amount": 1000000001}, {"amount": 2.5},
    {"reason": ""}, {"reason": "r" * 201},
    {"effective_at": "2026-09-20T12:00:00"}, {"effective_at": "2026-09-20"}, {"effective_at": "soon"},
    {"effective_at": "FUTURE"}])
def test_correction_validation(change):
    """
    Spec: "All fields are required."
    Spec: "Invalid input is 422 `validation_failed`."
    Spec: "Revision is a positive integer; amount is an integer 0..1000000000 (zero reverses the entire payment); reason is a string of 1..200 characters; effective time is an RFC 3339 instant not later than now."
    """
    t, p, _ = fresh_payment()
    body = {"expected_revision": 1, "amount": 400, "effective_at": p["created_at"], "reason": "x"}
    for k, v in change.items():
        if v is None:
            body.pop(k)
        elif v == "FUTURE":
            body[k] = iso(now_utc() + datetime.timedelta(hours=1))
        else:
            body[k] = v
    before = snapshot(t)
    assert_error(post(f"/payments/{p['payment_id']}/corrections", t["ada"], body, key()), 422, "validation_failed")
    assert snapshot(t) == before
    assert len(revisions(t["ada"], p["payment_id"])) == 1


def test_correction_bounds_ok():
    """
    Spec: "Revision is a positive integer; amount is an integer 0..1000000000 (zero reverses the entire payment); reason is a string of 1..200 characters; effective time is an RFC 3339 instant not later than now."
    """
    t = world2(balances={"ada": 2000000000, "bob": 0, "op": 0})
    p = pay(t["ada"], "bob", 5).json()
    r = correct(t["ada"], p["payment_id"], 1000000000, p["created_at"], reason="r" * 200)
    assert r.status_code == 201, r.text
    r = correct(t["ada"], p["payment_id"], 1, iso(ago(days=400)), expected_revision=2, reason="x")
    assert r.status_code == 201, r.text
    assert me(t["bob"])["balance"] == 1


def test_stale_revision():
    """
    Spec: "A stale expected revision gives 409 `stale_revision`."
    """
    t, p, _ = fresh_payment()
    assert correct(t["ada"], p["payment_id"], 400, p["created_at"]).status_code == 201
    before = snapshot(t)
    assert_error(correct(t["ada"], p["payment_id"], 300, p["created_at"], expected_revision=1), 409, "stale_revision")
    assert_error(correct(t["ada"], p["payment_id"], 300, p["created_at"], expected_revision=3), 409, "stale_revision")
    assert snapshot(t) == before
    assert len(revisions(t["ada"], p["payment_id"])) == 2


def test_correction_replay():
    """
    Spec: "Successful replay returns that original revision with 200 even after newer revisions."
    Spec: "Different body with the same key is 409 `idempotency_key_reuse`."
    """
    t, p, _ = fresh_payment()
    k = key()
    first = correct(t["ada"], p["payment_id"], 400, p["created_at"], idem=k)
    assert first.status_code == 201
    assert correct(t["ada"], p["payment_id"], 300, p["created_at"], expected_revision=2).status_code == 201
    after = snapshot(t)
    again = correct(t["ada"], p["payment_id"], 400, p["created_at"], idem=k)
    assert again.status_code == 200 and again.json() == first.json()
    assert_error(correct(t["ada"], p["payment_id"], 401, p["created_at"], idem=k), 409, "idempotency_key_reuse")
    assert snapshot(t) == after
    assert len(revisions(t["ada"], p["payment_id"])) == 3


def test_correction_insufficient_funds():
    """
    Spec: "A currently unaffordable debit gives 409 `insufficient_funds`."
    Spec: "Either failure preserves balances, revision history, statements and idempotency state."
    """
    t = world2(balances={"ada": 1000, "bob": 0, "cy": 0, "op": 0})
    p = pay(t["ada"], "bob", 600).json()
    k = key()
    assert_error(correct(t["ada"], p["payment_id"], 1001, p["created_at"], idem=k), 409, "insufficient_funds")
    assert pay(t["bob"], "cy", 500).status_code == 201
    before = snapshot(t)
    stmt = {h: statement(tok)["entries"] for h, tok in t.items()}
    assert_error(correct(t["ada"], p["payment_id"], 0, p["created_at"]), 409, "insufficient_funds")
    assert snapshot(t) == before
    assert {h: statement(tok)["entries"] for h, tok in t.items()} == stmt
    assert len(revisions(t["ada"], p["payment_id"])) == 1
    r = correct(t["ada"], p["payment_id"], 1000, p["created_at"], idem=k)
    assert r.status_code == 201, r.text


def overdraft_world():
    T1, T2, T3 = ago(days=3), ago(days=2), ago(days=1)
    t = world2(balances={"ada": 9000, "cy": 1100, "dan": 4900, "op": 0}, payments=[
        seeded_payment("p_o1", "ada", "cy", 1000, T1, note="in"),
        seeded_payment("p_o2", "cy", "dan", 900, T2, note="out"),
        seeded_payment("p_o3", "dan", "cy", 1000, T3, note="later")])
    return t, (T1, T2, T3), payment_ids_by_note(t["cy"])


def test_historical_overdraft_amount():
    """
    Spec: "Otherwise, if any user's corrected balance is negative at any effective-time boundary, return 409 `historical_overdraft`."
    Spec: "Either failure preserves balances, revision history, statements and idempotency state."
    Spec: "Opening balances equal seeded ending balances minus the net effect of original seeded payments."
    Spec: "Seeded history is consistent and nonnegative."
    """
    t, (T1, T2, T3), ids = overdraft_world()
    assert statement(t["cy"])["opening_balance"] == 0
    before = snapshot(t)
    stmt = statement(t["cy"])["entries"]
    k = key()
    assert_error(correct(t["ada"], ids["in"], 500, iso(T1), idem=k), 409, "historical_overdraft")
    assert snapshot(t) == before
    assert statement(t["cy"])["entries"] == stmt
    assert len(revisions(t["ada"], ids["in"])) == 1
    r = correct(t["ada"], ids["in"], 900, iso(T1), idem=k)
    assert r.status_code == 201, r.text
    assert me(t["cy"])["balance"] == 1000


def test_historical_overdraft_moved_effective_time():
    """
    Spec: "A correction may move a payment into or out of a statement window."
    """
    t, (T1, T2, T3), ids = overdraft_world()
    assert_error(correct(t["ada"], ids["in"], 1000, iso(T2 + datetime.timedelta(hours=1))), 409,
                 "historical_overdraft")
    r = correct(t["ada"], ids["in"], 1000, iso(T1 - datetime.timedelta(days=1)))
    assert r.status_code == 201, r.text
    s = statement(t["cy"], **{"from": iso(T1 - datetime.timedelta(hours=1))})
    assert [e["payment"]["note"] for e in s["entries"]] == ["out", "later"]
    s = statement(t["cy"])
    assert [e["payment"]["note"] for e in s["entries"]] == ["in", "out", "later"]


def test_boundary_combines_same_instant():
    """
    Spec: "Balances at a boundary include the combined effect of all movements at that instant."
    """
    t, (T1, T2, T3), ids = overdraft_world()
    r = correct(t["ada"], ids["in"], 1000, iso(T2))
    assert r.status_code == 201, r.text
    assert me_at(t["cy"], as_of=iso(T2))["balance"] == 100
    assert me_at(t["cy"], as_of=iso(T2 - datetime.timedelta(seconds=1)))["balance"] == 0


def test_corrected_history_views():
    """
    Spec: "Corrections must not change those opening balances."
    Spec: "The sum of balances must equal the seeded total in every historical view."
    Spec: "Historical queries must support both the effective date of a payment and the information available at a specified time."
    """
    t, (T1, T2, T3), ids = overdraft_world()
    assert correct(t["ada"], ids["in"], 950, iso(T1 + datetime.timedelta(hours=1))).status_code == 201
    assert statement(t["cy"])["opening_balance"] == 0 and statement(t["ada"])["opening_balance"] == 10000
    sec = datetime.timedelta(seconds=1)
    assert me_at(t["cy"], as_of=iso(T1))["balance"] == 0
    assert me_at(t["cy"], as_of=iso(T1 + datetime.timedelta(hours=1)))["balance"] == 950
    assert me_at(t["cy"], as_of=iso(T1 + datetime.timedelta(hours=1)), known_at=iso(T3))["balance"] == 1000
    assert me_at(t["cy"], as_of=iso(T1), known_at=iso(T3))["balance"] == 1000
    assert me_at(t["cy"], as_of=iso(T1 + datetime.timedelta(hours=1)), known_at=iso(T1 - sec))["balance"] == 0
    for instant in (T1 - sec, T1, T2, T3, now_utc()):
        for known in (None, iso(T2), iso(now_utc() + datetime.timedelta(days=1))):
            params = {"as_of": iso(instant)}
            if known:
                params["known_at"] = known
            assert sum(me_at(tok, **params)["balance"] for tok in t.values()) == 15000


def test_concurrent_corrections_one_wins():
    """
    Spec: "Concurrent corrections using the same expected revision cannot both succeed."
    """
    t, p, _ = fresh_payment()
    results = simultaneously([
        lambda c, a=a: post(f"/payments/{p['payment_id']}/corrections", t["ada"],
                            {"expected_revision": 1, "amount": a, "effective_at": p["created_at"], "reason": "race"},
                            key(), client=c) for a in (100, 200, 300, 400, 450, 499)])
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [409] * 5, [(r.status_code, r.text) for r in results]
    for r in results:
        if r.status_code == 409:
            assert_error(r, 409, "stale_revision")
    win = [r for r in results if r.status_code == 201][0].json()
    assert balances(t)["bob"] == 2500 + win["amount"]
    assert len(revisions(t["ada"], p["payment_id"])) == 2
