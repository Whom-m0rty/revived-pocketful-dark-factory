"""Browser helpers for the stage-2 UI scenarios (Playwright, sync API)."""
from decimal import Decimal

from playwright.sync_api import expect

from pf import BASE, PASSWORD

expect.set_options(timeout=10000)


def tid(page, name):
    return page.get_by_test_id(name)


def login_ui(page, email, password=PASSWORD, display=None):
    page.goto(BASE + "/login")
    tid(page, "login-email").fill(email)
    tid(page, "login-password").fill(password)
    tid(page, "login-submit").click()
    expect(tid(page, "current-user")).to_be_visible()
    if display:
        expect(tid(page, "current-user")).to_contain_text(display)


def goto(page, path):
    page.goto(BASE + path)
    expect(tid(page, "current-user")).to_be_visible()


def text(locator):
    return (locator.text_content() or "").strip()


def decimal_value(value):
    return Decimal(value.strip())


class PostWatch:
    """Records every POST the page sends."""

    def __init__(self, page):
        self.posts = []
        page.on("request", lambda r: self.posts.append(r) if r.method == "POST" else None)


def fill_pay(page, handle, amount, note=None, visibility=None):
    tid(page, "pay-handle").fill(handle)
    tid(page, "pay-amount").fill(amount)
    if note is not None:
        tid(page, "pay-note").fill(note)
    if visibility is not None:
        tid(page, "pay-visibility").select_option(visibility)


def items(page, container, prefix):
    """data-testid values of the items inside container, in DOM order."""
    loc = page.locator(f'[data-testid="{container}"] [data-testid^="{prefix}"]')
    return [loc.nth(i).get_attribute("data-testid") for i in range(loc.count())]


def lose_next_post(page):
    """Let the next POST commit on the server, then drop its response before the page sees it."""
    lost = []

    def handler(route):
        if route.request.method == "POST" and not lost:
            route.fetch()
            lost.append(route.request)
            route.abort("connectionreset")
            return
        route.continue_()

    page.route("**/*", handler)
    return lost
