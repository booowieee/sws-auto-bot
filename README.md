# SWS Auto-Fill Bot

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33?logo=playwright&logoColor=white)
![Docker](https://img.shields.io/badge/Docker--Compose-latest-2496ED?logo=docker&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot_API-26A5E4?logo=telegram&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

Автоматическое заполнение и отправка регистрационных анкет Google Forms для программы UK Seasonal Worker Scheme (SWS). Заполняет форму при открытии набора оператором, до того как места закончатся.

---

## Возможности

- **Двухуровневый гибридный маппинг полей (2-Tier Matching):**
  - **Tier 1 (Локальный):** 85 полей в словаре, распознавание на румынском, английском и русском языках. Составные regex-шаблоны, границы слов, RapidFuzz (1-3 мс).
  - **Tier 2 (LLM Fallback):** Пакетный асинхронный запрос к LLM (Groq, Gemini, OpenAI, OpenRouter) для редких или нестандартных формулировок вопросов.
  - Поддержка text, textarea, radio, checkbox, dropdown и date полей.
- **Режим автопилота (--watch):**
  - Непрерывный легковесный HTTP-опрос закрытой формы без запуска браузера.
  - Мгновенный запуск Playwright и отправка анкеты при открытии набора.
- **Ввод с задержками:**
  - Посимвольный ввод текста с динамическими случайными паузами (15-55 мс).
  - Случайные паузы между переходами к следующим полям.
  - Снятие признаков автоматизации браузера (`navigator.webdriver`).
- **Многостраничные формы:**
  - Автоматическое определение кнопок навигации (*Next* / *Urmator* / *Далее*).
  - Последовательное заполнение каждой страницы до финальной кнопки отправки.
  - Защита от зацикливания при ошибках валидации (лимит страниц, проверка изменения DOM).
- **Сессия Google:**
  - Persistent browser profile для сохранения авторизации.
  - Обход ограничений *"Ограничить до 1 ответа"*.
- **Пакетное тестирование (Batch Runner):**
  - Массовый прогон по списку форм из текстового файла или CSV.
  - Автоматическая генерация JSON и Markdown отчетов с таблицей результатов.
  - Подсчет общей точности маппинга полей и процента успешных заполнений.
- **Отчетность:**
  - Скриншоты ключевых этапов: загрузка, заполнение, отправка.
  - JSON-лог со списком всех заполненных полей.
  - Отправка отчета со скриншотами в Telegram и автоудаление графики с диска.
- **Отказоустойчивость:**
  - Валидация обязательных полей перед отправкой.
  - Проверка формы на статус закрытия (*closedform*).
  - Проверка текста подтверждения после отправки.

---

## Архитектура

```
                    SWS Auto-Fill Bot

   Profile & Synonyms     Form Analyzer           Form Filler

   profile.yaml  ----->  1. Загрузка DOM   ----->  1. Посимвольный ввод
   synonyms.yaml         2. Извлечение ARIA        2. Выбор опций Radio/Select
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
                          | (Groq/OpenAI)                  |
                          +-------------+                  |
                                                           |
   +------------------------------------------------------+
   |                 Playwright Browser Engine            |
   | - Chromium persistent context                        |
   | - Anti-bot evasion & stealth scripts                 |
   +------------------------------------------------------+
   |                 Execution Reporter                   |
   | - Скриншоты этапов                                  |
   | - Отправка отчета в Telegram                         |
   | - Автоочистка скриншотов                            |
   +------------------------------------------------------+
```

---

## Структура проекта

```
sws-auto-bot/
├── config/
│   ├── profile.example.yaml     # Шаблон персональных данных
│   ├── profile.yaml             # Персональные данные (в .gitignore)
│   ├── synonyms.yaml            # Словарь соответствий полей (RO/EN/RU)
│   └── test_urls.example.txt    # Пример списка URL для пакетного теста
├── src/
│   ├── __init__.py
│   ├── __main__.py              # CLI интерфейс (click)
│   ├── config.py                # Загрузчик настроек и профилей
│   ├── models.py                # Pydantic модели данных
│   ├── browser.py               # Управление Playwright и сессиями
│   ├── analyzer.py              # Парсинг DOM структуры Google Forms
│   ├── matcher.py               # Сопоставление полей с профилем
│   ├── llm.py                   # Tier 2 LLM Fallback клиент (OpenAI-compatible)
│   ├── filler.py                # Заполнение полей и отправка
│   ├── watcher.py               # Фоновый мониторинг и автозапуск
│   ├── batch_runner.py          # Пакетный прогон и QA-бенчмарки
│   └── reporter.py              # Логирование, скриншоты, Telegram
├── tests/
│   ├── test_config.py
│   ├── test_matcher.py
│   ├── test_llm.py
│   ├── test_watcher.py
│   └── test_batch_runner.py
├── data/                        # Persistent профиль Chromium (в .gitignore)
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

### 3. Настройка конфигурации

1. Скопируйте шаблон профиля и заполните свои данные:
   ```bash
   cp config/profile.example.yaml config/profile.yaml
   ```
2. Скопируйте `.env.example` в `.env` и укажите настройки (Telegram, LLM Fallback):
   ```bash
   cp .env.example .env
   ```

3. (Опционально) Включите Tier 2 LLM Fallback в `.env` для страховки от нестандартных вопросов:
   ```env
   LLM_FALLBACK_ENABLED=true
   LLM_API_KEY=gsk_your_groq_api_key_here
   LLM_BASE_URL=https://api.groq.com/openai/v1
   LLM_MODEL=llama-3.3-70b-versatile
   ```

---

## Использование

### 1. Первичная авторизация Google (один раз)

Если целевая форма требует авторизации в Google-аккаунте:

```bash
python -m src.__main__ --login
```

Откроется окно браузера. Авторизуйтесь в Google-аккаунте и закройте браузер. Сессия сохранится в `data/chrome_profile`.

Проверить статус сессии:
```bash
python -m src.__main__ --check-session
```

### 2. Тестовый запуск (Dry-Run без отправки)

Заполняет форму, делает скриншоты, но **не нажимает** кнопку Submit:

```bash
python -m src.__main__ --url "https://docs.google.com/forms/d/e/.../viewform" --test
```

### 3. Боевой запуск (заполнение и отправка)

```bash
python -m src.__main__ --url "https://docs.google.com/forms/d/e/.../viewform"
```

Для визуальной отладки добавьте флаг `--headed`.

### 4. Режим автопилота / слежения (--watch)

Опрашивает закрытую форму через легковесные HTTP-запросы (без расхода ресурсов браузера). Как только форма открывается, бот мгновенно запускает Playwright, заполняет все поля, отправляет форму и присылает отчет со скриншотами в Telegram:

```bash
# Слежение с интервалом проверки 30 секунд (по умолчанию)
python -m src.__main__ --watch "https://docs.google.com/forms/d/e/.../viewform"

# Слежение с интервалом 10 секунд в тестовом режиме (без нажатия Submit)
python -m src.__main__ --watch "https://docs.google.com/forms/d/e/.../viewform" --interval 10 --test

# Настройка максимального времени ожидания (в часах)
python -m src.__main__ --watch "https://docs.google.com/forms/d/e/.../viewform" --interval 15 --max-hours 48
```

### 5. Пакетное тестирование (Batch Runner)

Прогон по списку форм из файла:

```bash
# Текстовый файл с URL (по одному на строку)
python -m src.__main__ --batch config/test_urls.example.txt --test

# CSV/TSV файл (колонки: URL, название формы)
python -m src.__main__ --batch forms.csv --test
```

Результаты сохраняются в `logs/benchmark_*.json` и `logs/benchmark_*.md`.

---

## Развертывание в Docker

```bash
# Слежение за формой в фоне (автопилот)
docker compose run -d --rm sws-auto-bot --watch "https://docs.google.com/forms/d/e/.../viewform" --interval 20

# Тестовый прогон одной формы
docker compose run --rm sws-auto-bot --url "https://docs.google.com/forms/d/e/.../viewform" --test

# Пакетный прогон
docker compose run --rm sws-auto-bot --batch config/test_urls.example.txt --test

# Боевой запуск по прямому URL
docker compose run --rm sws-auto-bot --url "https://docs.google.com/forms/d/e/.../viewform"
```

---

## Запуск тестов

```bash
pip install pytest
pytest -v
```

---

## Лицензия

Проект распространяется под лицензией [MIT](LICENSE).
