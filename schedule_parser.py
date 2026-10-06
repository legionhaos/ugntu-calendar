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

def get_free_ru_proxies() -> list[str]:
    """Скачивает актуальный список бесплатных российских прокси."""
    url = "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&country=RU&proxy_format=protocolipport&format=text"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        proxies = [p.strip() for p in resp.text.splitlines() if p.strip()]
        return proxies[:10]  # Берем первые 10 для проверки
    except Exception as exc:
        logger.warning("Не удалось получить список прокси: %s", exc)
        return []


def make_session(
    group_name: str = GROUP_NAME,
    group_id: int = GROUP_ID,
) -> requests.Session:
    """Создаёт HTTP-сессию. При недоступности сайта в РФ использует бесплатные прокси."""
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
    params = {"page": "schedule", "search": search_param}

    # 1. Попытка прямого подключения
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        logger.info("Попытка прямого подключения к %s ...", BASE_URL)
        resp = session.get(f"{BASE_URL}/", params=params, timeout=10)
        resp.raise_for_status()
        logger.info("Прямое подключение успешно.")
        return session
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        logger.warning("Прямое подключение не удалось (вероятно блокировка по Geo-IP): %s", exc)

    # 2. Попытка подключения через прокси
    logger.info("Загружаем список бесплатных RU прокси для обхода блокировки...")
    proxies = get_free_ru_proxies()
    
    for p in proxies:
        logger.info("Пробуем прокси: %s", p)
        proxy_session = requests.Session()
        proxy_session.headers.update(HEADERS)
        proxy_session.proxies.update({"http": p, "https": p})
        try:
            resp = proxy_session.get(f"{BASE_URL}/", params=params, timeout=10)
            resp.raise_for_status()
            logger.info("Успешно подключились через прокси: %s", p)
            return proxy_session
        except Exception as exc:
            logger.debug("Прокси %s не сработал: %s", p, exc)

    raise ConnectionError("Не удалось подключиться к сайту ни напрямую, ни через прокси.")


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


def _make_custom_event(
    date_str: str,
    start: datetime,
    end: datetime,
    subject: str,
) -> dict[str, Any]:
    """Создаёт словарь рабочего/кастомного блока."""
    return {
        "date": date_str,
        "time": f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}",
        "subject": subject,
        "type": "рабочие часы",
        "teacher": "",
        "room": "Удаленно / Офис",
        "uid": f"work_{date_str}_{start.strftime('%H%M')}_{md5(subject.encode('utf-8')).hexdigest()[:8]}",
    }


def inject_work_blocks(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Добавляет рабочие блоки вокруг пар по правилам:
    - Только рабочие блоки попадают в календарь (через фильтрацию в ics_generator).
    - Утро: Поздний старт (>= 12:15) — буфер 1.5 часа, старт не раньше 09:00.
    - Утро: Обычный старт (< 12:15) — буфер 1 час, старт не раньше 08:00.
    - Окна >= 3 часов превращаются в рабочий блок (буфер 1 час с каждой стороны).
    - Вечер: Буфер 1.5 часа после последней пары -> Лекция (1.5 часа) -> Работа (2 часа).
    - Если пар нет: Лекция 18:00-19:30, Работа 19:30-21:30.
    """
    if not lessons:
        return []

    # Группировка по дням
    days_dict: dict[str, list[dict[str, Any]]] = {}
    for lesson in lessons:
        days_dict.setdefault(lesson["date"], []).append(lesson)

    dates = [datetime.strptime(d, "%d.%m.%Y").date() for d in days_dict.keys()]
    min_date = min(dates)
    max_date = max(dates)

    result: list[dict[str, Any]] = []

    curr_date = min_date
    while curr_date <= max_date:
        date_str = curr_date.strftime("%d.%m.%Y")
        day_lessons = days_dict.get(date_str, [])
        
        # Оставляем пары в результате для diff-ов и бота
        result.extend(day_lessons)

        day_base = datetime.combine(curr_date, datetime.min.time())

        if not day_lessons:
            # Если пар нет вообще — вечерняя связка по умолчанию
            lecture_start = day_base + timedelta(hours=18)
            lecture_end = lecture_start + timedelta(hours=1, minutes=30)
            work_end = lecture_end + timedelta(hours=2)
            
            result.append(_make_custom_event(date_str, lecture_start, lecture_end, "Подготовка к хакатону: Лекция"))
            result.append(_make_custom_event(date_str, lecture_end, work_end, "Работа / Фокус"))
        else:
            # Сортируем пары по времени начала
            day_lessons.sort(
                key=lambda x: datetime.strptime(x["time"].split("-")[0], "%H:%M")
            )

            # Собираем интервалы занятости
            occupied: list[tuple[datetime, datetime]] = []
            for les in day_lessons:
                s_str, e_str = les["time"].split("-")
                occupied.append((
                    _parse_time(date_str, s_str),
                    _parse_time(date_str, e_str),
                ))

            first_start = occupied[0][0]
            last_end = occupied[-1][1]

            # --- Утро ---
            if first_start.hour * 60 + first_start.minute >= 12 * 60 + 15:
                # Поздний старт (>= 12:15)
                work_start = day_base + timedelta(hours=9)
                work_end = first_start - timedelta(hours=1, minutes=30)
                if work_end - work_start >= timedelta(hours=1):
                    result.append(_make_custom_event(date_str, work_start, work_end, "Работа / Фокус"))
            else:
                # Ранний старт (< 12:15)
                work_start = day_base + timedelta(hours=8)
                work_end = first_start - timedelta(hours=1)
                if work_end - work_start >= timedelta(hours=1):
                    result.append(_make_custom_event(date_str, work_start, work_end, "Работа / Фокус"))

            # --- Окна (блок) ---
            for i in range(len(occupied) - 1):
                gap_start = occupied[i][1]
                gap_end = occupied[i + 1][0]
                
                # Буфер 1 час с обеих сторон
                block_start = gap_start + timedelta(hours=1)
                block_end = gap_end - timedelta(hours=1)
                
                # Чистое оставшееся время работы составляет не менее 1 часа (окно >= 3 часов)
                if block_end - block_start >= timedelta(hours=1):
                    result.append(_make_custom_event(date_str, block_start, block_end, "Работа / Фокус"))

            # --- Вечер ---
            # Буфер 1.5 часа после последней пары
            lecture_start = last_end + timedelta(hours=1, minutes=30)
            lecture_end = lecture_start + timedelta(hours=1, minutes=30)
            work_end = lecture_end + timedelta(hours=2)
            
            result.append(_make_custom_event(date_str, lecture_start, lecture_end, "Подготовка к хакатону: Лекция"))
            result.append(_make_custom_event(date_str, lecture_end, work_end, "Работа / Фокус"))

        curr_date += timedelta(days=1)

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
