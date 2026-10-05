from pf import (CLIENT, PASSWORD, assert_error, ask_ok, balance, fixture, get, is_id, key, login, me, pay, post,
                reset, snapshot, user,
                world)


def signup(email, password=PASSWORD, name="Newbie"):
    return CLIENT.post("/auth/signup", json={"email": email, "password": password, "display_name": name})


def test_signup_creates_account_with_derived_handle():
    """
    Spec: "POST /auth/signup { "email": "a@example.com", "password": "correct horse", "display_name": "Ada" }"
    Spec: "A user created through `POST /auth/signup` (§6 — there is no `handle` field in the signup body) has one **derived** from their email: take the local part, lowercase it, replace every character outside `[a-z0-9_]` with `_`, and truncate to 20 characters."
    Spec: "New users start with a balance of `0`."
    """
    world()
    r = signup("Mary.Jane-O+x@Example.org", name="Mary")
    assert r.status_code == 201, r.text
    body = r.json()
    assert is_id(body["user_id"]) and body["display_name"] == "Mary" and isinstance(body["token"], str)
    m = me(body["token"])
    assert m["handle"] == "mary_jane_o_x"
    assert m["balance"] == 0 and m["user_id"] == body["user_id"]
    assert m["currency"] == "EUR" and m["minor_units"] == 2


def test_signup_handle_truncated_to_20():
    """
    Spec: "Every user has a **handle**: unique across the service, matching `^[a-z0-9_]{1,20}$`, and never changing once set."
    """
    world()
    r = signup("ABCDEFGHIJklmnopqrstuvwxyz0123@example.org")
    assert r.status_code == 201, r.text
    assert me(r.json()["token"])["handle"] == "abcdefghijklmnopqrst"


def test_login_returns_token():
    """
    Spec: "POST /auth/login { "email": "a@example.com", "password": "correct horse" }"
    Spec: "Authentication supports signup and login."
    """
    world()
    r = signup("fresh@example.org", name="Fresh")
    uid = r.json()["user_id"]
    r = CLIENT.post("/auth/login", json={"email": "fresh@example.org", "password": PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == uid and body["display_name"] == "Fresh" and isinstance(body["token"], str)
    assert me(body["token"])["handle"] == "fresh"


def test_new_user_can_receive_and_be_asked_immediately():
    """
    Spec: "They can receive money and be asked for money immediately."
    """
    t = world()
    tok = signup("newbie@example.org").json()["token"]
    assert pay(t["ada"], "newbie", 250).status_code == 201
    assert balance(tok) == 250
    q = ask_ok(t["bob"], "newbie", 999)
    assert q["status"] == "pending" and q["payer_handle"] == "newbie"


def test_email_taken():
    """
    Spec: "| Email already registered | 409 `email_taken` |"
    """
    # The seeded user's handle differs from the email's local part, so only the email collides.
    reset(fixture(users=[user("caz", 300, email="carol@example.com"), user("bob", 100)]))
    t = {"caz": login("carol@example.com"), "bob": login("bob@example.com")}
    before = snapshot(t)
    assert_error(signup("carol@example.com", password="other password"), 409, "email_taken")
    assert snapshot(t) == before
    assert_error(CLIENT.post("/auth/login", json={"email": "carol@example.com", "password": "other password"}),
                 401, "unauthenticated")
    assert me(login("carol@example.com"))["handle"] == "caz"
    assert_error(pay(t["bob"], "carol", 1), 404, "not_found")


def test_handle_taken_creates_no_account():
    """
    Spec: "| The handle derived from the email (§4) is already taken | 409 `handle_taken`, and no account is created |"
    Spec: "If that handle is already taken the signup fails; see the signup table in §6."
    """
    world()
    assert_error(signup("ADA@other.org"), 409, "handle_taken")
    assert_error(CLIENT.post("/auth/login", json={"email": "ADA@other.org", "password": PASSWORD}),
                 401, "unauthenticated")
    assert_error(signup("ADA@other.org"), 409, "handle_taken")
    assert signup("first@a.org").status_code == 201
    assert_error(signup("first@b.org"), 409, "handle_taken")


def test_password_length():
    """
    Spec: "| Password shorter than 8 characters | 422 `validation_failed` |"
    """
    world()
    assert_error(signup("short@example.org", password="1234567"), 422, "validation_failed")
    assert_error(CLIENT.post("/auth/login", json={"email": "short@example.org", "password": "1234567"}),
                 401, "unauthenticated")
    assert signup("short@example.org", password="12345678").status_code == 201


def test_email_format():
    """
    Spec: "| `email` not of the form `local@domain` | 422 `validation_failed` |"
    """
    world()
    assert_error(signup("plainaddress"), 422, "validation_failed")
    assert_error(signup("no.at.sign.example.org"), 422, "validation_failed")


def test_wrong_password_or_unknown_email():
    """
    Spec: "| Wrong password or unknown email on login | 401 `unauthenticated` |"
    """
    world()
    assert_error(CLIENT.post("/auth/login", json={"email": "ada@example.com", "password": "wrong horse"}),
                 401, "unauthenticated")
    assert_error(CLIENT.post("/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}),
                 401, "unauthenticated")


def test_signup_malformed_body():
    """
    Spec: "| 400 | `malformed_request` | Unparseable body, or a field of the wrong JSON type |"
    """
    world()
    r = CLIENT.post("/auth/signup", content=b"{not json", headers={"Content-Type": "application/json"})
    assert_error(r, 400, "malformed_request")
    r = CLIENT.post("/auth/login", content=b"{\"email\": ", headers={"Content-Type": "application/json"})
    assert_error(r, 400, "malformed_request")


def test_protected_endpoints_need_token():
    """
    Spec: "Every other endpoint requires a bearer token, except `/health`, `/_test/reset` and the two above."
    Spec: "Wallet API endpoints require authentication."
    Spec: "| 401 | `unauthenticated` | Missing, malformed or unknown bearer token |"
    Spec: "Authorization: Bearer <token>"
    """
    t = world()
    q = ask_ok(t["bob"], "ada", 10)
    before = snapshot(t)
    calls = [
        ("GET", "/me", None, False), ("GET", "/activity", None, False), ("GET", "/requests", None, False),
        ("POST", "/payments", {"to_handle": "bob", "amount": 1}, True),
        ("POST", "/requests", {"payer_handle": "ada", "amount": 1, "note": "n"}, True),
        ("POST", f"/requests/{q['request_id']}/pay", {}, True),
        ("POST", f"/requests/{q['request_id']}/decline", None, False),
        ("POST", f"/requests/{q['request_id']}/cancel", None, False),
        ("POST", "/splits", {"amount": 10, "participant_handles": ["ada", "bob"], "note": "n"}, True),
        ("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, True),
    ]
    for auth in (None, "Bearer not-a-real-token", "Token " + t["ada"], "Bearer", t["ada"]):
        for method, path, body, idem in calls:
            h = {}
            if auth is not None:
                h["Authorization"] = auth
            if idem:
                h["Idempotency-Key"] = key()
            r = CLIENT.request(method, path, json=body, headers=h) if body is not None else \
                CLIENT.request(method, path, headers=h)
            assert_error(r, 401, "unauthenticated")
    assert snapshot(t) == before


def test_multiple_tokens_valid():
    """
    Spec: "Tokens do not expire."
    Spec: "An account may have multiple valid tokens and concurrent sessions."
    """
    world()
    s = signup("multi@example.org").json()["token"]
    a = login("multi@example.org")
    b = login("multi@example.org")
    x = login("ada@example.com")
    y = login("ada@example.com")
    for tok in (s, a, b):
        assert me(tok)["handle"] == "multi"
    assert me(x)["handle"] == "ada" and me(y)["handle"] == "ada"


def test_password_not_stored_in_plaintext():
    """
    Spec: "Passwords must be stored using a password-hashing function such as bcrypt, scrypt or Argon2, or an equivalent."
    Spec: "Plaintext password storage is not permitted."
    """
    world()
    secret = "Zq9-unique-plaintext-marker-77"
    assert signup("hashme@example.org", password=secret).status_code == 201
    r = CLIENT.get("/_test/export")
    assert r.status_code == 200
    assert secret not in r.text
    assert login("hashme@example.org", secret)
