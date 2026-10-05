"""Drive interactive states on the running service, screenshot at 375/1280 and run the layout probe on each."""
import sys, json, pathlib, importlib.util
from playwright.sync_api import sync_playwright
base, token, out = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("lc", str(pathlib.Path(__file__).parents[2] / "tools/layout_check.py")); lc = importlib.util.module_from_spec(spec); spec.loader.exec_module(lc)
def pay_error(p):
    p.goto(base + "/"); p.wait_for_selector('[data-testid=pay-handle]'); p.fill('[data-testid=pay-handle]', 'bob'); p.fill('[data-testid=pay-amount]', '95.00'); p.fill('[data-testid=pay-note]', 'Too much'); p.click('[data-testid=pay-submit]'); p.wait_for_selector('[data-testid=pay-error]')
def pay_invalid(p):
    p.goto(base + "/"); p.wait_for_selector('[data-testid=pay-handle]'); p.fill('[data-testid=pay-handle]', 'bob'); p.fill('[data-testid=pay-amount]', '15.005'); p.click('[data-testid=pay-submit]'); p.wait_for_selector('[data-testid=pay-error]')
def pay_uncertain(p):
    p.goto(base + "/"); p.wait_for_selector('[data-testid=pay-handle]')
    p.route("**/payments", lambda r: r.abort()); p.fill('[data-testid=pay-handle]', 'bob'); p.fill('[data-testid=pay-amount]', '1.00'); p.click('[data-testid=pay-submit]'); p.wait_for_selector('[data-testid=pay-uncertain]')
def request_error(p):
    p.goto(base + "/"); p.wait_for_selector('[data-testid=request-handle]'); p.fill('[data-testid=request-handle]', 'nobody'); p.fill('[data-testid=request-amount]', '5'); p.click('[data-testid=request-submit]'); p.wait_for_selector('[data-testid=request-error]')
def split_preview(p):
    p.goto(base + "/split"); p.wait_for_selector('[data-testid=split-amount]'); p.fill('[data-testid=split-amount]', '60.01'); p.fill('[data-testid=split-handles]', 'ada, bob, cleo'); p.fill('[data-testid=split-note]', 'Dinner'); p.wait_for_selector('[data-testid=split-share-bob]')
def split_error(p):
    split_preview(p); p.fill('[data-testid=split-handles]', 'ada, bob, zed'); p.click('[data-testid=split-submit]'); p.wait_for_selector('[data-testid=split-error]')
def authorize_error(p):
    p.goto(base + "/authorizations"); p.wait_for_selector('[data-testid=authorize-handle]'); p.fill('[data-testid=authorize-handle]', 'bob'); p.fill('[data-testid=authorize-amount]', '500'); p.click('[data-testid=authorize-submit]'); p.wait_for_selector('[data-testid=authorize-error]')
def capture_error(p):
    p.goto(base + "/authorizations"); p.wait_for_selector('[data-testid=authorization-capture-amount-a_2]'); p.fill('[data-testid=authorization-capture-amount-a_2]', '99'); p.click('[data-testid=authorization-capture-a_2]'); p.wait_for_selector('[data-testid=authorization-error]')
def loading(p):
    p.route("**/me", lambda r: None)  # never answers
    p.goto(base + "/"); p.wait_for_timeout(400)
STATES = [pay_error, pay_invalid, pay_uncertain, request_error, split_preview, split_error, authorize_error, capture_error, loading]
findings = []
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (375, 1280):
        for fn in STATES:
            ctx = br.new_context(viewport={"width": w, "height": 900}); ctx.add_init_script(f"localStorage.setItem('pocketful_token', {json.dumps(token)})")
            p = ctx.new_page()
            try:
                fn(p); p.evaluate("window.scrollTo(0, 0)"); p.wait_for_timeout(250)
                for f in p.evaluate(lc.PROBE): f.update(state=fn.__name__, width=w); findings.append(f)
            except Exception as e:
                findings.append({"kind": "state-failed", "state": fn.__name__, "width": w, "detail": str(e)[:200]})
            p.screenshot(path=str(out / f"{fn.__name__}-{w}.png"), full_page=True); ctx.close()
    br.close()
for f in findings: print(f["kind"], f["state"], f["width"], f.get("el", ""), f.get("other", ""), f["detail"])
print("STATES:", "clean" if not findings else f"{len(findings)} findings")
