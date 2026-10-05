# Pocketful — design system (stage 2)

Calm, trustworthy consumer wallet. Ink-blue neutrals, one teal-green accent, money set
large in tabular figures. Everything below ships in the container: no font CDN, no remote
images.

## Files and ownership

| Path (in `stage-2/`) | Owner | Notes |
|---|---|---|
| `web/static/app.css` | designer | The only stylesheet. Link it from every page: `<link rel="stylesheet" href="/static/app.css">` |
| `web/static/logo.svg` | designer | Mark + wordmark |
| `web/static/mark.svg` | designer | Square mark only (favicon: `<link rel="icon" href="/static/mark.svg">`) |
| `web/static/icons.svg` | designer | Sprite. `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#i-send"/></svg>` |
| `web/static/empty-*.svg`, `auth.svg` | designer | Illustrations (`alt=""`, decorative) |
| HTML / JS / Go handlers, `go:embed` of `web/` | builder | Serve `web/static/*` at `/static/*` with correct `Content-Type` (`text/css`, `image/svg+xml`). Dockerfile must `COPY web ./web` before the build. |

Icon ids in the sprite: `i-wallet`, `i-send`, `i-request`, `i-split`, `i-hold`, `i-inbox`,
`i-outbox`, `i-refresh`, `i-lock`, `i-globe`, `i-check`, `i-x`, `i-alert`, `i-clock`,
`i-logout`, `i-arrow-up` (money out), `i-arrow-down` (money in), `i-info`.

## Tokens (CSS custom properties on `:root`, dark set under `prefers-color-scheme: dark`)

- **Type**: system stack `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`.
  Scale `--fs-xs 13px`, `--fs-sm 14px`, `--fs-md 15px`, `--fs-lg 17px`, `--fs-xl 22px`, `--fs-2xl 30px`, `--fs-3xl 40px`.
  Weights 400 / 500 / 650. All amounts, times and counts: `.num` (tabular figures).
- **Colour** (light): `--bg #f4f6f8`, `--surface #ffffff`, `--surface-2 #f0f3f6`, `--ink #14202b`,
  `--ink-2 #4a5866` (secondary text, 7:1), `--ink-3 #66727f` (muted, 4.8:1), `--line rgba(20,32,43,.10)`,
  `--accent #0f7a5c` (primary, white text 5.3:1), `--accent-soft #e3f3ec`,
  `--success #157347` / `--success-soft #e2f4ea`, `--warning #8a5a00` / `--warning-soft #fdf1d8` (uncertain, held/pending),
  `--danger #b42318` / `--danger-soft #fdecea`, `--hold #4f46b5` / `--hold-soft #ecebfb` (held funds / authorisations).
- **Space**: 4 px base: `--s1 4` `--s2 8` `--s3 12` `--s4 16` `--s5 24` `--s6 32` `--s7 48`.
- **Shape**: `--r-control 12px`, `--r-card 18px`, `--r-pill 999px`. Borders 1 px `--line`; one soft shadow `--shadow` on cards.
- **Motion**: 160 ms ease-out; none under `prefers-reduced-motion`.
- **Focus**: 2 px `--accent` outline, offset 2 px, on every focusable element (never removed).

## Semantic states (visually distinct — colour **and** icon/text)

| State | Class | Look |
|---|---|---|
| Available | `.stat-hero` | 40 px ink number, label "Available to spend" |
| Held | `.stat-held`, `.badge-hold` | indigo, lock icon, "On hold" |
| Pending | `.badge-pending` | amber dot + "Pending" |
| Loading | `.skeleton`, `.btn[aria-busy="true"]` | shimmer blocks; spinner inside button, label kept |
| Success | `.banner-success`, `.badge-success` | green, check icon |
| Refused / error | `.banner-danger`, `.field-error` | red, alert icon |
| Uncertain | `.banner-warning` | amber, clock icon, explains "we couldn't confirm — retry is safe" |
| Neutral closed (declined/cancelled/voided/expired) | `.badge-muted` | grey |

## Layout shell (same on every signed-in route)

```html
<body>
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="/"><img class="brand-logo" src="/static/logo.svg" alt="Pocketful"></a>
    <nav class="nav" aria-label="Main">
      <a class="nav-link" href="/" aria-current="page"><svg class="icon" aria-hidden="true"><use href="/static/icons.svg#i-wallet"/></svg><span>Wallet</span></a>
      <a class="nav-link" href="/requests">…#i-request… <span>Requests</span></a>
      <a class="nav-link" href="/split">…#i-split… <span>Split</span></a>
      <a class="nav-link" href="/authorizations">…#i-hold… <span>Holds</span></a>
    </nav>
    <div class="user">
      <span class="avatar" aria-hidden="true">A</span>
      <span class="user-text">
        <span class="user-name" data-testid="current-user">Ada Lovelace</span>
        <span class="user-handle">@<span data-testid="current-handle">ada</span></span>
      </span>
      <button class="btn btn-quiet btn-icon" data-testid="logout-button" aria-label="Log out"><svg class="icon"><use href="/static/icons.svg#i-logout"/></svg><span class="btn-label">Log out</span></button>
    </div>
  </div>
</header>
<main class="page">
  <div class="page-header">
    <div><h1 class="page-title">Wallet</h1><p class="page-sub">One line of context.</p></div>
    <div class="page-actions">…optional quiet buttons…</div>
  </div>
  … content …
</main>
</body>
```

`aria-current="page"` marks the active nav item. At ≤ 720 px the nav becomes a full-width
second row of four equal tabs; desktop puts it inline. Content max-width 1120 px, 16 px gutters on phones.

## Components (class vocabulary — please use exactly these)

- **Containers**: `.page`, `.page-header`, `.page-title`, `.page-sub`, `.page-actions`,
  `.grid-2` (two equal columns ≥ 900 px, one column below; children stretch to equal height),
  `.grid-wallet` (forms column 5fr | feed column 7fr ≥ 1000 px), `.stack` (vertical gap 24 px),
  `.card`, `.card-header`, `.card-title` (h2), `.card-sub`, `.card-body`, `.card-footer`.
- **Balance**: `.balance` card containing
  ```html
  <section class="card balance">
    <div class="balance-main">
      <span class="stat-label">Available to spend</span>
      <span class="stat-hero num" data-testid="wallet-available" data-amount="8000">80.00 EUR</span>
    </div>
    <dl class="balance-meta">
      <div class="stat"><dt class="stat-label">Total</dt><dd class="stat-value num" data-testid="wallet-balance" data-amount="10000">100.00 EUR</dd></div>
      <div class="stat stat-held"><dt class="stat-label"><svg class="icon">#i-lock</svg>On hold</dt><dd class="stat-value num" data-testid="wallet-held" data-amount="2000">20.00 EUR</dd></div>  <!-- omit whole .stat when held is 0 -->
    </dl>
    <button class="btn btn-quiet btn-sm balance-refresh" data-testid="wallet-refresh"><svg class="icon">#i-refresh</svg><span>Refresh</span></button>
  </section>
  ```
  The `data-testid` element's text must be the plain formatted amount only (`80.00 EUR`); do not put extra spans in it.
- **Forms**: `<form class="form">` holding `.field` blocks:
  ```html
  <div class="field">
    <label class="label" for="pay-handle">Recipient</label>
    <div class="input-affix"><span class="affix">@</span><input class="input" id="pay-handle" data-testid="pay-handle" autocomplete="off"></div>
    <p class="hint">Their Pocketful handle</p>         <!-- optional -->
  </div>
  ```
  Amount input: `<div class="input-affix"><input class="input input-amount num" inputmode="decimal" …><span class="affix">EUR</span></div>`.
  `.field-row` puts two fields side by side (amount + visibility). Selects: `<select class="select">`.
  Form errors: `<div class="banner banner-danger" role="alert" data-testid="pay-error"><svg class="icon">#i-alert</svg><p>message</p></div>`
  placed directly above the submit button. Uncertain: same with `banner-warning` + `#i-clock`
  and `data-testid="pay-uncertain"`. Success: `banner-success` + `#i-check` (no testid needed).
  Submit row: `<div class="form-actions"><button class="btn btn-primary btn-block">Send 15.00 EUR</button></div>`.
- **Buttons**: `.btn` + one of `.btn-primary` (one per form), `.btn-secondary` (outlined),
  `.btn-quiet` (text), `.btn-danger-quiet` (decline / void / cancel); sizes `.btn-sm`, `.btn-block`;
  `.btn-icon` for icon+label buttons. Pending: set `aria-busy="true"` and `disabled`; CSS draws the spinner.
- **Lists**: `<ul class="list">` with `<li class="list-row">`:
  ```html
  <li class="list-row" data-testid="activity-item-p_1" data-visibility="public">
    <span class="row-icon row-icon-out"><svg class="icon">#i-arrow-up</svg></span>   <!-- row-icon-in / -out / -hold -->
    <div class="row-main">
      <p class="row-title" data-testid="activity-parties-p_1">You → @bob</p>   <!-- must contain both handles, e.g. "ada → bob" -->
      <p class="row-sub"><span data-testid="activity-note-p_1">Lunch</span><span class="dot">·</span><time class="num">Today, 13:10</time></p>
    </div>
    <div class="row-end">
      <span class="amount amount-out num" data-testid="activity-amount-p_1">15.00 EUR</span>  <!-- amount-in green, amount-out ink -->
      <span class="badge badge-muted"><svg class="icon">#i-lock</svg>Private</span>        <!-- or badge-public with #i-globe -->
    </div>
    <div class="row-actions">…buttons, capture input…</div>   <!-- optional; wraps to its own line on phones -->
  </li>
  ```
  Direction is shown by icon, sign-free colour and the words "You paid / paid you"; amounts stay exactly the formatted value (no +/−) inside the testid element.
- **Badges**: `.badge` + `.badge-pending`, `.badge-success` (paid/captured), `.badge-muted`
  (declined/cancelled/voided/expired), `.badge-hold` (open authorisation), `.badge-danger`, `.badge-public`.
- **Section in a card**: `.section-title` (small caps label above a list, e.g. "Incoming · 2").
- **Empty**: `<div class="empty" data-testid="empty-activity"><img class="empty-art" src="/static/empty-activity.svg" alt=""><p class="empty-title">No activity yet</p><p class="empty-text">Payments you send or receive will show up here.</p></div>`
  Illustrations: `empty-activity.svg`, `empty-requests.svg`, `empty-holds.svg`, `empty-split.svg`.
- **Loading**: before first data, render `.skeleton` blocks in place: `<span class="skeleton skeleton-hero"></span>`,
  `<div class="skeleton-row"><span class="skeleton skeleton-circle"></span><span class="skeleton skeleton-line"></span></div>` ×3.
- **Split preview**: `<div class="preview" data-testid="split-preview">` with `<ul class="share-list">` of
  `<li class="share"><span class="avatar avatar-sm">B</span><span class="share-name">@bob</span><span class="share-amount num" data-testid="split-share-bob">10.00 EUR</span></li>`, and a `.preview-total` line.
- **Authorisation row extras**: `.hold-meta` line ("Expires <time data-testid=authorization-expires-…>RFC3339</time>" — text must stay the raw RFC 3339 value; add a friendly `.hint` beside it like "in 9 min"),
  `.progress` bar (`<div class="progress"><span style="width:40%"></span></div>`) for captured vs authorised,
  capture group: `<div class="capture"><div class="input-affix input-sm"><input class="input num" data-testid="authorization-capture-amount-a_1"><span class="affix">EUR</span></div><button class="btn btn-primary btn-sm">Capture</button></div>`.
- **Auth pages** (`/login`, `/signup`): `<body class="auth-body"><main class="auth"><section class="auth-card card">` with
  logo, `h1.auth-title`, `.auth-sub`, form, `.auth-switch` link; `<aside class="auth-art"><img src="/static/auth.svg" alt=""></aside>` shown ≥ 900 px.
  `auth-error` is a `.banner.banner-danger`.
- **Toast** (optional): `<div class="toast" role="status">Sent 15.00 EUR to @bob</div>` fixed bottom-centre.

## Screen layouts

- `/` — page-header "Wallet" ; `.balance` card full width ; `.grid-wallet`: left `.stack` [Send card (pay form), Request card], right Activity card (`activity-list` or `empty-activity`).
- `/requests` — page-header "Requests" ; `request-error` banner under header ; `.grid-2`: Incoming card (`incoming-list`), Outgoing card (`outgoing-list`); when both empty one full-width `.empty` (`empty-requests`).
- `/split` — page-header "Split a bill" ; `.grid-2`: form card | preview card (`split-preview`, shows an `.empty` hint until amount+handles are valid).
- `/authorizations` — page-header "Holds" (sub: "Reserve money now, let them collect later") ; `.grid-wallet`: left Authorise card (form + small available-funds reminder), right list card (`authorization-list`/`empty-authorizations`), `authorization-error` banner at top of list card.
- `/login`, `/signup` — split auth layout, single column on phones.

Mockups: `factory/stage-2/design/mockups/<screen>-<state>-<375|1280>.svg`.
