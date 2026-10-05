import json

import pytest

from pf import (CLIENT, PASSWORD, activity, ask_ok, assert_error, balances, fixture, get, key, login, me, pay,
                pay_request, post, reset, snapshot, user, world)


def export():
    r = CLIENT.get("/_test/export")
    assert r.status_code == 200, r.text
    return r.json()


def do_import(body):
    r = CLIENT.post("/_test/import", json=body)
    assert r.status_code == 204, (r.status_code, r.text)


def busy_world():
    """A state with every kind of record plus the replayable calls that created it."""
    t = world()
    calls = []

    def call(token, path, body):
        k = key()
        r = post(path, token, body, k)
        assert r.status_code == 201, r.text
        calls.append((token, path, body, k, r.json()))
        return r.json()

    call(t["ada"], "/payments", {"to_handle": "bob", "amount": 300, "note": "pub"})
    call(t["ada"], "/payments", {"to_handle": "cy", "amount": 200, "note": "priv", "visibility": "private"})
    q = call(t["bob"], "/requests", {"payer_handle": "ada", "amount": 120, "note": "taxi"})
    call(t["ada"], f"/requests/{q['request_id']}/pay", {"visibility": "private"})
    call(t["dan"], "/requests", {"payer_handle": "cy", "amount": 5000, "note": "pending"})
    s = call(t["ada"], "/splits", {"amount": 1000, "participant_handles": ["ada", "bob", "dan"], "note": "sp"})
    post(f"/requests/{s['requests'][1]['request_id']}/decline", t["dan"])
    call(t["op"], "/settlements", {"transfers": [{"from_handle": "dan", "to_handle": "cy", "amount": 40},
                                                 {"from_handle": "cy", "to_handle": "bob", "amount": 10,
                                                  "visibility": "private"}]})
    r = CLIENT.post("/auth/signup", json={"email": "late.comer@example.org", "password": "late password",
                                          "display_name": "Late"})
    assert r.status_code == 201
    t["late_comer"] = r.json()["token"]
    call(t["ada"], "/payments", {"to_handle": "late_comer", "amount": 77, "note": "welcome"})
    return t, calls


def test_export_shape():
    """
    Spec: "Return 200 from export with a JSON object containing `track: "pocketful"`, `format_version: 1` and `state` (an implementation-defined JSON object)."
    Spec: "The service must support `GET /_test/export` and `POST /_test/import`."
    Spec: "Like reset, these are unauthenticated test endpoints."
    """
    world()
    body = export()
    assert body["track"] == "pocketful" and body["format_version"] == 1
    assert isinstance(body["state"], dict)


def test_export_is_read_only():
    """
    Spec: "Export is an atomic, read-only snapshot; subsequent source writes do not change it."
    """
    t, _ = busy_world()
    before = snapshot(t)
    export()
    export()
    assert snapshot(t) == before


def test_round_trip_restores_everything():
    """
    Spec: "Import takes that entire object and atomically replaces the service's state, returning 204."
    Spec: "The state format is opaque to the caller and must be accepted unchanged by import."
    Spec: "It must accept an unchanged export produced by this service."
    Spec: "Preserve accounts and hashed-password login, existing bearer tokens, currency, balances, payments, requests, permissions, all completed idempotent request bodies and original responses."
    Spec: "Identities, timestamps and monetary records must not be regenerated or replayed against an already-net balance."
    Spec: "Existing receipts, tokens and retries must remain valid after import; replacing the state with a fresh fixture does not satisfy this requirement."
    Spec: "A reset/import must preserve settlement operator permissions, original payments, requests, settlement membership and retry responses."
    Spec: "Export is an atomic, read-only snapshot; subsequent source writes do not change it."
    Spec: "No dependency on the source process, files, volume, port or network address is allowed."
    """
    t, calls = busy_world()
    before = snapshot(t)
    total = sum(v["me"]["balance"] for v in before.values())
    exported = export()
    text = json.dumps(exported)
    # Writes after the export must not leak into it.
    late_key = key()
    assert pay(t["ada"], "bob", 1, idem=late_key).status_code == 201
    r = CLIENT.post("/auth/signup", json={"email": "after@example.org", "password": PASSWORD, "display_name": "A"})
    assert r.status_code == 201
    after_token = r.json()["token"]
    reset(fixture(users=[user("zed", 5)]))
    do_import(json.loads(text))
    assert snapshot(t) == before
    assert sum(v["me"]["balance"] for v in snapshot(t).values()) == total
    for token, path, body, k, original in calls:
        again = post(path, token, body, k)
        assert again.status_code == 200, (path, again.status_code, again.text)
        assert again.json() == original
    assert snapshot(t) == before
    assert login("ada@example.com")
    assert login("late.comer@example.org", "late password")
    assert_error(CLIENT.post("/auth/login", json={"email": "after@example.org", "password": PASSWORD}),
                 401, "unauthenticated")
    assert_error(get("/me", after_token), 401, "unauthenticated")
    assert_error(CLIENT.post("/auth/login", json={"email": "zed@example.com", "password": PASSWORD}),
                 401, "unauthenticated")
    r = pay(t["ada"], "bob", 1, idem=late_key)
    assert r.status_code == 201, r.text
    r = post("/settlements", t["op"], {"transfers": [{"from_handle": "ada", "to_handle": "dan", "amount": 1}]}, key())
    assert r.status_code == 201, r.text
    assert_error(post("/settlements", t["ada"], {"transfers": [{"from_handle": "ada", "to_handle": "dan",
                                                                 "amount": 1}]}, key()), 403, "forbidden")
    m = me(t["ada"])
    assert m["currency"] == "EUR" and m["minor_units"] == 2


def test_import_twice_no_duplicates():
    """
    Spec: "Import is replacement, not merge; repeating it restores the exported state without duplicating anything."
    """
    t, _ = busy_world()
    before = snapshot(t)
    exported = export()
    do_import(exported)
    do_import(exported)
    assert snapshot(t) == before
    do_import(exported)
    assert snapshot(t) == before


def test_import_removes_destination_data():
    """
    Spec: "Import removes all previous destination data and credentials."
    """
    t, _ = busy_world()
    before = snapshot(t)
    exported = export()
    reset(fixture(users=[user("zed", 900), user("ada", 1, email="ada2@example.com")]))
    zed = login("zed@example.com")
    ada2 = login("ada2@example.com")
    assert pay(zed, "ada", 5).status_code == 201
    do_import(exported)
    assert_error(get("/me", zed), 401, "unauthenticated")
    assert_error(get("/me", ada2), 401, "unauthenticated")
    assert_error(CLIENT.post("/auth/login", json={"email": "zed@example.com", "password": PASSWORD}),
                 401, "unauthenticated")
    assert_error(pay(t["ada"], "zed", 1), 404, "not_found")
    assert snapshot(t) == before


def test_reset_clears_imported_state():
    """
    Spec: "Reset clears all state, including imported state."
    """
    t, _ = busy_world()
    exported = export()
    do_import(exported)
    reset(fixture(users=[user("ada", 50), user("bob", 50)]))
    assert_error(get("/me", t["late_comer"]), 401, "unauthenticated")
    ada = login("ada@example.com")
    assert me(ada)["balance"] == 50
    assert activity(ada) == []


def test_failed_keys_reusable_after_import():
    """
    Spec: "Failed request keys remain reusable."
    """
    t = world()
    k = key()
    assert_error(pay(t["cy"], "bob", 10, idem=k), 409, "insufficient_funds")
    k2 = key()
    assert post("/payments", t["ada"], {"to_handle": "bob", "amount": -1}, k2).status_code == 422
    do_import(export())
    assert pay(t["ada"], "cy", 10).status_code == 201
    assert pay(t["cy"], "bob", 10, idem=k).status_code == 201
    assert pay(t["ada"], "bob", 10, idem=k2).status_code == 201


@pytest.mark.parametrize("mutate", ["no_state", "no_track", "no_version", "wrong_track", "wrong_version",
                                    "version_string", "empty"])
def test_invalid_import_rejected(mutate):
    """
    Spec: "Invalid JSON follows §5; missing fields, wrong track/version or an invalid state give 422 `validation_failed` without changing the destination."
    """
    t, _ = busy_world()
    good = export()
    before = snapshot(t)
    bad = dict(good)
    if mutate == "no_state":
        bad.pop("state")
    elif mutate == "no_track":
        bad.pop("track")
    elif mutate == "no_version":
        bad.pop("format_version")
    elif mutate == "wrong_track":
        bad["track"] = "other-track"
    elif mutate == "wrong_version":
        bad["format_version"] = 2
    elif mutate == "version_string":
        bad["format_version"] = 99
    else:
        bad = {}
    r = CLIENT.post("/_test/import", json=bad)
    assert_error(r, 422, "validation_failed")
    assert snapshot(t) == before


def test_import_unparseable():
    """
    Spec: "Invalid JSON follows §5; missing fields, wrong track/version or an invalid state give 422 `validation_failed` without changing the destination."
    """
    t, _ = busy_world()
    before = snapshot(t)
    r = CLIENT.post("/_test/import", content=b'{"track": "pocketful", "format_', headers={"Content-Type": "application/json"})
    assert_error(r, 400, "malformed_request")
    assert snapshot(t) == before


def test_timestamps_and_ids_preserved():
    """
    Spec: "Identities, timestamps and monetary records must not be regenerated or replayed against an already-net balance."
    """
    t, _ = busy_world()
    ids_before = {h: me(tok)["user_id"] for h, tok in t.items()}
    feed_before = activity(t["ada"])
    exported = export()
    reset(fixture())
    do_import(exported)
    assert {h: me(tok)["user_id"] for h, tok in t.items()} == ids_before
    assert activity(t["ada"]) == feed_before
    assert balances(t)["ada"] == me(t["ada"])["balance"]
