"""
Парсер расписания с сайта raspisanie.rusoil.net.

Модуль **не требует** никаких токенов, ключей или переменных окружения.
Сайт API полностью публичен.

Использование:
    from schedule_parser import fetch_all_lessons, inject_work_blocks

    lessons = fetch_all_lessons()           # сырые пары с сайта
    full = inject_work_blocks(lessons)      # + рабочие блоки
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from hashlib import md5
from typing import Any
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Конфигурация (публичная, без секретов)
# ---------------------------------------------------------------------------
BASE_URL = "https://raspisanie.rusoil.net"
GROUP_NAME = "БЦШ02-26-02"
GROUP_ID = 163685

LESSON_TZ = ZoneInfo("Asia/Yekaterinburg")

ANCHOR_WEEK = 2
ANCHOR_MONDAY = date(2026, 9, 7)
MAX_WEEK = 30
EMPTY_WEEKS_TO_STOP = 3
REQUEST_TIMEOUT_SECONDS = 20

HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/155.0",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded",
}

# ---------------------------------------------------------------------------
#  Скрейпинг
# ---------------------------------------------------------------------------

def make_session(
    group_name: str = GROUP_NAME,
    group_id: int = GROUP_ID,
) -> requests.Session:
    """Создаёт HTTP-сессию с инициализацией страницы расписания."""
    session = requests.Session()
    session.headers.update(HEADERS)
    search_param = json.dumps(
        {
            "value": group_name,
            "id": group_id,
            "FILIAL": 1,
            "GRUPPA": group_name,
            "BELLFAK": 1,
            "FOB": 1,
        },
        ensure_ascii=False,
    )
    resp = session.get(
        f"{BASE_URL}/",
        params={"page": "schedule", "search": search_param},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return session


def fetch_week_raw(session: requests.Session, week: int) -> list[dict[str, Any]]:
    """Получает сырые данные расписания для конкретной недели."""
    payload = json.dumps(
        {"gruppa": GROUP_NAME, "beginweek": week, "endweek": week},
        ensure_ascii=False,
    )
    resp = session.post(
        f"{BASE_URL}/origins/get_rasp_student",
        data=payload.encode("utf-8"),
        headers={"Referer": f"{BASE_URL}/?page=schedule", "Origin": BASE_URL},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()


def week_day_to_date(week: int, dayweek: int) -> date:
    """Конвертирует (номер_недели, день_недели) в конкретную дату."""
    monday = ANCHOR_MONDAY + timedelta(weeks=week - ANCHOR_WEEK)
    return monday + timedelta(days=dayweek - 1)


def make_uid(lesson: dict[str, Any]) -> str:
    """Стабильный UID на базе даты, времени и названия предмета."""
    key = f"{lesson['date']}_{lesson['time']}_{lesson['subject']}"
    return md5(key.encode("utf-8")).hexdigest()


def transform(raw_lessons: list[dict[str, Any]], week: int) -> list[dict[str, Any]]:
    """Преобразует сырой JSON API в унифицированный формат."""
    out: list[dict[str, Any]] = []
    for item in raw_lessons:
        lesson_date = week_day_to_date(week, item["DAYWEEK"])
        start = item["START_TIME"][:5]
        end = item["END_TIME"][:5]
        lesson: dict[str, Any] = {
            "date": lesson_date.strftime("%d.%m.%Y"),
            "time": f"{start}-{end}",
            "subject": item["NDISC"],
            "type": item["NVIDZANAT"],
            "teacher": item["TEACHER_NAME"],
            "room": item["AUD"],
        }
        lesson["uid"] = make_uid(lesson)
        out.append(lesson)
    return out


def fetch_all_lessons(
    group_name: str = GROUP_NAME,
    group_id: int = GROUP_ID,
) -> list[dict[str, Any]]:
    """
    Скачивает всё расписание целиком (все недели).

    Не требует никаких токенов — API публичный.
    """
    session = make_session(group_name, group_id)
    all_lessons: list[dict[str, Any]] = []
    empty_streak = 0
    for week in range(1, MAX_WEEK + 1):
        try:
            raw = fetch_week_raw(session, week)
        except requests.RequestException as exc:
            logger.warning("Ошибка при загрузке недели %d: %s", week, exc)
            empty_streak += 1
            if empty_streak >= EMPTY_WEEKS_TO_STOP:
                break
            continue
        if not raw:
            empty_streak += 1
            if empty_streak >= EMPTY_WEEKS_TO_STOP:
                break
            continue
        empty_streak = 0
        all_lessons.extend(transform(raw, week))
    logger.info("Загружено %d пар", len(all_lessons))
    return all_lessons


# ---------------------------------------------------------------------------
#  Инъекция рабочих блоков (по ТЗ)
# ---------------------------------------------------------------------------

def _parse_time(date_str: str, time_str: str) -> datetime:
    """Парсит 'dd.mm.YYYY' + 'HH:MM' → datetime (naive)."""
    return datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")


def _make_work_event(
    date_str: str,
    start: datetime,
    end: datetime,
    label: str,
) -> dict[str, Any]:
    """Создаёт словарь рабочего блока."""
    return {
        "date": date_str,
        "time": f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}",
        "subject": f"💼 Работа ({label})",
        "type": "рабочие часы",
        "teacher": "",
        "room": "Удаленно / Офис",
        "uid": f"work_{date_str}_{start.strftime('%H%M')}_{label}",
    }


def inject_work_blocks(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Добавляет рабочие блоки вокруг пар по правилам:

    - «💼 Работа (утро)»  — ровно 2 часа перед первой парой дня.
    - «💼 Работа (блок)»  — 2-часовой блок между парами, если окно ≥ 2ч.
    - «💼 Работа (вечер)» — сразу после последней пары и до 01:00 ночи.

    Пары приоритетны и не сдвигаются; рабочие блоки не пересекаются с парами.
    """
    # Группировка по дням
    days: dict[str, list[dict[str, Any]]] = {}
    for lesson in lessons:
        days.setdefault(lesson["date"], []).append(lesson)

    result: list[dict[str, Any]] = []
    work_duration = timedelta(hours=2)

    for date_str, day_lessons in sorted(days.items()):
        # Сортируем пары по времени начала
        day_lessons.sort(
            key=lambda x: datetime.strptime(x["time"].split("-")[0], "%H:%M")
        )
        result.extend(day_lessons)

        # Собираем интервалы занятости
        occupied: list[tuple[datetime, datetime]] = []
        for les in day_lessons:
            s_str, e_str = les["time"].split("-")
            occupied.append((
                _parse_time(date_str, s_str),
                _parse_time(date_str, e_str),
            ))

        if not occupied:
            continue

        first_start = occupied[0][0]
        last_end = occupied[-1][1]

        # --- Утро: ровно 2 часа перед первой парой ---
        morning_start = first_start - work_duration
        if morning_start.hour >= 0:  # не уходим в отрицательное время
            result.append(
                _make_work_event(date_str, morning_start, first_start, "утро")
            )

        # --- Блоки между парами (окна ≥ 2 часов) ---
        for i in range(len(occupied) - 1):
            gap_start = occupied[i][1]
            gap_end = occupied[i + 1][0]
            gap = gap_end - gap_start
            if gap >= work_duration:
                block_end = gap_start + work_duration
                result.append(
                    _make_work_event(date_str, gap_start, block_end, "блок")
                )

        # --- Вечер: сразу после последней пары до 01:00 ночи ---
        day_base = _parse_time(date_str, "00:00")
        evening_end = day_base + timedelta(days=1, hours=1)  # 01:00 следующего дня
        if last_end < evening_end:
            result.append(
                _make_work_event(date_str, last_end, evening_end, "вечер")
            )

    return result


# ---------------------------------------------------------------------------
#  Утилиты для diff-ов
# ---------------------------------------------------------------------------

def format_lesson(lesson: dict[str, Any]) -> str:
    """Человекочитаемое описание пары/блока."""
    parts = [lesson["date"], lesson["time"], f"{lesson['subject']} ({lesson['type']})"]
    if lesson.get("teacher"):
        parts.append(lesson["teacher"])
    if lesson.get("room"):
        parts.append(f"ауд. {lesson['room']}")
    return " | ".join(parts)


def compute_diff(
    prev: dict[str, dict[str, Any]],
    curr: dict[str, dict[str, Any]],
) -> list[str]:
    """Вычисляет текстовый diff между двумя снимками расписания."""
    lines: list[str] = []
    added = sorted(
        curr.keys() - prev.keys(),
        key=lambda u: (curr[u]["date"], curr[u]["time"]),
    )
    for uid in added:
        lines.append(f"➕ Добавлено: {format_lesson(curr[uid])}")
    removed = sorted(
        prev.keys() - curr.keys(),
        key=lambda u: (prev[u]["date"], prev[u]["time"]),
    )
    for uid in removed:
        lines.append(f"➖ Отменено: {format_lesson(prev[uid])}")
    changed = sorted(
        (uid for uid in curr.keys() & prev.keys() if curr[uid] != prev[uid]),
        key=lambda u: (curr[u]["date"], curr[u]["time"]),
    )
    for uid in changed:
        lines.append(
            f"✏️ Изменено:\n"
            f"   было: {format_lesson(prev[uid])}\n"
            f"   стало: {format_lesson(curr[uid])}"
        )
    return lines


# ---------------------------------------------------------------------------
#  Standalone: запуск напрямую для проверки
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Загружаю расписание с raspisanie.rusoil.net …")
    raw = fetch_all_lessons()
    full = inject_work_blocks(raw)
    for lesson in sorted(full, key=lambda x: (x["date"], x["time"])):
        print(format_lesson(lesson))
    print(f"\nВсего событий: {len(full)} (пар: {len(raw)}, рабочих блоков: {len(full) - len(raw)})")
