"""Upgrade: state exported from the real stage-1 service is imported into the stage-2 service."""
import httpx

from pf import (CLIENT, PASSWORD, activity, assert_error, fixture, key, me, pay, requests_of, user, wallet,
                seeded_auth, reset)


def stage1_state(base):
    """Build real state on the stage-1 service and return what the upgrade must preserve."""
    c = httpx.Client(base_url=base, timeout=15)
    fx = fixture(users=[user(h, b) for h, b in {"ada": 10000, "bob": 2500, "cy": 0, "dan": 5000, "op": 0}.items()],
                 payments=[{"id": "p_seed", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500,
                            "note": "coffee", "visibility": "public"}],
                 requests=[{"id": "rq_seed", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200,
                            "note": "taxi", "status": "pending"}],
                 operators=["u_op"])
    assert c.post("/_test/reset", json=fx).status_code == 204

    def tok(email, pw=PASSWORD):
        r = c.post("/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    t = {h: tok(f"{h}@example.com") for h in ("ada", "bob", "cy", "dan", "op")}
    s = c.post("/auth/signup", json={"email": "old.timer@example.org", "password": "old password",
                                     "display_name": "Old"})
    assert s.status_code == 201
    t["old_timer"] = s.json()["token"]
    calls = []

    def call(who, path, body):
        k = key()
        r = c.post(path, json=body, headers={"Authorization": f"Bearer {t[who]}", "Idempotency-Key": k})
        assert r.status_code == 201, (path, r.text)
        calls.append((who, path, body, k, r.json()))
        return r.json()

    call("ada", "/payments", {"to_handle": "bob", "amount": 300, "note": "pub"})
    call("ada", "/payments", {"to_handle": "cy", "amount": 200, "note": "priv", "visibility": "private"})
    q = call("dan", "/requests", {"payer_handle": "cy", "amount": 50, "note": "pending one"})
    q2 = call("bob", "/requests", {"payer_handle": "ada", "amount": 120, "note": "paid one"})
    call("ada", f"/requests/{q2['request_id']}/pay", {"visibility": "private"})
    call("ada", "/splits", {"amount": 1000, "participant_handles": ["ada", "bob", "dan"], "note": "sp"})
    call("op", "/settlements", {"transfers": [{"from_handle": "dan", "to_handle": "old_timer", "amount": 40}]})
    # A payment whose response the client never saw: committed, same key and body will be retried.
    lost = call("bob", "/payments", {"to_handle": "dan", "amount": 77, "note": "lost response"})
    failed_key = key()
    r = c.post("/payments", json={"to_handle": "dan", "amount": -1},
               headers={"Authorization": f"Bearer {t['cy']}", "Idempotency-Key": failed_key})
    assert r.status_code == 422

    def view(token):
        h = {"Authorization": f"Bearer {token}"}
        m = c.get("/me", headers=h).json()
        acts = c.get("/activity", headers=h, params={"limit": 200}).json()["payments"]
        reqs = c.get("/requests", headers=h, params={"limit": 200}).json()["requests"]
        return m, acts, reqs

    views = {h: view(v) for h, v in t.items()}
    exported = c.get("/_test/export")
    assert exported.status_code == 200
    return {"tokens": t, "calls": calls, "pending": q, "lost": lost, "failed_key": failed_key, "views": views,
            "export": exported.json()}


def contains(new, old):
    """Every field the stage-1 service showed is still there with the same value."""
    for k, v in old.items():
        assert k in new and new[k] == v, (k, old, new)


def test_stage1_export_imports_into_stage2(previous_base_url):
    """
    Spec: "A stage-2 service must accept an export produced by the same team's stage-1 service."
    Spec: "The stage-1 requirements continue to apply, with the additions below."
    Spec: "Numbered section references such as §5 and §7 refer to `stage-1.md`."
    Spec: "These requirements apply when import completes between browser requests; migration during an in-flight request is not required."
    """
    s = stage1_state(previous_base_url)
    reset(fixture(users=[user("zed", 1)]))
    r = CLIENT.post("/_test/import", json=s["export"])
    assert r.status_code == 204, r.text
    total = 0
    for h, tok in s["tokens"].items():
        m_old, acts_old, reqs_old = s["views"][h]
        w = wallet(tok)
        contains(w, m_old)
        assert w["total"] == m_old["balance"] and w["held"] == 0 and w["available"] == m_old["balance"]
        total += w["total"]
        acts_new = {p["payment_id"]: p for p in activity(tok)}
        assert len(acts_new) == len(acts_old)
        for p in acts_old:
            contains(acts_new[p["payment_id"]], p)
            assert acts_new[p["payment_id"]].get("authorization_id") is None
        reqs_new = {q["request_id"]: q for q in requests_of(tok)}
        assert len(reqs_new) == len(reqs_old)
        for q in reqs_old:
            contains(reqs_new[q["request_id"]], q)
    assert total == 10000 + 2500 + 5000
    assert_error(CLIENT.post("/auth/login", json={"email": "zed@example.com", "password": PASSWORD}), 401,
                 "unauthenticated")
    assert CLIENT.post("/auth/login", json={"email": "old.timer@example.org", "password": "old password"}) \
        .status_code == 200
    assert CLIENT.post("/auth/login", json={"email": "ada@example.com", "password": PASSWORD}).status_code == 200


def test_stage1_replays_after_upgrade(previous_base_url):
    """
    Spec: "A payment whose response was lost before export remains retryable after import with the same body and key; the UI must recover the original payment and refresh the imported balance."
    Spec: "The same replay rules apply independently to each."
    """
    s = stage1_state(previous_base_url)
    assert CLIENT.post("/_test/import", json=s["export"]).status_code == 204
    t = s["tokens"]
    before = {h: wallet(tok)["total"] for h, tok in t.items()}
    for who, path, body, k, original in s["calls"]:
        r = CLIENT.post(path, json=body, headers={"Authorization": f"Bearer {t[who]}", "Idempotency-Key": k})
        assert r.status_code == 200, (path, r.status_code, r.text)
        contains(r.json(), original)
    assert {h: wallet(tok)["total"] for h, tok in t.items()} == before
    r = CLIENT.post("/payments", json={"to_handle": "dan", "amount": 78, "note": "lost response"},
                    headers={"Authorization": f"Bearer {t['bob']}", "Idempotency-Key": s["calls"][-1][3]})
    assert_error(r, 409, "idempotency_key_reuse")
    r = CLIENT.post("/payments", json={"to_handle": "dan", "amount": 5},
                    headers={"Authorization": f"Bearer {t['ada']}", "Idempotency-Key": s["failed_key"]})
    assert r.status_code == 201, r.text


def test_stage1_pending_request_payable_after_upgrade(previous_base_url):
    """
    Spec: "Existing pending requests remain payable through the request screen."
    """
    s = stage1_state(previous_base_url)
    assert CLIENT.post("/_test/import", json=s["export"]).status_code == 204
    t = s["tokens"]
    q = s["pending"]
    assert pay(t["ada"], "cy", 50).status_code == 201
    r = CLIENT.post(f"/requests/{q['request_id']}/pay", json={},
                    headers={"Authorization": f"Bearer {t['cy']}", "Idempotency-Key": key()})
    assert r.status_code == 201, r.text
    assert r.json()["request_id"] == q["request_id"] and r.json()["authorization_id"] is None
    r = CLIENT.post("/settlements", json={"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]},
                    headers={"Authorization": f"Bearer {t['op']}", "Idempotency-Key": key()})
    assert r.status_code == 201, r.text
    r = CLIENT.post("/authorizations", json={"to_handle": "bob", "amount": 100},
                    headers={"Authorization": f"Bearer {t['ada']}", "Idempotency-Key": key()})
    assert r.status_code == 201, r.text
    assert wallet(t["ada"])["held"] == 100


def test_stage2_export_round_trip_with_authorizations():
    """
    Spec: "There are now seven idempotent write paths: stage 1's five, authorizations and captures."
    """
    from pf import authorize_ok, capture, world2, auths_of, snapshot
    t = world2(authorizations=[seeded_auth("a_s", "dan", "ada", 300)])
    a = authorize_ok(t["ada"], "bob", 1000, visibility="private")
    k = key()
    p = capture(t["bob"], a["authorization_id"], {"amount": 400, "final": False}, idem=k)
    assert p.status_code == 201
    k2 = key()
    b = CLIENT.post("/authorizations", json={"to_handle": "cy", "amount": 50},
                    headers={"Authorization": f"Bearer {t['ada']}", "Idempotency-Key": k2})
    assert b.status_code == 201
    before = snapshot(t)
    auths_before = {h: sorted(auths_of(tok), key=lambda x: x["authorization_id"]) for h, tok in t.items()}
    exported = CLIENT.get("/_test/export").json()
    reset(fixture())
    assert CLIENT.post("/_test/import", json=exported).status_code == 204
    assert snapshot(t) == before
    assert {h: sorted(auths_of(tok), key=lambda x: x["authorization_id"]) for h, tok in t.items()} == auths_before
    again = capture(t["bob"], a["authorization_id"], {"amount": 400, "final": False}, idem=k)
    assert again.status_code == 200 and again.json() == p.json()
    again = CLIENT.post("/authorizations", json={"to_handle": "cy", "amount": 50},
                        headers={"Authorization": f"Bearer {t['ada']}", "Idempotency-Key": k2})
    assert again.status_code == 200 and again.json() == b.json()
    assert wallet(t["ada"])["held"] == 650
    assert wallet(t["dan"])["held"] == 300
