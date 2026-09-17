"""Read-only пробник: как выглядят строки диалогов в aria-снапшоте.

Печатает сырые строки сайдбара чатов (r.php) + для диалогов с системной
строкой («Робот: …») открывает диалог и печатает хвост переписки, чтобы
понять, как определить последнее СОДЕРЖАТЕЛЬНОЕ сообщение (клиент vs мы).

Никаких действий, кроме клика по строке диалога (как делает chat-auto).
  PYTHONPATH=src PYTHONUTF8=1 python scripts/diag/probe_chat_system_rows.py <port> [open]
"""
from __future__ import annotations

import re
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "9224"
    do_open = "open" in sys.argv[2:]
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=10_000)
    page = browser.contexts[0].new_page()
    try:
        page.goto(
            "https://profi.ru/backoffice/r.php", wait_until="domcontentloaded", timeout=45_000
        )
        page.wait_for_timeout(4_000)
        snap = page.locator("body").aria_snapshot()
        lines = [ln.rstrip() for ln in snap.splitlines()]
        print("=== RAW SIDEBAR LINES (после avatar-paragraph) ===")
        prev_avatar = False
        rows: list[str] = []
        for ln in lines:
            s = ln.strip()
            if s.startswith("- paragraph: ") and len(s[len("- paragraph: ") :].strip()) == 1:
                prev_avatar = True
                continue
            if prev_avatar and s.startswith("- text: "):
                t = s[len("- text: ") :].strip().strip('"').strip()
                if t:
                    rows.append(t)
                    print(repr(t))
                prev_avatar = False
                continue
            prev_avatar = False
        print(f"\nвсего строк диалогов: {len(rows)}")

        if do_open:
            sys_rows = [r for r in rows if re.search(r"\sРобот:", r)][:2]
            client_rows = [r for r in rows if not re.search(r"\s(Вы|Робот):", r)][:1]
            for r in sys_rows + client_rows:
                name = re.split(r"\s(Вы|Робот):", r)[0].strip() or r.split(" ", 1)[0]
                print(f"\n=== ОТКРЫВАЮ ДИАЛОГ: {name!r} (строка: {r[:80]!r}) ===")
                loc = page.get_by_text(name, exact=True)
                if loc.count() == 0:
                    loc = page.get_by_text(name, exact=False)
                loc.first.click(delay=90)
                page.wait_for_timeout(3_000)
                print("url:", page.url)
                body = page.locator("body").inner_text(timeout=8_000)
                print("--- хвост переписки (последние 1800 симв.) ---")
                print(body[-1800:])
        return 0
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
