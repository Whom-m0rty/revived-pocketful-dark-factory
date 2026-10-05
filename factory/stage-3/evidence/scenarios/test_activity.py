import pytest

from pf import (activity, ask_ok, assert_error, assert_newest_first, check_payment, get, key, pay, pay_request,
                post, world)


def ids(token):
    return {p["payment_id"] for p in activity(token)}


def test_feed_rule():
    """
    Spec: "A payment appears for a caller **if and only if** its `visibility` is `public`, **or** the caller is its sender or its receiver."
    Spec: "There is no other rule, no follow graph and no mute list."
    Spec: "A `private` payment is hidden from third parties, not from its own receiver."
    Spec: "Payments appear in an activity feed with public or private visibility."
    Spec: "Payments visible to the caller by the feed contract in §4, newest first by `created_at`."
    Spec: "GET /activity?limit=50&offset=0"
    """
    t = world()
    pub = pay(t["ada"], "bob", 10, visibility="public").json()["payment_id"]
    priv = pay(t["ada"], "bob", 11, visibility="private").json()["payment_id"]
    other_priv = pay(t["dan"], "cy", 12, visibility="private").json()["payment_id"]
    assert ids(t["ada"]) == {pub, priv}
    assert ids(t["bob"]) == {pub, priv}
    assert ids(t["cy"]) == {pub, other_priv}
    assert ids(t["dan"]) == {pub, other_priv}
    assert ids(t["op"]) == {pub}


def test_visibility_same_for_everyone():
    """
    Spec: "Visibility is **one value on the payment**, seen identically by both parties and by everyone else."
    """
    t = world()
    pub = pay(t["ada"], "bob", 10, note="n").json()
    priv = pay(t["ada"], "bob", 11, visibility="private").json()
    for who in ("ada", "bob", "cy"):
        for p in activity(t[who]):
            if p["payment_id"] == pub["payment_id"]:
                assert p == pub
    for who in ("ada", "bob"):
        found = [p for p in activity(t[who]) if p["payment_id"] == priv["payment_id"]]
        assert found == [priv]


def test_feed_payments_only():
    """
    Spec: "`GET /activity` returns payments only."
    Spec: "{ "payments": [ { ...payment... } ], "has_more": false }"
    """
    t = world()
    pay(t["ada"], "bob", 10)
    ask_ok(t["bob"], "ada", 99)
    q = ask_ok(t["ada"], "bob", 5)
    r = get("/activity", t["ada"])
    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is False
    assert len(body["payments"]) == 1
    for p in body["payments"]:
        check_payment(p)
    paid = pay_request(t["bob"], q["request_id"], {"visibility": "private"}).json()
    found = [p for p in activity(t["ada"]) if p["payment_id"] == paid["payment_id"]]
    assert found and found[0]["request_id"] == q["request_id"]
    assert len(activity(t["ada"])) == 2


def test_feed_newest_first():
    """
    Spec: "Payments visible to the caller by the feed contract in §4, newest first by `created_at`."
    Spec: "The relative order of two payments created within the same second is unspecified."
    """
    import time
    t = world()
    first = pay(t["ada"], "bob", 1).json()["payment_id"]
    time.sleep(1.1)
    second = pay(t["bob"], "ada", 2).json()["payment_id"]
    time.sleep(1.1)
    third = pay(t["ada"], "cy", 3).json()["payment_id"]
    items = activity(t["ada"])
    assert_newest_first(items)
    assert [p["payment_id"] for p in items] == [third, second, first]


def test_feed_pagination():
    """
    Spec: "`limit` and `offset` behave exactly as in `GET /requests`."
    Spec: "`has_more` is true when items exist beyond the last one returned."
    """
    payments = [{"id": f"p_s{i}", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1, "note": "",
                 "visibility": "public"} for i in range(53)]
    t = world(payments=payments)
    r = get("/activity", t["cy"])
    assert r.status_code == 200
    assert len(r.json()["payments"]) == 50 and r.json()["has_more"] is True
    r = get("/activity", t["cy"], {"limit": 53})
    assert len(r.json()["payments"]) == 53 and r.json()["has_more"] is False
    r = get("/activity", t["cy"], {"limit": 52})
    assert len(r.json()["payments"]) == 52 and r.json()["has_more"] is True
    r = get("/activity", t["cy"], {"limit": 200, "offset": 50})
    assert len(r.json()["payments"]) == 3 and r.json()["has_more"] is False
    r = get("/activity", t["cy"], {"offset": 53})
    assert r.json()["payments"] == [] and r.json()["has_more"] is False


@pytest.mark.parametrize("params", [{"limit": "0"}, {"limit": "201"}, {"offset": "-1"}, {"limit": "1e9"},
                                    {"limit": "4.0"}, {"limit": "+4"}, {"offset": "+0"}, {"limit": "x"}])
def test_feed_bad_query(params):
    """
    Spec: "`limit` and `offset` behave exactly as in `GET /requests`."
    Spec: "An integer-valued **query parameter** is written as plain decimal digits: `1e9`, `4.0` and `+4` are 422 `validation_failed` whatever their numeric value."
    Spec: "Shared ranges, enforced on every endpoint that takes them:"
    """
    t = world()
    assert_error(get("/activity", t["ada"], params), 422, "validation_failed")


def test_feed_limit_bounds_ok():
    """
    Spec: "| `limit` | integer 1 to 200 | 422 `validation_failed` |"
    """
    t = world()
    for params in ({"limit": "1"}, {"limit": "200"}, {"offset": "0"}):
        assert get("/activity", t["ada"], params).status_code == 200


def test_request_payment_feed_visibility():
    """
    Spec: "**Visibility belongs to the payment, not the request.** The payer chooses it when the money moves."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 10)
    p = pay_request(t["ada"], q["request_id"], {"visibility": "private"}).json()
    assert p["payment_id"] in ids(t["bob"]) and p["payment_id"] in ids(t["ada"])
    assert p["payment_id"] not in ids(t["cy"])
