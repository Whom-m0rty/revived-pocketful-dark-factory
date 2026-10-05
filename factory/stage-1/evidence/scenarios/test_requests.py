import pytest

from pf import (CLIENT, activity, ask, ask_ok, assert_error, assert_newest_first, balance, check_payment,
                check_request, find_request, get, key, me, pay, pay_request, post, requests_of, snapshot, user,
                fixture, reset, login, world)


def decline(token, rid):
    return post(f"/requests/{rid}/decline", token)


def cancel(token, rid):
    return post(f"/requests/{rid}/cancel", token)


# ---------- POST /requests ----------

def test_request_created():
    """
    Spec: "POST /requests Idempotency-Key: 9b1f04..."
    Spec: ""request_id": "rq_4","
    Spec: "The caller is the requester."
    Spec: "A **request** asks someone for money."
    Spec: "The `requester` will receive; the `payer` is being asked."
    """
    t = world()
    ma, mb = me(t["ada"]), me(t["bob"])
    r = ask(t["bob"], "ada", 1200, note="taxi")
    assert r.status_code == 201, r.text
    q = r.json()
    check_request(q)
    assert q["requester_id"] == mb["user_id"] and q["requester_handle"] == "bob"
    assert q["payer_id"] == ma["user_id"] and q["payer_handle"] == "ada"
    assert (q["amount"], q["currency"], q["note"], q["status"], q["payment_id"]) == (1200, "EUR", "taxi", "pending", None)
    assert balance(t["ada"]) == 10000 and balance(t["bob"]) == 2500
    assert find_request(t["ada"], q["request_id"]) == q
    assert find_request(t["bob"], q["request_id"]) == q


def test_request_may_exceed_payer_balance():
    """
    Spec: "**The payer's balance is not checked here.** A request for more than the payer holds is created normally and sits `pending`."
    """
    t = world()
    r = ask(t["ada"], "cy", 1000000000)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"


@pytest.mark.parametrize("amount", [0, -5, 1000000001, 2.5, "12", True])
def test_request_invalid_amount(amount):
    """
    Spec: "| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |"
    """
    t = world()
    before = snapshot(t)
    r = post("/requests", t["bob"], {"payer_handle": "ada", "amount": amount, "note": "n"}, key())
    assert_error(r, 422, "validation_failed")
    assert snapshot(t) == before


def test_request_amount_bounds_ok():
    """
    Spec: "| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |"
    """
    t = world()
    assert ask(t["bob"], "ada", 1).status_code == 201
    assert ask(t["bob"], "ada", 1000000000).status_code == 201
    r = post("/requests", t["bob"], raw='{"payer_handle": "ada", "amount": 1e3, "note": "n"}', idem=key())
    assert r.status_code == 201 and r.json()["amount"] == 1000


def test_self_request():
    """
    Spec: "| `payer_handle` is the caller's own handle | 422 `self_request` |"
    """
    t = world()
    before = snapshot(t)
    assert_error(ask(t["bob"], "bob", 10), 422, "self_request")
    assert snapshot(t) == before


def test_request_note_rules():
    """
    Spec: "| `note` longer than 200 characters | 422 `validation_failed` |"
    """
    t = world()
    before = snapshot(t)
    assert_error(ask(t["bob"], "ada", 10, note="z" * 201), 422, "validation_failed")
    assert_error(post("/requests", t["bob"], {"payer_handle": "ada", "amount": 10, "note": None}, key()),
                 422, "validation_failed")
    assert_error(post("/requests", t["bob"], {"payer_handle": "ada", "amount": 10, "note": 7}, key()),
                 422, "validation_failed")
    assert snapshot(t) == before
    q = ask_ok(t["bob"], "ada", 10, note="z" * 200)
    assert q["note"] == "z" * 200
    q = ask_ok(t["bob"], "ada", 10, note=" 🚕 taxi ")
    assert q["note"] == " 🚕 taxi "


def test_request_unknown_handle():
    """
    Spec: "| No user has that handle | 404 `not_found` |"
    """
    t = world()
    before = snapshot(t)
    assert_error(ask(t["bob"], "ghost", 10), 404, "not_found")
    assert snapshot(t) == before


def test_request_missing_and_malformed():
    """
    Spec: "| 422 | `validation_failed` | A required field or query parameter is missing, or a stated rule is violated with no more specific code |"
    Spec: "| 400 | `malformed_request` | Unparseable body, or a field of the wrong JSON type |"
    """
    t = world()
    before = snapshot(t)
    assert_error(post("/requests", t["bob"], {"amount": 10, "note": "n"}, key()), 422, "validation_failed")
    assert_error(post("/requests", t["bob"], {"payer_handle": "ada", "note": "n"}, key()), 422, "validation_failed")
    assert_error(post("/requests", t["bob"], raw="{oops", idem=key()), 400, "malformed_request")
    assert_error(post("/requests", t["bob"], {"payer_handle": 5, "amount": 10, "note": "n"}, key()),
                 400, "malformed_request")
    assert snapshot(t) == before


# ---------- POST /requests/{id}/pay ----------

def test_pay_request():
    """
    Spec: "POST /requests/rq_4/pay Idempotency-Key: c41d88..."
    Spec: "Returns `201` with the created **payment**, exactly as `POST /payments` returns one, with `request_id` set to this request."
    Spec: "The request becomes `paid` and carries the new `payment_id`."
    Spec: "It is either sent directly or created by paying a request."
    """
    t = world()
    ma, mb = me(t["ada"]), me(t["bob"])
    q = ask_ok(t["bob"], "ada", 1200, note="taxi")
    r = pay_request(t["ada"], q["request_id"], {"visibility": "private"})
    assert r.status_code == 201, r.text
    p = r.json()
    check_payment(p)
    assert p["from_user_id"] == ma["user_id"] and p["from_handle"] == "ada"
    assert p["to_user_id"] == mb["user_id"] and p["to_handle"] == "bob"
    assert (p["amount"], p["currency"], p["visibility"], p["request_id"]) == (1200, "EUR", "private", q["request_id"])
    assert p["note"] == "taxi"
    assert balance(t["ada"]) == 8800 and balance(t["bob"]) == 3700
    for who in ("ada", "bob"):
        now = find_request(t[who], q["request_id"])
        assert now["status"] == "paid" and now["payment_id"] == p["payment_id"]


def test_pay_visibility_default_public():
    """
    Spec: "The body carries `visibility` only, optional, default `"public"`."
    Spec: "It is the payer's choice, not the requester's."
    Spec: "**Visibility belongs to the payment, not the request.** The payer chooses it when the money moves."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 10)
    r = pay_request(t["ada"], q["request_id"], {})
    assert r.status_code == 201 and r.json()["visibility"] == "public"
    assert any(p["payment_id"] == r.json()["payment_id"] for p in activity(t["cy"]))
    q2 = ask_ok(t["bob"], "ada", 10)
    r2 = pay_request(t["ada"], q2["request_id"], {"visibility": "private"})
    assert r2.status_code == 201 and r2.json()["visibility"] == "private"
    assert not any(p["payment_id"] == r2.json()["payment_id"] for p in activity(t["cy"]))


@pytest.mark.parametrize("vis", ["hidden", "", None, 3])
def test_pay_bad_visibility(vis):
    """
    Spec: "Endpoint-specific field rules take precedence: invalid `amount` values (including strings and booleans), non-string `note` values (including `null`), and any `visibility` other than `public` or `private` are 422 `validation_failed`."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 10)
    before = snapshot(t)
    assert_error(pay_request(t["ada"], q["request_id"], {"visibility": vis}), 422, "validation_failed")
    assert snapshot(t) == before


def test_pay_short_then_payable_later():
    """
    Spec: "**A request may exceed the payer's balance.** That is a legal state, not an error at creation time: the request stays `pending` until it is paid, declined or cancelled, and an attempt to pay it while short is `409 insufficient_funds` and changes nothing."
    Spec: "Money can arrive later and the same request then becomes payable."
    Spec: "| The payer's balance is below `amount` | 409 `insufficient_funds` |"
    """
    t = world()
    q = ask_ok(t["bob"], "cy", 500)
    before = snapshot(t)
    assert_error(pay_request(t["cy"], q["request_id"]), 409, "insufficient_funds")
    assert snapshot(t) == before
    assert find_request(t["cy"], q["request_id"])["status"] == "pending"
    assert pay(t["ada"], "cy", 499).status_code == 201
    assert_error(pay_request(t["cy"], q["request_id"]), 409, "insufficient_funds")
    assert pay(t["dan"], "cy", 1).status_code == 201
    r = pay_request(t["cy"], q["request_id"])
    assert r.status_code == 201, r.text
    assert balance(t["cy"]) == 0
    assert find_request(t["bob"], q["request_id"])["status"] == "paid"


def test_pay_only_payer():
    """
    Spec: "**An idempotent write path.** Only the payer may call it."
    Spec: "| The caller is not the request's payer | 403 `forbidden` |"
    Spec: "Only the payer may pay or decline it; only the requester may cancel it."
    Spec: "| 403 | `forbidden` | Authenticated, but not permitted to touch this resource |"
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    before = snapshot(t)
    assert_error(pay_request(t["bob"], q["request_id"]), 403, "forbidden")
    assert snapshot(t) == before


def test_pay_unknown_request():
    """
    Spec: "| Unknown request | 404 `not_found` |"
    Spec: "| 404 | `not_found` | No such resource, or not visible to this caller |"
    """
    t = world()
    before = snapshot(t)
    assert_error(pay_request(t["ada"], "rq_does_not_exist"), 404, "not_found")
    assert snapshot(t) == before


@pytest.mark.parametrize("final", ["paid", "declined", "cancelled"])
def test_pay_not_pending(final):
    """
    Spec: "| The request is not `pending` | 409 `request_not_pending` |"
    Spec: "A request is `pending`, and then exactly one of `paid`, `declined` or `cancelled`."
    Spec: "A payment request may move money at most once."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    rid = q["request_id"]
    if final == "paid":
        assert pay_request(t["ada"], rid).status_code == 201
    elif final == "declined":
        assert decline(t["ada"], rid).status_code == 200
    else:
        assert cancel(t["bob"], rid).status_code == 200
    before = snapshot(t)
    assert_error(pay_request(t["ada"], rid), 409, "request_not_pending")
    assert snapshot(t) == before
    assert find_request(t["ada"], rid)["status"] == final


def test_pay_replay_after_paid():
    """
    Spec: "Replaying a successful payment returns 200 with its original payment body, including when the request is already `paid`."
    Spec: "It moves no additional money and must not return `409 request_not_pending`."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    k = key()
    first = pay_request(t["ada"], q["request_id"], {"visibility": "private"}, idem=k)
    assert first.status_code == 201
    after = snapshot(t)
    again = pay_request(t["ada"], q["request_id"], {"visibility": "private"}, idem=k)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert snapshot(t) == after


def test_pay_replay_body_must_be_identical():
    """
    Spec: "**A replay must send the identical body** — `{}` and `{"visibility": "public"}` are different JSON values, so reusing a key across the two is `409 idempotency_key_reuse`, per §7."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    k = key()
    assert pay_request(t["ada"], q["request_id"], {}, idem=k).status_code == 201
    after = snapshot(t)
    assert_error(pay_request(t["ada"], q["request_id"], {"visibility": "public"}, idem=k), 409, "idempotency_key_reuse")
    assert snapshot(t) == after
    q2 = ask_ok(t["bob"], "ada", 100)
    k2 = key()
    assert pay_request(t["ada"], q2["request_id"], {"visibility": "public"}, idem=k2).status_code == 201
    assert_error(pay_request(t["ada"], q2["request_id"], {}, idem=k2), 409, "idempotency_key_reuse")


# ---------- decline / cancel ----------

def test_decline():
    """
    Spec: "Returns `200` with the request, `status: "declined"`."
    Spec: "Declining an already-declined request is `200` with the current state — declining twice is not an error."
    Spec: "No idempotency key."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    r = decline(t["ada"], q["request_id"])
    assert r.status_code == 200, r.text
    d = r.json()
    check_request(d)
    assert d["status"] == "declined" and d["request_id"] == q["request_id"] and d["amount"] == 100
    r2 = decline(t["ada"], q["request_id"])
    assert r2.status_code == 200 and r2.json()["status"] == "declined"
    assert find_request(t["bob"], q["request_id"])["status"] == "declined"
    assert balance(t["ada"]) == 10000 and balance(t["bob"]) == 2500


@pytest.mark.parametrize("final", ["paid", "cancelled"])
def test_decline_not_pending(final):
    """
    Spec: "A `paid` or `cancelled` request is `409 request_not_pending`."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    if final == "paid":
        assert pay_request(t["ada"], q["request_id"]).status_code == 201
    else:
        assert cancel(t["bob"], q["request_id"]).status_code == 200
    before = snapshot(t)
    assert_error(decline(t["ada"], q["request_id"]), 409, "request_not_pending")
    assert snapshot(t) == before


def test_decline_only_payer():
    """
    Spec: "Only the payer."
    Spec: "Not the payer is `403 forbidden`."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    before = snapshot(t)
    assert_error(decline(t["bob"], q["request_id"]), 403, "forbidden")
    assert snapshot(t) == before


def test_cancel():
    """
    Spec: "Returns `200` with the request, `status: "cancelled"`."
    Spec: "Cancelling an already-cancelled request is `200`."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    r = cancel(t["bob"], q["request_id"])
    assert r.status_code == 200, r.text
    c = r.json()
    check_request(c)
    assert c["status"] == "cancelled" and c["request_id"] == q["request_id"]
    r2 = cancel(t["bob"], q["request_id"])
    assert r2.status_code == 200 and r2.json()["status"] == "cancelled"
    assert find_request(t["ada"], q["request_id"])["status"] == "cancelled"


@pytest.mark.parametrize("final", ["paid", "declined"])
def test_cancel_not_pending(final):
    """
    Spec: "A `paid` or `declined` request is `409 request_not_pending`."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    if final == "paid":
        assert pay_request(t["ada"], q["request_id"]).status_code == 201
    else:
        assert decline(t["ada"], q["request_id"]).status_code == 200
    before = snapshot(t)
    assert_error(cancel(t["bob"], q["request_id"]), 409, "request_not_pending")
    assert snapshot(t) == before


def test_cancel_only_requester():
    """
    Spec: "Only the requester."
    Spec: "Not the requester is `403 forbidden`."
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 100)
    before = snapshot(t)
    assert_error(cancel(t["ada"], q["request_id"]), 403, "forbidden")
    assert snapshot(t) == before


def test_decline_cancel_unknown():
    """
    Spec: "| 404 | `not_found` | No such resource, or not visible to this caller |"
    """
    t = world()
    assert_error(decline(t["ada"], "rq_nope"), 404, "not_found")
    assert_error(cancel(t["ada"], "rq_nope"), 404, "not_found")


# ---------- GET /requests ----------

def test_list_only_own_requests():
    """
    Spec: "Requests where the caller is the requester or the payer, and no others."
    Spec: "Requests never appear in the activity feed; they are read through `GET /requests`, which returns only requests where the caller is the requester or the payer."
    Spec: "A request carries no visibility of its own and never appears in anyone else's feed."
    Spec: "{ "requests": [ { ...request... } ], "has_more": false }"
    """
    t = world()
    q1 = ask_ok(t["bob"], "ada", 10)
    q2 = ask_ok(t["ada"], "dan", 20)
    q3 = ask_ok(t["dan"], "bob", 30)
    r = get("/requests", t["ada"])
    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is False
    for q in body["requests"]:
        check_request(q)
    assert {q["request_id"] for q in body["requests"]} == {q1["request_id"], q2["request_id"]}
    assert {q["request_id"] for q in requests_of(t["cy"])} == set()
    assert {q["request_id"] for q in requests_of(t["dan"])} == {q2["request_id"], q3["request_id"]}
    for who in t:
        ids = {p["payment_id"] for p in activity(t[who])}
        assert not ids & {q1["request_id"], q2["request_id"], q3["request_id"]}
        assert activity(t[who]) == []


def test_list_direction_and_status_filters():
    """
    Spec: "`direction` is `incoming` (the caller is the payer), `outgoing` (the caller is the requester) or absent for both."
    Spec: "`status` is one of the four statuses, or absent for all."
    Spec: "GET /requests?direction=incoming&status=pending&limit=50&offset=0"
    """
    t = world()
    inc_pending = ask_ok(t["bob"], "ada", 10)
    inc_paid = ask_ok(t["dan"], "ada", 11)
    pay_request(t["ada"], inc_paid["request_id"])
    out_pending = ask_ok(t["ada"], "bob", 12)
    out_cancelled = ask_ok(t["ada"], "dan", 13)
    cancel(t["ada"], out_cancelled["request_id"])
    out_declined = ask_ok(t["ada"], "cy", 14)
    decline(t["cy"], out_declined["request_id"])

    def ids(**params):
        r = get("/requests", t["ada"], params)
        assert r.status_code == 200, r.text
        return {q["request_id"] for q in r.json()["requests"]}

    assert ids(direction="incoming") == {inc_pending["request_id"], inc_paid["request_id"]}
    assert ids(direction="outgoing") == {out_pending["request_id"], out_cancelled["request_id"],
                                         out_declined["request_id"]}
    assert ids(status="pending") == {inc_pending["request_id"], out_pending["request_id"]}
    assert ids(status="paid") == {inc_paid["request_id"]}
    assert ids(status="cancelled") == {out_cancelled["request_id"]}
    assert ids(status="declined") == {out_declined["request_id"]}
    assert ids(direction="incoming", status="pending", limit=50, offset=0) == {inc_pending["request_id"]}
    assert ids(direction="outgoing", status="paid") == set()
    assert len(ids()) == 5


def test_list_newest_first():
    """
    Spec: "Newest first by `created_at`."
    """
    t = world()
    for i in range(4):
        ask_ok(t["bob"], "ada", 10 + i)
    items = requests_of(t["ada"])
    assert len(items) == 4
    assert_newest_first(items)


def test_list_pagination_and_default_limit():
    """
    Spec: "`limit` defaults to 50, range 1 to 200."
    Spec: "`has_more` is true when items exist beyond the last one returned."
    """
    reqs = [{"id": f"rq_s{i}", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 100 + i,
             "note": f"n{i}", "status": "pending"} for i in range(55)]
    t = world(requests=reqs)
    r = get("/requests", t["ada"])
    assert r.status_code == 200
    assert len(r.json()["requests"]) == 50 and r.json()["has_more"] is True
    r = get("/requests", t["ada"], {"limit": 200})
    assert len(r.json()["requests"]) == 55 and r.json()["has_more"] is False
    r = get("/requests", t["ada"], {"limit": 55})
    assert len(r.json()["requests"]) == 55 and r.json()["has_more"] is False
    r = get("/requests", t["ada"], {"limit": 54})
    assert len(r.json()["requests"]) == 54 and r.json()["has_more"] is True
    r = get("/requests", t["ada"], {"limit": 1})
    assert len(r.json()["requests"]) == 1 and r.json()["has_more"] is True
    r = get("/requests", t["ada"], {"limit": 10, "offset": 50})
    assert len(r.json()["requests"]) == 5 and r.json()["has_more"] is False
    r = get("/requests", t["ada"], {"offset": 55})
    assert r.json()["requests"] == [] and r.json()["has_more"] is False
    r = get("/requests", t["ada"], {"offset": 1000})
    assert r.status_code == 200 and r.json()["requests"] == []
    sizes = [len(get("/requests", t["ada"], {"limit": 20, "offset": off}).json()["requests"]) for off in (0, 20, 40)]
    assert sizes == [20, 20, 15]


@pytest.mark.parametrize("params", [{"limit": "0"}, {"limit": "201"}, {"limit": "-1"}, {"offset": "-1"},
                                    {"limit": "1e9"}, {"limit": "4.0"}, {"limit": "+4"}, {"offset": "1e1"},
                                    {"offset": "2.0"}, {"offset": "+2"}, {"limit": "abc"},
                                    {"direction": "sideways"}, {"status": "open"}, {"status": "PAID"}])
def test_list_bad_query(params):
    """
    Spec: "`offset` defaults to 0 and must be 0 or more. Outside either range is 422 `validation_failed`."
    Spec: "An unknown `direction` or `status` value is also 422."
    Spec: "An integer-valued **query parameter** is written as plain decimal digits: `1e9`, `4.0` and `+4` are 422 `validation_failed` whatever their numeric value."
    Spec: "| `limit` | integer 1 to 200 | 422 `validation_failed` |"
    Spec: "| `offset` | integer 0 or more | 422 `validation_failed` |"
    Spec: "This includes invalid dates, negative counts and values exceeding a stated maximum or length."
    """
    t = world()
    assert_error(get("/requests", t["ada"], params), 422, "validation_failed")


def test_list_limit_bounds_ok():
    """
    Spec: "| `limit` | integer 1 to 200 | 422 `validation_failed` |"
    """
    t = world()
    for limit in ("1", "200"):
        assert get("/requests", t["ada"], {"limit": limit}).status_code == 200
    assert get("/requests", t["ada"], {"offset": "0"}).status_code == 200
