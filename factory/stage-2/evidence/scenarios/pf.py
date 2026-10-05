"""Shared helpers for the Pocketful stage-1 scenarios. Public HTTP interface only."""
import datetime
import json
import os
import re
import threading
import uuid

import httpx

BASE = os.environ.get("TARGET_URL", "http://127.0.0.1:8080").rstrip("/")
TIMEOUT = 15.0
CLIENT = httpx.Client(base_url=BASE, timeout=TIMEOUT)
PASSWORD = "correct horse"

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")
PAYMENT_FIELDS = ["payment_id", "from_user_id", "from_handle", "to_user_id", "to_handle", "amount",
                  "currency", "note", "visibility", "request_id", "created_at"]
REQUEST_FIELDS = ["request_id", "requester_id", "requester_handle", "payer_id", "payer_handle", "amount",
                  "currency", "note", "status", "payment_id", "created_at"]


def key():
    return "k-" + uuid.uuid4().hex


def user(handle, balance, email=None, password=PASSWORD, uid=None, name=None):
    return {"id": uid or f"u_{handle}", "email": email or f"{handle}@example.com", "password": password,
            "display_name": name or handle.capitalize(), "handle": handle, "balance": balance}


STD_BALANCES = {"ada": 10000, "bob": 2500, "cy": 0, "dan": 5000, "op": 0}


def fixture(users=None, payments=None, requests=None, currency="EUR", minor_units=2, operators=None):
    if users is None:
        users = [user(h, b) for h, b in STD_BALANCES.items()]
    body = {"currency": currency, "minor_units": minor_units, "users": users,
            "payments": payments or [], "requests": requests or []}
    if operators is not None:
        body["settlement_operator_ids"] = operators
    return body


def reset(body):
    r = CLIENT.post("/_test/reset", json=body)
    assert r.status_code == 204, (r.status_code, r.text)
    return r


def login(email, password=PASSWORD):
    r = CLIENT.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()["token"]


def world(balances=None, operators=("u_op",), payments=None, requests=None, currency="EUR", minor_units=2):
    """Reset to a standard fixture and return {handle: token}."""
    balances = balances or STD_BALANCES
    users = [user(h, b) for h, b in balances.items()]
    reset(fixture(users=users, payments=payments, requests=requests, currency=currency,
                  minor_units=minor_units,
                  operators=[o for o in operators if o in {u["id"] for u in users}]))
    return {h: login(f"{h}@example.com") for h in balances}


def headers(token=None, idem=None):
    h = {}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


def post(path, token=None, body=None, idem=None, raw=None, client=None):
    c = client or CLIENT
    h = headers(token, idem)
    if raw is not None:
        h["Content-Type"] = "application/json"
        return c.post(path, content=raw.encode("utf-8") if isinstance(raw, str) else raw, headers=h)
    if body is None:
        return c.post(path, headers=h)
    return c.post(path, json=body, headers=h)


def get(path, token=None, params=None, client=None):
    c = client or CLIENT
    return c.get(path, headers=headers(token), params=params)


def pay(token, to, amount, note=None, visibility=None, idem=None):
    body = {"to_handle": to, "amount": amount}
    if note is not None:
        body["note"] = note
    if visibility is not None:
        body["visibility"] = visibility
    return post("/payments", token, body, idem or key())


def ask(token, payer, amount, note="x", idem=None):
    return post("/requests", token, {"payer_handle": payer, "amount": amount, "note": note}, idem or key())


def ask_ok(token, payer, amount, note="x"):
    r = ask(token, payer, amount, note)
    assert r.status_code == 201, (r.status_code, r.text)
    return r.json()


def pay_request(token, request_id, body=None, idem=None):
    return post(f"/requests/{request_id}/pay", token, {} if body is None else body, idem or key())


def me(token):
    r = get("/me", token)
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()


def balance(token):
    return me(token)["balance"]


def balances(tokens):
    return {h: balance(t) for h, t in tokens.items()}


def pages(path, token, field, params=None):
    items, offset = [], 0
    while True:
        p = dict(params or {})
        p.update({"limit": 200, "offset": offset})
        r = get(path, token, p)
        assert r.status_code == 200, (r.status_code, r.text)
        data = r.json()
        items.extend(data[field])
        if not data["has_more"]:
            return items
        offset += 200


def activity(token):
    return pages("/activity", token, "payments")


def requests_of(token, **params):
    return pages("/requests", token, "requests", params)


def snapshot(tokens):
    """Everything the public interface shows each user; compared before/after a refused call."""
    snap = {}
    for h, t in tokens.items():
        acts = sorted(activity(t), key=lambda p: p["payment_id"])
        reqs = sorted(requests_of(t), key=lambda q: q["request_id"])
        snap[h] = {"me": me(t), "activity": acts, "requests": reqs}
    return snap


def assert_error(r, status, code=None):
    assert r.status_code == status, (r.status_code, r.text)
    body = r.json()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict), body
    assert isinstance(body["error"].get("code"), str), body
    assert isinstance(body["error"].get("message"), str), body
    if code is not None:
        assert body["error"]["code"] == code, body


def is_id(v):
    return isinstance(v, str) and 1 <= len(v) <= 64


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_ts(v):
    return isinstance(v, str) and bool(TS_RE.match(v))


def parse_ts(v):
    return datetime.datetime.fromisoformat(v.replace("z", "Z").replace("t", "T"))


def check_payment(p):
    for f in PAYMENT_FIELDS:
        assert f in p, (f, p)
    assert is_id(p["payment_id"]) and is_id(p["from_user_id"]) and is_id(p["to_user_id"]), p
    assert isinstance(p["from_handle"], str) and isinstance(p["to_handle"], str), p
    assert is_int(p["amount"]), p
    assert isinstance(p["currency"], str) and isinstance(p["note"], str), p
    assert p["visibility"] in ("public", "private"), p
    assert p["request_id"] is None or is_id(p["request_id"]), p
    assert is_ts(p["created_at"]), p


def check_request(q):
    for f in REQUEST_FIELDS:
        assert f in q, (f, q)
    assert is_id(q["request_id"]) and is_id(q["requester_id"]) and is_id(q["payer_id"]), q
    assert isinstance(q["requester_handle"], str) and isinstance(q["payer_handle"], str), q
    assert is_int(q["amount"]), q
    assert isinstance(q["currency"], str) and isinstance(q["note"], str), q
    assert q["status"] in ("pending", "paid", "declined", "cancelled"), q
    assert q["payment_id"] is None or is_id(q["payment_id"]), q
    assert is_ts(q["created_at"]), q


def assert_newest_first(items):
    stamps = [parse_ts(i["created_at"]) for i in items]
    assert all(a >= b for a, b in zip(stamps, stamps[1:])), [i["created_at"] for i in items]


def find_request(token, request_id):
    for q in requests_of(token):
        if q["request_id"] == request_id:
            return q
    return None


def simultaneously(calls):
    """Fire every zero-argument callable at the same instant (one thread and client each)."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i, fn):
        with httpx.Client(base_url=BASE, timeout=TIMEOUT) as c:
            c.get("/health")
            barrier.wait()
            try:
                results[i] = fn(c)
            except Exception as error:  # surfaced to the test
                results[i] = error

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for r in results:
        assert not isinstance(r, Exception), r
    return results


def no_5xx(responses):
    for r in responses:
        assert r.status_code < 500, (r.status_code, r.text)


def raw_json(obj):
    return json.dumps(obj)


# ---------------- stage 2 ----------------
AUTH_FIELDS = ["authorization_id", "from_user_id", "from_handle", "to_user_id", "to_handle", "amount",
               "captured_amount", "remaining_amount", "currency", "note", "visibility", "status", "expires_at",
               "payment_id", "created_at"]


def ts_in(seconds):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    return t.replace(microsecond=0).isoformat()


def seeded_auth(aid, frm, to, amount, status="open", expires_in=7200, note="hold", visibility="public"):
    return {"id": aid, "from_user_id": f"u_{frm}", "to_user_id": f"u_{to}", "amount": amount, "note": note,
            "visibility": visibility, "status": status, "expires_at": ts_in(expires_in)}


def world2(balances=None, authorizations=None, ttl=None, payments=None, requests=None, currency="EUR",
           minor_units=2, operators=("u_op",)):
    balances = balances or STD_BALANCES
    users = [user(h, b) for h, b in balances.items()]
    body = fixture(users=users, payments=payments, requests=requests, currency=currency, minor_units=minor_units,
                   operators=[o for o in operators if o in {u["id"] for u in users}])
    if authorizations is not None:
        body["authorizations"] = authorizations
    if ttl is not None:
        body["authorization_ttl_seconds"] = ttl
    reset(body)
    return {h: login(f"{h}@example.com") for h in balances}


def authorize(token, to, amount, note=None, visibility=None, idem=None):
    body = {"to_handle": to, "amount": amount}
    if note is not None:
        body["note"] = note
    if visibility is not None:
        body["visibility"] = visibility
    return post("/authorizations", token, body, idem or key())


def authorize_ok(token, to, amount, **kw):
    r = authorize(token, to, amount, **kw)
    assert r.status_code == 201, (r.status_code, r.text)
    return r.json()


def capture(token, aid, body=None, idem=None):
    return post(f"/authorizations/{aid}/capture", token, {} if body is None else body, idem or key())


def void(token, aid):
    return post(f"/authorizations/{aid}/void", token)


def auths_of(token, **params):
    return pages("/authorizations", token, "authorizations", params)


def find_auth(token, aid):
    for a in auths_of(token):
        if a["authorization_id"] == aid:
            return a
    return None


def check_auth(a):
    for f in AUTH_FIELDS:
        assert f in a, (f, a)
    assert is_id(a["authorization_id"]) and is_id(a["from_user_id"]) and is_id(a["to_user_id"]), a
    for f in ("amount", "captured_amount", "remaining_amount"):
        assert is_int(a[f]) and a[f] >= 0, (f, a)
    assert a["status"] in ("open", "captured", "voided", "expired"), a
    assert a["visibility"] in ("public", "private"), a
    assert is_ts(a["expires_at"]) and is_ts(a["created_at"]), a
    assert a["payment_id"] is None or is_id(a["payment_id"]), a


def wallet(token):
    m = me(token)
    assert m["balance"] == m["total"], m
    assert m["available"] == m["total"] - m["held"] and m["available"] >= 0, m
    return m


def fmt(minor, units=2, currency="EUR"):
    if units == 0:
        return f"{minor} {currency}"
    s = str(minor).rjust(units + 1, "0")
    return f"{s[:-units]}.{s[-units:]} {currency}"
