import time

import pytest
from playwright.sync_api import expect

from pf import (BASE, activity, balance, find_request, fmt, key, me, pay, post, requests_of, world2)
from ui import PostWatch, fill_pay, goto, items, login_ui, lose_next_post, text, tid


# ---------- signup / login ----------

def test_ui_login(page):
    """
    Spec: "| `login-email`, `login-password`, `login-submit` | Inputs and button |"
    Spec: "| `current-user` | Visible on every screen when signed in. Text contains the display name |"
    Spec: "| `current-handle` | Text is exactly the caller's handle, with no `@` and no surrounding words |"
    Spec: "| `auth-error` | Error message. Present only when there is one |"
    """
    world2()
    login_ui(page, "ada@example.com", display="Ada")
    assert text(tid(page, "current-handle")) == "ada"
    expect(tid(page, "auth-error")).to_have_count(0)


def test_ui_login_refused(page):
    """
    Spec: "| `auth-error` | Error message. Present only when there is one |"
    """
    world2()
    page.goto(BASE + "/login")
    expect(tid(page, "auth-error")).to_have_count(0)
    tid(page, "login-email").fill("ada@example.com")
    tid(page, "login-password").fill("wrong password")
    tid(page, "login-submit").click()
    expect(tid(page, "auth-error")).to_be_visible()
    expect(tid(page, "current-user")).to_have_count(0)


def test_ui_signup(page):
    """
    Spec: "| `signup-email`, `signup-password`, `signup-display-name` | Inputs |"
    Spec: "| `signup-submit` | Button |"
    """
    world2()
    page.goto(BASE + "/signup")
    tid(page, "signup-email").fill("Neo.Person@example.org")
    tid(page, "signup-password").fill("long enough pw")
    tid(page, "signup-display-name").fill("Neo Person")
    tid(page, "signup-submit").click()
    expect(tid(page, "current-user")).to_contain_text("Neo Person")
    assert text(tid(page, "current-handle")) == "neo_person"
    r = post("/auth/login", body={"email": "Neo.Person@example.org", "password": "long enough pw"})
    assert r.status_code == 200


def test_ui_signup_refused(page):
    """
    Spec: "| `auth-error` | Error message. Present only when there is one |"
    """
    world2()
    page.goto(BASE + "/signup")
    tid(page, "signup-email").fill("shorty@example.org")
    tid(page, "signup-password").fill("short")
    tid(page, "signup-display-name").fill("Shorty")
    tid(page, "signup-submit").click()
    expect(tid(page, "auth-error")).to_be_visible()
    assert post("/auth/login", body={"email": "shorty@example.org", "password": "short"}).status_code == 401


def test_ui_current_user_on_every_screen(page):
    """
    Spec: "| `current-user` | Visible on every screen when signed in. Text contains the display name |"
    Spec: "| `logout-button` | Button |"
    Spec: "Other screens must be reachable through the UI."
    """
    world2()
    login_ui(page, "bob@example.com", display="Bob")
    for path in ("/", "/requests", "/split", "/authorizations"):
        goto(page, path)
        expect(tid(page, "current-user")).to_contain_text("Bob")
        assert text(tid(page, "current-handle")) == "bob"
        expect(tid(page, "logout-button")).to_be_visible()


# ---------- balance ----------

@pytest.mark.parametrize("currency,units,amount,shown", [("EUR", 2, 10000, "100.00 EUR"), ("JPY", 0, 1200, "1200 JPY"),
                                                         ("BHD", 3, 1234, "1.234 BHD"), ("EUR", 2, 5, "0.05 EUR"),
                                                         ("EUR", 2, 0, "0.00 EUR")])
def test_ui_wallet_balance_format(page, currency, units, amount, shown):
    """
    Spec: "| `wallet-balance` | Text is exactly the formatted amount. Carries `data-amount=\"{minor units}\"` |"
    Spec: "**Formatted amount.** `wallet-balance` is the decimal with exactly `minor_units` decimal places, a single space, then the currency code: `100.00 EUR`."
    Spec: "For a `minor_units` of `0` there is no decimal point at all: `1200 JPY`."
    Spec: "Balances are never negative, so there is no sign."
    """
    world2(balances={"ada": amount, "bob": 0}, currency=currency, minor_units=units)
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", str(amount))
    assert text(tid(page, "wallet-balance")) == shown


# ---------- pay form ----------

@pytest.mark.parametrize("typed,minor", [("15.00", 1500), ("15", 1500), ("15.5", 1550), ("0.01", 1)])
def test_ui_pay_decimal_amounts(page, typed, minor):
    """
    Spec: "| `pay-handle`, `pay-amount`, `pay-note` | Inputs. `pay-amount` is a **decimal** string as a person would type it, e.g. `15.00` |"
    Spec: "| `pay-submit` | Button |"
    Spec: "The form accepts decimal amounts and submits minor units to the API."
    Spec: "With `minor_units: 2`, `15.00` and `15` both submit `1500`; `15.5` submits `1550`."
    Spec: "After any successful action, the balance, the feed and the request lists on the same page must show the new state without a manual reload."
    Spec: "Navigation must wait for the write to succeed before it refreshes the data."
    Spec: "Any mechanism is fine, including a full navigation."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    fill_pay(page, "bob", typed, note="ui pay")
    tid(page, "pay-submit").click()
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", str(10000 - minor))
    assert text(tid(page, "wallet-balance")) == fmt(10000 - minor)
    assert balance(t["ada"]) == 10000 - minor and balance(t["bob"]) == 2500 + minor
    feed = activity(t["ada"])
    assert len(feed) == 1 and feed[0]["amount"] == minor and feed[0]["to_handle"] == "bob"
    expect(tid(page, f"activity-item-{feed[0]['payment_id']}")).to_be_visible()


def test_ui_pay_jpy_whole_units(page):
    """
    Spec: "Nonnumeric input or more than `minor_units` decimal places must show the form's error element without sending a request."
    """
    t = world2(currency="JPY", minor_units=0)
    login_ui(page, "ada@example.com")
    goto(page, "/")
    watch = PostWatch(page)
    fill_pay(page, "bob", "12.5")
    tid(page, "pay-submit").click()
    expect(tid(page, "pay-error")).to_be_visible()
    page.wait_for_timeout(500)
    assert watch.posts == []
    fill_pay(page, "bob", "1200")
    tid(page, "pay-submit").click()
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "8800")
    assert text(tid(page, "wallet-balance")) == "8800 JPY"
    assert balance(t["bob"]) == 3700


@pytest.mark.parametrize("typed", ["abc", "15.005", "1.2.3", "ten"])
def test_ui_pay_rejects_bad_amount_without_request(page, typed):
    """
    Spec: "Nonnumeric input or more than `minor_units` decimal places must show the form's error element without sending a request."
    Spec: "For example, `15.005` is rejected rather than rounded."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    watch = PostWatch(page)
    fill_pay(page, "bob", typed)
    tid(page, "pay-submit").click()
    expect(tid(page, "pay-error")).to_be_visible()
    page.wait_for_timeout(500)
    assert watch.posts == [], [p.url for p in watch.posts]
    assert balance(t["ada"]) == 10000 and activity(t["ada"]) == []


def test_ui_pay_resubmit_is_not_a_new_payment(page):
    """
    Spec: "Keep the pay form's values after success."
    Spec: "Submitting it again without changing a field must not send another payment: `wallet-balance` falls once, the feed contains one payment and `pay-error` is absent."
    Spec: "Changing a field makes the next submission a new payment request."
    Spec: "Retries follow §7."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    fill_pay(page, "bob", "12.34", note="once only", visibility="public")
    tid(page, "pay-submit").click()
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "8766")
    expect(tid(page, "pay-handle")).to_have_value("bob")
    expect(tid(page, "pay-amount")).to_have_value("12.34")
    expect(tid(page, "pay-note")).to_have_value("once only")
    tid(page, "pay-submit").click()
    page.wait_for_timeout(1500)
    expect(tid(page, "pay-error")).to_have_count(0)
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "8766")
    assert balance(t["ada"]) == 8766 and len(activity(t["ada"])) == 1
    expect(page.locator('[data-testid^="activity-item-"]')).to_have_count(1)
    tid(page, "pay-note").fill("second one")
    tid(page, "pay-submit").click()
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "7532")
    assert balance(t["ada"]) == 7532 and len(activity(t["ada"])) == 2


def test_ui_pay_visibility_select(page):
    """
    Spec: "| `pay-visibility` | Selects `public` or `private`. Option values are those two strings |"
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    values = tid(page, "pay-visibility").locator("option").evaluate_all("els => els.map(e => e.value)")
    assert sorted(values) == ["private", "public"]
    fill_pay(page, "bob", "1.00", note="secret", visibility="private")
    tid(page, "pay-submit").click()
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "9900")
    feed = activity(t["ada"])
    assert feed[0]["visibility"] == "private"
    expect(tid(page, f"activity-item-{feed[0]['payment_id']}")).to_have_attribute("data-visibility", "private")


def test_ui_pay_refused_shows_error(page):
    """
    Spec: "| `pay-error` | Error message, when the payment is refused — including insufficient funds |"
    Spec: "Another client may spend the balance after this browser reads it."
    Spec: "A refused payment shows `pay-error`, refreshes the balance/feed, and preserves all pay inputs."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "10000")
    fill_pay(page, "bob", "100.00", note="all of it", visibility="private")
    other = pay(t["ada"], "cy", 1, note="elsewhere")
    assert other.status_code == 201
    tid(page, "pay-submit").click()
    expect(tid(page, "pay-error")).to_be_visible()
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "9999")
    expect(tid(page, f"activity-item-{other.json()['payment_id']}")).to_be_visible()
    expect(tid(page, "pay-handle")).to_have_value("bob")
    expect(tid(page, "pay-amount")).to_have_value("100.00")
    expect(tid(page, "pay-note")).to_have_value("all of it")
    expect(tid(page, "pay-visibility")).to_have_value("private")
    assert balance(t["bob"]) == 2500


def test_ui_pay_unknown_handle_error(page):
    """
    Spec: "| `pay-error` | Error message, when the payment is refused — including insufficient funds |"
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    fill_pay(page, "nobody_here", "1.00")
    tid(page, "pay-submit").click()
    expect(tid(page, "pay-error")).to_be_visible()
    assert balance(t["ada"]) == 10000


# ---------- request form ----------

def test_ui_request_form(page):
    """
    Spec: "| `request-handle`, `request-amount`, `request-note`, `request-submit` | The request form |"
    Spec: "| `request-error` | Error message, when the request is refused |"
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    tid(page, "request-handle").fill("bob")
    tid(page, "request-amount").fill("7.50")
    tid(page, "request-note").fill("lunch")
    tid(page, "request-submit").click()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not requests_of(t["bob"]):
        page.wait_for_timeout(200)
    reqs = requests_of(t["bob"])
    assert len(reqs) == 1
    assert (reqs[0]["payer_handle"], reqs[0]["requester_handle"], reqs[0]["note"]) == ("bob", "ada", "lunch")
    expect(tid(page, "request-error")).to_have_count(0)
    tid(page, "request-handle").fill("ada")
    tid(page, "request-amount").fill("1.00")
    tid(page, "request-submit").click()
    expect(tid(page, "request-error")).to_be_visible()
    assert len(requests_of(t["ada"])) == 1


# ---------- activity feed ----------

def test_ui_activity_feed(page):
    """
    Spec: "| `activity-list` | Container. Its children are newest first in the DOM |"
    Spec: "| `activity-item-{payment_id}` | One per visible payment. Carries `data-visibility=\"public\"` or `data-visibility=\"private\"` |"
    Spec: "| `activity-parties-{payment_id}` | Text contains both handles |"
    Spec: "| `activity-amount-{payment_id}` | Text is exactly the formatted amount |"
    Spec: "| `activity-note-{payment_id}` | Text is exactly the note. Present even when the note is empty |"
    Spec: "Two payments with equal timestamps may appear in either order."
    """
    t = world2()
    p1 = pay(t["dan"], "bob", 1234, note="Dinner 🍕 & <b>drinks</b>").json()
    time.sleep(1.1)
    p2 = pay(t["ada"], "cy", 5, note="", visibility="private").json()
    time.sleep(1.1)
    p3 = pay(t["bob"], "ada", 100000 // 100, note="back", visibility="public").json()
    hidden = pay(t["dan"], "cy", 1, visibility="private").json()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, f"activity-item-{p3['payment_id']}")).to_be_visible()
    order = items(page, "activity-list", "activity-item-")
    assert order == [f"activity-item-{p['payment_id']}" for p in (p3, p2, p1)], order
    expect(tid(page, f"activity-item-{hidden['payment_id']}")).to_have_count(0)
    expect(tid(page, "empty-activity")).to_have_count(0)
    for p in (p1, p2, p3):
        pid = p["payment_id"]
        expect(tid(page, f"activity-item-{pid}")).to_have_attribute("data-visibility", p["visibility"])
        parties = text(tid(page, f"activity-parties-{pid}"))
        assert p["from_handle"] in parties and p["to_handle"] in parties
        assert text(tid(page, f"activity-amount-{pid}")) == fmt(p["amount"])
        expect(tid(page, f"activity-note-{pid}")).to_have_count(1)
        assert (tid(page, f"activity-note-{pid}").text_content() or "").strip() == p["note"]


def test_ui_empty_activity(page):
    """
    Spec: "| `empty-activity` | Shown instead of the list when nothing is visible |"
    """
    t = world2()
    pay(t["dan"], "bob", 1, visibility="private")
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, "empty-activity")).to_be_visible()
    expect(page.locator('[data-testid^="activity-item-"]')).to_have_count(0)


# ---------- refresh ----------

def test_ui_wallet_refresh_keeps_form(page):
    """
    Spec: "Add `wallet-refresh`, a button on `/` that refreshes the balance and feed without clearing the pay form."
    Spec: "**There is no live-update requirement here** — another client may change state, but this browser need only refresh after its own action or an explicit refresh."
    Spec: "No background polling, live synchronization, or recovery across page reloads is required."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "10000")
    fill_pay(page, "dan", "3.21", note="keep me", visibility="private")
    p = pay(t["bob"], "ada", 500, note="external").json()
    tid(page, "wallet-refresh").click()
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "10500")
    expect(tid(page, f"activity-item-{p['payment_id']}")).to_be_visible()
    expect(tid(page, "pay-handle")).to_have_value("dan")
    expect(tid(page, "pay-amount")).to_have_value("3.21")
    expect(tid(page, "pay-note")).to_have_value("keep me")
    expect(tid(page, "pay-visibility")).to_have_value("private")


def test_ui_latest_refresh_wins(page):
    """
    Spec: "**Latest refresh wins:** a delayed earlier read must not overwrite a later refresh, including when responses arrive out of order."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "10000")
    state = {"hold": False}
    held = []

    def handler(route):
        req = route.request
        if state["hold"] and req.method == "GET" and req.resource_type in ("fetch", "xhr", "document"):
            held.append((route, route.fetch()))
            return
        route.continue_()

    page.route("**/*", handler)
    state["hold"] = True
    tid(page, "wallet-refresh").click()
    deadline = time.monotonic() + 10
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert held, "the refresh sent no read"
    page.wait_for_timeout(300)
    state["hold"] = False
    assert pay(t["ada"], "bob", 100).status_code == 201
    tid(page, "wallet-refresh").click()
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "9900")
    for route, response in held:
        try:
            route.fulfill(response=response)
        except Exception:
            pass
    page.wait_for_timeout(2000)
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "9900")
    assert text(tid(page, "wallet-balance")) == "99.00 EUR"
    page.unroute("**/*")


# ---------- uncertain outcome ----------

def test_ui_lost_payment_response_is_uncertain_then_retry(page):
    """
    Spec: "If a payment response is lost, including after `POST /payments` commits, show `pay-uncertain` (nonempty text), not `pay-error`."
    Spec: "Keep the unchanged form retryable with the **same key and body**."
    Spec: "Successful retry removes both error/uncertainty elements, refreshes the balance and feed, and moves money exactly once."
    Spec: "Unknown outcomes are not confirmed rejections."
    """
    t = world2()
    login_ui(page, "ada@example.com")
    goto(page, "/")
    lost = lose_next_post(page)
    watch = PostWatch(page)
    fill_pay(page, "bob", "15.00", note="uncertain")
    tid(page, "pay-submit").click()
    expect(tid(page, "pay-uncertain")).to_be_visible()
    assert text(tid(page, "pay-uncertain")) != ""
    expect(tid(page, "pay-error")).to_have_count(0)
    assert lost and balance(t["ada"]) == 8500
    tid(page, "pay-submit").click()
    expect(tid(page, "pay-uncertain")).to_have_count(0)
    expect(tid(page, "pay-error")).to_have_count(0)
    expect(tid(page, "wallet-balance")).to_have_attribute("data-amount", "8500")
    assert balance(t["ada"]) == 8500 and balance(t["bob"]) == 4000
    feed = activity(t["ada"])
    assert len(feed) == 1
    expect(tid(page, f"activity-item-{feed[0]['payment_id']}")).to_be_visible()
    first = lost[0]
    if first.url.split("?")[0].endswith("/payments") and first.headers.get("idempotency-key"):
        retries = [p for p in watch.posts if p.url.split("?")[0].endswith("/payments")]
        assert retries and retries[-1].headers.get("idempotency-key") == first.headers.get("idempotency-key")
        assert retries[-1].post_data_json == first.post_data_json


# ---------- layout ----------

@pytest.mark.parametrize("width", [375, 1280])
def test_ui_no_horizontal_scroll(browser, width):
    """
    Spec: "The required flows must remain clear and usable at a 375 CSS-pixel viewport and at conventional desktop widths, without horizontal page scrolling."
    """
    t = world2(authorizations=None)
    pay(t["ada"], "bob", 1234, note="a fairly long note that should wrap rather than overflow the page width")
    post("/requests", t["bob"], {"payer_handle": "ada", "amount": 99, "note": "x"}, key())
    ctx = browser.new_context(viewport={"width": width, "height": 800})
    page = ctx.new_page()
    try:
        login_ui(page, "ada@example.com")
        for path in ("/", "/requests", "/split", "/authorizations"):
            goto(page, path)
            page.wait_for_timeout(300)
            sw = page.evaluate("document.documentElement.scrollWidth")
            assert sw <= width + 1, (path, sw)
        page.goto(BASE + "/login")
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
    finally:
        ctx.close()
