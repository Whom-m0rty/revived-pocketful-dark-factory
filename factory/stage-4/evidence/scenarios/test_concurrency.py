from pf import (ask_ok, assert_error, balance, balances, get, key, no_5xx, pay_request, post, simultaneously,
                CLIENT, find_request, world)


def test_concurrent_overspend():
    """
    Spec: "No wallet balance may be negative, including transiently."
    Spec: "The following apply to all operations, including concurrent requests and retries:"
    """
    t = world(balances={"ada": 1000, "bob": 0, "cy": 0, "dan": 0, "op": 0})
    tok = t["ada"]
    calls = [lambda c, to=to: post("/payments", tok, {"to_handle": to, "amount": 100}, key(), client=c)
             for to in ["bob", "cy", "dan"] * 7 + ["bob"]]
    results = simultaneously(calls)
    no_5xx(results)
    ok = [r for r in results if r.status_code == 201]
    refused = [r for r in results if r.status_code != 201]
    assert len(ok) == 10, [(r.status_code, r.text) for r in results]
    for r in refused:
        assert_error(r, 409, "insufficient_funds")
    b = balances(t)
    assert b["ada"] == 0 and sum(b.values()) == 1000


def test_concurrent_cross_payments_conserve_total():
    """
    Spec: "The sum of wallet balances always equals the total seeded by the last `POST /_test/reset`."
    Spec: "Requests must not produce 5xx responses, including under concurrent load."
    Spec: "| Concurrent requests | up to 50 in flight |"
    """
    t = world(balances={"ada": 500, "bob": 500, "cy": 500, "dan": 500, "op": 0})
    hs = ["ada", "bob", "cy", "dan"]
    calls = []
    for i in range(40):
        a, b = hs[i % 4], hs[(i + 1 + i // 4) % 4]
        if a == b:
            b = hs[(i + 2) % 4]
        calls.append(lambda c, a=a, b=b: post("/payments", t[a], {"to_handle": b, "amount": 60}, key(), client=c))
    for i in range(10):
        calls.append(lambda c, h=hs[i % 4]: get("/me", t[h], client=c))
    results = simultaneously(calls)
    no_5xx(results)
    for r in results[:40]:
        assert r.status_code in (201, 409), r.text
    b = balances(t)
    assert sum(b.values()) == 2000 and min(b.values()) >= 0


def test_balances_never_observed_negative():
    """
    Spec: "No wallet balance may be negative, including transiently."
    """
    t = world(balances={"ada": 300, "bob": 300, "cy": 0, "dan": 0, "op": 0})
    calls = []
    for i in range(20):
        frm, to = ("ada", "bob") if i % 2 else ("bob", "ada")
        calls.append(lambda c, f=frm, to=to: post("/payments", t[f], {"to_handle": to, "amount": 200}, key(), client=c))
    for i in range(20):
        calls.append(lambda c, h=("ada", "bob")[i % 2]: get("/me", t[h], client=c))
    results = simultaneously(calls)
    no_5xx(results)
    for r in results[20:]:
        assert r.status_code == 200 and r.json()["balance"] >= 0
    assert sum(balances(t).values()) == 600


def test_concurrent_pay_same_request_moves_once():
    """
    Spec: "A payment request may move money at most once."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 700)
    results = simultaneously([lambda c: post(f"/requests/{q['request_id']}/pay", t["ada"], {}, key(), client=c)
                              for _ in range(10)])
    no_5xx(results)
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [409] * 9, [(r.status_code, r.text) for r in results]
    for r in results:
        if r.status_code == 409:
            assert_error(r, 409, "request_not_pending")
    assert balance(t["ada"]) == 9300 and balance(t["bob"]) == 3200


def test_pay_cancel_race_consistent():
    """
    Spec: "A request is `pending`, and then exactly one of `paid`, `declined` or `cancelled`."
    """
    t = world()
    for _ in range(5):
        q = ask_ok(t["bob"], "ada", 100)
        start = balance(t["ada"])
        results = simultaneously([
            lambda c: post(f"/requests/{q['request_id']}/pay", t["ada"], {}, key(), client=c),
            lambda c: post(f"/requests/{q['request_id']}/cancel", t["bob"], client=c),
            lambda c: post(f"/requests/{q['request_id']}/decline", t["ada"], client=c),
        ])
        no_5xx(results)
        final = find_request(t["ada"], q["request_id"])
        winners = [r for r in results if r.status_code in (200, 201)]
        assert len(winners) == 1, [(r.status_code, r.text) for r in results]
        if final["status"] == "paid":
            assert results[0].status_code == 201 and balance(t["ada"]) == start - 100
            assert final["payment_id"] == results[0].json()["payment_id"]
        else:
            assert final["status"] in ("cancelled", "declined") and balance(t["ada"]) == start
            assert final["payment_id"] is None


def test_concurrent_settlements_atomic():
    """
    Spec: "Either all movements commit together or none do; failed validation claims no idempotency key and creates no payment or revision."
    """
    t = world(balances={"ada": 100, "bob": 0, "cy": 0, "dan": 0, "op": 0})
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 15},
                          {"from_handle": "ada", "to_handle": "cy", "amount": 15}]}
    results = simultaneously([lambda c: post("/settlements", t["op"], body, key(), client=c) for _ in range(8)])
    no_5xx(results)
    ok = [r for r in results if r.status_code == 201]
    assert len(ok) == 3, [(r.status_code, r.text) for r in results]
    for r in results:
        if r.status_code != 201:
            assert_error(r, 409, "insufficient_funds")
    b = balances(t)
    assert b == {"ada": 10, "bob": 45, "cy": 45, "dan": 0, "op": 0}


def test_concurrent_signup_same_email():
    """
    Spec: "| Email already registered | 409 `email_taken` |"
    """
    world()
    body = {"email": "racer@example.org", "password": "correct horse", "display_name": "R"}
    results = simultaneously([lambda c: c.post("/auth/signup", json=body) for _ in range(6)])
    no_5xx(results)
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [409] * 5, [(r.status_code, r.text) for r in results]
    for r in results:
        if r.status_code == 409:
            assert r.json()["error"]["code"] in ("email_taken", "handle_taken")
    assert CLIENT.post("/auth/login", json={"email": "racer@example.org", "password": "correct horse"}).status_code == 200
