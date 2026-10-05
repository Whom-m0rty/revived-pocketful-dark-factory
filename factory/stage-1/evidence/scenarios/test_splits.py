import pytest

from pf import (activity, assert_error, balance, balances, check_request, find_request, is_id, is_ts, key, me,
                pay_request, post, snapshot, world)


def split(token, amount, handles, note="dinner", idem=None):
    return post("/splits", token, {"amount": amount, "participant_handles": handles, "note": note}, idem or key())


def test_split_example():
    """
    Spec: "POST /splits Idempotency-Key: 7a3e52..."
    Spec: ""split_id": "sp_2","
    Spec: "**An idempotent write path.** Splits an amount the caller already paid, and asks each of the other participants for their share by creating one `pending` request each."
    Spec: "**A request is created for every participant except the caller**, each for that participant's share, with the caller as requester."
    Spec: "`shares` covers every participant including the caller, in the order given, and always sums to `amount`."
    Spec: "`requests` covers every participant except the caller, in the same order."
    Spec: "The caller may be included in `participant_handles` or omitted."
    """
    t = world()
    ma = me(t["ada"])
    r = split(t["ada"], 3000, ["ada", "bob", "cy"])
    assert r.status_code == 201, r.text
    s = r.json()
    assert is_id(s["split_id"]) and is_ts(s["created_at"])
    assert (s["amount"], s["currency"], s["note"]) == (3000, "EUR", "dinner")
    assert s["shares"] == [{"handle": "ada", "amount": 1000}, {"handle": "bob", "amount": 1000},
                           {"handle": "cy", "amount": 1000}]
    assert [q["payer_handle"] for q in s["requests"]] == ["bob", "cy"]
    for q in s["requests"]:
        check_request(q)
        assert q["requester_id"] == ma["user_id"] and q["requester_handle"] == "ada"
        assert q["amount"] == 1000 and q["status"] == "pending" and q["payment_id"] is None
        assert q["note"] == "dinner" or isinstance(q["note"], str)
        assert find_request(t["ada"], q["request_id"]) is not None
    assert find_request(t["bob"], s["requests"][0]["request_id"]) is not None
    assert find_request(t["cy"], s["requests"][1]["request_id"]) is not None
    assert balance(t["ada"]) == 10000


def test_split_caller_omitted():
    """
    Spec: "The caller may be included in `participant_handles` or omitted."
    """
    t = world()
    r = split(t["ada"], 1000, ["bob", "cy", "dan"])
    assert r.status_code == 201, r.text
    s = r.json()
    assert [(x["handle"], x["amount"]) for x in s["shares"]] == [("bob", 334), ("cy", 333), ("dan", 333)]
    assert [(q["payer_handle"], q["amount"]) for q in s["requests"]] == [("bob", 334), ("cy", 333), ("dan", 333)]


def test_split_caller_in_middle():
    """
    Spec: "Shares follow the equal-split rule in §9, in the order the handles are given."
    """
    t = world()
    s = split(t["bob"], 10, ["ada", "bob", "cy"]).json()
    assert [(x["handle"], x["amount"]) for x in s["shares"]] == [("ada", 4), ("bob", 3), ("cy", 3)]
    assert [(q["payer_handle"], q["amount"]) for q in s["requests"]] == [("ada", 4), ("cy", 3)]


@pytest.mark.parametrize("amount,n,expected", [(1000, 3, [334, 333, 333]), (1, 3, [1, 0, 0]),
                                               (10, 3, [4, 3, 3]), (999, 3, [333, 333, 333]),
                                               (5, 5, [1, 1, 1, 1, 1]), (7, 4, [2, 2, 2, 1]),
                                               (1000000000, 3, [333333334, 333333333, 333333333])])
def test_split_rounding_table(amount, n, expected):
    """
    Spec: "| 1000 | 3 | 334, 333, 333 |"
    Spec: "| 1 | 3 | 1, 0, 0 |"
    Spec: "| 10 | 3 | 4, 3, 3 |"
    Spec: "| 999 | 3 | 333, 333, 333 |"
    Spec: "| 5 | 5 | 1, 1, 1, 1, 1 |"
    Spec: "Shares must be whole minor units, sum exactly to `amount` and differ by at most one minor unit."
    Spec: "When the amount does not divide evenly, the larger shares go to the first participants in `participant_handles` order."
    """
    handles = ["p1", "p2", "p3", "p4", "p5"][:n]
    t = world(balances={**{h: 0 for h in handles}, "host": 0})
    r = split(t["host"], amount, handles)
    assert r.status_code == 201, r.text
    s = r.json()
    assert [x["amount"] for x in s["shares"]] == expected
    assert [x["handle"] for x in s["shares"]] == handles
    assert sum(x["amount"] for x in s["shares"]) == amount
    assert [q["amount"] for q in s["requests"]] == expected


def test_split_zero_share_still_requests():
    """
    Spec: "A share of `0` is legal and still produces a request for that participant."
    """
    t = world()
    s = split(t["ada"], 2, ["ada", "bob", "cy"]).json()
    assert [x["amount"] for x in s["shares"]] == [1, 1, 0]
    assert [(q["payer_handle"], q["amount"], q["status"]) for q in s["requests"]] == [("bob", 1, "pending"),
                                                                                    ("cy", 0, "pending")]
    assert find_request(t["cy"], s["requests"][1]["request_id"])["amount"] == 0


def test_split_order_changes_extra_unit():
    """
    Spec: "Splitting the same amount among the same people in a different `participant_handles` order gives the extra unit to a different person."
    Spec: "Each split's shares are independent of previous splits."
    """
    t = world()
    a = split(t["ada"], 1000, ["ada", "bob", "cy"]).json()
    b = split(t["ada"], 1000, ["cy", "bob", "ada"]).json()
    c = split(t["ada"], 1000, ["ada", "bob", "cy"]).json()
    assert {x["handle"]: x["amount"] for x in a["shares"]} == {"ada": 334, "bob": 333, "cy": 333}
    assert {x["handle"]: x["amount"] for x in b["shares"]} == {"cy": 334, "bob": 333, "ada": 333}
    assert c["shares"] == a["shares"]


def test_split_only_caller():
    """
    Spec: "A split whose only participant is the caller is **valid**: it computes one share, creates zero requests, and returns `"requests": []`."
    """
    t = world()
    before = snapshot(t)
    r = split(t["ada"], 500, ["ada"])
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["shares"] == [{"handle": "ada", "amount": 500}] and s["requests"] == []
    assert snapshot(t) == before


def test_split_ignores_balances():
    """
    Spec: "Nothing about a split checks anyone's balance."
    """
    t = world()
    r = split(t["cy"], 1000000000, ["cy", "op", "bob"])
    assert r.status_code == 201, r.text
    assert [q["amount"] for q in r.json()["requests"]] == [333333333, 333333333]


def test_split_not_in_feed_and_requests_private():
    """
    Spec: "A split is not a feed item."
    Spec: "The requests it creates are visible to their own two parties, and the payments that eventually fulfil them follow the rule above."
    """
    t = world()
    s = split(t["ada"], 300, ["ada", "bob", "cy"]).json()
    for who in t:
        assert activity(t[who]) == []
    rb, rc = s["requests"]
    assert find_request(t["dan"], rb["request_id"]) is None and find_request(t["dan"], rc["request_id"]) is None
    assert find_request(t["cy"], rb["request_id"]) is None
    assert find_request(t["bob"], rc["request_id"]) is None
    pub = pay_request(t["bob"], rb["request_id"], {"visibility": "public"}).json()
    pay(t["dan"], "cy", 100)
    priv = pay_request(t["cy"], rc["request_id"], {"visibility": "private"}).json()
    dan_ids = {p["payment_id"] for p in activity(t["dan"])}
    assert pub["payment_id"] in dan_ids and priv["payment_id"] not in dan_ids
    assert priv["payment_id"] in {p["payment_id"] for p in activity(t["ada"])}


def pay(token, to, amount):
    r = post("/payments", token, {"to_handle": to, "amount": amount}, key())
    assert r.status_code == 201, r.text
    return r.json()


def test_paid_splits_conserve_total():
    """
    Spec: "After any number of splits have been paid in full, wallet balances must still sum exactly to the seeded total."
    """
    t = world()
    total = sum(balances(t).values())
    for amount, handles in ((1000, ["ada", "bob", "dan"]), (7, ["bob", "dan", "ada"]), (2501, ["dan", "ada"])):
        s = split(t["ada"], amount, handles).json()
        for q in s["requests"]:
            if q["amount"] > 0:
                assert pay_request(t[q["payer_handle"]], q["request_id"]).status_code == 201
    assert sum(balances(t).values()) == total


@pytest.mark.parametrize("amount", [0, -3, 1000000001, 10.5, "30", False])
def test_split_invalid_amount(amount):
    """
    Spec: "| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |"
    """
    t = world()
    before = snapshot(t)
    r = post("/splits", t["ada"], {"amount": amount, "participant_handles": ["ada", "bob"], "note": "n"}, key())
    assert_error(r, 422, "validation_failed")
    assert snapshot(t) == before


def test_split_handles_rules():
    """
    Spec: "| `participant_handles` empty, or containing a duplicate handle | 422 `validation_failed` |"
    Spec: "| Any handle is unknown | 404 `not_found` |"
    Spec: "| `note` longer than 200 characters | 422 `validation_failed` |"
    """
    t = world()
    before = snapshot(t)
    assert_error(split(t["ada"], 100, []), 422, "validation_failed")
    assert_error(split(t["ada"], 100, ["bob", "cy", "bob"]), 422, "validation_failed")
    assert_error(split(t["ada"], 100, ["ada", "ada"]), 422, "validation_failed")
    assert_error(split(t["ada"], 100, ["bob", "ghost"]), 404, "not_found")
    assert_error(split(t["ada"], 100, ["bob", "cy"], note="n" * 201), 422, "validation_failed")
    assert_error(post("/splits", t["ada"], {"amount": 100, "participant_handles": ["bob"], "note": None}, key()),
                 422, "validation_failed")
    assert_error(post("/splits", t["ada"], {"amount": 100, "note": "n"}, key()), 422, "validation_failed")
    assert_error(post("/splits", t["ada"], {"amount": 100, "participant_handles": "bob", "note": "n"}, key()),
                 400, "malformed_request")
    assert snapshot(t) == before
