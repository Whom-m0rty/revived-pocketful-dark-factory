"""Screenshot pages of a running service at phone and desktop width.

    python shoot.py --base-url http://127.0.0.1:8080 --out DIR --page / --page /login \
        [--width 375 --width 1280] [--wait-ms 800]

Writes DIR/<page-slug>-<width>.png and DIR/shots.json (page, width, file, horizontal
overflow in px, console errors). Horizontal overflow > 0 at 375 px is a layout bug.
"""
import argparse
import json
import pathlib
import re

from playwright.sync_api import sync_playwright


def slug(page):
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", page).strip("-")
    return cleaned or "root"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--page", action="append", default=[])
    parser.add_argument("--width", action="append", type=int, default=[])
    parser.add_argument("--wait-ms", type=int, default=800)
    args = parser.parse_args()
    pages = args.page or ["/"]
    widths = args.width or [375, 1280]
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        for width in widths:
            context = browser.new_context(viewport={"width": width, "height": 900})
            for page_path in pages:
                tab = context.new_page()
                errors = []
                tab.on("console", lambda message, sink=errors: sink.append(message.text) if message.type == "error" else None)
                tab.goto(args.base_url.rstrip("/") + page_path)
                tab.wait_for_timeout(args.wait_ms)
                overflow = tab.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                file = args.out / f"{slug(page_path)}-{width}.png"
                tab.screenshot(path=str(file), full_page=True)
                results.append({"page": page_path, "width": width, "file": file.name,
                                "horizontal_overflow_px": overflow, "console_errors": errors})
                tab.close()
            context.close()
        browser.close()
    (args.out / "shots.json").write_text(json.dumps(results, indent=2))
    for row in results:
        flag = "  OVERFLOW" if row["horizontal_overflow_px"] > 0 else ""
        print(f"{row['file']}: overflow={row['horizontal_overflow_px']}px errors={len(row['console_errors'])}{flag}")


if __name__ == "__main__":
    main()
