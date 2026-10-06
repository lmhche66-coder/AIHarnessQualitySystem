"""用真实浏览器打开控制台并逐个视图截图，供人工确认界面。

用法::

    python tools/capture_console.py http://127.0.0.1:8000 .tmp-shots

需要 ``playwright`` 与 Chromium：``pip install playwright && playwright install chromium``。
这是开发期的确认工具，不是评测能力的一部分。
"""

from __future__ import annotations

import sys
from pathlib import Path

VIEWS = ["运行", "结论", "基线", "标注", "回流", "导入"]
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 390, "height": 844},
}


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else ".tmp-shots")
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, viewport in VIEWPORTS.items():
            page = browser.new_page(viewport=viewport)
            page.goto(base, wait_until="load")
            page.wait_for_timeout(800)
            for view in VIEWS:
                tab = page.locator("nav.tabs").get_by_role("button", name=view, exact=True)
                tab.click()
                page.wait_for_timeout(600)
                page.screenshot(path=str(out / f"{name}-{view}.png"))
            page.close()
        browser.close()
    print(f"screenshots written to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
