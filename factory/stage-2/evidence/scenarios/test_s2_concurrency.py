import pytest

from pf import (assert_error, authorize_ok, balances, capture, find_auth, key, me, no_5xx, post, simultaneously,
                snapshot, wallet, world2)


def setup(op, t):
    if op == "authorizations":
        return (t["ada"], "/authorizations", {"to_handle": "bob", "amount": 100, "note": "x"},
                {"to_handle": "bob", "amount": 101, "note": "x"}, {"to_handle": "bob", "amount": -1})
    a = authorize_ok(t["ada"], "bob", 1000)
    return (t["bob"], f"/authorizations/{a['authorization_id']}/capture", {"amount": 100},
            {"amount": 101}, {"amount": -1})


@pytest.mark.parametrize("op", ["authorizations", "capture"])
def test_new_paths_idempotency(op):
    """
    Spec: "There are now seven idempotent write paths: stage 1's five, authorizations and captures."
    Spec: "The same replay rules apply independently to each."
    Spec: "`Idempotency-Key` is required."
    """
    t = world2()
    token, path, body, other, invalid = setup(op, t)
    before = snapshot(t)
    assert_error(post(path, token, body), 400, "missing_idempotency_key")
    assert_error(post(path, token, body, idem=""), 400, "missing_idempotency_key")
    assert_error(post(path, token, body, idem="k" * 256), 422, "validation_failed")
    k = key()
    assert post(path, token, invalid, idem=k).status_code == 422
    assert snapshot(t) == before
    first = post(path, token, body, idem=k)
    assert first.status_code == 201, first.text
    after = snapshot(t)
    again = post(path, token, body, idem=k)
    assert again.status_code == 200 and again.json() == first.json()
    assert_error(post(path, token, other, idem=k), 409, "idempotency_key_reuse")
    assert_error(post(path, token, invalid, idem=k), 409, "idempotency_key_reuse")
    assert snapshot(t) == after


@pytest.mark.parametrize("op", ["authorizations", "capture"])
def test_new_paths_concurrent_identical(op):
    """
    Spec: "Each idempotent capture moves money once."
    """
    t = world2()
    token, path, body, _, _ = setup(op, t)
    k = key()
    results = simultaneously([lambda c: post(path, token, body, idem=k, client=c) for _ in range(8)])
    codes = sorted(r.status_code for r in results)
    assert codes == [200] * 7 + [201], [(r.status_code, r.text) for r in results]
    assert all(r.json() == results[0].json() for r in results)
    w = wallet(t["ada"])
    if op == "authorizations":
        assert (w["total"], w["held"]) == (10000, 100)
    else:
        assert (w["total"], w["held"]) == (9900, 0)
        assert wallet(t["bob"])["total"] == 2600


def test_concurrent_authorizations_respect_available():
    """
    Spec: "Concurrent requests must produce the same results as executing them one at a time in some order, and the requirements above hold at every read."
    Spec: "`available = total − held` must never be negative."
    """
    t = world2(balances={"ada": 1000, "bob": 0, "cy": 0, "op": 0})
    calls = [lambda c: post("/authorizations", t["ada"], {"to_handle": "bob", "amount": 300}, key(), client=c)
             for _ in range(6)]
    calls += [lambda c: post("/payments", t["ada"], {"to_handle": "cy", "amount": 300}, key(), client=c)
              for _ in range(4)]
    calls += [lambda c: c.get("/me", headers={"Authorization": f"Bearer {t['ada']}"}) for _ in range(6)]
    results = simultaneously(calls)
    no_5xx(results)
    ok = [r for r in results[:10] if r.status_code == 201]
    assert len(ok) == 3, [(r.status_code, r.text) for r in results[:10]]
    for r in results[:10]:
        if r.status_code != 201:
            assert_error(r, 409, "insufficient_funds")
    for r in results[10:]:
        m = r.json()
        assert m["available"] >= 0 and m["available"] == m["total"] - m["held"] and m["balance"] == m["total"]
    w = wallet(t["ada"])
    assert w["available"] == 100
    assert sum(balances(t).values()) == 1000


def test_concurrent_captures_never_exceed():
    """
    Spec: "Cumulative captures must not exceed the authorized amount."
    Spec: "The sum of all wallet `total` values always equals the total seeded by the last reset."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    aid = a["authorization_id"]
    results = simultaneously([lambda c: post(f"/authorizations/{aid}/capture", t["bob"],
                                             {"amount": 150, "final": False}, key(), client=c) for _ in range(10)])
    no_5xx(results)
    ok = [r for r in results if r.status_code == 201]
    assert len(ok) == 6, [(r.status_code, r.text) for r in results]
    for r in results:
        if r.status_code != 201:
            assert_error(r, 422, "capture_exceeds_authorization")
    now = find_auth(t["bob"], aid)
    assert (now["status"], now["captured_amount"], now["remaining_amount"]) == ("open", 900, 100)
    assert len(now["payment_ids"]) == 6
    w = wallet(t["ada"])
    assert (w["total"], w["held"], w["available"]) == (9100, 100, 9000)
    assert sum(balances(t).values()) == 10000 + 2500 + 0 + 5000 + 0


def test_concurrent_final_captures_one_wins():
    """
    Spec: "A closed hold cannot be captured again."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    aid = a["authorization_id"]
    results = simultaneously([lambda c: post(f"/authorizations/{aid}/capture", t["bob"], {}, key(), client=c)
                              for _ in range(8)])
    no_5xx(results)
    assert sorted(r.status_code for r in results) == [201] + [409] * 7
    for r in results:
        if r.status_code == 409:
            assert_error(r, 409, "authorization_not_open")
    assert wallet(t["ada"])["total"] == 9000 and wallet(t["bob"])["total"] == 3500


def test_capture_void_race():
    """
    Spec: "Concurrent requests must produce the same results as executing them one at a time in some order, and the requirements above hold at every read."
    """
    t = world2()
    for _ in range(5):
        a = authorize_ok(t["ada"], "bob", 400)
        aid = a["authorization_id"]
        start = wallet(t["ada"])["total"]
        cap, vd = simultaneously([
            lambda c: post(f"/authorizations/{aid}/capture", t["bob"], {}, key(), client=c),
            lambda c: post(f"/authorizations/{aid}/void", t["ada"], client=c),
        ])
        no_5xx([cap, vd])
        now = find_auth(t["ada"], aid)
        if now["status"] == "captured":
            assert cap.status_code == 201
            assert_error(vd, 409, "authorization_not_open")
            assert wallet(t["ada"])["total"] == start - 400
        else:
            assert now["status"] == "voided" and vd.status_code == 200
            assert_error(cap, 409, "authorization_not_open")
            assert wallet(t["ada"])["total"] == start
        assert wallet(t["ada"])["held"] == 0
