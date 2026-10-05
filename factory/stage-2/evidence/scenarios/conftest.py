import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest


@pytest.fixture(scope="session")
def previous_base_url():
    from previous import Previous
    prev = Previous()
    try:
        yield prev.start()
    finally:
        prev.stop()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    pg.set_default_timeout(10000)
    try:
        yield pg
    finally:
        context.close()
