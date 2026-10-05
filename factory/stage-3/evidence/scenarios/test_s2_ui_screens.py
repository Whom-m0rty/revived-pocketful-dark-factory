import time

import pytest
from playwright.sync_api import expect

from pf import (BASE, CLIENT, activity, ask_ok, authorize_ok, auths_of, balance, capture, find_auth, find_request,
                fixture, fmt, key, parse_ts, pay, post, requests_of, reset, seeded_auth, user, void, wallet, world2)
from ui import PostWatch, decimal_value, fill_pay, goto, items, login_ui, lose_next_post, text, tid


def wait_until(fn, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.2)
    return fn()


# ---------- requests screen ----------

def test_ui_requests_lists_and_buttons(page):
    """
    Spec: "| `incoming-list`, `outgoing-list` | Containers |"
    Spec: "| `request-item-{request_id}` | One per request. Carries `data-status=\"{status}\"` |"
    Spec: "| `request-amount-{request_id}` | Text is exactly the formatted amount |"
    Spec: "| `request-pay-{request_id}` | Button. Present only on a `pending` incoming request |"
    Spec: "| `request-decline-{request_id}` | Button. Present only on a `pending` incoming request |"
    Spec: "| `request-cancel-{request_id}` | Button. Present only on a `pending` outgoing request |"
    """
    t = world2()
    inc = ask_ok(t["bob"], "ada", 1234)
    inc_declined = ask_ok(t["dan"], "ada", 50)
    post(f"/requests/{inc_declined['request_id']}/decline", t["ada"])
    out = ask_ok(t["ada"], "dan", 999)
    out_paid = ask_ok(t["ada"], "bob", 10)
    assert post(f"/requests/{out_paid['request_id']}/pay", t["bob"], {}, key()).status_code == 201
    other = ask_ok(t["bob"], "dan", 5)
    login_ui(page, "ada@example.com")
    goto(page, "/requests")
    incoming = page.get_by_test_id("incoming-list")
    outgoing = page.get_by_test_id("outgoing-list")
    expect(incoming.get_by_test_id(f"request-item-{inc['request_id']}")).to_have_attribute("data-status", "pending")
    expect(incoming.get_by_test_id(f"request-item-{inc_declined['request_id']}")).to_have_attribute("data-status",
                                                                                                   "declined")
    expect(outgoing.get_by_test_id(f"request-item-{out['request_id']}")).to_have_attribute("data-status", "pending")
    expect(outgoing.get_by_test_id(f"request-item-{out_paid['request_id']}")).to_have_attribute("data-status", "paid")
    expect(tid(page, f"request-item-{other['request_id']}")).to_have_count(0)
    assert text(tid(page, f"request-amount-{inc['request_id']}")) == "12.34 EUR"
    assert text(tid(page, f"request-amount-{out['request_id']}")) == "9.99 EUR"
    expect(tid(page, f"request-pay-{inc['request_id']}")).to_be_visible()
    expect(tid(page, f"request-decline-{inc['request_id']}")).to_be_visible()
    expect(tid(page, f"request-cancel-{inc['request_id']}")).to_have_count(0)
    expect(tid(page, f"request-cancel-{out['request_id']}")).to_be_visible()
    expect(tid(page, f"request-pay-{out['request_id']}")).to_have_count(0)
    expect(tid(page, f"request-decline-{out['request_id']}")).to_have_count(0)
    for done in (inc_declined, out_paid):
        for b in ("pay", "decline", "cancel"):
            expect(tid(page, f"request-{b}-{done['request_id']}")).to_have_count(0)
    expect(tid(page, "empty-requests")).to_have_count(0)


@pytest.mark.parametrize("action", ["pay", "decline", "cancel"])
def test_ui_request_actions(page, action):
    """
    Spec: "| `/requests` | Incoming and outgoing requests, with pay, decline and cancel |"
    Spec: "After any successful action, the balance, the feed and the request lists on the same page must show the new state without a manual reload."
    """
    t = world2()
    q = ask_ok(t["bob"], "ada", 300) if action != "cancel" else ask_ok(t["ada"], "bob", 300)
    rid = q["request_id"]
    login_ui(page, "ada@example.com")
    goto(page, "/requests")
    tid(page, f"request-{action}-{rid}").click()
    expected = {"pay": "paid", "decline": "declined", "cancel": "cancelled"}[action]
    expect(tid(page, f"request-{action}-{rid}")).to_have_count(0)
    item = tid(page, f"request-item-{rid}")
    if item.count():
        expect(item).to_have_attribute("data-status", expected)
    assert find_request(t["ada"], rid)["status"] == expected
    expect(tid(page, "request-error")).to_have_count(0)
    if action == "pay":
        assert balance(t["ada"]) == 9700 and balance(t["bob"]) == 2800


def test_ui_request_cancelled_elsewhere(page):
    """
    Spec: "A request cancelled elsewhere while its pay button is visible must show `request-error` when payment is refused and refresh the request list so the stale pay button disappears."
    Spec: "| `request-error` | Shown when a pay, decline or cancel is refused |"
    """
    t = world2()
    q = ask_ok(t["bob"], "ada", 300)
    login_ui(page, "ada@example.com")
    goto(page, "/requests")
    expect(tid(page, f"request-pay-{q['request_id']}")).to_be_visible()
    assert post(f"/requests/{q['request_id']}/cancel", t["bob"]).status_code == 200
    tid(page, f"request-pay-{q['request_id']}").click()
    expect(tid(page, "request-error")).to_be_visible()
    expect(tid(page, f"request-pay-{q['request_id']}")).to_have_count(0)
    assert balance(t["ada"]) == 10000


def test_ui_empty_requests(page):
    """
    Spec: "| `empty-requests` | Shown when both lists are empty |"
    """
    t = world2()
    ask_ok(t["bob"], "dan", 5)
    login_ui(page, "cy@example.com")
    goto(page, "/requests")
    expect(tid(page, "empty-requests")).to_be_visible()
    expect(page.locator('[data-testid^="request-item-"]')).to_have_count(0)


# ---------- split screen ----------

def test_ui_split_preview_and_submit(page):
    """
    Spec: "| `split-amount` | Decimal input, same rule as `pay-amount` |"
    Spec: "| `split-handles` | Text input: handles separated by commas, in order |"
    Spec: "| `split-note`, `split-submit` | Input and button |"
    Spec: "| `split-preview` | Shows the computed shares before submitting. Contains one `split-share-{handle}` per participant |"
    Spec: "| `split-share-{handle}` | Text is exactly the formatted share amount |"
    Spec: "`split-preview` must show the shares the server would compute, by the rule in `stage-1.md` §9, before anything is posted."
    Spec: "The preview and submitted split must have identical shares."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/split")
    watch = PostWatch(page)
    tid(page, "split-amount").fill("10.00")
    tid(page, "split-handles").fill("ada,bob,cy")
    tid(page, "split-note").fill("pizza")
    preview = tid(page, "split-preview")
    expect(preview.get_by_test_id("split-share-ada")).to_have_text("3.34 EUR")
    assert text(preview.get_by_test_id("split-share-bob")) == "3.33 EUR"
    assert text(preview.get_by_test_id("split-share-cy")) == "3.33 EUR"
    assert watch.posts == []
    tid(page, "split-submit").click()
    assert wait_until(lambda: len(requests_of(t["ada"], direction="outgoing")) == 2)
    reqs = {q["payer_handle"]: q["amount"] for q in requests_of(t["ada"], direction="outgoing")}
    assert reqs == {"bob": 333, "cy": 333}
    expect(tid(page, "split-error")).to_have_count(0)


def test_ui_split_preview_order(page):
    """
    Spec: "Splitting the same amount among the same people in a different `participant_handles` order gives the extra unit to a different person."
    """
    t = world2()
    login_ui(page, "bob@example.com")
    goto(page, "/split")
    tid(page, "split-amount").fill("0.10")
    tid(page, "split-handles").fill("cy,ada,bob")
    preview = tid(page, "split-preview")
    expect(preview.get_by_test_id("split-share-cy")).to_have_text("0.04 EUR")
    assert text(preview.get_by_test_id("split-share-ada")) == "0.03 EUR"
    assert text(preview.get_by_test_id("split-share-bob")) == "0.03 EUR"
    tid(page, "split-submit").click()
    assert wait_until(lambda: len(requests_of(t["bob"], direction="outgoing")) == 2)
    reqs = {q["payer_handle"]: q["amount"] for q in requests_of(t["bob"], direction="outgoing")}
    assert reqs == {"cy": 4, "ada": 3}


def test_ui_split_refused(page):
    """
    Spec: "| `split-error` | Error message, when the split is refused |"
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/split")
    tid(page, "split-amount").fill("5.00")
    tid(page, "split-handles").fill("bob,ghost_user")
    tid(page, "split-note").fill("x")
    tid(page, "split-submit").click()
    expect(tid(page, "split-error")).to_be_visible()
    assert requests_of(t["bob"]) == []


def test_ui_split_bad_amount_not_sent(page):
    """
    Spec: "| `split-amount` | Decimal input, same rule as `pay-amount` |"
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/split")
    watch = PostWatch(page)
    tid(page, "split-amount").fill("10.005")
    tid(page, "split-handles").fill("ada,bob")
    tid(page, "split-note").fill("x")
    tid(page, "split-submit").click()
    expect(tid(page, "split-error")).to_be_visible()
    page.wait_for_timeout(500)
    assert watch.posts == []
    assert requests_of(t["bob"]) == []


# ---------- wallet numbers ----------

def test_ui_wallet_available_and_held_seeded(page):
    """
    Spec: "| `wallet-balance` | Formatted `total`, retaining the existing display and `data-amount` |"
    Spec: "| `wallet-available` | Formatted `available`, with `data-amount`. **Present this as the headline number** — it is what the user can actually spend |"
    Spec: "| `wallet-held` | Formatted `held`, with `data-amount`. Absent when `held` is zero |"
    Spec: "The UI must reflect seeded and newly created holds."
    Spec: "Show available funds as the user's spending balance, including immediately after reset with open holds."
    """
    world2(authorizations=[seeded_auth("a_1", "ada", "bob", 2000)])
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, "wallet-available")).to_have_attribute("data-amount", "8000")
    assert text(tid(page, "wallet-available")) == "80.00 EUR"
    expect(tid(page, "wallet-held")).to_have_attribute("data-amount", "2000")
    assert text(tid(page, "wallet-held")) == "20.00 EUR"
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "10000")
    assert text(tid(page, "wallet-balance")) == "100.00 EUR"


def test_ui_wallet_held_absent_when_zero(page):
    """
    Spec: "| `wallet-held` | Formatted `held`, with `data-amount`. Absent when `held` is zero |"
    """
    world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, "wallet-available")).to_have_attribute("data-amount", "10000")
    expect(tid(page, "wallet-held")).to_have_count(0)


def open_authorize_form(page):
    """The spec does not say which screen carries the authorise form: look on / and then /authorizations."""
    for path in ("/", "/authorizations"):
        goto(page, path)
        if tid(page, "authorize-handle").count():
            return path
    raise AssertionError("authorize-handle is on neither / nor /authorizations")


def test_ui_authorize_form(page):
    """
    Spec: "| `authorize-handle`, `authorize-amount`, `authorize-note`, `authorize-visibility`, `authorize-submit` | The authorise form. Same input rules as the pay form |"
    """
    t = world2()
    login_ui(page, "ada@example.com")
    open_authorize_form(page)
    tid(page, "authorize-handle").fill("bob")
    tid(page, "authorize-amount").fill("25.5")
    tid(page, "authorize-note").fill("deposit")
    tid(page, "authorize-visibility").select_option("private")
    tid(page, "authorize-submit").click()
    assert wait_until(lambda: len(auths_of(t["ada"])) == 1)
    expect(tid(page, "authorize-error")).to_have_count(0)
    a = auths_of(t["ada"])
    assert (a[0]["amount"], a[0]["to_handle"], a[0]["note"], a[0]["visibility"], a[0]["status"]) == \
        (2550, "bob", "deposit", "private", "open")
    goto(page, "/")
    expect(tid(page, "wallet-available")).to_have_attribute("data-amount", "7450")
    expect(tid(page, "wallet-held")).to_have_attribute("data-amount", "2550")
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "10000")


def test_ui_authorize_refused(page):
    """
    Spec: "| `authorize-error` | Shown when the authorisation is refused, including insufficient available funds |"
    """
    t = world2(authorizations=[seeded_auth("a_1", "ada", "bob", 9000)])
    login_ui(page, "ada@example.com")
    open_authorize_form(page)
    watch = PostWatch(page)
    tid(page, "authorize-handle").fill("bob")
    tid(page, "authorize-amount").fill("10.005")
    tid(page, "authorize-submit").click()
    expect(tid(page, "authorize-error")).to_be_visible()
    page.wait_for_timeout(300)
    assert watch.posts == []
    open_authorize_form(page)
    tid(page, "authorize-handle").fill("bob")
    tid(page, "authorize-amount").fill("10.01")
    tid(page, "authorize-submit").click()
    expect(tid(page, "authorize-error")).to_be_visible()
    assert len(auths_of(t["ada"])) == 1 and wallet(t["ada"])["held"] == 9000


def test_ui_refresh_updates_available_and_held(page):
    """
    Spec: "The same balance refresh rules apply to the available and held amounts introduced below."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, "wallet-available")).to_have_attribute("data-amount", "10000")
    authorize_ok(t["ada"], "bob", 1500)
    tid(page, "wallet-refresh").click()
    expect(tid(page, "wallet-available")).to_have_attribute("data-amount", "8500")
    expect(tid(page, "wallet-held")).to_have_attribute("data-amount", "1500")


# ---------- authorizations screen ----------

def test_ui_authorizations_list(page):
    """
    Spec: "| `authorization-list` | Container on `/authorizations`. Children newest first in the DOM |"
    Spec: "| `authorization-item-{authorization_id}` | Carries `data-status=\"{status}\"` |"
    Spec: "| `authorization-amount-{id}` | Text is exactly the formatted authorised amount |"
    Spec: "| `authorization-captured-{id}` | Formatted captured amount. Present only when `status` is `captured` |"
    Spec: "| `authorization-expires-{id}` | Text is the RFC 3339 `expires_at` |"
    Spec: "| `authorization-capture-amount-{id}` | Decimal input, pre-filled with the remaining amount. Present only on an incoming `open` authorisation |"
    Spec: "| `authorization-capture-{id}` | Button. Present only on an incoming `open` authorisation |"
    Spec: "| `authorization-void-{id}` | Button. Present only on an outgoing `open` authorisation |"
    """
    t = world2()
    inc_open = authorize_ok(t["dan"], "ada", 2000)
    time.sleep(1.1)
    capture(t["ada"], inc_open["authorization_id"], {"amount": 500, "final": False})
    out_open = authorize_ok(t["ada"], "bob", 1234)
    time.sleep(1.1)
    cap = authorize_ok(t["bob"], "ada", 800)
    capture(t["ada"], cap["authorization_id"], {"amount": 600})
    login_ui(page, "ada@example.com")
    goto(page, "/authorizations")
    order = items(page, "authorization-list", "authorization-item-")
    assert order == [f"authorization-item-{a['authorization_id']}" for a in (cap, out_open, inc_open)], order
    expect(tid(page, f"authorization-item-{inc_open['authorization_id']}")).to_have_attribute("data-status", "open")
    expect(tid(page, f"authorization-item-{cap['authorization_id']}")).to_have_attribute("data-status", "captured")
    assert text(tid(page, f"authorization-amount-{out_open['authorization_id']}")) == "12.34 EUR"
    assert text(tid(page, f"authorization-amount-{inc_open['authorization_id']}")) == "20.00 EUR"
    assert "6.00 EUR" in text(tid(page, f"authorization-captured-{cap['authorization_id']}"))
    expect(tid(page, f"authorization-captured-{inc_open['authorization_id']}")).to_have_count(0)
    expect(tid(page, f"authorization-captured-{out_open['authorization_id']}")).to_have_count(0)
    for a in (inc_open, out_open, cap):
        shown = text(tid(page, f"authorization-expires-{a['authorization_id']}"))
        assert parse_ts(shown) == parse_ts(find_auth(t["ada"], a["authorization_id"])["expires_at"])
    pre = tid(page, f"authorization-capture-amount-{inc_open['authorization_id']}").input_value()
    assert decimal_value(pre) * 100 == 1500
    expect(tid(page, f"authorization-capture-{inc_open['authorization_id']}")).to_be_visible()
    expect(tid(page, f"authorization-void-{inc_open['authorization_id']}")).to_have_count(0)
    expect(tid(page, f"authorization-void-{out_open['authorization_id']}")).to_be_visible()
    expect(tid(page, f"authorization-capture-{out_open['authorization_id']}")).to_have_count(0)
    expect(tid(page, f"authorization-capture-amount-{out_open['authorization_id']}")).to_have_count(0)
    for b in ("capture", "void", "capture-amount"):
        expect(tid(page, f"authorization-{b}-{cap['authorization_id']}")).to_have_count(0)
    expect(tid(page, "empty-authorizations")).to_have_count(0)


def test_ui_capture_partial(page):
    """
    Spec: "| `authorization-capture-{id}` | Button. Present only on an incoming `open` authorisation |"
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 500)
    login_ui(page, "bob@example.com")
    goto(page, "/authorizations")
    tid(page, f"authorization-capture-amount-{a['authorization_id']}").fill("3.00")
    tid(page, f"authorization-capture-{a['authorization_id']}").click()
    expect(tid(page, f"authorization-item-{a['authorization_id']}")).to_have_attribute("data-status", "captured")
    expect(tid(page, f"authorization-capture-{a['authorization_id']}")).to_have_count(0)
    now = find_auth(t["bob"], a["authorization_id"])
    assert (now["status"], now["captured_amount"]) == ("captured", 300)
    assert wallet(t["ada"])["total"] == 9700 and wallet(t["ada"])["held"] == 0
    assert wallet(t["bob"])["total"] == 2800
    expect(tid(page, "authorization-error")).to_have_count(0)


def test_ui_void(page):
    """
    Spec: "| `authorization-void-{id}` | Button. Present only on an outgoing `open` authorisation |"
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 500)
    login_ui(page, "ada@example.com")
    goto(page, "/authorizations")
    tid(page, f"authorization-void-{a['authorization_id']}").click()
    expect(tid(page, f"authorization-item-{a['authorization_id']}")).to_have_attribute("data-status", "voided")
    expect(tid(page, f"authorization-void-{a['authorization_id']}")).to_have_count(0)
    assert find_auth(t["ada"], a["authorization_id"])["status"] == "voided"
    assert wallet(t["ada"])["held"] == 0


def test_ui_capture_refused(page):
    """
    Spec: "| `authorization-error` | Shown when a capture or a void is refused |"
    """
    t = world2()
    a = authorize_ok(t["ada"], "bob", 500)
    login_ui(page, "bob@example.com")
    goto(page, "/authorizations")
    expect(tid(page, f"authorization-capture-{a['authorization_id']}")).to_be_visible()
    assert void(t["ada"], a["authorization_id"]).status_code == 200
    tid(page, f"authorization-capture-{a['authorization_id']}").click()
    expect(tid(page, "authorization-error")).to_be_visible()
    assert wallet(t["bob"])["total"] == 2500


def test_ui_empty_authorizations(page):
    """
    Spec: "| `empty-authorizations` | Shown when the list is empty |"
    """
    t = world2()
    authorize_ok(t["ada"], "bob", 5)
    login_ui(page, "cy@example.com")
    goto(page, "/authorizations")
    expect(tid(page, "empty-authorizations")).to_be_visible()
    expect(page.locator('[data-testid^="authorization-item-"]')).to_have_count(0)


# ---------- upgrade in the browser ----------

def test_ui_signed_in_and_lost_payment_survive_import(page):
    """
    Spec: "A browser signed in before that export/import upgrade must remain signed in afterwards."
    Spec: "A payment whose response was lost before export remains retryable after import with the same body and key; the UI must recover the original payment and refresh the imported balance."
    Spec: "No page reload or new screen is required."
    Spec: "The form and pending retry identity must survive the upgrade."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    lost = lose_next_post(page)
    fill_pay(page, "bob", "20.00", note="through upgrade")
    tid(page, "pay-submit").click()
    expect(tid(page, "pay-uncertain")).to_be_visible()
    assert lost and balance(t["ada"]) == 8000
    exported = CLIENT.get("/_test/export").json()
    reset(fixture(users=[user("zed", 1)]))
    assert CLIENT.post("/_test/import", json=exported).status_code == 204
    expect(tid(page, "pay-handle")).to_have_value("bob")
    expect(tid(page, "pay-amount")).to_have_value("20.00")
    tid(page, "pay-submit").click()
    expect(tid(page, "pay-uncertain")).to_have_count(0)
    expect(tid(page, "pay-error")).to_have_count(0)
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "8000")
    assert balance(t["ada"]) == 8000 and len(activity(t["ada"])) == 1
    goto(page, "/requests")
    expect(tid(page, "current-user")).to_contain_text("Ada")


def test_ui_stage1_pending_request_payable(page, previous_base_url):
    """
    Spec: "Existing pending requests remain payable through the request screen."
    """
    import httpx
    c = httpx.Client(base_url=previous_base_url, timeout=15)
    fx = fixture(users=[user("ada", 1000), user("bob", 1000)],
                 requests=[{"id": "rq_old", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 250,
                            "note": "old", "status": "pending"}])
    assert c.post("/_test/reset", json=fx).status_code == 204
    tok = c.post("/auth/login", json={"email": "bob@example.com", "password": "correct horse"}).json()["token"]
    r = c.post("/requests", json={"payer_handle": "ada", "amount": 125, "note": "newer"},
               headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": key()})
    assert r.status_code == 201
    exported = c.get("/_test/export").json()
    world2()
    assert CLIENT.post("/_test/import", json=exported).status_code == 204
    login_ui(page, "ada@example.com")
    goto(page, "/requests")
    rid = r.json()["request_id"]
    tid(page, f"request-pay-{rid}").click()
    expect(tid(page, f"request-pay-{rid}")).to_have_count(0)
    ada = CLIENT.post("/auth/login", json={"email": "ada@example.com", "password": "correct horse"}).json()["token"]
    assert find_request(ada, rid)["status"] == "paid"
    assert balance(ada) == 875
    pending = [q for q in requests_of(ada) if q["status"] == "pending"]
    assert len(pending) == 1
    tid(page, f"request-pay-{pending[0]['request_id']}").click()
    expect(tid(page, f"request-pay-{pending[0]['request_id']}")).to_have_count(0)
    assert balance(ada) == 625
