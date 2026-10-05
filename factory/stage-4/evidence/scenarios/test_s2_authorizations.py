import time

import pytest

from pf import (CLIENT, activity, ask_ok, assert_error, assert_newest_first, authorize, authorize_ok, auths_of,
                balances, capture, check_auth, check_payment, find_auth, fixture, get, key, me, parse_ts, pay,
                pay_request, post, reset, seeded_auth, snapshot, user, void, wallet, world2)


def held(token):
    return wallet(token)["held"]


def avail(token):
    return wallet(token)["available"]


# ---------- GET /me ----------

def test_me_total_available_held_without_holds():
    """
    Spec: "`GET /me` keeps `balance`, and `balance` **equals `total`**. `available` and `held` are new fields beside it."
    Spec: "With no open holds, `balance`, `total` and `available` agree and `held` is zero, and every earlier behaviour is unchanged."
    Spec: "With no open holds, the result is unchanged."
    Spec: "An earlier fixture may omit `authorizations` altogether; omission means an empty list."
    """
    t = world2()
    for h, tok in t.items():
        m = wallet(tok)
        assert m["balance"] == m["total"] == m["available"]
        assert m["held"] == 0


def test_me_with_seeded_hold():
    """
    Spec: "{ "user_id": "u_ada", "display_name": "Ada", "handle": "ada","
    Spec: "`balance` and `total` are always equal."
    Spec: "`held` is the sum of open holds, and `available` is `total − held`, never negative."
    Spec: "A user's seeded `balance` is still `total`. **`available` is derived, never seeded** — the service subtracts the seeded open holds itself."
    Spec: "The fixture gains a service-wide default lifetime and an `authorizations` array."
    Spec: ""authorization_ttl_seconds": 600,"
    """
    t = world2(authorizations=[seeded_auth("a_1", "ada", "bob", 2000), seeded_auth("a_2", "ada", "cy", 500)])
    m = wallet(t["ada"])
    assert (m["balance"], m["total"], m["available"], m["held"]) == (10000, 10000, 7500, 2500)
    b = wallet(t["bob"])
    assert (b["total"], b["available"], b["held"]) == (2500, 2500, 0)


def test_seeded_statuses_only_open_holds():
    """
    Spec: "Seeded `status` is `open`, `captured`, `voided` or `expired`. Only `open` holds anything."
    Spec: "Seeded authorisations carry their own absolute `expires_at` instead."
    """
    t = world2(authorizations=[seeded_auth("a_1", "ada", "bob", 100, status="captured"),
                               seeded_auth("a_2", "ada", "bob", 200, status="voided"),
                               seeded_auth("a_3", "ada", "bob", 400, status="expired", expires_in=-7200),
                               seeded_auth("a_4", "ada", "bob", 800, status="open")])
    m = wallet(t["ada"])
    assert m["held"] == 800 and m["available"] == 9200
    statuses = sorted(a["status"] for a in auths_of(t["ada"]))
    assert statuses == ["captured", "expired", "open", "voided"]


def test_seeded_open_past_expiry_holds_nothing():
    """
    Spec: "An authorization whose `expires_at` is at or before now is `expired` and holds no funds."
    Spec: "Seeded expiry times are at least an hour from reset time, in the past or future; newly created authorizations may have shorter lifetimes."
    """
    t = world2(authorizations=[seeded_auth("a_old", "ada", "bob", 3000, status="open", expires_in=-7200)])
    m = wallet(t["ada"])
    assert m["held"] == 0 and m["available"] == 10000
    a = auths_of(t["ada"])
    assert len(a) == 1 and a[0]["status"] == "expired"
    assert auths_of(t["ada"], status="open") == []
    assert len(auths_of(t["ada"], status="expired")) == 1


def test_reset_rejects_overheld_seed():
    """
    Spec: "A sum of seeded unexpired open holds larger than that user's `balance` is a reset error: `422 validation_failed` from `POST /_test/reset`, changing nothing, exactly like a negative seeded balance."
    """
    t = world2(authorizations=[seeded_auth("a_1", "ada", "bob", 100)])
    before = snapshot(t)
    bad = fixture(users=[user("ada", 1000), user("bob", 0)])
    bad["authorizations"] = [seeded_auth("x1", "ada", "bob", 600), seeded_auth("x2", "ada", "bob", 401)]
    r = CLIENT.post("/_test/reset", json=bad)
    assert_error(r, 422, "validation_failed")
    assert snapshot(t) == before
    ok = fixture(users=[user("ada", 1000), user("bob", 0)])
    ok["authorizations"] = [seeded_auth("x1", "ada", "bob", 600), seeded_auth("x2", "ada", "bob", 400),
                            seeded_auth("x3", "ada", "bob", 5000, expires_in=-7200),
                            seeded_auth("x4", "ada", "bob", 5000, status="voided")]
    reset(ok)


@pytest.mark.parametrize("ttl", [0, -5])
def test_reset_rejects_bad_ttl(ttl):
    """
    Spec: "`authorization_ttl_seconds` applies to every authorisation created through the API. It defaults to 600 when omitted. If supplied, it must be a positive integer number of seconds."
    """
    t = world2()
    before = snapshot(t)
    bad = fixture()
    bad["authorization_ttl_seconds"] = ttl
    assert_error(CLIENT.post("/_test/reset", json=bad), 422, "validation_failed")
    assert snapshot(t) == before


@pytest.mark.parametrize("ttl,expected", [(None, 600), (3600, 3600), (1, 1)])
def test_expires_at_is_created_plus_ttl(ttl, expected):
    """
    Spec: "`expires_at` is `created_at` plus `authorization_ttl_seconds`."
    Spec: "`authorization_ttl_seconds` applies to every authorisation created through the API. It defaults to 600 when omitted. If supplied, it must be a positive integer number of seconds."
    """
    t = world2(ttl=ttl)
    a = authorize_ok(t["ada"], "bob", 100)
    delta = (parse_ts(a["expires_at"]) - parse_ts(a["created_at"])).total_seconds()
    assert abs(delta - expected) < 1.0, (a["created_at"], a["expires_at"])


# ---------- POST /authorizations ----------

def test_authorize_creates_hold():
    """
    Spec: "{ "to_handle": "bob", "amount": 2000, "note": "deposit", "visibility": "private" }"
    Spec: ""authorization_id": "a_4","
    Spec: "The caller is the payer."
    Spec: "A payment may be **authorised** now and **captured** later, for the full amount or less."
    Spec: "An authorisation places a *hold* on the payer's wallet: it reserves money without moving it."
    Spec: "A hold moves no money; payments, settlements and captures transfer money between wallets."
    Spec: "Every authorization response adds `remaining_amount`: the amount still held, zero when closed."
    Spec: "They can also reserve money for a recipient to collect later, in one or more captures."
    """
    t = world2()
    ma, mb = me(t["ada"]), me(t["bob"])
    r = authorize(t["ada"], "bob", 2000, note="deposit", visibility="private")
    assert r.status_code == 201, r.text
    a = r.json()
    check_auth(a)
    assert a["from_user_id"] == ma["user_id"] and a["from_handle"] == "ada"
    assert a["to_user_id"] == mb["user_id"] and a["to_handle"] == "bob"
    assert (a["amount"], a["captured_amount"], a["remaining_amount"], a["currency"]) == (2000, 0, 2000, "EUR")
    assert (a["note"], a["visibility"], a["status"], a["payment_id"]) == ("deposit", "private", "open", None)
    w = wallet(t["ada"])
    assert (w["total"], w["held"], w["available"]) == (10000, 2000, 8000)
    assert wallet(t["bob"])["total"] == 2500 and wallet(t["bob"])["held"] == 0
    assert find_auth(t["bob"], a["authorization_id"])["status"] == "open"


def test_authorize_defaults():
    """
    Spec: "`note` and `visibility` are optional with the same defaults as `POST /payments`."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 10)
    assert a["note"] == "" and a["visibility"] == "public"


def test_open_authorization_not_in_feed():
    """
    Spec: "An open authorisation is **not** a feed item and never appears in `GET /activity`."
    """
    t = world2()
    authorize_ok(t["ada"], "bob", 10, visibility="public")
    for tok in t.values():
        assert activity(tok) == []


def test_authorize_insufficient_available():
    """
    Spec: "| The caller's `available` is below `amount` | 409 `insufficient_funds` |"
    Spec: "Held funds cannot fund new payments, authorizations or settlement net debits."
    """
    t = world2(authorizations=[seeded_auth("a_1", "bob", "ada", 2000)])
    before = snapshot(t)
    assert_error(authorize(t["bob"], "cy", 501), 409, "insufficient_funds")
    assert_error(authorize(t["cy"], "bob", 1), 409, "insufficient_funds")
    assert snapshot(t) == before
    assert authorize(t["bob"], "cy", 500).status_code == 201
    assert avail(t["bob"]) == 0


@pytest.mark.parametrize("amount", [0, -1, 1000000001, 1.5, "10", True])
def test_authorize_invalid_amount(amount):
    """
    Spec: "| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |"
    """
    t = world2(balances={"ada": 2000000000, "bob": 0})
    before = snapshot(t)
    assert_error(post("/authorizations", t["ada"], {"to_handle": "bob", "amount": amount}, key()), 422,
                 "validation_failed")
    assert snapshot(t) == before


def test_authorize_field_rules():
    """
    Spec: "| `to_handle` is the caller's own handle | 422 `self_payment` |"
    Spec: "| `note` over 200 characters, or `visibility` neither `public` nor `private` | 422 `validation_failed` |"
    Spec: "| No user has that handle | 404 `not_found` |"
    """
    t = world2()
    before = snapshot(t)
    assert_error(authorize(t["ada"], "ada", 10), 422, "self_payment")
    assert_error(authorize(t["ada"], "bob", 10, note="n" * 201), 422, "validation_failed")
    assert_error(authorize(t["ada"], "bob", 10, visibility="friends"), 422, "validation_failed")
    assert_error(authorize(t["ada"], "ghost", 10), 404, "not_found")
    assert snapshot(t) == before
    assert authorize(t["ada"], "bob", 10, note="n" * 200).status_code == 201
    raw = '{"to_handle": "bob", "amount": 1e3}'
    r = post("/authorizations", t["ada"], raw=raw, idem=key())
    assert r.status_code == 201 and r.json()["amount"] == 1000


def test_held_funds_block_payments_requests_settlements():
    """
    Spec: "Every `409 insufficient_funds` in stage 1 — on `POST /payments`, `POST /requests/{id}/pay` and settlements — is now evaluated against `available`."
    Spec: "`available = total − held` must never be negative."
    Spec: "Held funds cannot fund new payments, authorizations or settlement net debits."
    """
    t = world2(authorizations=[seeded_auth("a_1", "bob", "ada", 2000)])
    q = ask_ok(t["ada"], "bob", 600)
    before = snapshot(t)
    assert_error(pay(t["bob"], "cy", 501), 409, "insufficient_funds")
    assert_error(pay_request(t["bob"], q["request_id"]), 409, "insufficient_funds")
    body = {"transfers": [{"from_handle": "bob", "to_handle": "cy", "amount": 501}]}
    assert_error(post("/settlements", t["op"], body, key()), 409, "insufficient_funds")
    assert snapshot(t) == before
    assert pay(t["bob"], "cy", 500).status_code == 201
    assert wallet(t["bob"])["available"] == 0 and wallet(t["bob"])["held"] == 2000


def test_payments_have_null_authorization_id():
    """
    Spec: "Payments created without an authorisation carry `authorization_id: null`; their existing `request_id` semantics are unchanged."
    Spec: "`POST /payments` remains an immediate transfer. It must not leave an intermediate hold or require a separate capture."
    Spec: "Paying a request remains immediate."
    """
    t = world2()
    p = pay(t["ada"], "bob", 100).json()
    assert "authorization_id" in p and p["authorization_id"] is None and p["request_id"] is None
    assert wallet(t["ada"])["held"] == 0 and wallet(t["bob"])["total"] == 2600
    q = ask_ok(t["bob"], "ada", 50)
    pr = pay_request(t["ada"], q["request_id"]).json()
    assert pr["authorization_id"] is None and pr["request_id"] == q["request_id"]
    assert wallet(t["ada"])["held"] == 0 and wallet(t["bob"])["total"] == 2650
    for item in activity(t["ada"]):
        assert item["authorization_id"] is None


def test_splits_unchanged():
    """
    Spec: "`POST /splits` is unchanged."
    Spec: "Authorizing a request is out of scope."
    """
    t = world2()
    s = post("/splits", t["ada"], {"amount": 1000, "participant_handles": ["ada", "bob", "cy"], "note": "d"}, key())
    assert s.status_code == 201
    assert [x["amount"] for x in s.json()["shares"]] == [334, 333, 333]
    assert wallet(t["ada"])["held"] == 0


# ---------- capture ----------

def test_full_capture_default():
    """
    Spec: "{ "amount": 1500 }"
    Spec: "`amount` is optional and defaults to the authorisation's remaining amount."
    Spec: "Returns `201` with the created **payment**, in exactly the shape `POST /payments` returns, with `authorization_id` set to this authorisation and `request_id: null`."
    Spec: "The payment's `amount` is the captured amount; its `note` and `visibility` are copied from the authorisation; it appears in the activity feed by the ordinary visibility rule."
    Spec: "Capturing moves the money; a final capture also releases whatever was not captured."
    Spec: "Only the receiver (the `to` party) may capture."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 2000, note="deposit", visibility="private")
    r = capture(t["bob"], a["authorization_id"])
    assert r.status_code == 201, r.text
    p = r.json()
    check_payment(p)
    assert p["authorization_id"] == a["authorization_id"] and p["request_id"] is None
    assert (p["amount"], p["note"], p["visibility"], p["from_handle"], p["to_handle"]) == \
        (2000, "deposit", "private", "ada", "bob")
    wa, wb = wallet(t["ada"]), wallet(t["bob"])
    assert (wa["total"], wa["held"], wa["available"]) == (8000, 0, 8000)
    assert (wb["total"], wb["available"]) == (4500, 4500)
    now = find_auth(t["ada"], a["authorization_id"])
    check_auth(now)
    assert (now["status"], now["captured_amount"], now["payment_id"], now["remaining_amount"]) == \
        ("captured", 2000, p["payment_id"], 0)
    assert p["payment_id"] in {x["payment_id"] for x in activity(t["ada"])}
    assert p["payment_id"] in {x["payment_id"] for x in activity(t["bob"])}
    assert p["payment_id"] not in {x["payment_id"] for x in activity(t["cy"])}


def test_partial_final_capture_releases_remainder():
    """
    Spec: "By default the authorisation becomes `captured`, carries `captured_amount` and `payment_id`, and **releases the uncaptured remainder immediately**: capturing 1500 of 2000 returns 500 to the payer's `available` in the same step."
    Spec: "**Default: one final capture per authorisation.** A second capture after a final capture is `409 authorization_not_open`."
    Spec: "| The authorisation is not `open` | 409 `authorization_not_open` |"
    Spec: "A closed hold cannot be captured again."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 2000, visibility="public")
    assert wallet(t["ada"])["available"] == 8000
    r = capture(t["bob"], a["authorization_id"], {"amount": 1500})
    assert r.status_code == 201, r.text
    assert r.json()["amount"] == 1500
    wa = wallet(t["ada"])
    assert (wa["total"], wa["held"], wa["available"]) == (8500, 0, 8500)
    assert wallet(t["bob"])["total"] == 4000
    now = find_auth(t["bob"], a["authorization_id"])
    assert (now["status"], now["captured_amount"], now["remaining_amount"]) == ("captured", 1500, 0)
    assert r.json()["payment_id"] in {x["payment_id"] for x in activity(t["cy"])}
    before = snapshot(t)
    assert_error(capture(t["bob"], a["authorization_id"], {"amount": 100}), 409, "authorization_not_open")
    assert_error(capture(t["bob"], a["authorization_id"]), 409, "authorization_not_open")
    assert snapshot(t) == before


def test_capture_errors():
    """
    Spec: "| `amount` above the authorisation's uncaptured remainder | 422 `capture_exceeds_authorization` |"
    Spec: "| `amount` below 1, or not an integer | 422 `validation_failed` |"
    Spec: "| The caller is not the receiver | 403 `forbidden` |"
    Spec: "| Unknown authorisation | 404 `not_found` |"
    Spec: "For an existing authorization, capture and void return 403 `forbidden` when the caller is not the permitted party, including callers who are neither party."
    Spec: "Cumulative captures must not exceed the authorized amount."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 2000)
    aid = a["authorization_id"]
    before = snapshot(t)
    assert_error(capture(t["bob"], aid, {"amount": 2001}), 422, "capture_exceeds_authorization")
    for bad in (0, -1, 1.5, "100", True):
        assert_error(capture(t["bob"], aid, {"amount": bad}), 422, "validation_failed")
    assert_error(capture(t["ada"], aid), 403, "forbidden")
    assert_error(capture(t["cy"], aid), 403, "forbidden")
    assert_error(capture(t["bob"], "a_missing"), 404, "not_found")
    assert snapshot(t) == before
    assert capture(t["bob"], aid, {"amount": 2000}).status_code == 201


def test_capture_replay_and_body_identity():
    """
    Spec: "As on `POST /requests/{id}/pay`, **a replay must send the identical body** — `{}` and `{"amount": 2000}` are different JSON values even when they mean the same capture, so reusing a key across the two is 409 `idempotency_key_reuse` per `stage-1.md` §7."
    Spec: "Each idempotent capture moves money once."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 2000)
    k = key()
    first = capture(t["bob"], a["authorization_id"], {}, idem=k)
    assert first.status_code == 201
    after = snapshot(t)
    again = capture(t["bob"], a["authorization_id"], {}, idem=k)
    assert again.status_code == 200 and again.json() == first.json()
    assert_error(capture(t["bob"], a["authorization_id"], {"amount": 2000}, idem=k), 409, "idempotency_key_reuse")
    assert snapshot(t) == after
    b = authorize_ok(t["ada"], "bob", 2000)
    k2 = key()
    assert capture(t["bob"], b["authorization_id"], {"amount": 2000}, idem=k2).status_code == 201
    assert_error(capture(t["bob"], b["authorization_id"], {}, idem=k2), 409, "idempotency_key_reuse")


def test_extended_capture_mode():
    """
    Spec: "**Extended capture mode.** To keep the remainder held, send `{"amount": 700, "final": false}`."
    Spec: "`final` is boolean, default `true`, so earlier single-capture requests retain their behavior."
    Spec: "With `final: false` and an uncaptured remainder, status stays `open`; further captures are allowed up to that remainder."
    Spec: "`capture_exceeds_authorization` compares with the **remaining** amount; omitted amount defaults to that remainder."
    Spec: "`captured_amount` is cumulative; `payment_id` is the latest capture; `payment_ids` lists every capture in order."
    Spec: "Nonfinal captures keep the remainder held."
    Spec: "Capturing the entire remainder closes it even with `final: false`."
    Spec: "Captures may spend the money reserved for them."
    """
    t = world2(balances={"ada": 2000, "bob": 0, "cy": 0, "op": 0})
    a = authorize_ok(t["ada"], "bob", 2000)
    aid = a["authorization_id"]
    assert wallet(t["ada"])["available"] == 0
    p1 = capture(t["bob"], aid, {"amount": 700, "final": False})
    assert p1.status_code == 201, p1.text
    now = find_auth(t["ada"], aid)
    assert (now["status"], now["captured_amount"], now["remaining_amount"], now["payment_id"]) == \
        ("open", 700, 1300, p1.json()["payment_id"])
    wa = wallet(t["ada"])
    assert (wa["total"], wa["held"], wa["available"]) == (1300, 1300, 0)
    before = snapshot(t)
    assert_error(capture(t["bob"], aid, {"amount": 1301, "final": False}), 422, "capture_exceeds_authorization")
    assert snapshot(t) == before
    p2 = capture(t["bob"], aid, {"amount": 300, "final": False})
    assert p2.status_code == 201
    p3 = capture(t["bob"], aid, {"final": False})
    assert p3.status_code == 201 and p3.json()["amount"] == 1000
    now = find_auth(t["bob"], aid)
    check_auth(now)
    assert (now["status"], now["captured_amount"], now["remaining_amount"]) == ("captured", 2000, 0)
    assert now["payment_id"] == p3.json()["payment_id"]
    assert now["payment_ids"] == [p1.json()["payment_id"], p2.json()["payment_id"], p3.json()["payment_id"]]
    assert balances(t) == {"ada": 0, "bob": 2000, "cy": 0, "op": 0}
    assert_error(capture(t["bob"], aid, {"amount": 1, "final": False}), 409, "authorization_not_open")


def test_final_capture_after_partials_releases():
    """
    Spec: "A final capture closes it and releases any remainder."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    aid = a["authorization_id"]
    assert capture(t["bob"], aid, {"amount": 100, "final": False}).status_code == 201
    assert capture(t["bob"], aid, {"amount": 200, "final": True}).status_code == 201
    now = find_auth(t["bob"], aid)
    assert (now["status"], now["captured_amount"], now["remaining_amount"]) == ("captured", 300, 0)
    w = wallet(t["ada"])
    assert (w["total"], w["held"], w["available"]) == (9700, 0, 9700)


def test_capture_final_must_be_boolean():
    """
    Spec: "`final` is boolean, default `true`, so earlier single-capture requests retain their behavior."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    before = snapshot(t)
    r = capture(t["bob"], a["authorization_id"], {"amount": 100, "final": "no"})
    assert r.status_code in (400, 422), r.text
    assert snapshot(t) == before


def test_new_fields_do_not_change_body_equality():
    """
    Spec: "New fields do not change idempotency body equality."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    k = key()
    first = capture(t["bob"], a["authorization_id"], {"amount": 100}, idem=k)
    assert first.status_code == 201
    again = capture(t["bob"], a["authorization_id"], {"amount": 100}, idem=k)
    assert again.status_code == 200 and again.json() == first.json()


# ---------- void ----------

def test_void():
    """
    Spec: "**Only the payer may void** — the `from` party releasing their own hold."
    Spec: "No idempotency key, like decline and cancel."
    Spec: "`200` with the authorisation, `status: "voided"`, the hold released."
    Spec: "Voiding an already-voided authorisation is `200` with the current state."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 3000)
    r = void(t["ada"], a["authorization_id"])
    assert r.status_code == 200, r.text
    v = r.json()
    check_auth(v)
    assert v["status"] == "voided" and v["remaining_amount"] == 0
    w = wallet(t["ada"])
    assert (w["total"], w["held"], w["available"]) == (10000, 0, 10000)
    r2 = void(t["ada"], a["authorization_id"])
    assert r2.status_code == 200 and r2.json()["status"] == "voided"
    assert_error(capture(t["bob"], a["authorization_id"]), 409, "authorization_not_open")
    assert activity(t["ada"]) == []


def test_void_refusals():
    """
    Spec: "A `captured` or `expired` one is `409 authorization_not_open`."
    Spec: "For an existing authorization, capture and void return 403 `forbidden` when the caller is not the permitted party, including callers who are neither party."
    """
    t = world2(authorizations=[seeded_auth("a_exp", "ada", "bob", 100, status="expired", expires_in=-7200),
                               seeded_auth("a_clock", "ada", "bob", 100, status="open", expires_in=-7200)])
    a = authorize_ok(t["ada"], "bob", 1000)
    before = snapshot(t)
    assert_error(void(t["bob"], a["authorization_id"]), 403, "forbidden")
    assert_error(void(t["cy"], a["authorization_id"]), 403, "forbidden")
    assert_error(void(t["ada"], "a_missing"), 404, "not_found")
    assert snapshot(t) == before
    exp = [x for x in auths_of(t["ada"]) if x["authorization_id"] != a["authorization_id"]]
    assert len(exp) == 2
    for x in exp:
        assert_error(void(t["ada"], x["authorization_id"]), 409, "authorization_not_open")
    assert capture(t["bob"], a["authorization_id"]).status_code == 201
    after = snapshot(t)
    assert_error(void(t["ada"], a["authorization_id"]), 409, "authorization_not_open")
    assert snapshot(t) == after


def test_void_partially_captured_keeps_captures():
    """
    Spec: "Void and expiry can close a partially captured authorization, release only the remainder, and preserve all capture records."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    aid = a["authorization_id"]
    p = capture(t["bob"], aid, {"amount": 400, "final": False}).json()
    r = void(t["ada"], aid)
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["status"], v["captured_amount"], v["remaining_amount"]) == ("voided", 400, 0)
    assert v["payment_ids"] == [p["payment_id"]]
    w = wallet(t["ada"])
    assert (w["total"], w["held"], w["available"]) == (9600, 0, 9600)
    assert wallet(t["bob"])["total"] == 2900
    assert p["payment_id"] in {x["payment_id"] for x in activity(t["ada"])}


# ---------- expiry ----------

def test_expiry_by_clock():
    """
    Spec: "An open authorisation expires and releases its remainder on its own."
    Spec: "Reads and writes must reflect expiry even if no request occurred at the deadline."
    Spec: "`GET /authorizations` must show `status: "expired"`, and `GET /me` must include the released remainder in `available`."
    Spec: "| `expires_at` is at or before now | 409 `authorization_expired` |"
    Spec: "An authorisation expired by the clock matches `expired`, never `open`."
    """
    t = world2(ttl=2)
    a = authorize_ok(t["ada"], "bob", 1000)
    b = authorize_ok(t["ada"], "bob", 500)
    p = capture(t["bob"], b["authorization_id"], {"amount": 200, "final": False}).json()
    assert wallet(t["ada"])["held"] == 1300
    time.sleep(3.2)
    w = wallet(t["ada"])
    assert (w["total"], w["held"], w["available"]) == (9800, 0, 9800)
    now = {x["authorization_id"]: x for x in auths_of(t["ada"])}
    assert now[a["authorization_id"]]["status"] == "expired"
    assert now[b["authorization_id"]]["status"] == "expired"
    assert now[b["authorization_id"]]["captured_amount"] == 200
    assert now[b["authorization_id"]]["payment_ids"] == [p["payment_id"]]
    assert now[a["authorization_id"]]["remaining_amount"] == 0
    assert auths_of(t["ada"], status="open") == []
    assert len(auths_of(t["ada"], status="expired")) == 2
    before = snapshot(t)
    r = capture(t["bob"], a["authorization_id"])
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] in ("authorization_expired", "authorization_not_open")
    assert_error(void(t["ada"], a["authorization_id"]), 409, "authorization_not_open")
    assert snapshot(t) == before


def test_expired_hold_frees_funds_for_spending():
    """
    Spec: "An authorization whose `expires_at` is at or before now is `expired` and holds no funds."
    """
    t = world2(balances={"ada": 1000, "bob": 0, "op": 0}, ttl=2)
    authorize_ok(t["ada"], "bob", 1000)
    assert_error(pay(t["ada"], "bob", 1), 409, "insufficient_funds")
    time.sleep(3.2)
    assert pay(t["ada"], "bob", 1000).status_code == 201


# ---------- GET /authorizations ----------

def test_list_authorizations_scope_and_filters():
    """
    Spec: "Authorisations where the caller is the payer or the receiver, and no others."
    Spec: "`GET /authorizations` returns only authorizations involving the caller."
    Spec: "`direction` is `outgoing` (the caller is the payer), `incoming` (the caller is the receiver), or absent for both."
    Spec: "`status` is one of the four statuses, or absent for all."
    Spec: "GET /authorizations?direction=outgoing&status=open&limit=50&offset=0"
    """
    t = world2()
    out_open = authorize_ok(t["ada"], "bob", 10)
    out_void = authorize_ok(t["ada"], "cy", 20)
    void(t["ada"], out_void["authorization_id"])
    inc_cap = authorize_ok(t["dan"], "ada", 30)
    capture(t["ada"], inc_cap["authorization_id"])
    other = authorize_ok(t["dan"], "bob", 40)

    def ids(**params):
        r = get("/authorizations", t["ada"], params)
        assert r.status_code == 200, r.text
        for x in r.json()["authorizations"]:
            check_auth(x)
        return {x["authorization_id"] for x in r.json()["authorizations"]}

    assert ids() == {out_open["authorization_id"], out_void["authorization_id"], inc_cap["authorization_id"]}
    assert other["authorization_id"] not in ids()
    assert ids(direction="outgoing") == {out_open["authorization_id"], out_void["authorization_id"]}
    assert ids(direction="incoming") == {inc_cap["authorization_id"]}
    assert ids(status="open") == {out_open["authorization_id"]}
    assert ids(status="voided") == {out_void["authorization_id"]}
    assert ids(status="captured") == {inc_cap["authorization_id"]}
    assert ids(status="expired") == set()
    assert ids(direction="outgoing", status="open", limit=50, offset=0) == {out_open["authorization_id"]}
    assert {x["authorization_id"] for x in auths_of(t["cy"])} == {out_void["authorization_id"]}
    assert auths_of(t["op"]) == []


def test_list_authorizations_order_and_pagination():
    """
    Spec: "Newest first by `created_at`."
    Spec: "`limit`, `offset` and `has_more` behave exactly as on `GET /requests`."
    """
    t = world2()
    for i in range(5):
        authorize_ok(t["ada"], "bob", 10 + i)
    items = auths_of(t["ada"])
    assert len(items) == 5
    assert_newest_first(items)
    r = get("/authorizations", t["ada"], {"limit": 2})
    assert len(r.json()["authorizations"]) == 2 and r.json()["has_more"] is True
    r = get("/authorizations", t["ada"], {"limit": 2, "offset": 4})
    assert len(r.json()["authorizations"]) == 1 and r.json()["has_more"] is False
    r = get("/authorizations", t["ada"], {"limit": 5})
    assert r.json()["has_more"] is False


@pytest.mark.parametrize("params", [{"limit": "0"}, {"limit": "201"}, {"offset": "-1"}, {"limit": "1e9"},
                                    {"limit": "4.0"}, {"limit": "+4"}, {"direction": "both"},
                                    {"status": "pending"}, {"status": "OPEN"}])
def test_list_authorizations_bad_query(params):
    """
    Spec: "`limit`, `offset` and `has_more` behave exactly as on `GET /requests`."
    Spec: "`status` is one of the four statuses, or absent for all."
    """
    t = world2()
    assert_error(get("/authorizations", t["ada"], params), 422, "validation_failed")


def test_authorization_endpoints_require_token():
    """
    Spec: "`Idempotency-Key` is required. The caller is the payer."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 10)
    before = snapshot(t)
    for h in ({}, {"Authorization": "Bearer nope"}):
        hh = dict(h, **{"Idempotency-Key": key()})
        assert_error(CLIENT.post("/authorizations", json={"to_handle": "bob", "amount": 1}, headers=hh), 401,
                     "unauthenticated")
        assert_error(CLIENT.post(f"/authorizations/{a['authorization_id']}/capture", json={}, headers=hh), 401,
                     "unauthenticated")
        assert_error(CLIENT.post(f"/authorizations/{a['authorization_id']}/void", headers=h), 401,
                     "unauthenticated")
        assert_error(CLIENT.get("/authorizations", headers=h), 401, "unauthenticated")
    assert snapshot(t) == before


# ---------- content negotiation ----------

@pytest.mark.parametrize("path", ["/requests", "/authorizations"])
def test_shared_routes_negotiate(path):
    """
    Spec: "The browser and the API share `/requests`."
    Spec: "Return the UI for `Accept: text/html`; API requests without that header receive JSON."
    Spec: "The UI and the API share `/authorizations`: serve HTML for `Accept: text/html` and JSON otherwise, as for `/requests`."
    """
    t = world2()
    h = {"Authorization": f"Bearer {t['ada']}"}
    r = CLIENT.get(path, headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    r = CLIENT.get(path, headers=dict(h, Accept="application/json"))
    assert r.status_code == 200 and isinstance(r.json(), dict)
    r = CLIENT.get(path, headers={"Accept": "text/html"}, follow_redirects=True)
    assert r.status_code == 200, r.status_code
    assert r.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path", ["/", "/requests", "/split", "/signup", "/login", "/authorizations"])
def test_screens_reachable_by_url(path):
    """
    Spec: "The following screens must be reachable by URL."
    Spec: "| `/` | Balance, pay form, request form and the activity feed |"
    Spec: "| `/requests` | Incoming and outgoing requests, with pay, decline and cancel |"
    Spec: "| `/split` | Split form |"
    Spec: "| `/signup` | Signup |"
    Spec: "| `/login` | Login |"
    Spec: "A new route `/authorizations`, and the wallet gains two numbers."
    """
    world2()
    r = CLIENT.get(path, headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=True)
    assert r.status_code == 200, (path, r.status_code)
    assert r.headers["content-type"].startswith("text/html")
