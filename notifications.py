"""
Уведомления через ntfy.sh (push без токенов).

ntfy — бесплатный сервис push-уведомлений без регистрации.
Если NTFY_TOPIC не задан или отправка падает — тихо логируем, не ломая скрипт.

Использование:
    from notifications import send_ntfy

    send_ntfy("Привет!", title="Тест")
"""
from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Конфигурация (все опциональные)
# ---------------------------------------------------------------------------
NTFY_SERVER: str = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC: str = os.getenv("NTFY_TOPIC", "")  # пустой по умолчанию — не шлём
NTFY_ADMIN_TOPIC: str = os.getenv("NTFY_ADMIN_TOPIC", "")

NTFY_TIMEOUT_SECONDS: int = 10
NTFY_MAX_ATTEMPTS: int = 3
NTFY_RETRY_BACKOFF_SECONDS: int = 3
NTFY_MAX_MESSAGE_LEN: int = 3800


def send_ntfy(
    text: str,
    *,
    title: str | None = None,
    priority: int = 3,
    tags: list[str] | None = None,
    topic: str | None = None,
) -> bool:
    """
    Отправляет push через ntfy.sh.

    Args:
        text: Тело сообщения.
        title: Заголовок.
        priority: Приоритет (1-5, по умолчанию 3).
        tags: Список тегов / эмодзи.
        topic: Топик (по умолчанию берётся из NTFY_TOPIC).

    Returns:
        True если все чанки отправлены успешно, False иначе.
        При отсутствии топика возвращает False и тихо логирует.
    """
    target_topic = topic if topic is not None else NTFY_TOPIC
    if not target_topic:
        logger.debug("ntfy: топик не задан, пропускаю отправку")
        return False

    # Разбиваем длинные сообщения на чанки
    chunks = [
        text[i : i + NTFY_MAX_MESSAGE_LEN]
        for i in range(0, len(text), NTFY_MAX_MESSAGE_LEN)
    ] or [text]

    all_ok = True
    for i, chunk in enumerate(chunks):
        payload: dict = {
            "topic": target_topic,
            "message": chunk,
            "priority": priority,
        }
        if title:
            payload["title"] = (
                title
                if len(chunks) == 1
                else f"{title} ({i + 1}/{len(chunks)})"
            )
        if tags:
            payload["tags"] = tags

        chunk_ok = False
        for attempt in range(1, NTFY_MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(
                    NTFY_SERVER,
                    json=payload,
                    timeout=NTFY_TIMEOUT_SECONDS,
                )
                if resp.ok:
                    chunk_ok = True
                    logger.debug("ntfy: чанк %d/%d отправлен", i + 1, len(chunks))
                    break
                else:
                    logger.warning(
                        "ntfy: HTTP %d при отправке (попытка %d/%d)",
                        resp.status_code,
                        attempt,
                        NTFY_MAX_ATTEMPTS,
                    )
            except requests.exceptions.RequestException as exc:
                logger.warning(
                    "ntfy: ошибка сети (попытка %d/%d): %s",
                    attempt,
                    NTFY_MAX_ATTEMPTS,
                    exc,
                )
            if attempt < NTFY_MAX_ATTEMPTS:
                time.sleep(NTFY_RETRY_BACKOFF_SECONDS * attempt)

        if not chunk_ok:
            all_ok = False
            logger.error(
                "ntfy: не удалось отправить чанк %d/%d после %d попыток",
                i + 1,
                len(chunks),
                NTFY_MAX_ATTEMPTS,
            )

    return all_ok


def send_admin_ntfy(
    text: str,
    *,
    title: str | None = None,
    priority: int = 3,
    tags: list[str] | None = None,
) -> bool:
    """Отправляет push в админский топик (NTFY_ADMIN_TOPIC)."""
    if not NTFY_ADMIN_TOPIC:
        logger.debug("ntfy: NTFY_ADMIN_TOPIC не задан, пропускаю")
        return False
    return send_ntfy(text, title=title, priority=priority, tags=tags, topic=NTFY_ADMIN_TOPIC)


# ---------------------------------------------------------------------------
#  Standalone: тест отправки
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
    topic = NTFY_TOPIC or os.getenv("NTFY_TOPIC")
    if not topic:
        print("⚠️  Задайте NTFY_TOPIC для теста: export NTFY_TOPIC=my-topic")
        print("   Без топика уведомления тихо пропускаются (by design).")
    else:
        ok = send_ntfy("Тестовое сообщение от notifications.py", title="Тест ✅", tags=["test_tube"])
        print(f"Отправлено: {ok}")
