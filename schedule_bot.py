"""
Единый бот расписания с интеграцией личных часов.

Каждые CHECK_INTERVAL_SECONDS (10 минут):
  1) забирает расписание с raspisanie.rusoil.net через внутренний API
  2) добавляет в расписание личные часы (рабочие блоки)
  3) сравнивает с предыдущим снимком расписания
  4) если есть изменения — шлёт push-уведомление через ntfy
  5) собирает .ics и публикует (Supabase / локально)

Обязательные переменные окружения: НЕТ.
Опциональные:
  - SUPABASE_URL + SUPABASE_SERVICE_KEY — для публикации в Supabase Storage
  - NTFY_TOPIC / NTFY_ADMIN_TOPIC — для push-уведомлений
  - TELEGRAM_BOT_TOKEN — зарезервировано для будущей интеграции
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from ics_generator import build_ics, save_ics
from notifications import send_admin_ntfy, send_ntfy
from schedule_parser import (
    compute_diff,
    fetch_all_lessons,
    inject_work_blocks,
)

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Конфигурация (все переменные ОПЦИОНАЛЬНЫ)
# ---------------------------------------------------------------------------
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
BUCKET = "calendar"
ICS_FILENAME = "schedule.ics"
STATE_FILENAME = "schedule_state.json"
LOCAL_ICS_PATH = Path("calendar.ics")

CHECK_INTERVAL_SECONDS = 10 * 60  # 10 минут
PUBLISH_INTERVAL_SECONDS = 60 * 60  # 1 час
REQUEST_TIMEOUT_SECONDS = 20

SCHEDULE_LOCK = threading.Lock()


def _supabase_available() -> bool:
    """Проверяет, заданы ли credentials для Supabase."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


# ---------------------------------------------------------------------------
#  Supabase Storage (опционально)
# ---------------------------------------------------------------------------

def _supabase_headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }


def publish_supabase(ics_bytes: bytes) -> str | None:
    """Загружает .ics в Supabase Storage. Возвращает URL или None."""
    if not _supabase_available():
        logger.debug("Supabase не настроен — пропускаю публикацию в облако")
        return None
    try:
        upload_url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{ICS_FILENAME}"
        headers = {
            **_supabase_headers(),
            "Content-Type": "text/calendar; charset=utf-8",
            "x-upsert": "true",
        }
        resp = requests.post(
            upload_url, headers=headers, data=ics_bytes, timeout=REQUEST_TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{ICS_FILENAME}"
        logger.info("Опубликовано в Supabase: %s", public_url)
        return public_url
    except Exception as exc:
        logger.warning("Ошибка публикации в Supabase: %s", exc)
        return None


def fetch_state_supabase() -> dict[str, dict]:
    """Загружает предыдущее состояние из Supabase. Возвращает {} если недоступно."""
    if not _supabase_available():
        return {}
    try:
        url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{STATE_FILENAME}"
        resp = requests.get(
            url, headers=_supabase_headers(), timeout=REQUEST_TIMEOUT_SECONDS
        )
        if resp.status_code in (400, 404):
            return {}
        resp.raise_for_status()
        lessons = resp.json()
        return {lesson["uid"]: lesson for lesson in lessons}
    except Exception as exc:
        logger.warning("Ошибка загрузки состояния из Supabase: %s", exc)
        return {}


def save_state_supabase(lessons: list[dict]) -> None:
    """Сохраняет текущее состояние в Supabase."""
    if not _supabase_available():
        return
    try:
        upload_url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{STATE_FILENAME}"
        headers = {
            **_supabase_headers(),
            "Content-Type": "application/json",
            "x-upsert": "true",
        }
        requests.post(
            upload_url,
            headers=headers,
            data=json.dumps(lessons, ensure_ascii=False).encode("utf-8"),
            timeout=REQUEST_TIMEOUT_SECONDS,
        ).raise_for_status()
    except Exception as exc:
        logger.warning("Ошибка сохранения состояния в Supabase: %s", exc)


# ---------------------------------------------------------------------------
#  Локальное хранение состояния (fallback)
# ---------------------------------------------------------------------------
LOCAL_STATE_PATH = Path("schedule_state.json")


def fetch_state_local() -> dict[str, dict]:
    """Загружает предыдущее состояние из локального файла."""
    if not LOCAL_STATE_PATH.exists():
        return {}
    try:
        lessons = json.loads(LOCAL_STATE_PATH.read_text(encoding="utf-8"))
        return {lesson["uid"]: lesson for lesson in lessons}
    except Exception as exc:
        logger.warning("Ошибка чтения локального состояния: %s", exc)
        return {}


def save_state_local(lessons: list[dict]) -> None:
    """Сохраняет текущее состояние в локальный файл."""
    try:
        LOCAL_STATE_PATH.write_text(
            json.dumps(lessons, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Ошибка сохранения локального состояния: %s", exc)


def fetch_state() -> dict[str, dict]:
    """Загружает состояние (Supabase → локальный fallback)."""
    state = fetch_state_supabase()
    if not state:
        state = fetch_state_local()
    return state


def save_state(lessons: list[dict]) -> None:
    """Сохраняет состояние (Supabase + локальный)."""
    save_state_supabase(lessons)
    save_state_local(lessons)


# ---------------------------------------------------------------------------
#  Публикация
# ---------------------------------------------------------------------------

def publish(ics_bytes: bytes) -> str | None:
    """Публикует .ics (Supabase + локальный файл)."""
    # Всегда сохраняем локально
    save_ics(ics_bytes, LOCAL_ICS_PATH)
    # Опционально — в Supabase
    return publish_supabase(ics_bytes)


# ---------------------------------------------------------------------------
#  Основной цикл
# ---------------------------------------------------------------------------

def check_for_changes() -> tuple[list[dict], list[str], bool]:
    """Проверяет изменения в расписании, публикует .ics и шлёт уведомления."""
    with SCHEDULE_LOCK:
        lessons_raw = fetch_all_lessons()
        lessons = inject_work_blocks(lessons_raw)

        curr_state = {lesson["uid"]: lesson for lesson in lessons}
        prev_state = fetch_state()

        diff_lines: list[str] = []
        if prev_state:
            diff_lines = compute_diff(prev_state, curr_state)
            if diff_lines:
                send_ntfy(
                    "\n".join(diff_lines),
                    title="Изменения в расписании",
                    priority=4,
                    tags=["calendar"],
                )

        changed = curr_state != prev_state
        if changed:
            save_state(lessons)
            ics_bytes = build_ics(lessons)
            url = publish(ics_bytes)
            destination = url or str(LOCAL_ICS_PATH.resolve())
            logger.info("Обновлено и опубликовано → %s", destination)

        return lessons, diff_lines, changed


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Стартовое уведомление (тихо, если ntfy не настроен)
    send_admin_ntfy(
        "Бот расписания (с рабочими часами) запущен",
        title="Бот в строю ✅",
        tags=["rocket"],
    )

    mode_parts: list[str] = ["локальный файл"]
    if _supabase_available():
        mode_parts.append("Supabase")
    logger.info("Режим публикации: %s", " + ".join(mode_parts))

    last_publish = 0.0
    while True:
        try:
            lessons, _, changed = check_for_changes()
            now = time.monotonic()
            if changed:
                last_publish = now
            elif now - last_publish >= PUBLISH_INTERVAL_SECONDS:
                with SCHEDULE_LOCK:
                    ics_bytes = build_ics(lessons)
                    publish(ics_bytes)
                last_publish = now
        except Exception as exc:
            logger.error("Ошибка в цикле: %s", exc)
            try:
                send_admin_ntfy(
                    f"⚠️ {exc}", title="Ошибка", priority=4, tags=["warning"]
                )
            except Exception:
                pass
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()