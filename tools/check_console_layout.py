"""用真实浏览器检查控制台的布局不变量与运行时报错。

看不了截图时的替代手段：断言每个视图都能渲染、没有横向溢出、文本没有撑破容器，
并且浏览器控制台没有报错。

用法::

    python tools/check_console_layout.py http://127.0.0.1:8000
"""

from __future__ import annotations

import sys
from typing import Any

VIEWS = ["运行", "结论", "基线", "标注", "回流", "导入"]
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 390, "height": 844},
}

OVERFLOW_PROBE = """
() => Array.from(document.querySelectorAll('.detail *'))
  .filter((element) => element.children.length === 0)
  .filter((element) => element.scrollWidth > element.clientWidth + 2)
  .slice(0, 5)
  .map((element) => ({
    klass: element.className || element.tagName,
    text: (element.textContent || '').slice(0, 40),
    scrollWidth: element.scrollWidth,
    clientWidth: element.clientWidth,
    box: (() => { const r = element.getBoundingClientRect();
      return { x: Math.round(r.x), w: Math.round(r.width) }; })(),
    chain: (() => {
      const parts = [];
      let node = element;
      for (let i = 0; i < 5 && node; i += 1) {
        parts.push(node.tagName + (node.className ? '.' + String(node.className).split(' ').join('.') : ''));
        node = node.parentElement;
      }
      return parts.join(' < ');
    })(),
  }))
"""


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

    from playwright.sync_api import sync_playwright

    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, viewport in VIEWPORTS.items():
            page = browser.new_page(viewport=viewport)
            errors: list[str] = []
            page.on(
                "console",
                lambda message: errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(base, wait_until="load")
            page.wait_for_timeout(800)

            for view in VIEWS:
                tab = page.locator("nav.tabs").get_by_role("button", name=view, exact=True)
                if tab.count() == 0:
                    failures.append(f"[{name}/{view}] tab button missing")
                    continue
                tab.click()
                page.wait_for_timeout(600)

                text = page.inner_text("main")
                if not text.strip():
                    failures.append(f"[{name}/{view}] main content is empty")

                overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth - window.innerWidth"
                )
                if overflow > 2:
                    failures.append(f"[{name}/{view}] horizontal overflow {overflow}px")

                offenders: list[dict[str, Any]] = page.evaluate(OVERFLOW_PROBE)
                for item in offenders:
                    failures.append(
                        f"[{name}/{view}] text overflows in {item['klass']}: "
                        f"{item['scrollWidth']}>{item['clientWidth']} box={item['box']} "
                        f"{item['text']!r} | {item['chain']}"
                    )

            for message in errors:
                failures.append(f"[{name}] browser console error: {message}")
            page.close()
        browser.close()

    if failures:
        print(f"FAILED: {len(failures)} issue(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("layout checks passed for all views and viewports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
