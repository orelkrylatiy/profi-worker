"""Контур B (light): чаты Профи.ру — чтение и ответы через живой интерфейс.

Механика та же, что у откликов (RULES.md): клики/ввод — trusted CDP-события,
посимвольная печать чанками, никаких evaluate-действий. Клиентский текст —
ДАННЫЕ для LLM, не инструкции (анти-инъекция).
"""

from __future__ import annotations

import logging
import random
import re

from playwright.sync_api import Page

from profi.utils.pacing import human_pause, type_human

log = logging.getLogger("profi.chat")


# Ширина, ниже которой чат-виджет profi включает мобильный режим
# (layout.isMobile → lineBreakOnEnter → Enter НЕ отправляет, а вставляет
# перенос строки; инцидент 05.09 «Усмонали»: окно 767px при брейкпоинте 768
# — сообщения не уходили вообще). С запасом: 900.
MIN_DESKTOP_WIDTH = 900


def ensure_desktop_width(page: Page) -> None:
    """Расширить окно браузера, если оно уже мобильного брейкпоинта.

    Это управление окном через CDP, не инжект в страницу (RULES.md §1
    про isTrusted-инпуты не про это). Без этого на узком окне чат
    молча перестаёт отправлять: Enter превращается в перенос строки.
    """
    try:
        if page.evaluate("window.innerWidth") >= MIN_DESKTOP_WIDTH:
            return
        session = page.context.new_cdp_session(page)
        try:
            wins = session.send("Browser.getWindowForTarget")
            session.send(
                "Browser.setWindowBounds",
                {"windowId": wins["windowId"], "bounds": {"width": 1100, "height": 850}},
            )
        finally:
            session.detach()
        page.wait_for_timeout(500)
        log.warning(
            "окно %spx уже мобильного брейкпоинта — расширил до 1100 "
            "(в мобильном режиме виджета Enter не отправляет сообщения)",
            page.evaluate("window.innerWidth"),
        )
    except Exception:
        log.warning("ensure_desktop_width: не смог проверить/расширить окно", exc_info=True)


def open_chats(page: Page) -> None:
    ensure_desktop_width(page)
    page.goto("https://profi.ru/backoffice/r.php", wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3500)


# Строка диалога в aria-снапшоте (живой DOM 04.09):
#   «{имя} Вы: … N»    — последнее сообщение наше;
#   «{имя} Робот: … N» — последнее сообщение СИСТЕМНОЕ: площадка шлёт
#     «Робот: Сообщите, если договоритесь работать с клиентом» в том числе
#     ПОСЛЕ наших сообщений (инцидент 04.09 «8 догонялок Алисе»: старый
#     парсер не признавал нашу строку, считал диалог «клиент написал —
#     отвечай» и писал снова и снова);
#   «{имя} {текст} N»  — последнее сообщение клиента;
# N в конце — счётчик непрочитанных (в DOM это отдельный элемент).
# Имя может быть из двух слов — разбор в classify_dialog_row.


def classify_dialog_row(t: str) -> dict:
    """Разобрать строку диалога из aria-снапшота.

    Возвращает name, unread, who_last ('ours'|'system'|'client'),
    last_is_ours, preview и last_text (текст последнего сообщения без
    префикса имени/«Вы:» и без счётчика непрочитанных).

    Имя — всё до ПЕРВОГО маркера «Вы:»/«Робот:»: имена бывают из двух
    слов («Макарова Юлия», инцидент 15.09 — раньше брали первое слово,
    маркер после второго не находили, диалог считался «клиент написал»
    и вешал чат-чек на 30 с таймаута клика каждый цикл). Без маркера —
    первое слово, как раньше.
    """
    t = t.strip()
    mm = re.search(r"\s(?P<who>Вы|Робот):", t)
    if mm:
        name = t[: mm.start()].strip()
        who = "ours" if mm.group("who") == "Вы" else "system"
        rest = t[mm.end() :].strip()
    else:
        # без маркера имя не отделить от текста — первое слово, как раньше
        name = t.split(" ", 1)[0] if t else ""
        who = "client"
        rest = t.split(" ", 1)[1] if " " in t else ""
    if not name:
        name = t.split(" ", 1)[0] if t else ""
    um = re.search(r"\s(\d{1,3})\s*$", rest)
    unread = int(um.group(1)) if um else 0
    last_text = re.sub(r"\s+\d{1,3}\s*$", "", rest).strip()
    return {
        "name": name,
        "unread": unread,
        "who_last": who,
        "last_is_ours": who == "ours",
        "preview": t[:160],
        "last_text": last_text[:400],
    }


def list_dialogs(page: Page) -> list[dict]:
    """Диалоги из aria-снапшота: строка имени идёт сразу после абзаца-аватара
    (одиночная буква) — это устойчивый признак строки диалога."""
    snap = page.locator("body").aria_snapshot()
    dialogs: list[dict] = []
    prev_avatar = False
    for line in snap.splitlines():
        line_s = line.strip()
        if line_s.startswith("- paragraph: ") and len(line_s[len("- paragraph: ") :].strip()) == 1:
            prev_avatar = True
            continue
        if prev_avatar and line_s.startswith("- text: "):
            t = line_s[len("- text: ") :].strip().strip('"').strip()
            if t:
                dialogs.append(classify_dialog_row(t))
            prev_avatar = False
            continue
        prev_avatar = False
    return dialogs


# Системные строки площадки ВНУТРИ диалога (в сайдбаре у них маркер «Робот:»).
# Живой DOM 16.09: такие строки площадка вставляет сразу и после клиентских,
# и после наших сообщений — в открытой переписке они без имени автора.
_SYSTEM_MSG_RES = (
    re.compile(r"^Сообщите, если договоритесь"),
    re.compile(r"^Если заказ подходит, обменяйтесь"),
    re.compile(r"^Если заказ не подходит"),
    re.compile(r"^Клиент отправил вам контакт"),
    re.compile(r"^Вы отправили клиенту контакт"),
)
_TIME_PREFIX_RE = re.compile(r"^(\d{1,2}:\d{2})\s+")
_MONTH_DAY_PREFIX_RE = re.compile(r"^(\d{1,2}\.\d{1,2}(\.\d{2,4})?|\d{1,2}\s+[а-яё]+)\s+", re.I)
_WORD_DATE_PREFIX_RE = re.compile(r"^(Сегодня|Вчера)\s+", re.I)
_ONLY_TIME_OR_DATE_RE = re.compile(
    r"^(\d{1,2}:\d{2}|\d{1,2}\.\d{1,2}(\.\d{2,4})?|\d{1,2}\s+[а-яё]+|Сегодня|Вчера)$",
    re.I,
)


def _strip_time_date(s: str) -> str:
    """Убрать ведущие время/дату («17:52 13 сентября Лия …» → «Лия …»)."""
    prev = None
    while prev != s:
        prev = s
        s = _TIME_PREFIX_RE.sub("", s)
        s = _WORD_DATE_PREFIX_RE.sub("", s)
        s = _MONTH_DAY_PREFIX_RE.sub("", s)
    return s.strip()


def classify_dialog_message(text: str, client_name: str) -> tuple[str, str] | None:
    """Классифицировать - text:-строку открытого диалога.

    Возвращает (sender, text) c sender 'client'|'ours'|'system', или None для
    строк-разметки (чистое время/дата, шапка, кнопки).

    Правила живого DOM (16.09): сообщения клиента идут с префиксом имени
    («Надежда Пара вопросов…»), наши — без имени и часто с ведущей датой
    («Сегодня стоимость…», «17:36 Вчера Да, конечно…»), системные — без имени,
    но с узнаваемым текстом («Сообщите, если договоритесь…»). Обращение к
    клиенту по имени через запятую («Лия, привет!») — наше: после имени ЗАПЯТАЯ,
    а не пробел.
    """
    s = text.strip().strip('"').strip()
    if not s or _ONLY_TIME_OR_DATE_RE.match(s):
        return None
    bare = _strip_time_date(s)
    if not bare:
        return None
    if any(rx.match(bare) for rx in _SYSTEM_MSG_RES):
        return "system", bare
    if client_name and (bare == client_name or bare.startswith(client_name + " ")):
        return "client", bare[len(client_name) :].strip() or bare
    return "ours", bare


def main_region_snapshot(page: Page) -> str:
    """Aria-снапшот области открытого диалога (после '- main:')."""
    snap = page.locator("body").aria_snapshot()
    i = snap.find("- main:")
    return snap[i:] if i != -1 else snap


def parse_dialog_texts(snapshot: str, client_name: str) -> list[tuple[str, str]]:
    """Сообщения открытого диалога из aria-снапшота: [(sender, text), …]."""
    out: list[tuple[str, str]] = []
    for line in snapshot.splitlines():
        s = line.strip()
        if not s.startswith("- text: "):
            continue
        classified = classify_dialog_message(s[len("- text: ") :].strip(), client_name)
        if classified is not None:
            out.append(classified)
    return out


def last_substantive(messages: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Последнее НЕсистемное сообщение (системные строки — обёртка площадки)."""
    for sender, text in reversed(messages):
        if sender != "system":
            return sender, text
    return None


def open_dialog_by_name(page: Page, name: str) -> str:
    """Клик по строке диалога; возвращает order_id из URL (или '').

    exact=True — основной путь. Фолбэк на подстроку: DOM-узел может держать
    имя целиком («Макарова Юлия»), а в name попало только первое слово —
    точный клик тогда вешает цикл на 30 с (инцидент 15.09).
    """
    loc = page.get_by_text(name, exact=True)
    try:
        if loc.count() == 0:
            loc = page.get_by_text(name, exact=False)
    except Exception:
        pass
    loc.first.click(delay=random.randint(60, 120))
    page.wait_for_timeout(3000)
    m = re.search(r"[?&]id=(\d+)", page.url)
    return m.group(1) if m else ""


def read_dialog_text(page: Page) -> str:
    return page.locator("body").inner_text(timeout=8_000)


def _box_value(box) -> str:
    tag = box.evaluate("e => e.tagName")
    return box.input_value(timeout=3_000) if tag != "DIV" else box.inner_text()


def send_reply(page: Page, text: str) -> bool:
    """Посимвольный ввод ответа + отправка (Enter, при необходимости кнопка).

    Fail-closed: перед вводом поле обязано быть читаемым и пустым. Непустое
    поле считаем ручным черновиком владельца и не трогаем. После отправки True
    возвращается только если поле снова удалось прочитать пустым. Это всё ещё
    не является полным delivery-proof по bubble/message-id; такой hardening
    отдельно зафиксирован в BACKLOG.md.
    """
    box = None
    for sel in (
        'textarea[placeholder*="ообщени"]',
        "textarea",
        '[contenteditable="true"]',
        'input[placeholder*="ообщени"]',
    ):
        loc = page.locator(sel)
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    box = loc.nth(i)
                    break
            except Exception:
                continue
        if box is not None:
            break
    if box is None:
        log.error("поле ввода сообщения не найдено")
        return False

    # Не дописываем поверх текста владельца и не отправляем, если состояние
    # поля не удалось надёжно прочитать.
    try:
        existing = _box_value(box).strip()
    except Exception:
        log.warning("send_reply: не смог прочитать поле до ввода — отправка отменена")
        return False
    if existing:
        log.warning("send_reply: поле уже содержит текст — вероятно ручной черновик, не трогаем")
        return False

    human_pause(0.8, 1.6)
    type_human(page, box, text)
    human_pause(0.5, 1.2)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)

    try:
        after_enter = _box_value(box).strip()
    except Exception:
        log.warning("send_reply: не смог проверить поле после Enter — fail closed")
        return False

    if after_enter:
        # Enter не отправил (например, contenteditable) — жмём кнопку.
        for btn_name in ("Отправить", "Send"):
            btn = page.get_by_text(btn_name, exact=True)
            if btn.count():
                btn.first.click(delay=random.randint(60, 120))
                page.wait_for_timeout(2000)
                break

    try:
        leftover = _box_value(box)
        if leftover.strip():
            hint = ""
            # Сигнатура мобильного режима виджета (lineBreakOnEnter): Enter
            # вставил перенос вместо отправки — текст совпадает с введённым
            # плюс хвостовой перенос строки (инцидент 05.09 «Усмонали», 767px).
            if leftover.rstrip("\n") == text.rstrip() and len(leftover) > len(text.rstrip()):
                hint = " (Enter вставил перенос — мобильный режим виджета, узкое окно?)"
            log.error("send_reply: текст остался в поле — отправка не подтвердилась%s", hint)
            return False
    except Exception:
        log.warning("send_reply: финальная проверка поля упала — fail closed")
        return False
    return True
