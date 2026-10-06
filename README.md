# 📅 УГНТУ — Подписной календарь расписания

Автоматический парсер расписания с сайта `raspisanie.rusoil.net` с генерацией
подписного `.ics` календаря для iPhone, Android и Outlook.

**Работает без единого обязательного токена или секрета.**

## Быстрый старт

```bash
pip install -r requirements.txt
python generate_calendar.py
```

Файл `calendar.ics` появится в текущей директории. Подпишитесь на него
с iPhone или добавьте в Google Calendar.

## Архитектура

```
schedule_parser.py    — парсинг расписания (zero secrets, публичный API)
ics_generator.py      — генерация .ics (Asia/Yekaterinburg, VALARM)
notifications.py      — push через ntfy.sh (опционально)
generate_calendar.py  — точка входа для CI / локального запуска
schedule_bot.py       — фоновый бот с циклом обновлений (опционально)
```

| Модуль | Зависит от токенов? | Можно запустить отдельно? |
|--------|:-------------------:|:------------------------:|
| `schedule_parser.py` | ❌ Нет | ✅ Да |
| `ics_generator.py` | ❌ Нет | ✅ Да |
| `notifications.py` | ❌ Нет (ntfy опционален) | ✅ Да |
| `generate_calendar.py` | ❌ Нет | ✅ Да |
| `schedule_bot.py` | ❌ Нет (Supabase опционален) | ✅ Да |

## Подписка на iPhone

### Вариант 1: GitHub Pages (рекомендуется)

При пуше в GitHub запускается Actions workflow, который генерирует `calendar.ics`
и публикует его на GitHub Pages:

```
https://<username>.github.io/<repo>/calendar.ics
```

На iPhone: **Настройки → Календарь → Учётные записи → Добавить → Подписной календарь** → вставить URL.

### Вариант 2: Локально

```bash
python generate_calendar.py --output calendar.ics
```

Открыть файл на телефоне или настроить собственный хостинг.

## Переменные окружения (все опциональны)

| Переменная | Описание | По умолчанию |
|------------|----------|:------------:|
| `NTFY_TOPIC` | Топик ntfy.sh для push-уведомлений | *(не задан — тихо пропускается)* |
| `NTFY_ADMIN_TOPIC` | Топик для админских уведомлений | *(не задан)* |
| `NTFY_SERVER` | Сервер ntfy | `https://ntfy.sh` |
| `SUPABASE_URL` | URL Supabase проекта | *(не задан — только локально)* |
| `SUPABASE_SERVICE_KEY` | Service key Supabase | *(не задан)* |

Скопируйте `.env.example` → `.env` и заполните нужные значения.

## Рабочие блоки (логика)

Вокруг каждой пары автоматически создаются рабочие блоки:

- **💼 Работа (утро)** — ровно 2 часа перед первой парой дня
- **💼 Работа (блок)** — 2-часовой блок между парами (если окно ≥ 2ч)
- **💼 Работа (вечер)** — сразу после последней пары и до 01:00 ночи

Пары приоритетны и не сдвигаются.

## GitHub Actions

Workflow `.github/workflows/update_calendar.yml`:
- Запускается ежедневно в 06:00 UTC (11:00 Екб) и вручную
- Генерирует `calendar.ics` и публикует на GitHub Pages
- Опционально шлёт ntfy-уведомление (если задан `NTFY_TOPIC` в Secrets)

### Настройка GitHub Pages

1. Перейдите в **Settings → Pages**
2. Source: **GitHub Actions**
3. Готово — workflow сделает всё автоматически

## Файлы

- `schedule_parser.py` — парсинг расписания с rusoil.net (публичный API, без авторизации)
- `ics_generator.py` — генерация .ics с VALARM, Asia/Yekaterinburg
- `notifications.py` — push через ntfy.sh (graceful degradation)
- `generate_calendar.py` — CLI для генерации .ics
- `schedule_bot.py` — фоновый бот (цикл обновлений, опциональный Supabase)
- `.github/workflows/update_calendar.yml` — CI/CD для GitHub Pages
- `.env.example` — шаблон переменных окружения
