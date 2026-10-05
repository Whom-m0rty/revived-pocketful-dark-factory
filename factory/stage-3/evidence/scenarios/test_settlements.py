import pytest

from pf import (activity, ask_ok, assert_error, balances, check_payment, is_id, is_ts, key, parse_ts, pay, post,
                requests_of, snapshot, world)


def settle(token, transfers, idem=None, extra=None):
    body = {"transfers": transfers}
    body.update(extra or {})
    return post("/settlements", token, body, idem or key())


def tr(a, b, amount, **kw):
    d = {"from_handle": a, "to_handle": b, "amount": amount}
    d.update(kw)
    return d


def test_settlement_commits():
    """
    Spec: "{"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100},"
    Spec: "Return 201 with `settlement_id`, `committed_at` and `payments` in input order."
    Spec: "Every member is an ordinary payment with `settlement_id` linking the batch; nonmembers expose null for that field."
    Spec: "Members have null request_id and the same server-assigned created_at, equal to committed_at."
    Spec: "The settlement response contains every member's receipt."
    Spec: "An operator may execute a settlement across any wallets."
    Spec: "Authorized operators can submit groups of transfers as settlements."
    Spec: "`POST /settlements` requires an operator and an idempotency key."
    Spec: "This is the fifth idempotent write path in stage 1."
    """
    t = world()
    r = settle(t["op"], [tr("ada", "bob", 100), tr("bob", "cy", 50)])
    assert r.status_code == 201, r.text
    s = r.json()
    assert is_id(s["settlement_id"]) and is_ts(s["committed_at"])
    ps = s["payments"]
    assert [(p["from_handle"], p["to_handle"], p["amount"]) for p in ps] == [("ada", "bob", 100), ("bob", "cy", 50)]
    for p in ps:
        check_payment(p)
        assert p["settlement_id"] == s["settlement_id"]
        assert p["request_id"] is None
        assert parse_ts(p["created_at"]) == parse_ts(s["committed_at"])
        assert p["currency"] == "EUR"
    assert len({p["payment_id"] for p in ps}) == 2
    assert balances(t) == {"ada": 9900, "bob": 2550, "cy": 50, "dan": 5000, "op": 0}
    feed = {p["payment_id"]: p for p in activity(t["dan"])}
    for p in ps:
        assert feed[p["payment_id"]]["settlement_id"] == s["settlement_id"]


def test_settlement_member_defaults():
    """
    Spec: "Each uses ordinary payment amount, note and visibility rules (defaults: empty note, public)."
    """
    t = world()
    r = settle(t["op"], [tr("ada", "bob", 1), tr("ada", "cy", 2, note="memo ✓", visibility="private")])
    assert r.status_code == 201, r.text
    a, b = r.json()["payments"]
    assert (a["note"], a["visibility"]) == ("", "public")
    assert (b["note"], b["visibility"]) == ("memo ✓", "private")


def test_settlement_net_affordability():
    """
    Spec: "A settlement is affordable when every wallet's balance after all incoming and outgoing transfers is nonnegative."
    """
    t = world()
    r = settle(t["op"], [tr("cy", "dan", 300), tr("bob", "cy", 300)])
    assert r.status_code == 201, r.text
    assert balances(t)["cy"] == 0 and balances(t)["bob"] == 2200 and balances(t)["dan"] == 5300
    r = settle(t["op"], [tr("cy", "ada", 2200), tr("bob", "cy", 2200)])
    assert r.status_code == 201, r.text
    assert balances(t)["bob"] == 0


def test_settlement_insufficient_collective():
    """
    Spec: "Insufficient collective funds gives 409 `insufficient_funds`."
    Spec: "Either all movements commit together or none do; failed validation claims no idempotency key and creates no payment or revision."
    """
    t = world()
    before = snapshot(t)
    assert_error(settle(t["op"], [tr("ada", "cy", 100), tr("cy", "dan", 101)]), 409, "insufficient_funds")
    assert_error(settle(t["op"], [tr("ada", "bob", 1), tr("bob", "dan", 2502)]), 409, "insufficient_funds")
    assert_error(settle(t["op"], [tr("bob", "ada", 2000), tr("bob", "dan", 501)]), 409, "insufficient_funds")
    assert snapshot(t) == before


def test_settlement_permissions():
    """
    Spec: "No token gives 401; authenticated non-operator gives 403 `forbidden`."
    """
    t = world()
    before = snapshot(t)
    assert_error(settle(None, [tr("ada", "bob", 1)]), 401, "unauthenticated")
    assert_error(settle(t["ada"], [tr("ada", "bob", 1)]), 403, "forbidden")
    assert_error(settle(t["bob"], [tr("ada", "bob", 1)]), 403, "forbidden")
    assert snapshot(t) == before


def test_operator_no_extra_visibility():
    """
    Spec: "This permission does not grant access to another user's requests or private activity items."
    Spec: "Constituents follow ordinary activity-feed visibility."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 10)
    priv = pay(t["ada"], "bob", 10, visibility="private").json()
    r = settle(t["op"], [tr("ada", "bob", 5, visibility="private"), tr("dan", "cy", 6)])
    assert r.status_code == 201, r.text
    member_priv, member_pub = r.json()["payments"]
    op_feed = {p["payment_id"] for p in activity(t["op"])}
    assert priv["payment_id"] not in op_feed and member_priv["payment_id"] not in op_feed
    assert member_pub["payment_id"] in op_feed
    assert q["request_id"] not in {x["request_id"] for x in requests_of(t["op"])}
    assert requests_of(t["op"]) == []
    cy_feed = {p["payment_id"] for p in activity(t["cy"])}
    assert member_priv["payment_id"] not in cy_feed and member_pub["payment_id"] in cy_feed
    for who in ("ada", "bob"):
        assert member_priv["payment_id"] in {p["payment_id"] for p in activity(t[who])}


def test_settlement_entry_errors():
    """
    Spec: "Unknown handle is 404; self-transfer is 422 `self_payment`; malformed batch shape is 422 `validation_failed`."
    Spec: "transfers contains 1..32 objects."
    """
    t = world()
    before = snapshot(t)
    assert_error(settle(t["op"], [tr("ada", "ghost", 1)]), 404, "not_found")
    assert_error(settle(t["op"], [tr("ghost", "ada", 1)]), 404, "not_found")
    assert_error(settle(t["op"], [tr("ada", "ada", 1)]), 422, "self_payment")
    assert_error(settle(t["op"], []), 422, "validation_failed")
    assert_error(settle(t["op"], [tr("ada", "bob", 1)] * 33), 422, "validation_failed")
    assert_error(post("/settlements", t["op"], {}, key()), 422, "validation_failed")
    assert snapshot(t) == before
    r = settle(t["op"], [tr("ada", "bob", 1)] * 32)
    assert r.status_code == 201, r.text
    assert len(r.json()["payments"]) == 32
    assert balances(t)["bob"] == 2532


@pytest.mark.parametrize("entry", [{"amount": 0}, {"amount": -1}, {"amount": 1000000001}, {"amount": 1.5},
                                   {"amount": "5"}, {"amount": True}, {"note": "n" * 201}, {"note": None},
                                   {"visibility": "friends"}, {"visibility": None}])
def test_settlement_entry_field_rules(entry):
    """
    Spec: "Each uses ordinary payment amount, note and visibility rules (defaults: empty note, public)."
    """
    t = world()
    before = snapshot(t)
    bad = tr("ada", "bob", 5)
    bad.update(entry)
    assert_error(settle(t["op"], [tr("dan", "cy", 1), bad]), 422, "validation_failed")
    assert snapshot(t) == before


def test_settlement_amount_forms():
    """
    Spec: "Each uses ordinary payment amount, note and visibility rules (defaults: empty note, public)."
    """
    t = world()
    raw = '{"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1e3},' \
          ' {"from_handle": "ada", "to_handle": "bob", "amount": 1000.0}]}'
    r = post("/settlements", t["op"], raw=raw, idem=key())
    assert r.status_code == 201, r.text
    assert [p["amount"] for p in r.json()["payments"]] == [1000, 1000]


def test_settlement_error_precedence():
    """
    Spec: "Entry errors take precedence in input order, before insufficient funds."
    """
    t = world()
    before = snapshot(t)
    assert_error(settle(t["op"], [tr("ada", "ghost", 1), tr("bob", "bob", 1)]), 404, "not_found")
    assert_error(settle(t["op"], [tr("bob", "bob", 1), tr("ada", "ghost", 1)]), 422, "self_payment")
    assert_error(settle(t["op"], [tr("cy", "ada", 999), tr("ada", "ghost", 1)]), 404, "not_found")
    assert_error(settle(t["op"], [tr("cy", "ada", 999), tr("ada", "ada", 1)]), 422, "self_payment")
    assert_error(settle(t["op"], [tr("cy", "ada", 999), tr("ada", "bob", 0)]), 422, "validation_failed")
    assert snapshot(t) == before


def test_settlement_failure_claims_no_key():
    """
    Spec: "Either all movements commit together or none do; failed validation claims no idempotency key and creates no payment or revision."
    """
    t = world()
    k = key()
    assert_error(settle(t["op"], [tr("ada", "ghost", 1)], idem=k), 404, "not_found")
    assert_error(settle(t["op"], [tr("cy", "bob", 10)], idem=k), 409, "insufficient_funds")
    r = settle(t["op"], [tr("ada", "bob", 10)], idem=k)
    assert r.status_code == 201, r.text
    again = settle(t["op"], [tr("ada", "bob", 10)], idem=k)
    assert again.status_code == 200 and again.json() == r.json()
    assert balances(t)["bob"] == 2510


def test_settlement_unknown_fields_ignored():
    """
    Spec: "Unknown fields are ignored."
    """
    t = world()
    r = settle(t["op"], [tr("ada", "bob", 3, colour="blue")], extra={"comment": "batch 1"})
    assert r.status_code == 201, r.text
    assert balances(t)["bob"] == 2503


def test_settlement_replay():
    """
    Spec: "Replays return 200 with the original complete response."
    """
    t = world()
    k = key()
    first = settle(t["op"], [tr("ada", "bob", 100), tr("bob", "cy", 50)], idem=k)
    assert first.status_code == 201
    pay(t["bob"], "dan", 2550)
    after = snapshot(t)
    again = settle(t["op"], [tr("ada", "bob", 100), tr("bob", "cy", 50)], idem=k)
    assert again.status_code == 200 and again.json() == first.json()
    assert snapshot(t) == after
