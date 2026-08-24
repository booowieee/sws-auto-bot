# SWS Auto-Fill Bot

Автоматическое заполнение и отправка регистрационных анкет Google Forms для программы UK Seasonal Worker Scheme (SWS). Заполняет форму при открытии набора оператором.

---

## Возможности

- **Маппинг полей:**
  - Локальный словарь: 85 полей (румынский, английский, русский языки). Regex-шаблоны, границы слов, RapidFuzz.
  - LLM Fallback: запрос к LLM (Gemini, Groq, OpenAI, Ollama) для нестандартных формулировок.
  - Поддержка text, textarea, radio, checkbox, dropdown, date.
- **Мониторинг (--watch):**
  - HTTP-опрос закрытой формы без запуска браузера.
  - Запуск Playwright и отправка анкеты при открытии набора.
- **Многостраничные формы:**
  - Определение кнопок навигации (Next / Urmator / Далее).
  - Посекционное заполнение до финальной кнопки отправки.
  - Защита от зацикливания при ошибках валидации.
- **Сессия Google:**
  - Persistent browser profile для сохранения авторизации.
  - Поддержка форм с ограничением до 1 ответа.
- **Отчетность:**
  - Скриншоты заполненных секций и финальной отправки.
  - JSON-лог со списком всех заполненных полей.
  - Отправка отчета со скриншотами в Telegram.

---

## Архитектура

```
                    SWS Auto-Fill Bot

   Profile & Synonyms     Form Analyzer           Form Filler

   profile.yaml  ----->  1. Загрузка DOM   ----->  1. Ввод текста
   synonyms.yaml         2. Извлечение полей       2. Выбор Radio/Select
                          3. Определение типов      3. Переход по секциям
                                 |                  4. Нажатие Submit
                          +------+------+                  |
                          | Tier 1:     |                  |
                          | FieldMatcher|                  |
                          | (Regex/Fuzz)|                  |
                          +------+------+                  |
                                 | (unmapped fields)       |
                          +------+------+                  |
                          | Tier 2:     |                  |
                          | LLM Fallback|                  |
                          | (Gemini/Groq)                  |
                          +-------------+                  |
                                                           |
   +------------------------------------------------------+
   |                 Playwright Engine                    |
   | - Chromium persistent context                        |
   +------------------------------------------------------+
   |                 Execution Reporter                   |
   | - Скриншоты секций                                   |
   | - Отправка отчета в Telegram                         |
   +------------------------------------------------------+
```

---

## Структура проекта

```
sws-auto-bot/
├── config/
│   ├── profile.example.yaml     # Шаблон персональных данных
│   ├── profile.yaml             # Персональные данные (в .gitignore)
│   └── synonyms.yaml            # Словарь соответствий полей (RO/EN/RU)
├── src/
│   ├── __init__.py
│   ├── __main__.py              # CLI интерфейс (click)
│   ├── config.py                # Загрузчик настроек и профилей
│   ├── models.py                # Pydantic модели данных
│   ├── browser.py               # Управление Playwright и сессиями
│   ├── analyzer.py              # Парсинг DOM структуры Google Forms
│   ├── matcher.py               # Сопоставление полей с профилем
│   ├── llm.py                   # LLM клиент
│   ├── llm_router.py            # Каскадный LLM роутер с кэшем
│   ├── filler.py                # Заполнение полей и отправка
│   ├── watcher.py               # Фоновый мониторинг формы
│   ├── watcher_manager.py       # Пул фоновых задач мониторинга
│   ├── batch_runner.py          # Пакетный прогон
│   ├── reporter.py              # Логирование, скриншоты, Telegram
│   └── bot/                     # Telegram панель управления
│       ├── __init__.py
│       ├── bot.py               # Сервис Telegram бота
│       ├── db.py                # SQLite база данных
│       ├── handlers.py          # Обработчики команд
│       └── keyboards.py         # Клавиатуры
├── data/                        # Persistent профиль Chromium и БД (в .gitignore)
├── logs/                        # JSON-логи выполнения
├── screenshots/                 # Временные скриншоты (удаляются после отправки)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pyproject.toml
```

---

## Установка и настройка

### 1. Клонирование репозитория

```bash
git clone https://github.com/booowieee/sws-auto-bot.git
cd sws-auto-bot
```

### 2. Установка зависимостей

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Конфигурация

1. Скопируйте шаблон профиля и укажите свои данные:
   ```bash
   cp config/profile.example.yaml config/profile.yaml
   ```
2. Скопируйте `.env.example` в `.env` и задайте параметры:
   ```bash
   cp .env.example .env
   ```

---

## Использование

### 1. Первичная авторизация Google (один раз)

Если форма требует авторизации в Google-аккаунте:

```bash
python -m src.__main__ --login
```

Откроется окно браузера. Авторизуйтесь и закройте окно. Сессия сохранится в `data/chrome_profile`.

Проверка статуса сессии:
```bash
python -m src.__main__ --check-session
```

### 2. Тестовый запуск (Dry-Run без отправки)

Заполняет форму, сохраняет скриншоты, не нажимает Submit:

```bash
python -m src.__main__ --url "https://docs.google.com/forms/d/e/.../viewform" --test
```

### 3. Боевой запуск (заполнение и отправка)

```bash
python -m src.__main__ --url "https://docs.google.com/forms/d/e/.../viewform"
```

### 4. Telegram бот (--bot)

Запуск панели управления через Telegram Bot API:

```bash
python -m src.__main__ --bot
```

**Команды бота:**
- `/status` - статус мониторинга и список задач
- `/watch <url> [сек] [--test]` - запустить слежение за формой
- `/unwatch <url>` - остановить слежение
- `/fill <url> [--test]` - заполнить форму из чата
- `/profile` - просмотр данных профиля
- `/logs` - последние отчеты
- `/whitelist` - управление доступом пользователей

### 5. Режим слежения из CLI (--watch)

Опрашивает закрытую форму через HTTP-запросы. При открытии запускает Playwright, заполняет поля, отправляет форму и присылает отчет в Telegram:

```bash
# Проверка каждые 30 секунд
python -m src.__main__ --watch "https://docs.google.com/forms/d/e/.../viewform"

# Проверка каждые 10 секунд в тестовом режиме
python -m src.__main__ --watch "https://docs.google.com/forms/d/e/.../viewform" --interval 10 --test
```

---

## Развертывание в Docker

```bash
# Сборка образа
docker compose build

# Запуск бота в фоне (24/7)
docker compose up -d

# Просмотр логов
docker compose logs -f --tail 50
```

---

## Лицензия

[MIT](LICENSE)
