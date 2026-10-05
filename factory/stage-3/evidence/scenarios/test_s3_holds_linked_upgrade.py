import datetime
import time

import httpx

from pf import (CLIENT, PASSWORD, activity, assert_error, ago, authorize_ok, auths_of, capture, correct, find_auth,
                fixture, is_ts, iso, key, login, me, me_at, now_utc, parse_ts, pay, payment_ids_by_note, post,
                reset, revisions, seeded_auth, seeded_payment, statement, user, void, wallet, world2)


def mid(a, b):
    return iso(a + (b - a) / 2)


def money(m):
    assert m["balance"] == m["total"] and m["available"] == m["total"] - m["held"], m
    return m["total"], m["held"], m["available"]


def test_hold_timeline_as_of():
    """
    Spec: "For `GET /me?as_of=T&known_at=K`, all four money fields describe that same view: `balance = total`, `available = total - held`."
    Spec: "A hold starts at authorization creation; nonfinal capture reduces it at capture time; final capture, void or expiry releases the remainder at that event's time."
    Spec: "Without `as_of`, use the instant the request began."
    Spec: "Historical `total` follows stage-3 effective/recorded-time rules."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    C = parse_ts(a["created_at"])
    time.sleep(1.2)
    p1 = capture(t["bob"], a["authorization_id"], {"amount": 300, "final": False}).json()
    X = parse_ts(p1["created_at"])
    time.sleep(1.2)
    p2 = capture(t["bob"], a["authorization_id"], {"amount": 200}).json()
    Y = parse_ts(p2["created_at"])
    sec = datetime.timedelta(seconds=1)
    assert money(me_at(t["ada"], as_of=iso(C - sec))) == (10000, 0, 10000)
    assert money(me_at(t["ada"], as_of=mid(C, X))) == (10000, 1000, 9000)
    assert money(me_at(t["ada"], as_of=mid(X, Y))) == (9700, 700, 9000)
    assert money(me_at(t["ada"], as_of=iso(Y + sec))) == (9500, 0, 9500)
    assert money(me_at(t["ada"])) == (9500, 0, 9500)
    assert money(me_at(t["bob"], as_of=mid(X, Y))) == (2800, 0, 2800)


def test_closed_at():
    """
    Spec: "Authorizations expose `closed_at` (null while open; event time when closed)."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    assert "closed_at" in a and a["closed_at"] is None
    assert find_auth(t["ada"], a["authorization_id"])["closed_at"] is None
    v = post(f"/authorizations/{a['authorization_id']}/void", t["ada"]).json()
    assert is_ts(v["closed_at"])
    assert parse_ts(v["closed_at"]) >= parse_ts(a["created_at"])
    b = authorize_ok(t["ada"], "bob", 100)
    p = capture(t["bob"], b["authorization_id"]).json()
    closed = find_auth(t["ada"], b["authorization_id"])["closed_at"]
    assert is_ts(closed) and abs((parse_ts(closed) - parse_ts(p["created_at"])).total_seconds()) < 1.5


def test_hold_expires_in_future_views():
    """
    Spec: "For queries beyond now, an open hold expires at its deadline."
    Spec: "Expiry takes effect at `expires_at`."
    """
    t = world2(ttl=600)
    a = authorize_ok(t["ada"], "bob", 1000)
    exp = parse_ts(a["expires_at"])
    sec = datetime.timedelta(seconds=1)
    assert money(me_at(t["ada"], as_of=iso(exp - sec))) == (10000, 1000, 9000)
    assert money(me_at(t["ada"], as_of=iso(exp))) == (10000, 0, 10000)
    assert money(me_at(t["ada"], as_of=iso(exp + datetime.timedelta(hours=1)))) == (10000, 0, 10000)
    assert money(me_at(t["ada"])) == (10000, 1000, 9000)


def test_hold_known_at():
    """
    Spec: "Events other than clock expiry are known at their server-assigned event time."
    Spec: "Once creation is known, the expiry deadline is known too."
    """
    t = world2(ttl=600)
    a = authorize_ok(t["ada"], "bob", 1000)
    C = parse_ts(a["created_at"])
    time.sleep(1.2)
    v = post(f"/authorizations/{a['authorization_id']}/void", t["ada"]).json()
    V = parse_ts(v["closed_at"])
    sec = datetime.timedelta(seconds=1)
    later = iso(V + datetime.timedelta(seconds=30))
    assert money(me_at(t["ada"], as_of=later, known_at=iso(C - sec))) == (10000, 0, 10000)
    assert money(me_at(t["ada"], as_of=later, known_at=mid(C, V))) == (10000, 1000, 9000)
    assert money(me_at(t["ada"], as_of=later)) == (10000, 0, 10000)
    beyond = iso(parse_ts(a["expires_at"]) + sec)
    assert money(me_at(t["ada"], as_of=beyond, known_at=mid(C, V))) == (10000, 0, 10000)


def test_seeded_hold_creation_times():
    """
    Spec: "Seeded open holds are assumed created at reset unless `created_at` is supplied; seeded closed holds need not reconstruct a prior lifecycle."
    """
    created = ago(days=2)
    h1 = seeded_auth("a_c", "ada", "bob", 1000)
    h1["created_at"] = iso(created)
    h2 = seeded_auth("a_r", "ada", "bob", 500)
    t = world2(authorizations=[h1, h2, seeded_auth("a_done", "ada", "bob", 50, status="voided")])
    assert money(me_at(t["ada"], as_of=iso(created - datetime.timedelta(seconds=1)))) == (10000, 0, 10000)
    assert money(me_at(t["ada"], as_of=iso(ago(days=1)))) == (10000, 1000, 9000)
    assert money(me_at(t["ada"])) == (10000, 1500, 8500)


def test_correction_overdraft_through_held_funds():
    """
    Spec: "A correction is rejected with 409 `historical_overdraft` if it makes either total or available negative at any past effective/event boundary, under the latest known revisions."
    Spec: "Current unaffordable debits still take precedence as `insufficient_funds`."
    """
    t = world2(balances={"ada": 5000, "cy": 0, "dan": 0, "op": 0})
    p = pay(t["ada"], "cy", 1000, note="fund").json()
    time.sleep(1.1)
    a = authorize_ok(t["cy"], "dan", 1000)
    assert_error(correct(t["ada"], p["payment_id"], 999, p["created_at"]), 409, "insufficient_funds")
    time.sleep(1.1)
    assert post(f"/authorizations/{a['authorization_id']}/void", t["cy"]).status_code == 200
    k = key()
    assert_error(correct(t["ada"], p["payment_id"], 500, p["created_at"], idem=k), 409, "historical_overdraft")
    assert money(me(t["cy"])) == (1000, 0, 1000)
    assert len(revisions(t["ada"], p["payment_id"])) == 1
    r = correct(t["ada"], p["payment_id"], 1200, p["created_at"], idem=k)
    assert r.status_code == 201, r.text


def test_statement_money_movements_only():
    """
    Spec: "`GET /statement` still contains money movements only: authorization, release and expiry are not payments."
    Spec: "Captures appear exactly once with their links."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    b = authorize_ok(t["ada"], "bob", 400)
    post(f"/authorizations/{b['authorization_id']}/void", t["ada"])
    first = statement(t["ada"])
    assert first["entries"] == []
    p = capture(t["bob"], a["authorization_id"], {"amount": 600}).json()
    for who, delta in (("ada", -600), ("bob", 600)):
        s = statement(t[who])
        assert len(s["entries"]) == 1
        e = s["entries"][0]
        assert e["payment"]["payment_id"] == p["payment_id"] and e["delta"] == delta
        assert e["payment"]["authorization_id"] == a["authorization_id"]
    old = statement(t["ada"], snapshot=first["snapshot"])
    assert old["entries"] == [] and old["closing_balance"] == first["closing_balance"] == 10000


def test_capture_correction_immutable():
    """
    Spec: "Captures are immutable linked payments: a correction of a capture gives 422 `linked_payment_immutable`."
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 1000)
    p = capture(t["bob"], a["authorization_id"]).json()
    assert_error(correct(t["ada"], p["payment_id"], 500, p["created_at"]), 422, "linked_payment_immutable")
    assert len(revisions(t["ada"], p["payment_id"])) == 1
    assert money(me(t["ada"])) == (9000, 0, 9000)


def test_settlement_members_immutable():
    """
    Spec: "Single-payment corrections reject settlement members with 422 `linked_payment_immutable`."
    Spec: "Each member's original revision uses its shared committed_at as both effective_at and recorded_at."
    Spec: "Stage-1 settlements retain their original receipts and privacy rules."
    """
    t = world2()
    k = key()
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100},
                          {"from_handle": "dan", "to_handle": "cy", "amount": 50, "visibility": "private"}]}
    s = post("/settlements", t["op"], body, k)
    assert s.status_code == 201
    st = s.json()
    m1 = st["payments"][0]
    assert_error(correct(t["ada"], m1["payment_id"], 50, m1["created_at"]), 422, "linked_payment_immutable")
    r1 = revisions(t["ada"], m1["payment_id"])
    assert len(r1) == 1
    assert parse_ts(r1[0]["effective_at"]) == parse_ts(r1[0]["recorded_at"]) == parse_ts(st["committed_at"])
    again = post("/settlements", t["op"], body, k)
    assert again.status_code == 200 and again.json() == st
    assert st["payments"][1]["payment_id"] not in {p["payment_id"] for p in activity(t["ada"])}


def test_stage1_export_into_stage3(previous_base_url):
    """
    Spec: "A stage-3 service must accept exports produced by the same team's stage-1 or stage-2 service."
    Spec: "The requirements from stages 1 and 2 continue to apply, with the additions below."
    Spec: "Numbered section references such as §5 and §7 refer to `stage-1.md`."
    """
    c = httpx.Client(base_url=previous_base_url, timeout=15)
    fx = fixture(users=[user("ada", 10000), user("bob", 2500), user("op", 0)], operators=["u_op"])
    assert c.post("/_test/reset", json=fx).status_code == 204
    ta = c.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD}).json()["token"]
    to = c.post("/auth/login", json={"email": "op@example.com", "password": PASSWORD}).json()["token"]
    k = key()
    p = c.post("/payments", json={"to_handle": "bob", "amount": 700, "note": "old"},
               headers={"Authorization": f"Bearer {ta}", "Idempotency-Key": k}).json()
    time.sleep(1.1)  # distinct created_at seconds, so statement order does not depend on id tie-breaks
    st = c.post("/settlements", json={"transfers": [{"from_handle": "bob", "to_handle": "ada", "amount": 50}]},
                headers={"Authorization": f"Bearer {to}", "Idempotency-Key": key()}).json()
    exported = c.get("/_test/export").json()
    reset(fixture(users=[user("zed", 1)]))
    assert CLIENT.post("/_test/import", json=exported).status_code == 204
    revs = revisions(ta, p["payment_id"])
    assert len(revs) == 1 and revs[0]["amount"] == 700
    assert parse_ts(revs[0]["effective_at"]) == parse_ts(revs[0]["recorded_at"]) == parse_ts(p["created_at"])
    s = statement(ta)
    assert s["opening_balance"] == 10000 and s["closing_balance"] == 9350
    assert [e["delta"] for e in s["entries"]] == [-700, 50]
    again = CLIENT.post("/payments", json={"to_handle": "bob", "amount": 700, "note": "old"},
                        headers={"Authorization": f"Bearer {ta}", "Idempotency-Key": k})
    assert again.status_code == 200 and again.json()["payment_id"] == p["payment_id"]
    member = st["payments"][0]
    tb = login("bob@example.com")
    assert_error(correct(tb, member["payment_id"], 10, member["created_at"]), 422, "linked_payment_immutable")
    r = correct(ta, p["payment_id"], 600, p["created_at"], reason="after upgrade")
    assert r.status_code == 201, r.text
    assert me(ta)["balance"] == 9450


def test_stage2_export_into_stage3(stage2_base_url):
    """
    Spec: "The ledger must import and account for authorizations and captures."
    """
    c = httpx.Client(base_url=stage2_base_url, timeout=15)
    fx = fixture(users=[user("ada", 10000), user("bob", 2500), user("op", 0)], operators=["u_op"])
    fx["authorizations"] = [seeded_auth("a_seed", "ada", "bob", 300)]
    assert c.post("/_test/reset", json=fx).status_code == 204
    ta = c.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD}).json()["token"]
    tb = c.post("/auth/login", json={"email": "bob@example.com", "password": PASSWORD}).json()["token"]
    ha, hb = {"Authorization": f"Bearer {ta}"}, {"Authorization": f"Bearer {tb}"}
    a = c.post("/authorizations", json={"to_handle": "bob", "amount": 1000}, headers=dict(ha, **{"Idempotency-Key": key()})).json()
    ck = key()
    cap = c.post(f"/authorizations/{a['authorization_id']}/capture", json={"amount": 400, "final": False},
                 headers=dict(hb, **{"Idempotency-Key": ck}))
    assert cap.status_code == 201
    cap = cap.json()
    exported = c.get("/_test/export").json()
    reset(fixture(users=[user("zed", 1)]))
    assert CLIENT.post("/_test/import", json=exported).status_code == 204
    assert money(me(ta)) == (9600, 900, 8700)
    assert money(me(tb)) == (2900, 0, 2900)
    s = statement(ta)
    assert [e["payment"]["payment_id"] for e in s["entries"]] == [cap["payment_id"]]
    assert s["entries"][0]["delta"] == -400 and s["closing_balance"] == 9600 and s["opening_balance"] == 10000
    assert_error(correct(ta, cap["payment_id"], 100, cap["created_at"]), 422, "linked_payment_immutable")
    again = CLIENT.post(f"/authorizations/{a['authorization_id']}/capture", json={"amount": 400, "final": False},
                        headers={"Authorization": f"Bearer {tb}", "Idempotency-Key": ck})
    assert again.status_code == 200 and again.json()["payment_id"] == cap["payment_id"]
    assert money(me(ta)) == (9600, 900, 8700)
    p2 = capture(tb, a["authorization_id"], {"amount": 600})
    assert p2.status_code == 201, p2.text
    assert money(me(ta)) == (9000, 300, 8700)
    assert sum(me(x)["total"] for x in (ta, tb)) == 12500
