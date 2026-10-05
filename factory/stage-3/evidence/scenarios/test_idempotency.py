import json

import pytest

from pf import (CLIENT, ask_ok, assert_error, balance, balances, key, pay, pay_request, post, simultaneously,
                snapshot, world)

OPS = ["payments", "requests", "pay", "splits", "settlements"]


def setup(op, t):
    """Return (token, path, body, other_body, invalid_body) for one of the five idempotent write paths."""
    if op == "payments":
        return (t["ada"], "/payments", {"to_handle": "bob", "amount": 100, "note": "x"},
                {"to_handle": "bob", "amount": 101, "note": "x"}, {"to_handle": "bob", "amount": -1, "note": "x"})
    if op == "requests":
        return (t["bob"], "/requests", {"payer_handle": "ada", "amount": 100, "note": "x"},
                {"payer_handle": "ada", "amount": 101, "note": "x"}, {"payer_handle": "ada", "amount": -1, "note": "x"})
    if op == "pay":
        q = ask_ok(t["bob"], "ada", 100)
        return (t["ada"], f"/requests/{q['request_id']}/pay", {"visibility": "public"}, {"visibility": "private"},
                {"visibility": "nope"})
    if op == "splits":
        return (t["ada"], "/splits", {"amount": 300, "participant_handles": ["ada", "bob", "cy"], "note": "x"},
                {"amount": 301, "participant_handles": ["ada", "bob", "cy"], "note": "x"},
                {"amount": -1, "participant_handles": ["ada", "bob", "cy"], "note": "x"})
    return (t["op"], "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100}]},
            {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 101}]}, {"transfers": []})


@pytest.mark.parametrize("op", OPS)
def test_missing_or_empty_key(op):
    """
    Spec: "| 400 | `missing_idempotency_key` | Required `Idempotency-Key` header absent or empty |"
    Spec: "| Header absent or empty | 400 `missing_idempotency_key` |"
    Spec: "Five write paths require an idempotency key (§8 and §11): **`POST /payments`**, **`POST /requests`**, **`POST /requests/{id}/pay`**, **`POST /splits`** and **`POST /settlements`**."
    Spec: "**An idempotent write path.** `Idempotency-Key` is required; see §7."
    Spec: "Idempotency-Key: <client-chosen string, 1..255 characters>"
    """
    t = world()
    token, path, body, _, _ = setup(op, t)
    before = snapshot(t)
    assert_error(post(path, token, body), 400, "missing_idempotency_key")
    assert_error(post(path, token, body, idem=""), 400, "missing_idempotency_key")
    assert snapshot(t) == before


@pytest.mark.parametrize("op", OPS)
def test_key_length(op):
    """
    Spec: "| `Idempotency-Key` | 1 to 255 characters | 422 `validation_failed` |"
    Spec: "| First use of the key | The normal response, **201** |"
    """
    t = world()
    token, path, body, _, _ = setup(op, t)
    before = snapshot(t)
    assert_error(post(path, token, body, idem="k" * 256), 422, "validation_failed")
    assert snapshot(t) == before
    r = post(path, token, body, idem="K" * 255)
    assert r.status_code == 201, r.text


def test_key_one_char():
    """
    Spec: "| `Idempotency-Key` | 1 to 255 characters | 422 `validation_failed` |"
    """
    t = world()
    assert pay(t["ada"], "bob", 5, idem="z").status_code == 201


@pytest.mark.parametrize("op", OPS)
def test_replay_returns_original(op):
    """
    Spec: "| Replay: same key, same body | **200**, body identical to the original response as a JSON value |"
    Spec: "A replay means the same user sending the **same method, the same path and the same body**."
    Spec: "It makes no further state changes."
    Spec: "Everything below applies to each of them independently."
    Spec: "Replays return 200 with the original complete response."
    """
    t = world()
    token, path, body, _, _ = setup(op, t)
    k = key()
    first = post(path, token, body, idem=k)
    assert first.status_code == 201, first.text
    after = snapshot(t)
    for _ in range(2):
        again = post(path, token, body, idem=k)
        assert again.status_code == 200, again.text
        assert again.json() == first.json()
    assert snapshot(t) == after


@pytest.mark.parametrize("op", OPS)
def test_replay_key_order_whitespace(op):
    """
    Spec: "means the same JSON value after parsing — key order and whitespace do not matter."
    """
    t = world()
    token, path, body, _, _ = setup(op, t)
    k = key()
    first = post(path, token, body, idem=k)
    assert first.status_code == 201
    after = snapshot(t)
    reordered = dict(reversed(list(body.items())))
    raw = "\n  " + json.dumps(reordered, indent=4) + "  \n"
    again = post(path, token, raw=raw, idem=k)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert snapshot(t) == after


@pytest.mark.parametrize("op", OPS)
def test_same_key_different_body(op):
    """
    Spec: "| Same key, different body | 409 `idempotency_key_reuse` |"
    Spec: "| 409 | `idempotency_key_reuse` | Key already used by this caller with a different request body |"
    """
    t = world()
    token, path, body, other, _ = setup(op, t)
    k = key()
    assert post(path, token, body, idem=k).status_code == 201
    after = snapshot(t)
    assert_error(post(path, token, other, idem=k), 409, "idempotency_key_reuse")
    assert snapshot(t) == after


@pytest.mark.parametrize("op", OPS)
def test_claimed_key_before_validation(op):
    """
    Spec: "After the body has parsed as a JSON object and the caller is authenticated, an already claimed key is resolved before endpoint field validation or current-resource checks."
    Spec: "Thus changing a successful request to an invalid body with the same key still returns `409 idempotency_key_reuse`."
    """
    t = world()
    token, path, body, _, invalid = setup(op, t)
    k = key()
    assert post(path, token, body, idem=k).status_code == 201
    after = snapshot(t)
    assert_error(post(path, token, invalid, idem=k), 409, "idempotency_key_reuse")
    assert snapshot(t) == after


@pytest.mark.parametrize("op", OPS)
def test_key_reusable_after_4xx(op):
    """
    Spec: "| Key reused after the original request failed with 4xx | Treated as a first use |"
    """
    t = world()
    token, path, body, _, invalid = setup(op, t)
    k = key()
    assert post(path, token, invalid, idem=k).status_code == 422
    r = post(path, token, body, idem=k)
    assert r.status_code == 201, r.text
    assert post(path, token, body, idem=k).status_code == 200


def test_key_reusable_after_insufficient_funds():
    """
    Spec: "| Key reused after the original request failed with 4xx | Treated as a first use |"
    """
    t = world()
    k = key()
    assert_error(pay(t["cy"], "bob", 50, idem=k), 409, "insufficient_funds")
    assert pay(t["ada"], "cy", 50).status_code == 201
    r = pay(t["cy"], "bob", 50, idem=k)
    assert r.status_code == 201, r.text
    assert balance(t["cy"]) == 0


def test_key_scoped_per_user():
    """
    Spec: "The key is scoped to **the authenticated user**."
    Spec: "Two different users may use the same key string with no interaction between them."
    """
    t = world()
    k = "shared-key-1"
    a = pay(t["ada"], "cy", 100, idem=k)
    b = pay(t["bob"], "cy", 100, idem=k)
    d = post("/payments", t["dan"], {"to_handle": "cy", "amount": 7}, k)
    assert (a.status_code, b.status_code, d.status_code) == (201, 201, 201)
    assert a.json()["payment_id"] != b.json()["payment_id"]
    assert balance(t["cy"]) == 207
    q1 = ask_ok(t["bob"], "ada", 10)
    q2 = ask_ok(t["bob"], "dan", 10)
    assert pay_request(t["ada"], q1["request_id"], {}, idem="k2").status_code == 201
    assert pay_request(t["dan"], q2["request_id"], {}, idem="k2").status_code == 201
    s1 = post("/splits", t["ada"], {"amount": 10, "participant_handles": ["bob"], "note": "s"}, "k3")
    s2 = post("/splits", t["bob"], {"amount": 10, "participant_handles": ["ada"], "note": "s"}, "k3")
    assert (s1.status_code, s2.status_code) == (201, 201)
    r1 = post("/requests", t["ada"], {"payer_handle": "dan", "amount": 5, "note": "r"}, "k4")
    r2 = post("/requests", t["bob"], {"payer_handle": "dan", "amount": 5, "note": "r"}, "k4")
    assert (r1.status_code, r2.status_code) == (201, 201)


def test_same_key_same_body_different_path():
    """
    Spec: "The same key with the same body on a different path is a different request, not a replay, and must succeed normally."
    """
    t = world()
    q1 = ask_ok(t["bob"], "ada", 10)
    q2 = ask_ok(t["bob"], "ada", 20)
    a = pay_request(t["ada"], q1["request_id"], {}, idem="same")
    b = pay_request(t["ada"], q2["request_id"], {}, idem="same")
    assert (a.status_code, b.status_code) == (201, 201), (a.text, b.text)
    assert a.json()["payment_id"] != b.json()["payment_id"]
    assert balance(t["ada"]) == 9970


def test_replay_after_resource_changes():
    """
    Spec: "A successful replay returns the original response, even after the resource changes or is cancelled."
    """
    t = world()
    k = key()
    body = {"payer_handle": "ada", "amount": 70, "note": "x"}
    first = post("/requests", t["bob"], body, k)
    assert first.status_code == 201
    assert post(f"/requests/{first.json()['request_id']}/cancel", t["bob"]).status_code == 200
    again = post("/requests", t["bob"], body, k)
    assert again.status_code == 200 and again.json() == first.json()
    assert again.json()["status"] == "pending"
    k2 = key()
    p = pay(t["bob"], "ada", 2500, idem=k2)
    assert p.status_code == 201 and balance(t["bob"]) == 0
    again = pay(t["bob"], "ada", 2500, idem=k2)
    assert again.status_code == 200 and again.json() == p.json()
    assert balance(t["bob"]) == 0


@pytest.mark.parametrize("op", OPS)
def test_concurrent_identical_requests(op):
    """
    Spec: "For concurrent identical requests with an unused key, exactly one returns 201."
    Spec: "The others return 200 with the same body."
    Spec: "The operation takes effect only once."
    """
    t = world()
    token, path, body, _, _ = setup(op, t)
    k = key()
    before = balances(t)
    results = simultaneously([lambda c: post(path, token, body, idem=k, client=c) for _ in range(8)])
    codes = sorted(r.status_code for r in results)
    assert codes == [200] * 7 + [201], [(r.status_code, r.text) for r in results]
    bodies = [r.json() for r in results]
    assert all(b == bodies[0] for b in bodies)
    after = balances(t)
    if op in ("payments", "pay", "settlements"):
        assert after["ada"] == before["ada"] - 100 and after["bob"] == before["bob"] + 100
    else:
        assert after == before
    if op == "requests":
        from pf import requests_of
        assert len(requests_of(t["bob"])) == 1
    if op == "splits":
        from pf import requests_of
        assert len(requests_of(t["ada"])) == 2
