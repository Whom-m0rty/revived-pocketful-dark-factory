import pytest

from pf import (activity, assert_error, balance, balances, check_payment, key, me, pay, post, snapshot, world)


def test_payment_created():
    """
    Spec: "POST /payments Authorization: Bearer <token> Idempotency-Key: 2f9c1a..."
    Spec: ""payment_id": "p_7","
    Spec: "A **payment** moves money from one wallet to another, immediately and atomically."
    Spec: "Users can send money by handle, request money and split bills."
    Spec: "Users identify recipients by handle."
    """
    t = world()
    ma, mb = me(t["ada"]), me(t["bob"])
    r = pay(t["ada"], "bob", 1500, note="dinner", visibility="public")
    assert r.status_code == 201, r.text
    p = r.json()
    check_payment(p)
    assert p["from_user_id"] == ma["user_id"] and p["from_handle"] == "ada"
    assert p["to_user_id"] == mb["user_id"] and p["to_handle"] == "bob"
    assert p["amount"] == 1500 and p["currency"] == "EUR"
    assert p["note"] == "dinner" and p["visibility"] == "public" and p["request_id"] is None
    assert balance(t["ada"]) == 8500 and balance(t["bob"]) == 4000


def test_payment_defaults():
    """
    Spec: "`note` is optional and defaults to `""`."
    Spec: "`visibility` is optional and defaults to `"public"`."
    Spec: "Omission alone selects the optional-field defaults."
    """
    t = world()
    r = pay(t["ada"], "bob", 10)
    assert r.status_code == 201, r.text
    assert r.json()["note"] == "" and r.json()["visibility"] == "public"


def test_payment_visible_in_both_wallets():
    """
    Spec: "The debit and the credit are one atomic step."
    Spec: "A payment is never visible in one wallet and not the other, and a failed payment leaves no trace in either."
    """
    t = world()
    p = pay(t["ada"], "bob", 300, visibility="private").json()
    assert p in activity(t["ada"]) and p in activity(t["bob"])
    assert balance(t["ada"]) == 9700 and balance(t["bob"]) == 2800


def test_payment_exact_balance_boundary():
    """
    Spec: "| The caller's balance is below `amount` | 409 `insufficient_funds` |"
    Spec: "No wallet balance may be negative, including transiently."
    """
    t = world()
    before = snapshot(t)
    assert_error(pay(t["bob"], "ada", 2501), 409, "insufficient_funds")
    assert snapshot(t) == before
    assert pay(t["bob"], "ada", 2500).status_code == 201
    assert balance(t["bob"]) == 0
    before = snapshot(t)
    assert_error(pay(t["bob"], "ada", 1), 409, "insufficient_funds")
    assert_error(pay(t["cy"], "ada", 1), 409, "insufficient_funds")
    assert snapshot(t) == before


@pytest.mark.parametrize("amount", [0, -1, -100, 1000000001, 1.5, 0.5, "100", "abc", True, False])
def test_payment_invalid_amount(amount):
    """
    Spec: "| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |"
    Spec: "Endpoint-specific field rules take precedence: invalid `amount` values (including strings and booleans), non-string `note` values (including `null`), and any `visibility` other than `public` or `private` are 422 `validation_failed`."
    Spec: "Booleans and strings are not numbers here."
    """
    t = world(balances={"ada": 2000000000, "bob": 0})
    before = snapshot(t)
    assert_error(post("/payments", t["ada"], {"to_handle": "bob", "amount": amount}, key()), 422, "validation_failed")
    assert snapshot(t) == before


def test_payment_amount_upper_bound_ok():
    """
    Spec: "| `amount` below 1, above 1000000000, or not an integer | 422 `validation_failed` |"
    """
    t = world(balances={"ada": 2000000000, "bob": 0})
    r = pay(t["ada"], "bob", 1000000000)
    assert r.status_code == 201
    r = pay(t["ada"], "bob", 1)
    assert r.status_code == 201
    assert balance(t["bob"]) == 1000000001


@pytest.mark.parametrize("raw_amount", ["1000", "1000.0", "1e3", "1E3", "1000.00"])
def test_payment_integral_numeric_forms(raw_amount):
    """
    Spec: "API amounts must have an integral numeric value: JSON `1000`, `1000.0` and `1e3` all represent the same valid minor-unit amount."
    """
    t = world()
    raw = '{"to_handle": "bob", "amount": %s}' % raw_amount
    r = post("/payments", t["ada"], raw=raw, idem=key())
    assert r.status_code == 201, r.text
    assert r.json()["amount"] == 1000
    assert balance(t["ada"]) == 9000 and balance(t["bob"]) == 3500


def test_self_payment():
    """
    Spec: "| `to_handle` is the caller's own handle | 422 `self_payment` |"
    """
    t = world()
    before = snapshot(t)
    assert_error(pay(t["ada"], "ada", 100), 422, "self_payment")
    assert snapshot(t) == before


def test_payment_note_length():
    """
    Spec: "| `note` longer than 200 characters | 422 `validation_failed` |"
    """
    t = world()
    before = snapshot(t)
    assert_error(pay(t["ada"], "bob", 1, note="x" * 201), 422, "validation_failed")
    assert snapshot(t) == before
    r = pay(t["ada"], "bob", 1, note="y" * 200)
    assert r.status_code == 201 and r.json()["note"] == "y" * 200


@pytest.mark.parametrize("note", [None, 5, True, ["a"], {"a": 1}])
def test_payment_note_non_string(note):
    """
    Spec: "Endpoint-specific field rules take precedence: invalid `amount` values (including strings and booleans), non-string `note` values (including `null`), and any `visibility` other than `public` or `private` are 422 `validation_failed`."
    """
    t = world()
    before = snapshot(t)
    r = post("/payments", t["ada"], {"to_handle": "bob", "amount": 5, "note": note}, key())
    assert_error(r, 422, "validation_failed")
    assert snapshot(t) == before


@pytest.mark.parametrize("vis", ["secret", "PUBLIC", "", None, 1, True])
def test_payment_bad_visibility(vis):
    """
    Spec: "| `visibility` is neither `public` nor `private` | 422 `validation_failed` |"
    """
    t = world()
    before = snapshot(t)
    r = post("/payments", t["ada"], {"to_handle": "bob", "amount": 5, "visibility": vis}, key())
    assert_error(r, 422, "validation_failed")
    assert snapshot(t) == before


def test_payment_unknown_handle():
    """
    Spec: "| No user has that handle | 404 `not_found` |"
    Spec: "Money moves only between existing wallets."
    """
    t = world()
    before = snapshot(t)
    assert_error(pay(t["ada"], "nobody", 100), 404, "not_found")
    assert snapshot(t) == before


def test_payment_missing_fields():
    """
    Spec: "| 422 | `validation_failed` | A required field or query parameter is missing, or a stated rule is violated with no more specific code |"
    """
    t = world()
    before = snapshot(t)
    assert_error(post("/payments", t["ada"], {"amount": 5}, key()), 422, "validation_failed")
    assert_error(post("/payments", t["ada"], {"to_handle": "bob"}, key()), 422, "validation_failed")
    assert snapshot(t) == before


def test_payment_malformed_body():
    """
    Spec: "| 400 | `malformed_request` | Unparseable body, or a field of the wrong JSON type |"
    Spec: "Reserve 400 `malformed_request` for a body that does not parse or a field of the wrong type."
    Spec: "Other wrong JSON types follow the rule below."
    """
    t = world()
    before = snapshot(t)
    assert_error(post("/payments", t["ada"], raw='{"to_handle": "bob", "amount": ', idem=key()), 400,
                 "malformed_request")
    assert_error(post("/payments", t["ada"], raw="not json at all", idem=key()), 400, "malformed_request")
    assert_error(post("/payments", t["ada"], {"to_handle": 123, "amount": 5}, key()), 400, "malformed_request")
    assert_error(post("/payments", t["ada"], {"to_handle": ["bob"], "amount": 5}, key()), 400, "malformed_request")
    assert snapshot(t) == before


@pytest.mark.parametrize("note", ["  padded  ", "<b>bold</b> & \"quotes\" 'x'", "emoji 🍕🎉👩‍👩‍👧 ünïcødé 漢字",
                                  "line1\nline2\ttab", "\\back\\slash", "é vs é"])
def test_note_verbatim(note):
    """
    Spec: "`note` is stored and returned verbatim: no trimming, no escaping, no normalisation."
    Spec: "Unicode and emoji survive a round trip byte for byte."
    """
    t = world()
    r = pay(t["ada"], "bob", 1, note=note)
    assert r.status_code == 201, r.text
    assert r.json()["note"] == note
    feed = [p for p in activity(t["bob"]) if p["payment_id"] == r.json()["payment_id"]]
    assert feed and feed[0]["note"] == note


def test_ordinary_payment_settlement_id_null():
    """
    Spec: "Every member is an ordinary payment with `settlement_id` linking the batch; nonmembers expose null for that field."
    """
    t = world()
    r = pay(t["ada"], "bob", 1)
    assert r.status_code == 201
    assert "settlement_id" in r.json() and r.json()["settlement_id"] is None
    for p in activity(t["ada"]):
        assert "settlement_id" in p and p["settlement_id"] is None


def test_sum_of_balances_conserved():
    """
    Spec: "The sum of wallet balances always equals the total seeded by the last `POST /_test/reset`."
    """
    t = world()
    total = sum(balances(t).values())
    pay(t["ada"], "bob", 700)
    pay(t["bob"], "cy", 3000)
    pay(t["dan"], "cy", 1)
    pay(t["cy"], "ada", 5)
    assert sum(balances(t).values()) == total
