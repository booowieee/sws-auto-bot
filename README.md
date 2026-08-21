# SWS Auto-Fill Bot

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33?logo=playwright&logoColor=white)
![Docker](https://img.shields.io/badge/Docker--Compose-latest-2496ED?logo=docker&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Bot_API-26A5E4?logo=telegram&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

Инструмент автоматического заполнения и отправки регистрационных анкет Google Forms для соискателей программы UK Seasonal Worker Scheme (SWS). Предназначен для мгновенной реакции и отправки заявки в момент открытия набора операторов, когда критически важно попасть в первую волну регистрации.

---

## Ключевые возможности

- **Интеллектуальный парсинг и маппинг полей:**
  - Автоматическое распознавание названий полей на румынском, английском и русском языках.
  - Трехуровневая система сопоставления: составные шаблоны (полное имя vs имя/фамилия), регулярные выражения и нечеткий поиск (RapidFuzz).
  - Корректная обработка текстовых полей, многострочных блоков, Radio-кнопок, Checkbox, выпадающих списков Dropdown и дат.
- **Имитация поведения человека:**
  - Посимвольный ввод текста с динамическими случайными задержками (20-80 мс).
  - Случайные паузы между переходом к следующим элементам формы.
  - Снятие признаков автоматизации браузера (`navigator.webdriver`).
- **Поддержка многостраничных форм:**
  - Автоматическое определение кнопок перехода между секциями (*Next* / *Următor* / *Далее*).
  - Последовательное заполнение каждой страницы до финальной кнопки отправки.
- **Управление сессией Google:**
  - Поддержка persistent browser profile для сохранения авторизации в аккаунте Google.
  - Обход ограничений на формах с включенной настройкой *«Ограничить до 1 ответа»*.
- **Отчетность и защита дискового пространства:**
  - Фиксация ключевых этапов: форма загружена, форма заполнена, результат отправки.
  - Формирование структурированного JSON-лога со списком всех заполненных полей.
  - Мгновенная отправка отчета со скриншотами в Telegram и немедленное удаление графических файлов с диска для экономии места на сервере.
- **Отказоустойчивость:**
  - Валидация обязательных полей перед отправкой. Если хотя бы одно обязательное поле не удалось сопоставить, бот не нажимает Submit и сообщает об ошибке.
  - Проверка формы на статус закрытия (*closedform*).
  - Проверка текста подтверждения (*«Ответ записан»*) после нажатия кнопки отправки.

---

## Архитектура системы

```
                    SWS Auto-Fill Bot
                    
   Profile & Synonyms     Form Analyzer           Form Filler
   
   profile.yaml  ----->  1. Загрузка DOM   ----->  1. Посимвольный ввод
   synonyms.yaml         2. Извлечение ARIA        2. Выбор опций Radio/Select
                         3. Определение типов      3. Переход по секциям
                                |                  4. Нажатие Submit
                         +------+------+                  |
                         | FieldMatcher|                  |
                         | - Regex     |                  |
                         | - RapidFuzz |                  |
                         +-------------+                  |
                                                          |
   +------------------------------------------------------+
   |                 Playwright Browser Engine            |
   | - Chromium persistent context                        |
   | - Anti-bot evasion & stealth scripts                 |
   +------------------------------------------------------+
   |                 Execution Reporter                   |
   | - Фиксация скриншотов этапов                         |
   | - Отправка отчета в Telegram                         |
   | - Немедленная очистка скриншотов с диска             |
   +------------------------------------------------------+
```

---

## Структура проекта

```
sws-auto-bot/
├── config/
│   ├── profile.example.yaml   # Шаблон персональных данных
│   ├── profile.yaml           # Персональные данные пользователя (в .gitignore)
│   └── synonyms.yaml          # Словарь соответствий полей (RO/EN/RU)
├── src/
│   ├── __init__.py
│   ├── __main__.py            # CLI интерфейс
│   ├── config.py              # Загрузчик настроек и профилей
│   ├── models.py              # Pydantic модели данных
│   ├── browser.py             # Управление Playwright и сессиями
│   ├── analyzer.py            # Парсинг DOM структуры Google Forms
│   ├── matcher.py             # Сопоставление полей с профилем
│   ├── filler.py              # Заполнение полей и отправка
│   └── reporter.py            # Логирование, скриншоты, Telegram
├── tests/
│   ├── test_config.py
│   └── test_matcher.py
├── data/                      # Persistent профиль Chromium (в .gitignore)
├── logs/                      # JSON-логи выполнения
├── screenshots/               # Временные скриншоты (удаляются после отправки)
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
2. Скопируйте `.env.example` в `.env` и при необходимости укажите токен Telegram:
   ```bash
   cp .env.example .env
   ```

---

## Использование

### 1. Первичная авторизация Google (один раз)

Если целевая форма требует авторизации в Google-аккаунте:

```bash
python -m src.__main__ --login
```

Откроется окно браузера. Авторизуйтесь в Google-аккаунте и закройте браузер. Сессия сохранится в `data/chrome_profile`.

Проверить статус сессии можно командой:
```bash
python -m src.__main__ --check-session
```

### 2. Тестовый запуск (Dry-Run без отправки)

Заполняет форму, делает скриншоты, но **не нажимает** кнопку Submit:

```bash
python -m src.__main__ --url "https://docs.google.com/forms/d/e/.../viewform" --test
```

### 3. Боевой запуск (Заполнение и отправка)

```bash
python -m src.__main__ --url "https://docs.google.com/forms/d/e/.../viewform"
```

Для визуальной отладки можно добавить флаг `--headed`.

---

## Развертывание в Docker

```bash
# Сборка и запуск тестового прогона
docker compose run --rm sws-auto-bot --url "https://docs.google.com/forms/d/e/.../viewform" --test

# Боевой запуск
docker compose run --rm sws-auto-bot --url "https://docs.google.com/forms/d/e/.../viewform"
```

---

## Запуск тестов

```bash
pytest -v
```

---

## Лицензия

Проект распространяется под лицензией [MIT](LICENSE).
