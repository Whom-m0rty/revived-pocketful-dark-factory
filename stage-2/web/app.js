'use strict';

// Pocketful browser client. Every screen is rendered here from the JSON API; the markup
// follows the design system's class vocabulary (factory/stage-2/design/system.md).

const TOKEN_KEY = 'pocketful_token';
const ICON_SPRITE = '/static/icons.svg';
const LIST_LIMIT = 200;

// ---------- small helpers ----------

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const icon = (id) => `<svg class="icon" aria-hidden="true"><use href="${ICON_SPRITE}#${id}"/></svg>`;

const $ = (selector, root = document) => root.querySelector(selector);

function newIdempotencyKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Keeps one idempotency key per distinct request body: resubmitting an unchanged form
 * reuses the key (a replay, never a second payment); any change starts a new request.
 */
function keyKeeper() {
  const keys = new Map();
  return (scope, body) => {
    const serialized = JSON.stringify(body);
    const last = keys.get(scope);
    if (last && last.body === serialized) return last.key;
    const key = newIdempotencyKey();
    keys.set(scope, { body: serialized, key });
    return key;
  };
}

/** Ignores responses from all but the most recent call: latest refresh wins. */
function latestOnly() {
  let latest = 0;
  return async (load, apply) => {
    const ticket = ++latest;
    const result = await load();
    if (ticket === latest) apply(result);
  };
}

// ---------- money ----------

let currentUser = null; // GET /me response

function formatDecimal(minor) {
  const units = currentUser.minor_units;
  if (units === 0) return String(minor);
  const digits = String(minor).padStart(units + 1, '0');
  return `${digits.slice(0, -units)}.${digits.slice(-units)}`;
}

const formatAmount = (minor) => `${formatDecimal(minor)} ${currentUser.currency}`;

/** Parses a decimal amount as a person types it into minor units, or null if invalid. */
function parseAmount(text) {
  const units = currentUser.minor_units;
  const match = /^(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!match) return null;
  const fraction = match[2] || '';
  if (fraction.length > units) return null;
  const minor = BigInt(match[1]) * 10n ** BigInt(units) + BigInt(fraction.padEnd(units, '0') || '0');
  return minor > BigInt(Number.MAX_SAFE_INTEGER) ? null : Number(minor);
}

/** Equal split by the server's rule: larger shares go to the first participants. */
function splitShares(amount, count) {
  const base = Math.floor(amount / count);
  const remainder = amount % count;
  return Array.from({ length: count }, (_, i) => base + (i < remainder ? 1 : 0));
}

function invalidAmountMessage() {
  const units = currentUser.minor_units;
  const example = units === 0 ? '1500' : `15.${'0'.repeat(units)}`;
  return `Enter an amount like ${example}` + (units === 0
    ? ' — whole numbers only.' : ` — at most ${units} decimal places.`);
}

// ---------- time ----------

function humanTime(iso) {
  const date = new Date(iso);
  const time = date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86400000);
  if (date.toDateString() === today.toDateString()) return `Today, ${time}`;
  if (date.toDateString() === yesterday.toDateString()) return `Yesterday, ${time}`;
  return `${date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })}, ${time}`;
}

function untilText(iso) {
  const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  if (minutes <= 0) return 'Expires in under a minute';
  if (minutes < 60) return `Expires in ${minutes} min`;
  return `Expires ${humanTime(iso)}`;
}

function greeting(name) {
  const hour = new Date().getHours();
  const part = hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
  return `Good ${part}, ${name}.`;
}

// ---------- API ----------

class NetworkError extends Error {}

const token = () => localStorage.getItem(TOKEN_KEY);

/** Calls the API. Resolves to {status, data}; rejects with NetworkError when no response arrives. */
async function api(method, path, { body, key } = {}) {
  const headers = { Accept: 'application/json' };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (key) headers['Idempotency-Key'] = key;
  let response;
  let data = null;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (response.status !== 204) data = await response.json();
  } catch (err) {
    throw new NetworkError(String(err));
  }
  if (response.status === 401 && path !== '/auth/login') {
    signOut();
  }
  return { status: response.status, data };
}

const succeeded = (result) => result.status >= 200 && result.status < 300;

/** Treats a 5xx like a lost response: the outcome is unknown, not a refusal. */
function outcomeOf(result) {
  if (succeeded(result)) return 'ok';
  return result.status >= 500 ? 'unknown' : 'refused';
}

const errorCode = (result) => (result.data && result.data.error && result.data.error.code) || '';
const errorText = (result) => (result.data && result.data.error && result.data.error.message) || 'Something went wrong.';

function signOut() {
  localStorage.removeItem(TOKEN_KEY);
  window.location.replace('/login');
}

async function loadMe() {
  const result = await api('GET', '/me');
  if (!succeeded(result)) throw new NetworkError('could not load the signed-in user');
  return result.data;
}

// ---------- banners and buttons ----------

const BANNER_ICONS = { danger: 'i-alert', warning: 'i-clock', success: 'i-check' };

function bannerHtml(kind, testid, html) {
  const role = kind === 'success' ? 'status' : 'alert';
  const testAttr = testid ? ` data-testid="${testid}"` : '';
  return `<div class="banner banner-${kind}" role="${role}"${testAttr}>${icon(BANNER_ICONS[kind])}<p>${html}</p></div>`;
}

/** Shows one banner in a form, directly above its submit button, replacing earlier ones. */
function setFormBanner(form, kind, testid, html) {
  clearFormBanners(form);
  $('.form-actions', form).insertAdjacentHTML('afterbegin', bannerHtml(kind, testid, html));
}

function clearFormBanners(form) {
  form.querySelectorAll('.form-actions .banner').forEach((banner) => banner.remove());
}

function setBanner(container, kind, testid, html) {
  container.innerHTML = html ? bannerHtml(kind, testid, html) : '';
}

function setBusy(button, busy, busyLabel) {
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyLabel;
    button.setAttribute('aria-busy', 'true');
    button.disabled = true;
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.removeAttribute('aria-busy');
    button.disabled = false;
  }
}

/** Human message for a refused write, by error code. */
function refusalMessage(result, { what, handle }) {
  switch (errorCode(result)) {
    case 'insufficient_funds':
      return `<strong>${what} refused — not enough available funds.</strong> You have ${escapeHtml(formatAmount(currentUser.available))} available.`;
    case 'not_found':
      return handle
        ? `<strong>${what} refused.</strong> No one has the handle @${escapeHtml(handle)}.`
        : `<strong>${what} refused.</strong> It no longer exists.`;
    case 'self_payment':
    case 'self_request':
      return `<strong>${what} refused.</strong> You can’t do that with your own handle.`;
    case 'request_not_pending':
      return `<strong>${what} refused — this request is no longer pending.</strong> The list has been refreshed.`;
    case 'authorization_not_open':
      return `<strong>${what} refused — this hold is no longer open.</strong> The list has been refreshed.`;
    case 'authorization_expired':
      return `<strong>${what} refused — this hold has expired.</strong> The money went back to the payer.`;
    case 'capture_exceeds_authorization':
      return `<strong>${what} refused.</strong> That is more than the hold has left.`;
    case 'forbidden':
      return `<strong>${what} refused.</strong> You aren’t allowed to do that.`;
    default:
      return `<strong>${what} refused.</strong> ${escapeHtml(errorText(result))}`;
  }
}

const UNCERTAIN_MESSAGE = '<strong>We couldn’t confirm this.</strong> It may or may not have gone through. '
  + 'Press the button again — retrying is safe and won’t do it twice.';

// ---------- shells ----------

const NAV = [
  { href: '/', label: 'Wallet', icon: 'i-wallet' },
  { href: '/requests', label: 'Requests', icon: 'i-request' },
  { href: '/split', label: 'Split', icon: 'i-split' },
  { href: '/authorizations', label: 'Holds', icon: 'i-hold' },
];

function topbarHtml(activePath) {
  const links = NAV.map((item) => {
    const current = item.href === activePath ? ' aria-current="page"' : '';
    return `<a class="nav-link" href="${item.href}"${current}>${icon(item.icon)}<span>${item.label}</span></a>`;
  }).join('');
  const name = currentUser.display_name;
  return `<header class="topbar"><div class="topbar-inner">
<a class="brand" href="/"><img class="brand-logo" src="/static/logo.svg" alt="Pocketful"></a>
<nav class="nav" aria-label="Main">${links}</nav>
<div class="user"><span class="avatar" aria-hidden="true">${escapeHtml((name || currentUser.handle).slice(0, 1).toUpperCase())}</span><span class="user-text"><span class="user-name" data-testid="current-user">${escapeHtml(name)}</span><span class="user-handle">@<span data-testid="current-handle">${escapeHtml(currentUser.handle)}</span></span></span>
<button class="btn btn-quiet btn-icon" type="button" data-testid="logout-button" data-action="logout" aria-label="Log out">${icon('i-logout')}<span class="btn-label">Log out</span></button></div>
</div></header>`;
}

function renderSignedIn(activePath, title, sub, contentHtml) {
  document.title = `${title} · Pocketful`;
  document.body.className = '';
  document.body.innerHTML = `${topbarHtml(activePath)}
<main class="page">
<div class="page-header"><div><h1 class="page-title">${title}</h1><p class="page-sub">${escapeHtml(sub)}</p></div></div>
${contentHtml}
</main>`;
}

function cardHeader(iconId, title, sub = '') {
  const subHtml = sub ? `<span class="card-sub">${sub}</span>` : '';
  return `<div class="card-header"><div class="card-heading">${icon(iconId)}<h2 class="card-title">${title}</h2></div>${subHtml}</div>`;
}

function textField(id, label, { placeholder = '', prefix = '', optional = false, hint = '', type = 'text' } = {}) {
  const labelHtml = optional ? `${label} <span class="label-opt">(optional)</span>` : label;
  const input = `<input class="input" id="${id}" data-testid="${id}" type="${type}" placeholder="${escapeHtml(placeholder)}" autocomplete="off">`;
  const control = prefix ? `<div class="input-affix"><span class="affix">${prefix}</span>${input}</div>` : input;
  const hintHtml = hint ? `<p class="hint">${hint}</p>` : '';
  return `<div class="field"><label class="label" for="${id}">${labelHtml}</label>${control}${hintHtml}</div>`;
}

function amountField(id, label) {
  const placeholder = currentUser.minor_units === 0 ? '0' : `0.${'0'.repeat(currentUser.minor_units)}`;
  return `<div class="field"><label class="label" for="${id}">${label}</label><div class="input-affix"><input class="input input-amount num" id="${id}" data-testid="${id}" placeholder="${placeholder}" autocomplete="off" inputmode="decimal"><span class="affix">${escapeHtml(currentUser.currency)}</span></div></div>`;
}

function visibilityField(id) {
  return `<div class="field"><label class="label" for="${id}">Who can see it</label><select class="select" id="${id}" data-testid="${id}"><option value="public" selected>Public</option><option value="private">Private</option></select></div>`;
}

function emptyHtml(testid, art, title, text, extra = '') {
  const testAttr = testid ? ` data-testid="${testid}"` : '';
  return `<div class="empty"${testAttr}><img class="empty-art" src="/static/${art}" alt=""><p class="empty-title">${title}</p><p class="empty-text">${text}</p>${extra}</div>`;
}

const skeletonRows = () => '<div class="skeleton-row"><span class="skeleton skeleton-circle"></span><span class="skeleton skeleton-line"></span></div>'.repeat(3);

const visibilityBadge = (visibility) => (visibility === 'private'
  ? `<span class="badge badge-muted">${icon('i-lock')}Private</span>`
  : `<span class="badge badge-public">${icon('i-globe')}Public</span>`);

function formValues(form) {
  const values = {};
  form.querySelectorAll('[data-testid]').forEach((el) => {
    if ('value' in el && el.tagName !== 'BUTTON') values[el.dataset.testid] = el.value;
  });
  return values;
}

// ---------- wallet: / ----------

function balanceHtml() {
  const held = currentUser.held;
  const heldStat = held > 0
    ? `<div class="stat stat-held"><dt class="stat-label">${icon('i-lock')}On hold</dt><dd class="stat-value num" data-testid="wallet-held" data-amount="${held}">${formatAmount(held)}</dd></div>`
    : '';
  const note = held > 0
    ? `<p class="balance-note">${formatAmount(held)} is reserved for open holds. <a href="/authorizations">View holds</a></p>`
    : '';
  return `<div class="balance-main"><span class="stat-label">Available to spend</span>
<span class="stat-hero num" data-testid="wallet-available" data-amount="${currentUser.available}">${formatAmount(currentUser.available)}</span></div>
<dl class="balance-meta"><div class="stat"><dt class="stat-label">Total balance</dt><dd class="stat-value num" data-testid="wallet-balance" data-amount="${currentUser.total}">${formatAmount(currentUser.total)}</dd></div>${heldStat}</dl>
${refreshButtonHtml()}${note}`;
}

const refreshButtonHtml = () => `<button class="btn btn-quiet btn-sm btn-icon balance-refresh" type="button" data-testid="wallet-refresh" data-action="refresh-wallet">${icon('i-refresh')}<span>Refresh</span></button>`;

function balanceLoadingHtml() {
  return `<div class="balance-main"><span class="stat-label">Available to spend</span><span class="skeleton skeleton-hero"></span></div>
<dl class="balance-meta"><div class="stat"><dt class="stat-label">Total</dt><dd><span class="skeleton skeleton-line" style="width:110px"></span></dd></div></dl>
${refreshButtonHtml()}`;
}

function activityRowHtml(payment) {
  const me = currentUser.handle;
  const outgoing = payment.from_handle === me;
  const incoming = payment.to_handle === me;
  let title = `${escapeHtml(payment.from_handle)} paid ${escapeHtml(payment.to_handle)}`;
  if (outgoing) title = `You paid ${escapeHtml(payment.to_handle)}`;
  if (incoming) title = `${escapeHtml(payment.from_handle)} paid you`;
  const rowIcon = incoming
    ? `<span class="row-icon row-icon-in">${icon('i-arrow-down')}</span>`
    : `<span class="row-icon row-icon-out">${icon(outgoing ? 'i-arrow-up' : 'i-globe')}</span>`;
  const id = escapeHtml(payment.payment_id);
  return `<li class="list-row" data-testid="activity-item-${id}" data-visibility="${payment.visibility}">${rowIcon}
<div class="row-main"><p class="row-title">${title}</p><p class="row-sub"><span data-testid="activity-parties-${id}">${escapeHtml(payment.from_handle)} → ${escapeHtml(payment.to_handle)}</span><span class="dot">·</span><time class="num" datetime="${escapeHtml(payment.created_at)}">${humanTime(payment.created_at)}</time></p>
<p class="row-sub"><span data-testid="activity-note-${id}">${escapeHtml(payment.note)}</span></p></div>
<div class="row-end"><span class="amount ${incoming ? 'amount-in' : 'amount-out'} num" data-testid="activity-amount-${id}">${formatAmount(payment.amount)}</span>${visibilityBadge(payment.visibility)}</div></li>`;
}

function activityHtml(payments) {
  if (payments.length === 0) {
    return emptyHtml('empty-activity', 'empty-activity.svg', 'No activity yet',
      'Payments you send or receive, and public payments between others, show up here.');
  }
  return `<ul class="list" data-testid="activity-list">${payments.map(activityRowHtml).join('')}</ul>`;
}

function walletPage() {
  const keys = keyKeeper();
  const refreshLatest = latestOnly();

  renderSignedIn('/', 'Wallet', greeting(currentUser.display_name), `
<section class="card balance" id="balance" aria-busy="true">${balanceLoadingHtml()}</section>
<div class="grid-wallet"><div class="stack"><section class="card">${cardHeader('i-send', 'Send money')}
<form class="form" id="pay-form" novalidate>${textField('pay-handle', 'To', { placeholder: 'handle', prefix: '@' })}
<div class="field-row">${amountField('pay-amount', 'Amount')}${visibilityField('pay-visibility')}</div>
${textField('pay-note', 'Note', { placeholder: 'What’s it for?', optional: true })}
<div class="form-actions"><button class="btn btn-primary btn-block" type="submit" data-testid="pay-submit">Send money</button></div></form></section>
<section class="card">${cardHeader('i-request', 'Request money')}
<form class="form" id="request-form" novalidate>${textField('request-handle', 'From', { placeholder: 'handle', prefix: '@' })}
${amountField('request-amount', 'Amount')}
${textField('request-note', 'Note', { placeholder: 'Concert tickets', optional: true })}
<div class="form-actions"><button class="btn btn-secondary btn-block" type="submit" data-testid="request-submit">Send request</button></div></form></section></div>
<section class="card">${cardHeader('i-clock', 'Activity', 'Newest first')}
<div class="card-body" id="activity">${skeletonRows()}</div></section></div>`);

  async function refresh() {
    await refreshLatest(
      () => Promise.all([loadMe(), api('GET', `/activity?limit=${LIST_LIMIT}`)]),
      ([me, activity]) => {
        currentUser = me;
        const balance = $('#balance');
        balance.removeAttribute('aria-busy');
        balance.innerHTML = balanceHtml();
        if (succeeded(activity)) $('#activity').innerHTML = activityHtml(activity.data.payments);
      },
    ).catch(() => {});
  }

  const payForm = $('#pay-form');
  payForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = formValues(payForm);
    const amount = parseAmount(values['pay-amount']);
    if (amount === null) {
      setFormBanner(payForm, 'danger', 'pay-error', `<strong>Check the amount.</strong> ${invalidAmountMessage()}`);
      return;
    }
    const handle = values['pay-handle'].trim();
    const body = { to_handle: handle, amount, note: values['pay-note'], visibility: values['pay-visibility'] };
    const key = keys('payment', body);
    const button = $('[data-testid="pay-submit"]', payForm);
    setBusy(button, true, 'Sending…');
    let result;
    try {
      result = await api('POST', '/payments', { body, key });
    } catch (err) {
      result = null;
    }
    setBusy(button, false);
    const outcome = result ? outcomeOf(result) : 'unknown';
    if (outcome === 'unknown') {
      setFormBanner(payForm, 'warning', 'pay-uncertain', UNCERTAIN_MESSAGE);
      return;
    }
    await refresh();
    if (outcome === 'ok') {
      setFormBanner(payForm, 'success', '', `<strong>Sent ${escapeHtml(formatAmount(amount))} to @${escapeHtml(handle)}.</strong> Your balance is up to date.`);
    } else {
      setFormBanner(payForm, 'danger', 'pay-error', refusalMessage(result, { what: 'Payment', handle }));
    }
  });

  const requestForm = $('#request-form');
  requestForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = formValues(requestForm);
    const amount = parseAmount(values['request-amount']);
    if (amount === null) {
      setFormBanner(requestForm, 'danger', 'request-error', `<strong>Check the amount.</strong> ${invalidAmountMessage()}`);
      return;
    }
    const handle = values['request-handle'].trim();
    const body = { payer_handle: handle, amount, note: values['request-note'] };
    const key = keys('request', body);
    const button = $('[data-testid="request-submit"]', requestForm);
    setBusy(button, true, 'Sending…');
    let result;
    try {
      result = await api('POST', '/requests', { body, key });
    } catch (err) {
      result = null;
    }
    setBusy(button, false);
    const outcome = result ? outcomeOf(result) : 'unknown';
    if (outcome === 'unknown') {
      setFormBanner(requestForm, 'warning', 'request-uncertain', UNCERTAIN_MESSAGE);
      return;
    }
    await refresh();
    if (outcome === 'ok') {
      setFormBanner(requestForm, 'success', '', `<strong>Asked @${escapeHtml(handle)} for ${escapeHtml(formatAmount(amount))}.</strong> You’ll see it under Requests.`);
    } else {
      setFormBanner(requestForm, 'danger', 'request-error', refusalMessage(result, { what: 'Request', handle }));
    }
  });

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="refresh-wallet"]')) refresh();
  });

  refresh();
}

// ---------- requests: /requests ----------

const REQUEST_BADGES = {
  pending: '<span class="badge badge-pending">Pending</span>',
  paid: `<span class="badge badge-success">${icon('i-check')}Paid</span>`,
  declined: '<span class="badge badge-muted">Declined</span>',
  cancelled: '<span class="badge badge-muted">Cancelled</span>',
};

function requestRowHtml(request, incoming) {
  const id = escapeHtml(request.request_id);
  const pending = request.status === 'pending';
  const title = incoming
    ? `@${escapeHtml(request.requester_handle)} requests`
    : `You asked @${escapeHtml(request.payer_handle)}`;
  const amount = formatAmount(request.amount);
  let actions = '';
  if (pending && incoming) {
    actions = `<div class="row-actions"><button class="btn btn-primary btn-sm" type="button" data-testid="request-pay-${id}" data-action="pay-request" data-id="${id}">Pay ${amount}</button><button class="btn btn-danger-quiet btn-sm" type="button" data-testid="request-decline-${id}" data-action="decline-request" data-id="${id}">Decline</button></div>`;
  } else if (pending) {
    actions = `<div class="row-actions"><button class="btn btn-danger-quiet btn-sm" type="button" data-testid="request-cancel-${id}" data-action="cancel-request" data-id="${id}">Cancel request</button></div>`;
  }
  const note = request.note ? `<span>${escapeHtml(request.note)}</span><span class="dot">·</span>` : '';
  return `<li class="list-row" data-testid="request-item-${id}" data-status="${request.status}"><span class="row-icon ${pending ? 'row-icon-pending' : 'row-icon-out'}">${icon(incoming ? 'i-inbox' : 'i-outbox')}</span>
<div class="row-main"><p class="row-title">${title}</p><p class="row-sub">${note}<time class="num" datetime="${escapeHtml(request.created_at)}">${humanTime(request.created_at)}</time></p></div>
<div class="row-end"><span class="amount num" data-testid="request-amount-${id}">${amount}</span>${REQUEST_BADGES[request.status]}</div>${actions}</li>`;
}

function requestListCard(testid, iconId, title, requests, incoming, emptyText) {
  const rows = requests.map((request) => requestRowHtml(request, incoming)).join('');
  const emptyNote = requests.length === 0 ? `<p class="card-sub">${emptyText}</p>` : '';
  return `<section class="card">${cardHeader(iconId, `${title} <span class="count">· ${requests.length}</span>`)}
${emptyNote}<ul class="list" data-testid="${testid}">${rows}</ul></section>`;
}

function requestsPage() {
  const keys = keyKeeper();
  const loadLatest = latestOnly();

  renderSignedIn('/requests', 'Requests', 'Money you’ve asked for, and money asked of you.', `
<div id="request-banner"></div>
<div id="request-lists"><div class="grid-2"><section class="card">${skeletonRows()}</section><section class="card">${skeletonRows()}</section></div></div>`);

  async function load() {
    await loadLatest(
      () => Promise.all([
        api('GET', `/requests?direction=incoming&limit=${LIST_LIMIT}`),
        api('GET', `/requests?direction=outgoing&limit=${LIST_LIMIT}`),
      ]),
      ([incoming, outgoing]) => {
        if (!succeeded(incoming) || !succeeded(outgoing)) return;
        const incomingList = incoming.data.requests;
        const outgoingList = outgoing.data.requests;
        $('#request-lists').innerHTML = incomingList.length === 0 && outgoingList.length === 0
          ? `<section class="card">${emptyHtml('empty-requests', 'empty-requests.svg', 'No requests yet',
            'Ask a friend to pay you back from your wallet, and requests sent to you will wait here.',
            '<a class="btn btn-secondary" href="/">Request money</a>')}`
            + '<ul class="list" data-testid="incoming-list" hidden></ul><ul class="list" data-testid="outgoing-list" hidden></ul></section>'
          : `<div class="grid-2">${requestListCard('incoming-list', 'i-inbox', 'Incoming', incomingList, true, 'Nobody has asked you for money.')}${requestListCard('outgoing-list', 'i-outbox', 'Outgoing', outgoingList, false, 'You haven’t asked anyone for money.')}</div>`;
      },
    ).catch(() => {});
  }

  const ACTIONS = {
    'pay-request': { verb: 'Payment', busy: 'Paying…', path: (id) => `/requests/${id}/pay`, idempotent: true },
    'decline-request': { verb: 'Decline', busy: 'Declining…', path: (id) => `/requests/${id}/decline` },
    'cancel-request': { verb: 'Cancel', busy: 'Cancelling…', path: (id) => `/requests/${id}/cancel` },
  };

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    const action = button && ACTIONS[button.dataset.action];
    if (!action) return;
    const id = button.dataset.id;
    const options = action.idempotent ? { body: {}, key: keys(id, {}) } : {};
    const banner = $('#request-banner');
    setBusy(button, true, action.busy);
    let result;
    try {
      result = await api('POST', encodeURI(action.path(id)), options);
    } catch (err) {
      result = null;
    }
    const outcome = result ? outcomeOf(result) : 'unknown';
    if (outcome === 'ok') {
      setBanner(banner, 'success', '', '');
    } else if (outcome === 'unknown') {
      setBanner(banner, 'warning', 'request-error', UNCERTAIN_MESSAGE);
    } else {
      setBanner(banner, 'danger', 'request-error', refusalMessage(result, { what: action.verb }));
    }
    await load();
  });

  load();
}

// ---------- split: /split ----------

function parseHandles(text) {
  return text.split(',').map((handle) => handle.trim()).filter((handle) => handle !== '');
}

function splitPreviewHtml(amount, handles) {
  if (amount === null || amount < 1 || handles.length === 0) {
    return emptyHtml('', 'empty-split.svg', 'Shares appear here', 'Enter an amount and at least one handle to see who pays what.');
  }
  const shares = splitShares(amount, handles.length);
  const rows = handles.map((handle, i) => {
    const you = handle === currentUser.handle ? ' <span class="share-you">(you)</span>' : '';
    const safe = escapeHtml(handle);
    return `<li class="share"><span class="avatar avatar-sm" aria-hidden="true">${escapeHtml(handle.slice(0, 1))}</span><span class="share-name">@${safe}${you}</span><span class="share-amount num" data-testid="split-share-${safe}">${formatAmount(shares[i])}</span></li>`;
  }).join('');
  const uneven = amount % handles.length !== 0
    ? '<p class="hint">Any leftover goes to the first people listed, exactly as the server will compute it.</p>' : '';
  return `<ul class="share-list">${rows}</ul><div class="preview-total"><span>Total</span><span class="num">${formatAmount(amount)}</span></div>${uneven}`;
}

function splitPage() {
  const keys = keyKeeper();
  renderSignedIn('/split', 'Split a bill', 'Share a cost fairly — everyone gets a request for their part.', `
<div class="grid-2"><section class="card">${cardHeader('i-split', 'Bill details')}
<form class="form" id="split-form" novalidate>${amountField('split-amount', 'Total amount')}
${textField('split-handles', 'People', { placeholder: 'ada, bob, cleo', hint: 'Handles separated by commas, in order. Include yourself to take a share.' })}
${textField('split-note', 'Note', { placeholder: 'What was it?', optional: true })}
<div class="form-actions"><button class="btn btn-primary btn-block" type="submit" data-testid="split-submit">Request shares</button></div></form></section>
<section class="card">${cardHeader('i-split', 'Preview', 'Before anything is sent')}<div class="preview" data-testid="split-preview" id="split-preview"></div></section></div>`);

  const form = $('#split-form');
  const updatePreview = () => {
    const values = formValues(form);
    $('#split-preview').innerHTML = splitPreviewHtml(parseAmount(values['split-amount']), parseHandles(values['split-handles']));
  };
  form.addEventListener('input', updatePreview);
  updatePreview();

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = formValues(form);
    const amount = parseAmount(values['split-amount']);
    const handles = parseHandles(values['split-handles']);
    if (amount === null) {
      setFormBanner(form, 'danger', 'split-error', `<strong>Check the amount.</strong> ${invalidAmountMessage()}`);
      return;
    }
    if (handles.length === 0) {
      setFormBanner(form, 'danger', 'split-error', '<strong>Add people.</strong> Enter at least one handle.');
      return;
    }
    const body = { amount, participant_handles: handles, note: values['split-note'] };
    const button = $('[data-testid="split-submit"]', form);
    setBusy(button, true, 'Sending…');
    let result;
    try {
      result = await api('POST', '/splits', { body, key: keys('split', body) });
    } catch (err) {
      result = null;
    }
    setBusy(button, false);
    const outcome = result ? outcomeOf(result) : 'unknown';
    if (outcome === 'ok') {
      const count = result.data.requests.length;
      setFormBanner(form, 'success', '', `<strong>Split ${escapeHtml(formatAmount(amount))}.</strong> Sent ${count} request${count === 1 ? '' : 's'} for the shares.`);
    } else if (outcome === 'unknown') {
      setFormBanner(form, 'warning', 'split-uncertain', UNCERTAIN_MESSAGE);
    } else {
      const missing = handles.find((handle) => !/^[a-z0-9_]{1,20}$/.test(handle)) || '';
      let message = refusalMessage(result, { what: 'Split', handle: missing });
      if (errorCode(result) === 'not_found') message = '<strong>Split refused.</strong> One of those handles doesn’t belong to anyone.';
      setFormBanner(form, 'danger', 'split-error', message);
    }
  });
}

// ---------- holds: /authorizations ----------

const AUTH_BADGES = {
  open: `<span class="badge badge-hold">${icon('i-lock')}On hold</span>`,
  captured: `<span class="badge badge-success">${icon('i-check')}Captured</span>`,
  voided: '<span class="badge badge-muted">Voided</span>',
  expired: '<span class="badge badge-muted">Expired</span>',
};

function authorizationRowHtml(auth) {
  const id = escapeHtml(auth.authorization_id);
  const incoming = auth.to_handle === currentUser.handle;
  const open = auth.status === 'open';
  const title = incoming
    ? `@${escapeHtml(auth.from_handle)} reserved for you`
    : `You reserved for @${escapeHtml(auth.to_handle)}`;
  const details = [];
  if (auth.note) details.push(escapeHtml(auth.note));
  if (auth.captured_amount > 0 && open) details.push(`${formatAmount(auth.captured_amount)} collected`);
  if (auth.status === 'captured' && auth.captured_amount < auth.amount) details.push(`${formatAmount(auth.amount - auth.captured_amount)} released`);
  const sub = details.length ? `<p class="row-sub"><span>${details.join(' · ')}</span></p>` : '';
  const progress = open && auth.captured_amount > 0
    ? `<div class="progress" aria-hidden="true"><span style="width:${Math.round((auth.captured_amount / auth.amount) * 100)}%"></span></div>` : '';
  const captured = auth.status === 'captured'
    ? `<span class="hint">Captured <span class="num" data-testid="authorization-captured-${id}">${formatAmount(auth.captured_amount)}</span></span>` : '';
  let actions = '';
  if (open && incoming) {
    actions = `<div class="row-actions capture"><label class="capture-label" for="cap-${id}">Amount to collect</label><div class="input-affix"><input class="input num" id="cap-${id}" inputmode="decimal" autocomplete="off" data-testid="authorization-capture-amount-${id}" value="${formatDecimal(auth.remaining_amount)}"><span class="affix">${escapeHtml(currentUser.currency)}</span></div><button class="btn btn-primary btn-sm" type="button" data-testid="authorization-capture-${id}" data-action="capture" data-id="${id}">Capture</button></div>`;
  } else if (open) {
    actions = `<div class="row-actions"><button class="btn btn-danger-quiet btn-sm" type="button" data-testid="authorization-void-${id}" data-action="void" data-id="${id}">Release hold</button></div>`;
  }
  const rowIcon = open ? 'row-icon-hold' : auth.status === 'captured' ? 'row-icon-in' : 'row-icon-out';
  return `<li class="list-row" data-testid="authorization-item-${id}" data-status="${auth.status}"><span class="row-icon ${rowIcon}">${icon('i-hold')}</span>
<div class="row-main"><p class="row-title">${title}</p>${sub}<p class="hold-meta">${icon('i-clock')}<span>${open ? untilText(auth.expires_at) : 'Expiry'}</span><span class="dot">·</span><time class="num" data-testid="authorization-expires-${id}">${escapeHtml(auth.expires_at)}</time></p>${progress}</div>
<div class="row-end"><span class="amount amount-hold num" data-testid="authorization-amount-${id}">${formatAmount(auth.amount)}</span>${AUTH_BADGES[auth.status]}${captured}</div>${actions}</li>`;
}

function authorizationsHtml(authorizations) {
  if (authorizations.length === 0) {
    return emptyHtml('empty-authorizations', 'empty-holds.svg', 'No holds', 'Reserve money for someone and it will appear here until they collect it.');
  }
  return `<ul class="list" data-testid="authorization-list">${authorizations.map(authorizationRowHtml).join('')}</ul>`;
}

function authorizationsPage() {
  const keys = keyKeeper();
  const loadLatest = latestOnly();

  renderSignedIn('/authorizations', 'Holds', 'Reserve money now — they collect it later, in one or more captures.', `
<section class="card">${cardHeader('i-hold', 'Reserve money')}
<p class="card-sub">Holds keep money aside for someone to collect later. Nothing moves until they capture it; anything not collected comes back to you.</p>
<form class="form form-wide" id="authorize-form" novalidate><div class="field-row">${textField('authorize-handle', 'For', { placeholder: 'handle', prefix: '@' })}${amountField('authorize-amount', 'Amount')}</div>
<div class="field-row">${visibilityField('authorize-visibility')}${textField('authorize-note', 'Note', { placeholder: 'Deposit', optional: true })}</div>
<div class="form-actions"><button class="btn btn-primary btn-block" type="submit" data-testid="authorize-submit">Place hold</button></div></form>
<div class="card-footer">Available to reserve: <strong class="num" id="available-to-reserve">${formatAmount(currentUser.available)}</strong></div></section>
<section class="card">${cardHeader('i-lock', 'Your holds', 'Newest first')}<div class="card-body"><div id="authorization-banner"></div><div id="authorizations">${skeletonRows()}</div></div></section>`);

  async function load() {
    await loadLatest(
      () => Promise.all([loadMe(), api('GET', `/authorizations?limit=${LIST_LIMIT}`)]),
      ([me, list]) => {
        currentUser = me;
        $('#available-to-reserve').textContent = formatAmount(me.available);
        if (succeeded(list)) $('#authorizations').innerHTML = authorizationsHtml(list.data.authorizations);
      },
    ).catch(() => {});
  }

  const form = $('#authorize-form');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = formValues(form);
    const amount = parseAmount(values['authorize-amount']);
    if (amount === null) {
      setFormBanner(form, 'danger', 'authorize-error', `<strong>Check the amount.</strong> ${invalidAmountMessage()}`);
      return;
    }
    const handle = values['authorize-handle'].trim();
    const body = { to_handle: handle, amount, note: values['authorize-note'], visibility: values['authorize-visibility'] };
    const button = $('[data-testid="authorize-submit"]', form);
    setBusy(button, true, 'Placing hold…');
    let result;
    try {
      result = await api('POST', '/authorizations', { body, key: keys('authorize', body) });
    } catch (err) {
      result = null;
    }
    setBusy(button, false);
    const outcome = result ? outcomeOf(result) : 'unknown';
    if (outcome === 'unknown') {
      setFormBanner(form, 'warning', 'authorize-uncertain', UNCERTAIN_MESSAGE);
      return;
    }
    await load();
    if (outcome === 'ok') {
      setFormBanner(form, 'success', '', `<strong>Reserved ${escapeHtml(formatAmount(amount))} for @${escapeHtml(handle)}.</strong> They can collect it until it expires.`);
    } else {
      setFormBanner(form, 'danger', 'authorize-error', refusalMessage(result, { what: 'Hold', handle }));
    }
  });

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action="capture"], [data-action="void"]');
    if (!button) return;
    const id = button.dataset.id;
    const banner = $('#authorization-banner');
    let request;
    if (button.dataset.action === 'capture') {
      const amount = parseAmount($(`[data-testid="authorization-capture-amount-${CSS.escape(id)}"]`).value);
      if (amount === null) {
        setBanner(banner, 'danger', 'authorization-error', `<strong>Check the amount.</strong> ${invalidAmountMessage()}`);
        return;
      }
      const body = { amount };
      request = { verb: 'Capture', busy: 'Capturing…', path: `/authorizations/${id}/capture`, options: { body, key: keys(id, body) } };
    } else {
      request = { verb: 'Release', busy: 'Releasing…', path: `/authorizations/${id}/void`, options: {} };
    }
    setBusy(button, true, request.busy);
    let result;
    try {
      result = await api('POST', encodeURI(request.path), request.options);
    } catch (err) {
      result = null;
    }
    const outcome = result ? outcomeOf(result) : 'unknown';
    if (outcome === 'ok') {
      setBanner(banner, 'success', '', '');
    } else if (outcome === 'unknown') {
      setBanner(banner, 'warning', 'authorization-error', UNCERTAIN_MESSAGE);
    } else {
      setBanner(banner, 'danger', 'authorization-error', refusalMessage(result, { what: request.verb }));
    }
    await load();
  });

  load();
}

// ---------- login and signup ----------

const AUTH_ART = `<aside class="auth-art"><img src="/static/auth.svg" alt=""><p class="auth-art-title">Money between friends, without the awkward part.</p><p class="auth-art-text">Every payment is confirmed exactly once — even when your connection drops.</p></aside>`;

function renderAuthPage(title, heading, sub, fieldsHtml, submitTestid, submitLabel, switchHtml) {
  document.title = `${title} · Pocketful`;
  document.body.className = 'auth-body';
  document.body.innerHTML = `<main class="auth">
<section class="card auth-card"><img class="brand-logo" src="/static/logo.svg" alt="Pocketful"><div><h1 class="auth-title">${heading}</h1><p class="auth-sub">${sub}</p></div>
<form class="form" id="auth-form" novalidate>${fieldsHtml}<div class="form-actions"><button class="btn btn-primary btn-block" type="submit" data-testid="${submitTestid}">${submitLabel}</button></div></form><p class="auth-switch">${switchHtml}</p></section>
${AUTH_ART}
</main>`;
}

function authFormHandler(path, bodyFrom, messageFor) {
  const form = $('#auth-form');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('button[type="submit"]', form);
    setBusy(button, true, 'One moment…');
    let result;
    try {
      result = await api('POST', path, { body: bodyFrom(formValues(form)) });
    } catch (err) {
      result = null;
    }
    if (result && succeeded(result)) {
      localStorage.setItem(TOKEN_KEY, result.data.token);
      window.location.assign('/');
      return;
    }
    setBusy(button, false);
    const message = result ? messageFor(result) : 'We couldn’t reach Pocketful. Check your connection and try again.';
    setFormBanner(form, 'danger', 'auth-error', escapeHtml(message));
  });
}

function loginPage() {
  renderAuthPage('Welcome back', 'Welcome back', 'Log in to your Pocketful wallet.',
    textField('login-email', 'Email', { placeholder: 'you@example.com', type: 'email' })
    + textField('login-password', 'Password', { placeholder: '••••••••', type: 'password' }),
    'login-submit', 'Log in', 'New to Pocketful? <a href="/signup">Create an account</a>');
  authFormHandler('/auth/login',
    (values) => ({ email: values['login-email'].trim(), password: values['login-password'] }),
    (result) => (result.status === 401 ? 'That email and password don’t match an account.' : errorText(result)));
}

function signupPage() {
  renderAuthPage('Create your wallet', 'Create your wallet', 'Pay friends, split bills and reserve money in seconds.',
    textField('signup-display-name', 'Your name', { placeholder: 'How friends know you' })
    + textField('signup-email', 'Email', { placeholder: 'you@example.com', type: 'email' })
    + textField('signup-password', 'Password', { placeholder: '••••••••', type: 'password', hint: 'At least 8 characters.' }),
    'signup-submit', 'Create account', 'Already have an account? <a href="/login">Log in</a>');
  authFormHandler('/auth/signup',
    (values) => ({ email: values['signup-email'].trim(), password: values['signup-password'], display_name: values['signup-display-name'] }),
    (result) => {
      switch (errorCode(result)) {
        case 'email_taken': return 'An account with that email already exists. Log in instead.';
        case 'handle_taken': return 'The handle made from that email is already taken. Try a different email.';
        case 'validation_failed': return 'Use an email like you@example.com and a password of at least 8 characters.';
        default: return errorText(result);
      }
    });
}

// ---------- routing ----------

const SIGNED_IN_PAGES = {
  '/': walletPage,
  '/requests': requestsPage,
  '/split': splitPage,
  '/authorizations': authorizationsPage,
};

async function start() {
  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  if (path === '/login') return loginPage();
  if (path === '/signup') return signupPage();
  if (!token()) return signOut();
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="logout"]')) signOut();
  });
  try {
    currentUser = await loadMe();
  } catch (err) {
    document.body.innerHTML = `<main class="page">${bannerHtml('danger', '', '<strong>We couldn’t load your wallet.</strong> Check your connection and reload the page.')}</main>`;
    return;
  }
  (SIGNED_IN_PAGES[path] || walletPage)();
}

start();
