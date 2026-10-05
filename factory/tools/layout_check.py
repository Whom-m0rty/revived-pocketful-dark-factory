"""Find layout defects in a running web UI with headless Chromium.

    python layout_check.py --base-url http://127.0.0.1:8080 --page / --page /login \
        [--width 375 --width 1280] [--storage KEY=VALUE] [--cookie NAME=VALUE] \
        [--out DIR] [--wait-ms 800]

For every page and width it reports, with a CSS path for each element:
  overflow      the page scrolls sideways
  offscreen     an element sticks out past the viewport's right edge
  overlap       two visible text or control boxes overlap
  clipped       text is cut off inside its box
  uneven-row    sibling cards side by side end at different heights
  ragged-row    sibling cards side by side start at different heights
  small-target  a control smaller than 40x40 px at phone width
  small-text    body text under 14 px at phone width
  console       a console error
It also saves a full-page screenshot per page and width in DIR (default ./layout-check).
Prints one line per finding and a summary; exits 0 (read the summary).
"""
import argparse
import json
import pathlib
import re

from playwright.sync_api import sync_playwright

PROBE = r"""
() => {
  const path = (el) => {
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 4) {
      let part = el.tagName.toLowerCase();
      if (el.id) { parts.unshift(part + '#' + el.id); break; }
      const tid = el.getAttribute('data-testid');
      if (tid) { parts.unshift(part + '[data-testid="' + tid + '"]'); break; }
      if (el.classList.length) part += '.' + [...el.classList].slice(0, 2).join('.');
      parts.unshift(part);
      el = el.parentElement;
    }
    return parts.join(' > ');
  };
  const visible = (el) => {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0 && r.width > 0 && r.height > 0;
  };
  const vw = document.documentElement.clientWidth;
  const out = [];
  const all = [...document.body.querySelectorAll('*')].filter(visible);

  if (document.documentElement.scrollWidth > vw + 1)
    out.push({kind: 'overflow', detail: `page is ${document.documentElement.scrollWidth}px wide in a ${vw}px viewport`});

  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 && getComputedStyle(el).position !== 'fixed')
      out.push({kind: 'offscreen', el: path(el), detail: `right edge at ${Math.round(r.right)}px`});
  }

  const leaves = all.filter(el => {
    const tag = el.tagName;
    if (['INPUT', 'BUTTON', 'SELECT', 'TEXTAREA', 'A', 'IMG', 'svg'].includes(tag)) return true;
    return [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  });
  for (let i = 0; i < leaves.length; i++) {
    const a = leaves[i].getBoundingClientRect();
    for (let j = i + 1; j < leaves.length; j++) {
      const b = leaves[j];
      if (leaves[i].contains(b) || b.contains(leaves[i])) continue;
      const c = b.getBoundingClientRect();
      const w = Math.min(a.right, c.right) - Math.max(a.left, c.left);
      const h = Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top);
      if (w > 4 && h > 4) out.push({kind: 'overlap', el: path(leaves[i]), other: path(b), detail: `${Math.round(w)}x${Math.round(h)}px`});
    }
  }

  for (const el of leaves) {
    const s = getComputedStyle(el);
    if ((s.overflow === 'hidden' || s.overflowX === 'hidden' || s.textOverflow === 'ellipsis') &&
        el.scrollWidth > el.clientWidth + 1 && s.textOverflow !== 'ellipsis')
      out.push({kind: 'clipped', el: path(el), detail: 'text wider than its box'});
  }

  const parents = new Set(all.map(el => el.parentElement).filter(Boolean));
  for (const p of parents) {
    const kids = [...p.children].filter(visible).filter(k => k.getBoundingClientRect().height > 60);
    const rows = {};
    for (const k of kids) {
      const top = Math.round(k.getBoundingClientRect().top / 8);
      (rows[top] = rows[top] || []).push(k);
    }
    const byRow = [];
    for (const k of kids) {
      const r = k.getBoundingClientRect();
      const row = byRow.find(g => Math.abs(g.top - r.top) < 24 || (r.top < g.bottom && r.bottom > g.top && r.left >= g.right - 1));
      if (row) { row.items.push(k); row.bottom = Math.max(row.bottom, r.bottom); row.right = Math.max(row.right, r.right); }
      else byRow.push({top: r.top, bottom: r.bottom, right: r.right, items: [k]});
    }
    for (const g of byRow) {
      if (g.items.length < 2) continue;
      const rects = g.items.map(k => k.getBoundingClientRect());
      const sideBySide = rects.every((r, i) => i === 0 || r.left >= rects[i - 1].right - 1);
      if (!sideBySide) continue;
      const tops = rects.map(r => r.top), bottoms = rects.map(r => r.bottom);
      if (Math.max(...tops) - Math.min(...tops) > 2)
        out.push({kind: 'ragged-row', el: path(p), detail: `side-by-side children start ${Math.round(Math.max(...tops) - Math.min(...tops))}px apart`});
      if (Math.max(...bottoms) - Math.min(...bottoms) > 8)
        out.push({kind: 'uneven-row', el: path(p), detail: `side-by-side children end ${Math.round(Math.max(...bottoms) - Math.min(...bottoms))}px apart (${g.items.map(path).join(' | ')})`});
    }
  }

  if (vw <= 480) {
    for (const el of all) {
      const tag = el.tagName;
      const r = el.getBoundingClientRect();
      if (['BUTTON', 'A', 'SELECT'].includes(tag) || (tag === 'INPUT' && el.type !== 'hidden'))
        if (r.height < 40 || (r.width < 40 && tag !== 'A'))
          out.push({kind: 'small-target', el: path(el), detail: `${Math.round(r.width)}x${Math.round(r.height)}px`});
    }
    for (const el of leaves) {
      const size = parseFloat(getComputedStyle(el).fontSize);
      if (size < 14 && el.textContent.trim().length > 20)
        out.push({kind: 'small-text', el: path(el), detail: `${size}px`});
    }
  }
  return out;
}
"""


def slug(page):
    return re.sub(r"[^a-zA-Z0-9]+", "-", page).strip("-") or "root"


def pairs(values):
    result = {}
    for value in values:
        key, _, rest = value.partition("=")
        result[key] = rest
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--page", action="append", default=[])
    parser.add_argument("--width", action="append", type=int, default=[])
    parser.add_argument("--storage", action="append", default=[], help="localStorage KEY=VALUE set before load")
    parser.add_argument("--cookie", action="append", default=[], help="cookie NAME=VALUE")
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("layout-check"))
    parser.add_argument("--wait-ms", type=int, default=800)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    storage = pairs(args.storage)
    cookies = pairs(args.cookie)
    args.out.mkdir(parents=True, exist_ok=True)
    findings = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        for width in args.width or [375, 1280]:
            context = browser.new_context(viewport={"width": width, "height": 900})
            if storage:
                script = "(() => { const items = %s; for (const k in items) localStorage.setItem(k, items[k]); })();" % json.dumps(storage)
                context.add_init_script(script)
            if cookies:
                context.add_cookies([{"name": k, "value": v, "url": base} for k, v in cookies.items()])
            for page_path in args.page or ["/"]:
                tab = context.new_page()
                errors = []
                tab.on("console", lambda message, sink=errors: sink.append(message.text) if message.type == "error" else None)
                tab.goto(base + page_path)
                tab.wait_for_timeout(args.wait_ms)
                for item in tab.evaluate(PROBE):
                    item.update(page=page_path, width=width)
                    findings.append(item)
                for text in errors:
                    findings.append({"kind": "console", "page": page_path, "width": width, "detail": text[:200]})
                tab.screenshot(path=str(args.out / f"{slug(page_path)}-{width}.png"), full_page=True)
                tab.close()
            context.close()
        browser.close()
    unique = []
    seen = set()
    for item in findings:
        key = (item["kind"], item["page"], item["width"], item.get("el"), item.get("other"))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    (args.out / "layout-findings.json").write_text(json.dumps(unique, indent=2))
    for item in unique:
        where = item.get("el", "")
        if item.get("other"):
            where += " ~ " + item["other"]
        print(f"{item['kind']:13s} {item['page']} @{item['width']}: {where} — {item['detail']}")
    counts = {}
    for item in unique:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    print("LAYOUT:", "clean" if not unique else ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
