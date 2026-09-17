"""Системные строки в чатах: инцидент 16.09 («Надежда», «Николай», profi3).

После клиентского вопроса площадка вставляет «Робот: Сообщите, если
договоритесь…» — строка становится последней в превью сайдбара, unread
остаётся 0, диалог навсегда выпадает из таргетинга (вопрос про цену молчал
25,5 ч). Фрагменты ниже — живые aria-снапшоты открытых диалогов 16.09.
"""

from __future__ import annotations

import time

from profi.integration.chat import (
    classify_dialog_message,
    classify_dialog_row,
    last_substantive,
    parse_dialog_texts,
)
from profi.main import (
    _chat_llm_plan,
    _chat_target,
    _classify_chat_dialog,
    _matches_logged_tutor,
)
from profi.storage.store import Store

# --- парсер сообщений открытого диалога (живой DOM 16.09) ---

NADIa_MAIN = """
- main:
  - paragraph: Н
  - text: Надежда Не в сети
  - button "Детали заказа":
    - paragraph: Детали заказа
  - text: Вчера Здравствуйте! Готовлю к ЕГЭ по информатике, работаю онлайн - подойдёт и для Нижнего Новгорода. Когда удобно созвониться?)
  - img
  - text: 19:44
  - img
  - text: Надежда Пара вопросов. Какова стоимость индивидуальных занятий?
  - img
  - text: 19:47 Если заказ подходит, обменяйтесь контактами с клиентом — это бесплатно.
  - img
  - text: 19:47 Сегодня стоимость на пробном обсуждаем уже)
  - img
  - text: 21:15
  - img
  - text: Сообщите, если договоритесь работать с клиентом.
  - img
  - text: 21:15
  - button "Отправить мой номер"
  - button "Договорились"
  - button "Отправить в архив"
  - button
  - textbox "Сообщение"
"""

NIKOLAY_MAIN = """
- main:
  - text: Николай Не в сети
  - text: Вчера Здравствуйте! Помогу Максиму подготовиться к ЕГЭ по информатике на 70+. Когда вам удобно созвониться и обсудить график?
  - text: 20:03
  - text: Сегодня
  - text: Николай Здравствуйте, скажите пожалуйста, когда возможно провести пробное занятие?
  - text: 19:14
  - text: Если заказ подходит, обменяйтесь контактами с клиентом — это бесплатно.
  - text: 19:14
  - text: Будет удобно завтра после 16?
  - text: 21:15
  - text: Сообщите, если договоритесь работать с клиентом.
  - text: 21:15
"""

LIYA_MAIN = """
- main:
  - text: Лия Не в сети
  - text: 5 сентября Здравствуйте! Помогу с подготовкой к МЦКО по информатике.
  - text: 17:25
  - text: Стоимость занятия 1300 руб. / 60 мин.
  - text: 17:25
  - text: Лия Здравствуйте! Скажите пожалуйста могу ли я связаться с вами для уточнения некоторых деталей?
  - text: 17:52 Если заказ подходит, обменяйтесь контактами с клиентом — это бесплатно.
  - text: 17:52 13 сентября Лия Добрый день! Скажите пожалуйста, удобно отправить контакт для уточнения деталей?
  - text: 17:36 Вчера Да, конечно, Лия. Номер можно отправить кнопкой прямо в чате на платформе, там это бесплатно.
  - text: 8:11
  - text: Сообщите, если договоритесь работать с клиентом.
  - text: 8:11
"""


def test_nadezhda_last_substantive_is_our_manual_answer():
    msgs = parse_dialog_texts(NADIa_MAIN, "Надежда")
    sender, text = last_substantive(msgs)
    assert sender == "ours"
    assert text.startswith("стоимость на пробном обсуждаем")


def test_nikolay_client_question_then_our_answer():
    msgs = parse_dialog_texts(NIKOLAY_MAIN, "Николай")
    assert ("client", "Здравствуйте, скажите пожалуйста, когда возможно провести пробное занятие?") in msgs
    sender, text = last_substantive(msgs)
    assert sender == "ours"
    assert text.startswith("Будет удобно завтра")


def test_liya_last_substantive_is_ours_with_vocative_comma():
    # «Да, конечно, Лия. Номер…» — начинается НЕ с имени; а «Лия, привет!»
    # начиналось бы с имени+запятая → всё равно наше
    msgs = parse_dialog_texts(LIYA_MAIN, "Лия")
    sender, text = last_substantive(msgs)
    assert sender == "ours"
    assert text.startswith("Да, конечно, Лия")


def test_client_message_with_merged_time_prefix():
    # «17:52 13 сентября Лия Добрый день!…» — время+дата склеены с текстом
    got = classify_dialog_message(
        "17:52 13 сентября Лия Добрый день! Скажите пожалуйста, удобно?", "Лия"
    )
    assert got == ("client", "Добрый день! Скажите пожалуйста, удобно?")


def test_our_vocative_comma_not_client():
    got = classify_dialog_message("Лия, привет! Когда удобно?", "Лия")
    assert got[0] == "ours"


def test_system_phrase_with_time_prefix():
    got = classify_dialog_message(
        "19:47 Если заказ подходит, обменяйтесь контактами с клиентом — это бесплатно.",
        "Надежда",
    )
    assert got[0] == "system"


def test_pure_time_and_date_lines_skipped():
    assert classify_dialog_message("21:15", "Лия") is None
    assert classify_dialog_message("Вчера", "Лия") is None
    assert classify_dialog_message("5 сентября", "Лия") is None


# --- классификатор целей: системная строка больше не глушит диалог ---


def _store(tmp_path, events, client_name="Клиент", order_id="900"):
    s = Store(str(tmp_path / "t.db"))
    for sender, text in events:
        s.log_chat(order_id, client_name, sender, text)
    return s


def _system_row(name="Клиент"):
    return classify_dialog_row(f"{name} Робот: Сообщите, если договоритесь работать с клиентом. 0")


def test_system_row_without_history_goes_to_inspection(tmp_path):
    store = _store(tmp_path, [])
    action, reason = _classify_chat_dialog(store, _system_row())
    assert action == "inspect"
    assert "истории нет" in reason


def test_system_row_with_unanswered_client_event_is_target(tmp_path):
    # клиент спросил, системная строка придавила, мы не ответили — ЦЕЛЬ
    store = _store(tmp_path, [("client", "Какова стоимость занятий?")])
    action, _ = _classify_chat_dialog(store, _system_row())
    assert action == "target"


def test_system_row_with_stale_client_event_skips(tmp_path):
    store = _store(tmp_path, [("client", "Какова стоимость занятий?")])
    store.conn.execute(
        "UPDATE chat_log SET created_at = ?", (int(time.time()) - 72 * 3600,)
    )
    store.conn.commit()
    action, reason = _classify_chat_dialog(store, _system_row())
    assert action == "skip"
    assert "старше" in reason


def test_system_row_right_after_our_answer_skips_then_reinspects(tmp_path):
    store = _store(tmp_path, [("client", "вопрос"), ("tutor", "ответ")])
    action, reason = _classify_chat_dialog(store, _system_row())
    assert action == "skip"  # только что ответили — не дёргаем диалог
    store.conn.execute("UPDATE chat_log SET created_at = ?", (int(time.time()) - 31 * 60,))
    store.conn.commit()
    action, reason = _classify_chat_dialog(store, _system_row())
    assert action == "inspect"  # через 30 мин — перекрытая проверка


def test_system_row_llm_json_error_is_retryable(tmp_path):
    store = _store(
        tmp_path,
        [("client", "вопрос"), ("system", "LLM_JSON_ERROR: Unterminated string")],
    )
    action, _ = _classify_chat_dialog(store, _system_row())
    assert action == "target"


def test_client_row_targeting_unchanged(tmp_path):
    # регресс: прежний путь who_last=client работает как раньше
    store = _store(tmp_path, [("client", "Ты в каком городе")], client_name="Усмонали")
    d = classify_dialog_row("Усмонали Максим Ты в каком городе 0")
    assert _chat_target(store, d)
    action, _ = _classify_chat_dialog(store, d)
    assert action == "target"
    d_ours = classify_dialog_row("Усмонали Вы: ответ 0")
    assert _classify_chat_dialog(store, d_ours)[0] == "skip"


def test_matches_logged_tutor_guard(tmp_path):
    store = _store(tmp_path, [("tutor", "Лия, привет! Когда удобно?")], client_name="Лия")
    assert _matches_logged_tutor(store, "Лия", "Лия, привет! Когда удобно? — точно наше")
    assert not _matches_logged_tutor(store, "Лия", "Совсем другой текст сообщения")


# --- наблюдаемость: chat_seen ---

def test_chat_seen_upsert_keeps_latest_state(tmp_path):
    store = _store(tmp_path, [], client_name="Алиса")
    store.upsert_chat_seen("Алиса", "", "client", 2, "Привет, когда удобно?")
    store.upsert_chat_seen("Алиса", "", "ours", 0, "Вы: ответ")
    rows = store.conn.execute("SELECT * FROM chat_seen").fetchall()
    assert len(rows) == 1
    assert rows[0]["who_last"] == "ours"
    assert rows[0]["unread"] == 0


def test_chat_seen_separate_order_ids(tmp_path):
    store = _store(tmp_path, [], client_name="Елена")
    store.upsert_chat_seen("Елена", "111", "ours", 0, "a")
    store.upsert_chat_seen("Елена", "222", "client", 1, "b")
    n = store.conn.execute("SELECT COUNT(*) FROM chat_seen").fetchone()[0]
    assert n == 2


# --- план LLM: ретрай с бОльшим max_tokens при битом JSON ---

def test_chat_llm_plan_retries_primary_with_bigger_budget():
    plan = _chat_llm_plan(["glm-5.3-flash", "glm-5.3"])
    assert plan[0] == ("glm-5.3-flash", 1500)
    assert plan[1] == ("glm-5.3-flash", 3000)
    assert plan[2] == ("glm-5.3", 3000)


def test_chat_llm_plan_single_model():
    assert _chat_llm_plan(["m"]) == [("m", 1500), ("m", 3000)]
    assert _chat_llm_plan([]) == []
