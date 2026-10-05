from pf import (CLIENT, PASSWORD, assert_error, ask_ok, balance, check_payment, check_request, fixture, get,
                is_id, is_int, key, login, me, pay, post, reset, requests_of, activity, snapshot, user, world)


def test_health_ok():
    """
    Spec: "GET /health  ->  200  {"status": "ok"}"
    Spec: "Return 200 once the service and its data store can serve requests, within 60 seconds of container start."
    """
    r = CLIENT.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_reset_returns_204_without_auth():
    """
    Spec: "POST /_test/reset Content-Type: application/json"
    Spec: "This test endpoint must be enabled in the delivered image and requires no authentication."
    """
    r = CLIENT.post("/_test/reset", json=fixture())
    assert r.status_code == 204, r.text


def test_reset_replaces_all_state():
    """
    Spec: "Replace all service state with the fixture in the request body (§4)."
    Spec: "When reset returns 204, subsequent requests must see only that fixture."
    Spec: "Repeated resets are supported."
    """
    t = world()
    assert pay(t["ada"], "bob", 100).status_code == 201
    ask_ok(t["bob"], "ada", 50)
    s = post("/auth/signup", body={"email": "newbie@example.org", "password": PASSWORD, "display_name": "N"})
    assert s.status_code == 201
    reset(fixture(users=[user("zed", 700), user("yan", 300)]))
    reset(fixture(users=[user("ada", 1000), user("bob", 1)]))
    ada = login("ada@example.com")
    bob = login("bob@example.com")
    assert balance(ada) == 1000 and balance(bob) == 1
    assert activity(ada) == [] and activity(bob) == []
    assert requests_of(ada) == [] and requests_of(bob) == []
    r = CLIENT.post("/auth/login", json={"email": "newbie@example.org", "password": PASSWORD})
    assert_error(r, 401, "unauthenticated")
    r = CLIENT.post("/auth/login", json={"email": "zed@example.com", "password": PASSWORD})
    assert_error(r, 401, "unauthenticated")
    assert_error(get("/me", s.json()["token"]), 401, "unauthenticated")


def test_seeded_fixture_visible():
    """
    Spec: "Seeded users must be able to log in with the given password immediately."
    Spec: "`balance` is the wallet balance **after** every seeded payment has been applied. Seeded numbers are consistent; you do not replay seeded payments against balances."
    Spec: "Seeded users take their handle from the fixture."
    Spec: ""payments": [ { "id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob","
    Spec: ""requests": [ { "id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada","
    """
    body = fixture(users=[user("ada", 10000, password="s3cret pass"), user("bob", 2500)],
                   payments=[{"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500,
                              "note": "coffee", "visibility": "public"}],
                   requests=[{"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200,
                              "note": "taxi", "status": "pending"}])
    reset(body)
    ada = login("ada@example.com", "s3cret pass")
    bob = login("bob@example.com")
    ma, mb = me(ada), me(bob)
    assert ma["handle"] == "ada" and mb["handle"] == "bob"
    assert ma["balance"] == 10000 and mb["balance"] == 2500
    acts = activity(bob)
    assert len(acts) == 1
    check_payment(acts[0])
    p = acts[0]
    assert (p["amount"], p["note"], p["visibility"]) == (500, "coffee", "public")
    assert p["from_user_id"] == ma["user_id"] and p["to_user_id"] == mb["user_id"]
    assert p["from_handle"] == "ada" and p["to_handle"] == "bob"
    reqs = requests_of(ada)
    assert len(reqs) == 1
    check_request(reqs[0])
    q = reqs[0]
    assert (q["amount"], q["note"], q["status"]) == (1200, "taxi", "pending")
    assert q["requester_id"] == mb["user_id"] and q["payer_id"] == ma["user_id"]


def test_negative_seed_balance_rejected_and_nothing_changes():
    """
    Spec: "A `balance` below zero in a fixture is a reset error: return `422 validation_failed` from `POST /_test/reset` and change nothing."
    """
    t = world()
    assert pay(t["ada"], "bob", 100, note="before").status_code == 201
    before = snapshot(t)
    r = CLIENT.post("/_test/reset", json=fixture(users=[user("ada", 100), user("neg", -1)]))
    assert_error(r, 422, "validation_failed")
    assert snapshot(t) == before


def test_me_shape_and_currency_eur():
    """
    Spec: "{ "user_id": "u_ada", "display_name": "Ada", "handle": "ada","
    Spec: "The service has **one currency**, declared in the fixture."
    """
    reset(fixture(users=[user("ada", 10000, name="Ada")]))
    m = me(login("ada@example.com"))
    assert is_id(m["user_id"])
    assert m["display_name"] == "Ada" and m["handle"] == "ada"
    assert m["balance"] == 10000 and is_int(m["balance"])
    assert m["currency"] == "EUR" and m["minor_units"] == 2


def test_minor_units_zero_and_three():
    """
    Spec: "`minor_units` is `0`, `2` or `3`. Fixtures use `EUR` (2), `JPY` (0) and `BHD` (3)."
    Spec: "Every amount in the API is an integer count of its minor units: `1000` in a `minor_units: 2` service is €10.00, and `1000` in a `minor_units: 0` service is ¥1000."
    """
    for currency, units in (("JPY", 0), ("BHD", 3)):
        t = world(currency=currency, minor_units=units)
        m = me(t["ada"])
        assert m["currency"] == currency and m["minor_units"] == units
        r = pay(t["ada"], "bob", 1000)
        assert r.status_code == 201
        assert r.json()["currency"] == currency and r.json()["amount"] == 1000
        assert balance(t["ada"]) == 9000 and balance(t["bob"]) == 3500


def test_large_balances_exact():
    """
    Spec: "`amount` is at most `1000000000` on any single request, and no operation produces a balance outside ±2⁵³."
    Spec: "Monetary arithmetic must preserve exact minor-unit values without rounding error."
    Spec: "All amounts are exact integer counts of minor units."
    """
    big = 9007199254740000
    t = world(balances={"ada": big, "bob": 991})
    assert balance(t["ada"]) == big
    r = pay(t["ada"], "bob", 1000000000)
    assert r.status_code == 201, r.text
    assert r.json()["amount"] == 1000000000
    assert balance(t["ada"]) == big - 1000000000
    assert balance(t["bob"]) == 1000000991
    r = pay(t["ada"], "bob", 1)
    assert r.status_code == 201
    assert balance(t["ada"]) + balance(t["bob"]) == big + 991


def test_json_content_type():
    """
    Spec: "Requests and responses are `application/json; charset=utf-8`."
    """
    t = world()
    for r in (get("/me", t["ada"]), pay(t["ada"], "bob", 1), get("/me", "nope")):
        ctype = r.headers.get("content-type", "").lower()
        assert ctype.split(";")[0].strip() == "application/json", ctype
        if "charset" in ctype:
            assert "utf-8" in ctype


def test_unknown_fields_and_query_params_ignored():
    """
    Spec: "Unknown fields in a request body are ignored, never an error."
    Spec: "Unknown query parameters are ignored."
    """
    t = world()
    r = post("/payments", t["ada"], {"to_handle": "bob", "amount": 10, "colour": "red", "extra": {"a": [1]}}, key())
    assert r.status_code == 201, r.text
    r = post("/requests", t["bob"], {"payer_handle": "ada", "amount": 10, "note": "n", "zzz": 1}, key())
    assert r.status_code == 201, r.text
    r = post("/auth/login", body={"email": "ada@example.com", "password": PASSWORD, "remember": True})
    assert r.status_code == 200
    r = get("/activity", t["ada"], {"foo": "bar", "sort": "weird"})
    assert r.status_code == 200
    r = get("/requests", t["ada"], {"foo": "bar"})
    assert r.status_code == 200
    r = get("/me", t["ada"], {"verbose": "1"})
    assert r.status_code == 200


def test_ids_and_timestamps_format():
    """
    Spec: "IDs are opaque strings of at most 64 characters."
    Spec: "Timestamps in responses are RFC 3339 with an explicit offset, e.g. `2026-09-24T19:00:00+02:00`."
    """
    t = world()
    p = pay(t["ada"], "bob", 10).json()
    check_payment(p)
    q = ask_ok(t["bob"], "ada", 10)
    check_request(q)
    s = post("/splits", t["ada"], {"amount": 30, "participant_handles": ["ada", "bob"], "note": "s"}, key()).json()
    assert is_id(s["split_id"])
    from pf import is_ts
    assert is_ts(s["created_at"])


def test_settlement_operator_ids_default_empty():
    """
    Spec: "The reset fixture may include `settlement_operator_ids`, an array of user ids, default []."
    """
    reset(fixture())
    op = login("op@example.com")
    ada = login("ada@example.com")
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}
    assert_error(post("/settlements", op, body, key()), 403, "forbidden")
    assert_error(post("/settlements", ada, body, key()), 403, "forbidden")
    assert balance(ada) == 10000
