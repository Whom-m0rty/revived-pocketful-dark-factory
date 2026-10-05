import pytest

from pf import (CLIENT, activity, ask_ok, assert_error, authorize_ok, balances, capture, check_payment, correct,
                find_auth, find_request, key, me, pay, pay_request, post, refund, revisions, simultaneously, snapshot,
                statement, wallet, world2)


def paid(amount=500, visibility="private", note="dinner"):
    t = world2()
    p = pay(t["ada"], "bob", amount, note=note, visibility=visibility).json()
    return t, p


def test_refund_created():
    """
    Spec: "`POST /payments/{payment_id}/refunds`, body `{"amount": 200}`, requires an idempotency key."
    Spec: "A refund is a new payment in the opposite direction, with `refund_of` naming the target, `request_id: null`, `authorization_id: null`, and the original note/visibility."
    Spec: "Return 201 with that payment; replay returns 200 with the original body."
    Spec: "Recipients can refund payments."
    """
    t, p = paid()
    k = key()
    r = refund(t["bob"], p["payment_id"], 200, idem=k)
    assert r.status_code == 201, r.text
    f = r.json()
    check_payment(f)
    assert (f["from_handle"], f["to_handle"], f["amount"]) == ("bob", "ada", 200)
    assert f["refund_of"] == p["payment_id"] and f["request_id"] is None and f["authorization_id"] is None
    assert (f["note"], f["visibility"]) == ("dinner", "private")
    assert f["payment_id"] != p["payment_id"]
    assert balances(t)["ada"] == 9700 and balances(t)["bob"] == 2800
    after = snapshot(t)
    again = refund(t["bob"], p["payment_id"], 200, idem=k)
    assert again.status_code == 200 and again.json() == f
    assert_error(refund(t["bob"], p["payment_id"], 201, idem=k), 409, "idempotency_key_reuse")
    assert snapshot(t) == after
    feed = {x["payment_id"] for x in activity(t["ada"])}
    assert f["payment_id"] in feed and p["payment_id"] in feed
    assert f["payment_id"] not in {x["payment_id"] for x in activity(t["cy"])}


def test_other_payments_refund_of_null():
    """
    Spec: "Other payments have `refund_of: null`."
    """
    t, p = paid()
    assert "refund_of" in p and p["refund_of"] is None
    q = ask_ok(t["bob"], "ada", 10)
    assert pay_request(t["ada"], q["request_id"]).json()["refund_of"] is None
    for x in activity(t["ada"]):
        assert x["refund_of"] is None


def test_refund_cumulative_limit():
    """
    Spec: "Refunds cumulatively may not exceed the payment's current corrected amount: 422 `refund_exceeds_payment`."
    """
    t, p = paid()
    assert refund(t["bob"], p["payment_id"], 300).status_code == 201
    before = snapshot(t)
    assert_error(refund(t["bob"], p["payment_id"], 201), 422, "refund_exceeds_payment")
    assert snapshot(t) == before
    assert refund(t["bob"], p["payment_id"], 200).status_code == 201
    assert_error(refund(t["bob"], p["payment_id"], 1), 422, "refund_exceeds_payment")
    assert balances(t)["ada"] == 10000 and balances(t)["bob"] == 2500


def test_refund_limit_follows_corrected_amount():
    """
    Spec: "Refunds cumulatively may not exceed the payment's current corrected amount: 422 `refund_exceeds_payment`."
    """
    t, p = paid()
    assert correct(t["ada"], p["payment_id"], 300, p["created_at"]).status_code == 201
    assert_error(refund(t["bob"], p["payment_id"], 301), 422, "refund_exceeds_payment")
    assert refund(t["bob"], p["payment_id"], 300).status_code == 201
    t2, p2 = paid()
    assert correct(t2["ada"], p2["payment_id"], 800, p2["created_at"]).status_code == 201
    assert refund(t2["bob"], p2["payment_id"], 800).status_code == 201


def test_refund_permissions():
    """
    Spec: "Only the original receiver may refund, else 403 `forbidden`; unknown payment is 404."
    """
    t, p = paid()
    before = snapshot(t)
    assert_error(refund(t["ada"], p["payment_id"], 10), 403, "forbidden")
    assert_error(refund(t["cy"], p["payment_id"], 10), 403, "forbidden")
    assert_error(refund(t["bob"], "p_nope", 10), 404, "not_found")
    assert_error(CLIENT.post(f"/payments/{p['payment_id']}/refunds", json={"amount": 10},
                             headers={"Idempotency-Key": key()}), 401, "unauthenticated")
    assert_error(post(f"/payments/{p['payment_id']}/refunds", t["bob"], {"amount": 10}), 400,
                 "missing_idempotency_key")
    assert snapshot(t) == before


@pytest.mark.parametrize("amount", [0, -1, 1.5, "10", True])
def test_refund_invalid_amount(amount):
    """
    Spec: "Invalid amount is 422 `validation_failed`."
    """
    t, p = paid()
    before = snapshot(t)
    assert_error(post(f"/payments/{p['payment_id']}/refunds", t["bob"], {"amount": amount}, key()), 422,
                 "validation_failed")
    assert_error(post(f"/payments/{p['payment_id']}/refunds", t["bob"], {}, key()), 422, "validation_failed")
    assert snapshot(t) == before


def test_refund_of_refund_rejected():
    """
    Spec: "The target may be a direct payment, request payment or capture, but never a refund."
    Spec: "Refunds of refunds give 422 `invalid_refund_target`."
    """
    t, p = paid()
    f = refund(t["bob"], p["payment_id"], 100).json()
    before = snapshot(t)
    assert_error(refund(t["ada"], f["payment_id"], 50), 422, "invalid_refund_target")
    assert snapshot(t) == before


def test_refund_request_payment_and_capture():
    """
    Spec: "Refunds never reopen a request or authorization or restore a released hold."
    """
    t = world2()
    q = ask_ok(t["bob"], "ada", 400)
    pp = pay_request(t["ada"], q["request_id"]).json()
    r = refund(t["bob"], pp["payment_id"], 400)
    assert r.status_code == 201, r.text
    assert r.json()["request_id"] is None and r.json()["refund_of"] == pp["payment_id"]
    assert find_request(t["ada"], q["request_id"])["status"] == "paid"
    a = authorize_ok(t["ada"], "bob", 1000)
    cp = capture(t["bob"], a["authorization_id"], {"amount": 600}).json()
    r = refund(t["bob"], cp["payment_id"], 600)
    assert r.status_code == 201, r.text
    assert r.json()["authorization_id"] is None and r.json()["refund_of"] == cp["payment_id"]
    now = find_auth(t["ada"], a["authorization_id"])
    assert now["status"] == "captured" and now["remaining_amount"] == 0
    w = wallet(t["ada"])
    assert (w["total"], w["held"], w["available"]) == (10000, 0, 10000)


def test_refund_needs_available_funds():
    """
    Spec: "It moves existing money from the receiver's **available** funds, or fails 409 `insufficient_funds`, atomically."
    """
    t, p = paid()
    assert pay(t["bob"], "cy", 2800).status_code == 201
    before = snapshot(t)
    assert_error(refund(t["bob"], p["payment_id"], 201), 409, "insufficient_funds")
    assert snapshot(t) == before
    authorize_ok(t["bob"], "dan", 150)
    assert_error(refund(t["bob"], p["payment_id"], 51), 409, "insufficient_funds")
    assert refund(t["bob"], p["payment_id"], 50).status_code == 201
    assert wallet(t["bob"])["available"] == 0


def test_refund_and_capture_immutable():
    """
    Spec: "Captures and refund payments cannot themselves be corrected: 422 `linked_payment_immutable`."
    Spec: "Stage-3 corrections remain available for ordinary direct/request payments."
    """
    t, p = paid()
    f = refund(t["bob"], p["payment_id"], 100).json()
    assert_error(correct(t["bob"], f["payment_id"], 50, f["created_at"]), 422, "linked_payment_immutable")
    q = ask_ok(t["bob"], "ada", 300)
    pp = pay_request(t["ada"], q["request_id"]).json()
    assert correct(t["ada"], pp["payment_id"], 250, pp["created_at"]).status_code == 201
    assert correct(t["ada"], p["payment_id"], 450, p["created_at"]).status_code == 201


def test_correction_not_below_refunded():
    """
    Spec: "A correction cannot reduce a payment below its already-refunded amount: 422 `refund_exceeds_payment`."
    Spec: "Correction debits are checked against available funds."
    """
    t, p = paid()
    assert refund(t["bob"], p["payment_id"], 300).status_code == 201
    before = snapshot(t)
    assert_error(correct(t["ada"], p["payment_id"], 299, p["created_at"]), 422, "refund_exceeds_payment")
    assert snapshot(t) == before
    assert correct(t["ada"], p["payment_id"], 300, p["created_at"]).status_code == 201
    t2, p2 = paid()
    authorize_ok(t2["bob"], "cy", 2900)
    assert_error(correct(t2["ada"], p2["payment_id"], 399, p2["created_at"]), 409, "insufficient_funds")
    assert correct(t2["ada"], p2["payment_id"], 400, p2["created_at"]).status_code == 201


def test_refund_in_statement():
    """
    Spec: "Existing receipts and saved statements must remain available in their original form."
    """
    t, p = paid()
    old = statement(t["ada"])
    import time
    time.sleep(1.1)
    f = refund(t["bob"], p["payment_id"], 120).json()
    s = statement(t["ada"])
    assert [(e["payment"]["payment_id"], e["delta"]) for e in s["entries"]] == \
        [(p["payment_id"], -500), (f["payment_id"], 120)]
    assert s["closing_balance"] == 9620
    frozen = statement(t["ada"], snapshot=old["snapshot"])
    assert frozen["entries"] == old["entries"] and frozen["closing_balance"] == 9500


def test_settlement_member_refund_keeps_membership():
    """
    Spec: "A settlement payment may be refunded under the existing refund rules, but refunds never change settlement membership."
    """
    t = world2()
    st = post("/settlements", t["op"], {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 300},
                                                       {"from_handle": "dan", "to_handle": "cy", "amount": 100}]},
              key()).json()
    m = st["payments"][0]
    f = refund(t["bob"], m["payment_id"], 100)
    assert f.status_code == 201, f.text
    assert f.json()["settlement_id"] is None and f.json()["refund_of"] == m["payment_id"]
    feed = {x["payment_id"]: x for x in activity(t["ada"])}
    assert feed[m["payment_id"]]["settlement_id"] == st["settlement_id"]


def test_concurrent_refunds_never_exceed():
    """
    Spec: "Refunds cumulatively may not exceed the payment's current corrected amount: 422 `refund_exceeds_payment`."
    """
    t, p = paid()
    results = simultaneously([lambda c: post(f"/payments/{p['payment_id']}/refunds", t["bob"], {"amount": 150}, key(),
                                             client=c) for _ in range(8)])
    ok = [r for r in results if r.status_code == 201]
    assert len(ok) == 3, [(r.status_code, r.text) for r in results]
    for r in results:
        if r.status_code != 201:
            assert_error(r, 422, "refund_exceeds_payment")
    assert balances(t)["ada"] == 9950


def test_concurrent_identical_refund():
    """
    Spec: "There are ten idempotent write paths: stage 1's five, authorizations and captures from stage 2, corrections from stage 3, and refunds and correction batches in this stage."
    Spec: "All requirements from stages 1–3 continue to apply."
    """
    t, p = paid()
    k = key()
    results = simultaneously([lambda c: post(f"/payments/{p['payment_id']}/refunds", t["bob"], {"amount": 100}, k,
                                             client=c) for _ in range(6)])
    assert sorted(r.status_code for r in results) == [200] * 5 + [201]
    assert all(r.json() == results[0].json() for r in results)
    assert balances(t)["ada"] == 9600
