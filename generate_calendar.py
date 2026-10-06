"""
Точка входа для генерации calendar.ics (для CI и локального запуска).

Запускается без каких-либо обязательных переменных окружения.
Опционально отправляет ntfy-уведомление (если задан NTFY_TOPIC).

Использование:
    python generate_calendar.py                # → calendar.ics
    python generate_calendar.py --output out/  # → out/calendar.ics
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ics_generator import build_ics, save_ics
from notifications import send_ntfy
from schedule_parser import fetch_all_lessons, inject_work_blocks

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Генератор подписного календаря (.ics) из расписания УГНТУ"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="calendar.ics",
        help="Путь к выходному файлу (по умолчанию: calendar.ics)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Отправить ntfy-уведомление после генерации",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Подробный вывод",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- 1. Загрузка расписания ---
    logger.info("Загружаю расписание с raspisanie.rusoil.net …")
    try:
        lessons_raw = fetch_all_lessons()
    except Exception as exc:
        logger.error("Не удалось загрузить расписание: %s", exc)
        sys.exit(1)

    if not lessons_raw:
        logger.warning("Расписание пустое — пар не найдено")

    # --- 2. Инъекция рабочих блоков ---
    lessons = inject_work_blocks(lessons_raw)
    logger.info(
        "Пар: %d, рабочих блоков: %d, всего: %d",
        len(lessons_raw),
        len(lessons) - len(lessons_raw),
        len(lessons),
    )

    # --- 3. Генерация .ics ---
    ics_bytes = build_ics(lessons)

    # --- 4. Сохранение ---
    output_path = Path(args.output)
    if output_path.is_dir():
        output_path = output_path / "calendar.ics"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    saved = save_ics(ics_bytes, output_path)

    print(f"✅ Календарь сохранён: {saved}")
    print(f"   Событий: {len(lessons)}, размер: {len(ics_bytes)} байт")

    # --- 5. Опциональное уведомление ---
    if args.notify:
        send_ntfy(
            f"Календарь обновлён: {len(lessons)} событий",
            title="📅 Календарь обновлён",
            tags=["calendar"],
        )


if __name__ == "__main__":
    main()
