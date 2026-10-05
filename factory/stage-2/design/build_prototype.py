"""Generate static HTML prototypes of every screen/state using the system.md class vocabulary.
These are the source for the SVG mockups and a markup reference for the builder."""
import pathlib

OUT = pathlib.Path(__file__).parent / "prototype"
OUT.mkdir(exist_ok=True)

def ic(name, cls="icon"):
    return f'<svg class="{cls}" aria-hidden="true"><use href="/static/icons.svg#{name}"/></svg>'

def head(title):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Pocketful</title><link rel="icon" href="/static/mark.svg"><link rel="stylesheet" href="/static/app.css"></head>'''

NAV = [("/", "i-wallet", "Wallet"), ("/requests", "i-request", "Requests"), ("/split", "i-split", "Split"), ("/authorizations", "i-hold", "Holds")]

def shell(active, title, sub, body, actions=""):
    nav = "".join(f'<a class="nav-link" href="{h}"{" aria-current=\"page\"" if h == active else ""}>{ic(i)}<span>{l}</span></a>' for h, i, l in NAV)
    return head(title) + f'''<body>
<header class="topbar"><div class="topbar-inner">
<a class="brand" href="/"><img class="brand-logo" src="/static/logo.svg" alt="Pocketful"></a>
<nav class="nav" aria-label="Main">{nav}</nav>
<div class="user"><span class="avatar" aria-hidden="true">A</span><span class="user-text"><span class="user-name" data-testid="current-user">Ada Lovelace</span><span class="user-handle">@<span data-testid="current-handle">ada</span></span></span>
<button class="btn btn-quiet btn-icon" data-testid="logout-button" aria-label="Log out">{ic("i-logout")}<span class="btn-label">Log out</span></button></div>
</div></header>
<main class="page">
<div class="page-header"><div><h1 class="page-title">{title}</h1><p class="page-sub">{sub}</p></div>{f'<div class="page-actions">{actions}</div>' if actions else ''}</div>
{body}
</main></body></html>'''

def banner(kind, testid, icon, html):
    t = f' data-testid="{testid}"' if testid else ""
    role = ' role="alert"' if kind in ("danger", "warning") else ' role="status"'
    return f'<div class="banner banner-{kind}"{role}{t}>{ic(icon)}<p>{html}</p></div>'

def field(label, tid, value="", affix_pre=None, affix_post=None, opt=False, hint=None, amount=False, placeholder=""):
    cls = "input input-amount num" if amount else "input"
    im = ' inputmode="decimal"' if amount else ""
    inp = f'<input class="{cls}" id="{tid}" data-testid="{tid}" value="{value}" placeholder="{placeholder}" autocomplete="off"{im}>'
    if affix_pre: inp = f'<div class="input-affix"><span class="affix">{affix_pre}</span>{inp}</div>'
    elif affix_post: inp = f'<div class="input-affix">{inp}<span class="affix">{affix_post}</span></div>'
    lab = f'{label} <span class="label-opt">(optional)</span>' if opt else label
    h = f'<p class="hint">{hint}</p>' if hint else ""
    return f'<div class="field"><label class="label" for="{tid}">{lab}</label>{inp}{h}</div>'

def visibility(tid, val="private"):
    o = "".join(f'<option value="{v}"{" selected" if v == val else ""}>{l}</option>' for v, l in (("private", "Private"), ("public", "Public")))
    return f'<div class="field"><label class="label" for="{tid}">Who can see it</label><select class="select" id="{tid}" data-testid="{tid}">{o}</select></div>'

# ---------- wallet ----------
def balance(available, total, held, loading=False):
    if loading:
        return f'''<section class="card balance" aria-busy="true"><div class="balance-main"><span class="stat-label">Available to spend</span><span class="skeleton skeleton-hero"></span></div>
<dl class="balance-meta"><div class="stat"><dt class="stat-label">Total</dt><dd><span class="skeleton skeleton-line" style="width:110px"></span></dd></div></dl>
<button class="btn btn-quiet btn-sm balance-refresh" data-testid="wallet-refresh" aria-busy="true" disabled><span>Refreshing</span></button></section>'''
    held_html = f'<div class="stat stat-held"><dt class="stat-label">{ic("i-lock")}On hold</dt><dd class="stat-value num" data-testid="wallet-held" data-amount="{held[0]}">{held[1]}</dd></div>' if held else ""
    note = f'<p class="balance-note">{held[1]} is reserved for 2 open holds. <a href="/authorizations">View holds</a></p>' if held else ""
    return f'''<section class="card balance"><div class="balance-main"><span class="stat-label">Available to spend</span>
<span class="stat-hero num" data-testid="wallet-available" data-amount="{available[0]}">{available[1]}</span></div>
<dl class="balance-meta"><div class="stat"><dt class="stat-label">Total balance</dt><dd class="stat-value num" data-testid="wallet-balance" data-amount="{total[0]}">{total[1]}</dd></div>{held_html}</dl>
<button class="btn btn-quiet btn-sm btn-icon balance-refresh" data-testid="wallet-refresh">{ic("i-refresh")}<span>Refresh</span></button>{note}</section>'''

def pay_card(state, values=("bob", "15.00", "Lunch at Rosa’s"), visibility_val="private"):
    h, a, n = values
    fb = ""
    btn = '<button class="btn btn-primary btn-block" data-testid="pay-submit">Send money</button>'
    if state == "success": fb = banner("success", None, "i-check", "<strong>Sent 15.00 EUR to @bob.</strong> Your balance is up to date.")
    if state == "error": fb = banner("danger", "pay-error", "i-alert", "<strong>Payment refused — not enough available funds.</strong> You have 12.40 EUR available; 20.00 EUR is on hold. Nothing was sent.")
    if state == "uncertain": fb = banner("warning", "pay-uncertain", "i-clock", "<strong>We couldn’t confirm this payment.</strong> It may or may not have gone through. Press Send again — retrying is safe and won’t pay twice.")
    if state == "pending": btn = '<button class="btn btn-primary btn-block" data-testid="pay-submit" aria-busy="true" disabled>Sending…</button>'
    return f'''<section class="card"><div class="card-header"><div class="card-heading">{ic("i-send")}<h2 class="card-title">Send money</h2></div></div>
<form class="form">{field("To", "pay-handle", h, affix_pre="@", placeholder="handle")}
<div class="field-row">{field("Amount", "pay-amount", a, affix_post="EUR", amount=True, placeholder="0.00")}{visibility("pay-visibility", visibility_val)}</div>
{field("Note", "pay-note", n, opt=True, placeholder="What’s it for?")}
<div class="form-actions">{fb}{btn}</div></form></section>'''

def request_card(state):
    fb = banner("danger", "request-error", "i-alert", "<strong>Request refused.</strong> No one has the handle @bobby.") if state == "error" else ""
    return f'''<section class="card"><div class="card-header"><div class="card-heading">{ic("i-request")}<h2 class="card-title">Request money</h2></div></div>
<form class="form">{field("From", "request-handle", "bobby" if state=="error" else "", affix_pre="@", placeholder="handle")}
{field("Amount", "request-amount", "", affix_post="EUR", amount=True, placeholder="0.00")}
{field("Note", "request-note", "", opt=True, placeholder="Concert tickets")}
<div class="form-actions">{fb}<button class="btn btn-secondary btn-block" data-testid="request-submit">Send request</button></div></form></section>'''

FEED = [
    ("p_9", "out", "ada", "bob", "15.00 EUR", "Lunch at Rosa’s", "Today, 13:10", "private"),
    ("p_8", "in", "cleo", "ada", "42.50 EUR", "Concert tickets", "Today, 09:02", "public"),
    ("p_7", "in", "bob", "ada", "18.00 EUR", "Hold capture · deposit", "Yesterday, 18:45", "private"),
    ("p_6", "other", "bob", "cleo", "7.20 EUR", "", "Mon 21 Sep, 08:15", "public"),
    ("p_5", "out", "ada", "dan", "120.00 EUR", "Rent share", "Sun 20 Sep, 19:30", "private"),
]

def feed_rows(items):
    rows = []
    for pid, d, f, t, amt, note, when, vis in items:
        icon = {"in": ("row-icon-in", "i-arrow-down"), "out": ("row-icon-out", "i-arrow-up"), "other": ("row-icon-out", "i-globe")}[d]
        title = {"in": f"{f} paid you", "out": f"You paid {t}", "other": f"{f} paid {t}"}[d]
        # parties text must contain both handles
        parties = {"in": f"{f} → {t}", "out": f"{f} → {t}", "other": f"{f} → {t}"}[d]
        badge = f'<span class="badge badge-muted">{ic("i-lock")}Private</span>' if vis == "private" else f'<span class="badge badge-public">{ic("i-globe")}Public</span>'
        rows.append(f'''<li class="list-row" data-testid="activity-item-{pid}" data-visibility="{vis}"><span class="row-icon {icon[0]}">{ic(icon[1])}</span>
<div class="row-main"><p class="row-title">{title}</p><p class="row-sub"><span data-testid="activity-parties-{pid}">{parties}</span><span class="dot">·</span><time class="num">{when}</time></p>
<p class="row-sub"><span data-testid="activity-note-{pid}">{note}</span></p></div>
<div class="row-end"><span class="amount amount-{"in" if d=="in" else "out"} num" data-testid="activity-amount-{pid}">{amt}</span>{badge}</div></li>''')
    return "".join(rows)

def activity_card(state):
    if state == "empty":
        body = '<div class="empty" data-testid="empty-activity"><img class="empty-art" src="/static/empty-activity.svg" alt=""><p class="empty-title">No activity yet</p><p class="empty-text">Payments you send or receive, and public payments between others, show up here.</p></div>'
    elif state == "loading":
        sk = '<div class="skeleton-row"><span class="skeleton skeleton-circle"></span><div><span class="skeleton skeleton-line"></span><span class="skeleton skeleton-line short"></span></div></div>'
        body = f'<div aria-busy="true" aria-label="Loading activity">{sk*4}</div>'
    else:
        items = FEED if state != "success" else FEED
        body = f'<ul class="list" data-testid="activity-list">{feed_rows(items)}</ul>'
    return f'''<section class="card"><div class="card-header"><div class="card-heading">{ic("i-clock")}<h2 class="card-title">Activity</h2></div><span class="card-sub">Newest first</span></div>
<div class="card-body">{body}</div></section>'''

def wallet(state):
    if state == "empty":
        bal = balance(("10000", "100.00 EUR"), ("10000", "100.00 EUR"), None)
        pay = pay_card("idle", ("", "", "")); feed = "empty"
    elif state == "loading":
        bal = balance(None, None, None, loading=True); pay = pay_card("idle", ("", "", "")); feed = "loading"
    else:
        held = ("2000", "20.00 EUR")
        bal = balance(("6740", "67.40 EUR"), ("8740", "87.40 EUR"), held) if state != "error" else balance(("1240", "12.40 EUR"), ("3240", "32.40 EUR"), held)
        pay = pay_card({"default": "idle", "success": "success", "error": "error", "uncertain": "uncertain", "pending": "pending"}[state],
                       ("bob", "25.00", "Lunch at Rosa’s") if state == "error" else ("bob", "15.00", "Lunch at Rosa’s"))
        feed = "default"
    body = f'{bal}<div class="grid-wallet"><div class="stack">{pay}{request_card("error" if state=="error" else "idle")}</div>{activity_card(feed)}</div>'
    return shell("/", "Wallet", "Good afternoon, Ada.", body)

# ---------- requests ----------
def req_row(rid, incoming, who, amt, note, when, status):
    badge = {"pending": '<span class="badge badge-pending">Pending</span>', "paid": f'<span class="badge badge-success">{ic("i-check")}Paid</span>',
             "declined": '<span class="badge badge-muted">Declined</span>', "cancelled": '<span class="badge badge-muted">Cancelled</span>'}[status]
    title = f"{who} requests" if incoming else f"You asked {who}"
    acts = ""
    if status == "pending" and incoming:
        acts = f'<div class="row-actions"><button class="btn btn-primary btn-sm" data-testid="request-pay-{rid}">Pay {amt}</button><button class="btn btn-danger-quiet btn-sm" data-testid="request-decline-{rid}">Decline</button></div>'
    elif status == "pending":
        acts = f'<div class="row-actions"><button class="btn btn-danger-quiet btn-sm" data-testid="request-cancel-{rid}">Cancel request</button></div>'
    icon = "row-icon-pending" if status == "pending" else "row-icon-out"
    return f'''<li class="list-row" data-testid="request-item-{rid}" data-status="{status}"><span class="row-icon {icon}">{ic("i-inbox" if incoming else "i-outbox")}</span>
<div class="row-main"><p class="row-title">{title}</p><p class="row-sub"><span>{note}</span><span class="dot">·</span><time class="num">{when}</time></p></div>
<div class="row-end"><span class="amount num" data-testid="request-amount-{rid}">{amt}</span>{badge}</div>{acts}</li>'''

def requests(state):
    if state == "empty":
        body = '<section class="card"><div class="empty" data-testid="empty-requests"><img class="empty-art" src="/static/empty-requests.svg" alt=""><p class="empty-title">No requests yet</p><p class="empty-text">Ask a friend to pay you back from your wallet, and requests sent to you will wait here.</p><a class="btn btn-secondary" href="/">Request money</a></div></section>'
        return shell("/requests", "Requests", "Money you’ve asked for, and money asked of you.", body)
    err = banner("danger", "request-error", "i-alert", "<strong>Couldn’t pay this request — it was cancelled by @cleo.</strong> The list has been refreshed.") if state == "error" else ""
    inc = [("r_4", True, "@bob", "12.00 EUR", "Taxi home", "Today, 11:20", "pending"), ("r_2", True, "@dan", "30.00 EUR", "Groceries", "Fri 18 Sep", "paid")]
    if state != "error": inc.insert(1, ("r_3", True, "@cleo", "8.50 EUR", "Coffee beans", "Yesterday", "pending"))
    else: inc.insert(1, ("r_3", True, "@cleo", "8.50 EUR", "Coffee beans", "Yesterday", "cancelled"))
    out = [("r_5", False, "@cleo", "42.50 EUR", "Concert tickets", "Today, 08:40", "pending"), ("r_1", False, "@bob", "6.00 EUR", "Parking", "Wed 16 Sep", "declined")]
    def card(title, icon, tid, rows):
        return f'''<section class="card"><div class="card-header"><div class="card-heading">{ic(icon)}<h2 class="card-title">{title} <span class="count">· {len(rows)}</span></h2></div></div>
<ul class="list" data-testid="{tid}">{"".join(req_row(*r) for r in rows)}</ul></section>'''
    body = err + f'<div class="grid-2">{card("Incoming", "i-inbox", "incoming-list", inc)}{card("Outgoing", "i-outbox", "outgoing-list", out)}</div>'
    return shell("/requests", "Requests", "Money you’ve asked for, and money asked of you.", body)

# ---------- split ----------
def split(state):
    vals = {"preview": ("60.01", "ada, bob, cleo", "Dinner at Nori"), "empty": ("", "", ""), "error": ("60.01", "ada, bob, zed", "Dinner at Nori")}[state]
    err = banner("danger", "split-error", "i-alert", "<strong>Split refused.</strong> No one has the handle @zed.") if state == "error" else ""
    form = f'''<section class="card"><div class="card-header"><div class="card-heading">{ic("i-split")}<h2 class="card-title">Bill details</h2></div></div>
<form class="form">{field("Total amount", "split-amount", vals[0], affix_post="EUR", amount=True, placeholder="0.00")}
{field("People", "split-handles", vals[1], hint="Handles separated by commas, in order. Include yourself to take a share.", placeholder="ada, bob, cleo")}
{field("Note", "split-note", vals[2], opt=True, placeholder="What was it?")}
<div class="form-actions">{err}<button class="btn btn-primary btn-block" data-testid="split-submit">Request shares</button></div></form></section>'''
    if state == "empty":
        prev = '<div class="preview" data-testid="split-preview"><div class="empty"><img class="empty-art" src="/static/empty-split.svg" alt=""><p class="empty-title">Shares appear here</p><p class="empty-text">Enter an amount and at least one handle to see who pays what.</p></div></div>'
    else:
        shares = [("ada", "20.01 EUR", True), ("bob", "20.00 EUR", False), ("cleo" if state == "preview" else "zed", "20.00 EUR", False)]
        li = "".join(f'<li class="share"><span class="avatar avatar-sm" aria-hidden="true">{h[0]}</span><span class="share-name">@{h}{" <span class=\"share-you\">(you)</span>" if you else ""}</span><span class="share-amount num" data-testid="split-share-{h}">{a}</span></li>' for h, a, you in shares)
        prev = f'<div class="preview" data-testid="split-preview"><ul class="share-list">{li}</ul><div class="preview-total"><span>Total</span><span class="num">60.01 EUR</span></div><p class="hint">The extra cent goes to the first person listed, exactly as the server will compute it.</p></div>'
    pcard = f'<section class="card"><div class="card-header"><div class="card-heading">{ic("i-user")}<h2 class="card-title">Preview</h2></div><span class="card-sub">Before anything is sent</span></div>{prev}</section>'
    return shell("/split", "Split a bill", "Share a cost fairly — everyone gets a request for their part.", f'<div class="grid-2">{form}{pcard}</div>')

# ---------- authorizations ----------
def auth_row(aid, incoming, who, amt, captured, status, note, expires, friendly, remaining=None, pct=0):
    badge = {"open": f'<span class="badge badge-hold">{ic("i-lock")}On hold</span>', "captured": f'<span class="badge badge-success">{ic("i-check")}Captured</span>',
             "voided": '<span class="badge badge-muted">Voided</span>', "expired": '<span class="badge badge-muted">Expired</span>'}[status]
    title = f"{who} reserved for you" if incoming else f"You reserved for {who}"
    cap = f'<span class="hint">Captured <span class="num" data-testid="authorization-captured-{aid}">{captured}</span></span>' if status == "captured" else ""
    meta = f'<p class="hold-meta">{ic("i-clock")}<span>{"Expires" if status=="open" else "Expiry"} {friendly}</span><span class="dot">·</span><time class="num" data-testid="authorization-expires-{aid}">{expires}</time></p>'
    prog = f'<div class="progress" aria-hidden="true"><span style="width:{pct}%"></span></div>' if status == "open" and pct else ""
    acts = ""
    if status == "open" and incoming:
        acts = f'''<div class="row-actions capture"><label class="capture-label" for="cap-{aid}">Amount to collect</label><div class="input-affix"><input class="input num" id="cap-{aid}" inputmode="decimal" data-testid="authorization-capture-amount-{aid}" value="{remaining}"><span class="affix">EUR</span></div><button class="btn btn-primary btn-sm" data-testid="authorization-capture-{aid}">Capture</button></div>'''
    elif status == "open":
        acts = f'<div class="row-actions"><button class="btn btn-danger-quiet btn-sm" data-testid="authorization-void-{aid}">Release hold</button></div>'
    icon = "row-icon-hold" if status == "open" else ("row-icon-in" if status == "captured" else "row-icon-out")
    return f'''<li class="list-row" data-testid="authorization-item-{aid}" data-status="{status}"><span class="row-icon {icon}">{ic("i-hold")}</span>
<div class="row-main"><p class="row-title">{title}</p><p class="row-sub"><span>{note}</span></p>{meta}{prog}</div>
<div class="row-end"><span class="amount amount-hold num" data-testid="authorization-amount-{aid}">{amt}</span>{badge}{cap}</div>{acts}</li>'''

def authorizations(state):
    aerr = banner("danger", "authorize-error", "i-alert", "<strong>Hold refused — not enough available funds.</strong> You have 67.40 EUR available to reserve.") if state == "error" else ""
    form = f'''<section class="card"><div class="card-header"><div class="card-heading">{ic("i-hold")}<h2 class="card-title">Reserve money</h2></div></div>
<p class="card-sub">Holds keep money aside for someone to collect later. Nothing moves until they capture it; anything not collected comes back to you.</p>
<form class="form form-wide"><div class="field-row">{field("For", "authorize-handle", "bob" if state=="error" else "", affix_pre="@", placeholder="handle")}{field("Amount", "authorize-amount", "500.00" if state=="error" else "", affix_post="EUR", amount=True, placeholder="0.00")}</div>
<div class="field-row">{visibility("authorize-visibility")}{field("Note", "authorize-note", "Bike deposit" if state=="error" else "", opt=True, placeholder="Deposit")}</div>
<div class="form-actions">{aerr}<button class="btn btn-primary btn-block" data-testid="authorize-submit">Place hold</button></div></form>
<div class="card-footer">Available to reserve: <strong class="num">{"100.00 EUR" if state=="empty" else "67.40 EUR"}</strong></div></section>'''
    if state == "empty":
        lst = '<div class="empty" data-testid="empty-authorizations"><img class="empty-art" src="/static/empty-holds.svg" alt=""><p class="empty-title">No holds</p><p class="empty-text">Reserve money for someone and it will appear here until they collect it.</p></div>'
    else:
        rows = [
            auth_row("a_6", True, "@cleo", "30.00 EUR", None, "open", "Bike rental", "2026-10-05T19:52:00+00:00", "in 9 min", "30.00", 0),
            auth_row("a_5", False, "@bob", "20.00 EUR", None, "open", "Deposit", "2026-10-05T19:50:00+00:00", "in 7 min", None, 0),
            auth_row("a_4", True, "@dan", "40.00 EUR", None, "open", "Hotel incidentals · 12.00 EUR collected", "2026-10-05T19:45:00+00:00", "in 2 min", "28.00", 30),
            auth_row("a_3", False, "@bob", "25.00 EUR", "18.00 EUR", "captured", "Deposit · 7.00 EUR released", "2026-10-05T18:40:00+00:00", "", None),
            auth_row("a_2", False, "@cleo", "10.00 EUR", None, "voided", "Tickets", "2026-10-04T12:00:00+00:00", "", None),
            auth_row("a_1", True, "@dan", "15.00 EUR", None, "expired", "Car share", "2026-10-03T09:00:00+00:00", "", None),
        ]
        lst = f'<ul class="list" data-testid="authorization-list">{"".join(rows)}</ul>'
    lerr = banner("danger", "authorization-error", "i-alert", "<strong>Capture refused — this hold has expired.</strong> The money went back to @dan.") if state == "error" else ""
    lcard = f'''<section class="card"><div class="card-header"><div class="card-heading">{ic("i-lock")}<h2 class="card-title">Your holds</h2></div><span class="card-sub">Newest first</span></div>{lerr}<div class="card-body">{lst}</div></section>'''
    return shell("/authorizations", "Holds", "Reserve money now — they collect it later, in one or more captures.", f'{form}{lcard}')

# ---------- auth ----------
def auth_page(kind, state):
    err = ""
    if state == "error":
        msg = "That email and password don’t match an account." if kind == "login" else "That email is already registered. Try logging in instead."
        err = f'<div class="banner banner-danger" role="alert" data-testid="auth-error">{ic("i-alert")}<p>{msg}</p></div>'
    if kind == "login":
        fields = field("Email", "login-email", "ada@example.com" if state=="error" else "", placeholder="you@example.com") + field("Password", "login-password", "", placeholder="••••••••")
        title, sub, btn, sw = "Welcome back", "Log in to your Pocketful wallet.", '<button class="btn btn-primary btn-block" data-testid="login-submit">Log in</button>', 'New to Pocketful? <a href="/signup">Create an account</a>'
    else:
        fields = field("Your name", "signup-display-name", "Ada Lovelace" if state=="error" else "", placeholder="How friends know you") + field("Email", "signup-email", "ada@example.com" if state=="error" else "", placeholder="you@example.com") + field("Password", "signup-password", "", hint="At least 8 characters.", placeholder="••••••••")
        title, sub, btn, sw = "Create your wallet", "Pay friends, split bills and reserve money in seconds.", '<button class="btn btn-primary btn-block" data-testid="signup-submit">Create account</button>', 'Already have an account? <a href="/login">Log in</a>'
    return head(title) + f'''<body class="auth-body"><main class="auth">
<section class="card auth-card"><img class="brand-logo" src="/static/logo.svg" alt="Pocketful"><div><h1 class="auth-title">{title}</h1><p class="auth-sub">{sub}</p></div>
<form class="form">{fields}<div class="form-actions">{err}{btn}</div></form><p class="auth-switch">{sw}</p></section>
<aside class="auth-art"><img src="/static/auth.svg" alt=""><p class="auth-art-title">Money between friends, without the awkward part.</p><p class="auth-art-text">Every payment is confirmed exactly once — even when your connection drops.</p></aside>
</main></body></html>'''

PAGES = {
    "wallet-default": wallet("default"), "wallet-empty": wallet("empty"), "wallet-loading": wallet("loading"),
    "wallet-pending": wallet("pending"), "wallet-success": wallet("success"), "wallet-error": wallet("error"), "wallet-uncertain": wallet("uncertain"),
    "requests-default": requests("default"), "requests-empty": requests("empty"), "requests-error": requests("error"),
    "split-preview": split("preview"), "split-empty": split("empty"), "split-error": split("error"),
    "authorizations-default": authorizations("default"), "authorizations-empty": authorizations("empty"), "authorizations-error": authorizations("error"),
    "login-default": auth_page("login", "default"), "login-error": auth_page("login", "error"),
    "signup-default": auth_page("signup", "default"), "signup-error": auth_page("signup", "error"),
}
link = OUT / "static"
if not link.exists():
    link.symlink_to("../../../../stage-2/web/static")
for name, html in PAGES.items():
    (OUT / f"{name}.html").write_text(html)
print(len(PAGES), "pages")
