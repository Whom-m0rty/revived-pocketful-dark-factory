"""Render prototype/*.html at 375 and 1280 and write annotated SVG mockups (screenshot + every data-testid boxed and labelled)."""
import base64, pathlib, sys
from playwright.sync_api import sync_playwright
base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
here = pathlib.Path(__file__).parent
out = here / "mockups"; out.mkdir(exist_ok=True)
JS = """() => [...document.querySelectorAll('[data-testid]')].map(e => {const r = e.getBoundingClientRect();
 return {id: e.getAttribute('data-testid'), x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height}}).filter(b => b.w > 0 && b.h > 0)"""
def esc(t): return t.replace("&", "&amp;").replace("<", "&lt;")
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for width in (375, 1280):
        ctx = br.new_context(viewport={"width": width, "height": 900}, device_scale_factor=1)
        for f in sorted((here / "prototype").glob("*.html")):
            pg = ctx.new_page(); pg.goto(f"{base}/{f.name}"); pg.wait_for_timeout(150)
            png = pg.screenshot(full_page=True); boxes = pg.evaluate(JS)
            h = pg.evaluate("document.documentElement.scrollHeight"); pg.close()
            marks = []
            for b in boxes:
                lw = 6.2 * len(b["id"]) + 8
                lx = min(max(b["x"], 0), width - lw)
                ly = b["y"] - 13 if b["y"] > 14 else b["y"]
                marks.append(f'<rect x="{b["x"]:.1f}" y="{b["y"]:.1f}" width="{b["w"]:.1f}" height="{b["h"]:.1f}" fill="none" stroke="#d6336c" stroke-width="1" stroke-dasharray="3 2"/>'
                             f'<rect x="{lx:.1f}" y="{ly:.1f}" width="{lw:.1f}" height="13" rx="3" fill="#d6336c" opacity=".9"/>'
                             f'<text x="{lx+4:.1f}" y="{ly+10:.1f}" font-family="ui-monospace, Menlo, monospace" font-size="10" fill="#fff">{esc(b["id"])}</text>')
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{h}" viewBox="0 0 {width} {h}">'
                   f'<title>{f.stem} @ {width}px</title><image width="{width}" height="{h}" xlink:href="data:image/png;base64,{base64.b64encode(png).decode()}"/>'
                   f'<g class="testids">{"".join(marks)}</g></svg>')
            (out / f"{f.stem}-{width}.svg").write_text(svg)
        ctx.close()
    br.close()
print(len(list(out.glob("*.svg"))), "mockups")
