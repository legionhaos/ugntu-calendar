"""
Генератор подписного календаря (.ics) для iPhone / Google Calendar.

Часовой пояс: Asia/Yekaterinburg (UTC+5).
Длительность пары: 1.5 часа (берётся из данных парсера).
VALARM: напоминание за 15 минут до каждого события.
UID: стабильные, на базе даты + названия (md5).

Использование:
    from ics_generator import build_ics, save_ics
    from schedule_parser import fetch_all_lessons, inject_work_blocks

    lessons = inject_work_blocks(fetch_all_lessons())
    ics_bytes = build_ics(lessons)
    save_ics(ics_bytes, "calendar.ics")
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from hashlib import md5
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event

logger = logging.getLogger(__name__)

LESSON_TZ = ZoneInfo("Asia/Yekaterinburg")
ALARM_BEFORE = timedelta(minutes=15)
CALENDAR_NAME = "Расписание БЦШ02-26-02"
PRODID = "-//BCSH02-26-02 Schedule//rusoil.net parser//RU"
REFRESH_INTERVAL = "PT6H"
UID_DOMAIN = "rusoil-schedule"


def _parse_dt(date_str: str, time_str: str) -> datetime:
    """Парсит 'dd.mm.YYYY' + 'HH:MM' → datetime с tzinfo=Asia/Yekaterinburg."""
    naive = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    return naive.replace(tzinfo=LESSON_TZ)


def _stable_uid(date_str: str, subject: str, time_str: str) -> str:
    """
    Стабильный UID события: md5(дата + предмет + время).

    Не меняется между запусками, пока совпадают дата, предмет и время.
    """
    key = f"{date_str}|{subject}|{time_str}"
    return f"{md5(key.encode('utf-8')).hexdigest()}@{UID_DOMAIN}"


def _make_alarm() -> Alarm:
    """Создаёт VALARM — напоминание за 15 минут."""
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", "Напоминание")
    alarm.add("trigger", -ALARM_BEFORE)
    return alarm


def build_ics(
    lessons: list[dict[str, Any]],
    calendar_name: str = CALENDAR_NAME,
) -> bytes:
    """
    Собирает полный .ics из списка пар/блоков.

    Каждое событие содержит:
    - DTSTART / DTEND в Asia/Yekaterinburg
    - Стабильный UID (md5 от даты+предмет+время)
    - VALARM за 15 минут
    - SUMMARY, DESCRIPTION, LOCATION
    """
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("X-WR-CALNAME", calendar_name)
    cal.add("X-WR-TIMEZONE", "Asia/Yekaterinburg")
    cal.add("X-PUBLISHED-TTL", REFRESH_INTERVAL)

    work_blocks_count = 0
    for lesson in lessons:
        if lesson.get("type") != "рабочие часы":
            continue

        work_blocks_count += 1
        start_str, end_str = lesson["time"].split("-")
        start = _parse_dt(lesson["date"], start_str)
        end = _parse_dt(lesson["date"], end_str)

        event = Event()
        event.add("uid", _stable_uid(lesson["date"], lesson["subject"], lesson["time"]))

        # Формируем SUMMARY
        subject = lesson["subject"]
        summary = subject  # рабочие блоки уже содержат эмодзи в subject
        event.add("summary", summary)

        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("dtstamp", datetime.now(timezone.utc))

        # Описание
        desc_parts: list[str] = []
        if lesson.get("teacher"):
            desc_parts.append(f"Преподаватель: {lesson['teacher']}")
        if lesson.get("type"):
            desc_parts.append(f"Тип: {lesson['type']}")
        if desc_parts:
            event.add("description", "\n".join(desc_parts))

        if lesson.get("room"):
            event.add("location", lesson["room"])

        # VALARM — напоминание за 15 минут
        event.add_component(_make_alarm())

        cal.add_component(event)

    ics_bytes = cal.to_ical()
    logger.info("Сгенерирован .ics: %d событий, %d байт", work_blocks_count, len(ics_bytes))
    return ics_bytes


def save_ics(ics_bytes: bytes, path: str | Path = "calendar.ics") -> Path:
    """Сохраняет .ics файл на диск."""
    output = Path(path)
    output.write_bytes(ics_bytes)
    logger.info("Файл сохранён: %s", output.resolve())
    return output.resolve()


# ---------------------------------------------------------------------------
#  Standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    from schedule_parser import fetch_all_lessons, inject_work_blocks

    print("Загружаю расписание…")
    lessons = inject_work_blocks(fetch_all_lessons())
    ics_bytes = build_ics(lessons)
    path = save_ics(ics_bytes)
    print(f"Календарь сохранен: {path}")
    print(f"   Событий: {len(lessons)}, размер: {len(ics_bytes)} байт")
