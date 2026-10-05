import datetime

import pytest

from pf import (CLIENT, activity, ago, assert_error, authorize_ok, balances, batch, batch_item, capture, correct,
                iso, key, me, me_at, now_utc, parse_ts, pay, payment_ids_by_note, post, refund, revisions,
                seeded_payment, simultaneously, snapshot, statement, world2)


def two_payments():
    t = world2()
    k1, k2 = key(), key()
    a = pay(t["ada"], "bob", 500, note="a", idem=k1).json()
    b = pay(t["dan"], "cy", 300, note="b", idem=k2).json()
    return t, a, b, k1, k2


def settled():
    t = world2()
    k = key()
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 300},
                          {"from_handle": "dan", "to_handle": "cy", "amount": 100}]}
    st = post("/settlements", t["op"], body, k).json()
    return t, st, body, k


def test_batch_commits():
    """
    Spec: "`POST /correction-batches` requires a settlement operator and an idempotency key, with the same 401/403 rules as settlements."
    Spec: "{"corrections": [{"payment_id": "p_a", "expected_revision": 1, "amount": 0,"
    Spec: "Return 201 with `correction_batch_id`, `recorded_at` and `revisions` in input order."
    Spec: "All new revisions share recorded_at, strictly later than the previous recorded_at of every member; each revision also exposes correction_batch_id."
    Spec: "Settlement operators can correct several payments in one request, including payments that belong to a settlement."
    Spec: "This adds one idempotent write path."
    """
    t, a, b, _, _ = two_payments()
    r = batch(t["op"], [batch_item(b["payment_id"], 0, b["created_at"], reason="reversal"),
                        batch_item(a["payment_id"], 400, a["created_at"])])
    assert r.status_code == 201, r.text
    body = r.json()
    revs = body["revisions"]
    assert [x["payment_id"] for x in revs] == [b["payment_id"], a["payment_id"]]
    assert [x["revision"] for x in revs] == [2, 2] and [x["amount"] for x in revs] == [0, 400]
    assert revs[0]["reason"] == "reversal"
    for x in revs:
        assert x["correction_batch_id"] == body["correction_batch_id"]
        assert parse_ts(x["recorded_at"]) == parse_ts(body["recorded_at"])
    assert parse_ts(body["recorded_at"]) > parse_ts(a["created_at"])
    assert parse_ts(body["recorded_at"]) > parse_ts(b["created_at"])
    assert balances(t) == {"ada": 9600, "bob": 2900, "cy": 0, "dan": 5000, "op": 0}
    stored = revisions(t["ada"], a["payment_id"])
    assert stored[-1] == revs[1]


def test_batch_permissions_and_key():
    """
    Spec: "`POST /correction-batches` requires a settlement operator and an idempotency key, with the same 401/403 rules as settlements."
    """
    t, a, b, _, _ = two_payments()
    items = [batch_item(a["payment_id"], 400, a["created_at"])]
    before = snapshot(t)
    assert_error(batch(None, items), 401, "unauthenticated")
    assert_error(batch(t["ada"], items), 403, "forbidden")
    assert_error(post("/correction-batches", t["op"], {"corrections": items}), 400, "missing_idempotency_key")
    assert snapshot(t) == before


def test_batch_validation():
    """
    Spec: "corrections contains 1..32 objects with distinct payment_ids, else 422 `validation_failed`."
    Spec: "Every item has the ordinary correction fields and validation."
    Spec: "Effective times cannot be later than now."
    """
    t, a, b, _, _ = two_payments()
    ok = batch_item(a["payment_id"], 400, a["created_at"])
    before = snapshot(t)
    future = batch_item(a["payment_id"], 400, now_utc() + datetime.timedelta(hours=1))
    no_reason = dict(ok)
    no_reason.pop("reason")
    for items in ([], [ok, dict(ok)], [future], [no_reason], [dict(ok, amount=-1)], [dict(ok, reason="")],
                  [dict(ok, expected_revision=0)], [dict(ok, effective_at="2026-09-20")]):
        assert_error(batch(t["op"], items), 422, "validation_failed")
    assert_error(post("/correction-batches", t["op"], {}, key()), 422, "validation_failed")
    pids = [pay(t["ada"], "cy", 1, note=f"n{i}").json() for i in range(33)]
    many = [batch_item(p["payment_id"], 0, p["created_at"]) for p in pids]
    assert_error(batch(t["op"], many), 422, "validation_failed")
    assert batch(t["op"], many[:32]).status_code == 201


def test_batch_unknown_and_stale():
    """
    Spec: "Unknown payment is 404; a stale expected revision is 409 `stale_revision`."
    Spec: "Unknown fields are ignored."
    """
    t, a, b, _, _ = two_payments()
    before = snapshot(t)
    assert_error(batch(t["op"], [batch_item("p_nope", 1, a["created_at"])]), 404, "not_found")
    assert_error(batch(t["op"], [batch_item(a["payment_id"], 1, a["created_at"], expected_revision=2)]), 409,
                 "stale_revision")
    assert snapshot(t) == before
    item = dict(batch_item(a["payment_id"], 450, a["created_at"]), colour="red")
    r = batch(t["op"], [item], extra={"note": "ignored"})
    assert r.status_code == 201, r.text


def test_batch_immutable_targets():
    """
    Spec: "The operator may correct ordinary, request and settlement payments, but captures and refunds remain immutable."
    """
    t, a, b, _, _ = two_payments()
    au = authorize_ok(t["ada"], "bob", 100)
    cp = capture(t["bob"], au["authorization_id"]).json()
    f = refund(t["bob"], a["payment_id"], 100).json()
    before = snapshot(t)
    assert_error(batch(t["op"], [batch_item(cp["payment_id"], 50, cp["created_at"])]), 422,
                 "linked_payment_immutable")
    assert_error(batch(t["op"], [batch_item(f["payment_id"], 50, f["created_at"])]), 422, "linked_payment_immutable")
    assert_error(batch(t["op"], [batch_item(a["payment_id"], 99, a["created_at"])]), 422, "refund_exceeds_payment")
    assert snapshot(t) == before


def test_batch_settlement_completeness():
    """
    Spec: "Correcting any settlement member requires including every member of that settlement, else 422 `incomplete_settlement`."
    Spec: "Members of one settlement must have identical effective instants (offset spellings may differ), else 422 `validation_failed`."
    Spec: "Ordinary single-payment corrections remain available for nonmembers."
    """
    t, st, body, k = settled()
    m1, m2 = st["payments"]
    C = parse_ts(st["committed_at"])
    before = snapshot(t)
    assert_error(batch(t["op"], [batch_item(m1["payment_id"], 200, C)]), 422, "incomplete_settlement")
    assert_error(batch(t["op"], [batch_item(m1["payment_id"], 200, C),
                                 batch_item(m2["payment_id"], 50, C - datetime.timedelta(minutes=1))]),
                 422, "validation_failed")
    assert snapshot(t) == before
    plus2 = C.astimezone(datetime.timezone(datetime.timedelta(hours=2))).isoformat()
    r = batch(t["op"], [batch_item(m1["payment_id"], 200, iso(C)), batch_item(m2["payment_id"], 50, plus2)])
    assert r.status_code == 201, r.text
    assert balances(t) == {"ada": 9800, "bob": 2700, "cy": 50, "dan": 4950, "op": 0}
    other = pay(t["ada"], "bob", 10).json()
    assert correct(t["ada"], other["payment_id"], 5, other["created_at"]).status_code == 201


def test_batch_receipts_and_snapshots_unchanged():
    """
    Spec: "Original payments and receipts never change."
    Spec: "Original payment and settlement retries return their original bodies."
    Spec: "New statements reflect the new revisions; earlier snapshot tokens continue to page their frozen entries."
    """
    t, st, body, k = settled()
    pk = key()
    p = pay(t["ada"], "cy", 70, note="plain", idem=pk).json()
    old = statement(t["ada"])
    C = st["committed_at"]
    r = batch(t["op"], [batch_item(st["payments"][0]["payment_id"], 0, C),
                        batch_item(st["payments"][1]["payment_id"], 0, C),
                        batch_item(p["payment_id"], 7, p["created_at"])])
    assert r.status_code == 201, r.text
    again = post("/settlements", t["op"], body, k)
    assert again.status_code == 200 and again.json() == st
    again = pay(t["ada"], "cy", 70, note="plain", idem=pk)
    assert again.status_code == 200 and again.json() == p
    feed = {x["payment_id"]: x for x in activity(t["ada"])}
    assert feed[st["payments"][0]["payment_id"]] == st["payments"][0]
    frozen = statement(t["ada"], snapshot=old["snapshot"])
    assert frozen["entries"] == old["entries"] and frozen["closing_balance"] == old["closing_balance"]
    new = statement(t["ada"])
    amounts = {e["payment"]["payment_id"]: (e["payment"]["amount"], e["delta"]) for e in new["entries"]}
    assert amounts[st["payments"][0]["payment_id"]] == (0, 0)
    assert amounts[p["payment_id"]] == (7, -7)
    assert new["closing_balance"] == 10000 - 7


def test_batch_replay():
    """
    Spec: "Replays return the original batch response with 200."
    """
    t, a, b, _, _ = two_payments()
    k = key()
    items = [batch_item(a["payment_id"], 400, a["created_at"])]
    first = batch(t["op"], items, idem=k)
    assert first.status_code == 201
    assert correct(t["ada"], a["payment_id"], 300, a["created_at"], expected_revision=2).status_code == 201
    after = snapshot(t)
    again = batch(t["op"], items, idem=k)
    assert again.status_code == 200 and again.json() == first.json()
    assert_error(batch(t["op"], [batch_item(a["payment_id"], 401, a["created_at"])], idem=k), 409,
                 "idempotency_key_reuse")
    assert snapshot(t) == after


def combined_world():
    T0, T1, T2, T3 = ago(days=4), ago(days=3), ago(days=2), ago(days=1)
    t = world2(balances={"ada": 9000, "bob": 100, "cy": 2900, "dan": 3000, "op": 0}, payments=[
        seeded_payment("p_c0", "dan", "cy", 2000, T0, note="seed cy"),
        seeded_payment("p_c1", "ada", "bob", 1000, T1, note="A"),
        seeded_payment("p_c2", "bob", "cy", 1000, T2, note="spend"),
        seeded_payment("p_c3", "cy", "bob", 100, T3, note="B")])
    ids = payment_ids_by_note(t["bob"]) | payment_ids_by_note(t["cy"])
    return t, (T0, T1, T2, T3), ids


def test_batch_combined_affordability():
    """
    Spec: "Affordability is determined by the combined effect of all proposed revisions."
    """
    t, (T0, T1, T2, T3), ids = combined_world()
    assert_error(correct(t["ada"], ids["A"], 0, iso(T1)), 409, "insufficient_funds")
    r = batch(t["op"], [batch_item(ids["A"], 0, T1), batch_item(ids["B"], 1100, T1)])
    assert r.status_code == 201, r.text
    assert balances(t) == {"ada": 10000, "bob": 100, "cy": 1900, "dan": 3000, "op": 0}
    assert me_at(t["bob"], as_of=iso(T2))["balance"] == 100


def test_batch_error_precedence():
    """
    Spec: "Error precedence is: item errors in input order, settlement completeness, resulting current available funds, then historical total and available funds at every effective/event boundary."
    Spec: "The existing codes apply: `linked_payment_immutable`, `refund_exceeds_payment`, `insufficient_funds`, `historical_overdraft`."
    Spec: "A rejected batch leaves history, balances and idempotency records unchanged."
    """
    t, (T0, T1, T2, T3), ids = combined_world()
    st = post("/settlements", t["op"], {"transfers": [{"from_handle": "dan", "to_handle": "ada", "amount": 10},
                                                       {"from_handle": "ada", "to_handle": "cy", "amount": 5}]},
              key()).json()
    m1 = st["payments"][0]
    C = st["committed_at"]
    au = authorize_ok(t["ada"], "dan", 10)
    cp = capture(t["dan"], au["authorization_id"]).json()
    before = snapshot(t)
    k = key()

    def check(items, status, code):
        assert_error(batch(t["op"], items, idem=k), status, code)

    check([batch_item("p_missing", 0, T1), batch_item(cp["payment_id"], 1, cp["created_at"])], 404, "not_found")
    check([batch_item(cp["payment_id"], 1, cp["created_at"]), batch_item("p_missing", 0, T1)], 422,
          "linked_payment_immutable")
    check([batch_item(m1["payment_id"], 1, C), batch_item("p_missing", 0, T1)], 404, "not_found")
    check([batch_item(m1["payment_id"], 1, C), batch_item(ids["A"], 0, T1, expected_revision=5)], 409,
          "stale_revision")
    check([batch_item(m1["payment_id"], 1000000000, C)], 422, "incomplete_settlement")
    check([batch_item(ids["A"], 0, T1), batch_item(ids["spend"], 1000, T3)], 409, "insufficient_funds")
    check([batch_item(ids["A"], 1000, T3)], 409, "historical_overdraft")
    assert snapshot(t) == before
    r = batch(t["op"], [batch_item(ids["A"], 1000, T1 - datetime.timedelta(hours=1))], idem=k)
    assert r.status_code == 201, r.text


def test_batch_historical_overdraft():
    """
    Spec: "Error precedence is: item errors in input order, settlement completeness, resulting current available funds, then historical total and available funds at every effective/event boundary."
    """
    t, (T0, T1, T2, T3), ids = combined_world()
    before = snapshot(t)
    assert_error(batch(t["op"], [batch_item(ids["spend"], 1000, T0 - datetime.timedelta(days=1))]), 409,
                 "historical_overdraft")
    assert snapshot(t) == before
    assert len(revisions(t["bob"], ids["spend"])) == 1


def test_concurrent_batch_and_single_correction():
    """
    Spec: "Concurrent corrections sharing any expected payment revision cannot both succeed."
    """
    t, a, b, _, _ = two_payments()
    results = simultaneously([
        lambda c: post("/correction-batches", t["op"],
                       {"corrections": [batch_item(a["payment_id"], 400, a["created_at"]),
                                        batch_item(b["payment_id"], 200, b["created_at"])]}, key(), client=c),
        lambda c: post(f"/payments/{a['payment_id']}/corrections", t["ada"],
                       {"expected_revision": 1, "amount": 300, "effective_at": a["created_at"], "reason": "x"},
                       key(), client=c),
        lambda c: post("/correction-batches", t["op"],
                       {"corrections": [batch_item(b["payment_id"], 100, b["created_at"])]}, key(), client=c),
    ])
    ok = [r for r in results if r.status_code == 201]
    for r in results:
        if r.status_code != 201:
            assert_error(r, 409, "stale_revision")
    revs_a = revisions(t["ada"], a["payment_id"])
    revs_b = revisions(t["dan"], b["payment_id"])
    assert len(revs_a) <= 2 and len(revs_b) <= 2
    if results[0].status_code == 201:
        assert len(ok) == 1
    else:
        assert results[1].status_code == 201 or results[2].status_code == 201
    total = sum(balances(t).values())
    assert total == 17500
