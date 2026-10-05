import time

import httpx

from pf import (CLIENT, PASSWORD, assert_error, batch, batch_item, fixture, get, key, me, refund, reset, revisions,
                statement, user)


def test_stage3_export_into_stage4(stage3_base_url):
    """
    Spec: "A stage-4 service must accept exports produced by the same team's stages 1–3, retaining settlement membership, corrections and snapshots."
    Ruling (coordinator, stage 4 round 1, factory/stage-4/decisions.md): the stage-3 export never contained
    snapshot tokens, so snapshot survival is checked on a stage-4 export/import round trip instead (below).
    """
    c = httpx.Client(base_url=stage3_base_url, timeout=15)
    fx = fixture(users=[user("ada", 10000), user("bob", 2500), user("cy", 0), user("dan", 5000), user("op", 0)],
                 operators=["u_op"])
    assert c.post("/_test/reset", json=fx).status_code == 204

    def tok(h):
        return c.post("/auth/login", json={"email": f"{h}@example.com", "password": PASSWORD}).json()["token"]

    ta, tb, top = tok("ada"), tok("bob"), tok("op")
    H = lambda tk, k=None: {"Authorization": f"Bearer {tk}", **({"Idempotency-Key": k} if k else {})}
    p = c.post("/payments", json={"to_handle": "bob", "amount": 500, "note": "x"}, headers=H(ta, key())).json()
    time.sleep(1.1)
    sk = key()
    sbody = {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 100},
                           {"from_handle": "dan", "to_handle": "bob", "amount": 40}]}
    st = c.post("/settlements", json=sbody, headers=H(top, sk)).json()
    corr = c.post(f"/payments/{p['payment_id']}/corrections",
                  json={"expected_revision": 1, "amount": 450, "effective_at": p["created_at"], "reason": "s3 fix"},
                  headers=H(ta, key()))
    assert corr.status_code == 201, corr.text
    revs_before = c.get(f"/payments/{p['payment_id']}/revisions", headers=H(ta)).json()["revisions"]
    me_before = c.get("/me", headers=H(ta)).json()
    exported = c.get("/_test/export").json()

    reset(fixture(users=[user("zed", 1)]))
    assert CLIENT.post("/_test/import", json=exported).status_code == 204
    for k, v in me_before.items():
        assert me(ta)[k] == v, k
    revs = revisions(ta, p["payment_id"])
    assert len(revs) == 2
    for old, new in zip(revs_before, revs):
        for k, v in old.items():
            assert new[k] == v, (k, old, new)
    m1, m2 = st["payments"]
    assert_error(batch(top, [batch_item(m1["payment_id"], 50, st["committed_at"])]), 422, "incomplete_settlement")
    again = CLIENT.post("/settlements", json=sbody, headers=H(top, sk))
    assert again.status_code == 200
    assert again.json()["settlement_id"] == st["settlement_id"]
    r = batch(top, [batch_item(m1["payment_id"], 50, st["committed_at"]),
                    batch_item(m2["payment_id"], 20, st["committed_at"])])
    assert r.status_code == 201, r.text
    f = refund(tb, p["payment_id"], 450)
    assert f.status_code == 201, f.text
    assert_error(refund(tb, p["payment_id"], 1), 422, "refund_exceeds_payment")


def test_stage4_round_trip_keeps_snapshots_and_corrections():
    """
    Spec: "A stage-4 service must accept exports produced by the same team's stages 1–3, retaining settlement membership, corrections and snapshots."
    Spec: "Existing receipts and saved statements must remain available in their original form."
    """
    from pf import world2, pay, correct, post
    t = world2()
    p = pay(t["ada"], "bob", 500, note="x").json()
    time.sleep(1.1)
    st = post("/settlements", t["op"], {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 100},
                                                       {"from_handle": "dan", "to_handle": "bob", "amount": 40}]},
              key()).json()
    assert correct(t["ada"], p["payment_id"], 450, p["created_at"]).status_code == 201
    snap = statement(t["ada"], limit=1)
    page2 = statement(t["ada"], snapshot=snap["snapshot"], limit=1, offset=1)
    revs = revisions(t["ada"], p["payment_id"])
    exported = CLIENT.get("/_test/export").json()
    reset(fixture(users=[user("zed", 1)]))
    assert CLIENT.post("/_test/import", json=exported).status_code == 204
    assert statement(t["ada"], snapshot=snap["snapshot"], limit=1, offset=1) == page2
    assert statement(t["ada"], snapshot=snap["snapshot"], limit=1, offset=0)["entries"] == snap["entries"]
    assert revisions(t["ada"], p["payment_id"]) == revs
    m1, m2 = st["payments"]
    assert_error(batch(t["op"], [batch_item(m1["payment_id"], 50, st["committed_at"])]), 422, "incomplete_settlement")
