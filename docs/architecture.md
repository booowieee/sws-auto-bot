# SWS Auto-Fill Bot -- архитектура

---

## Назначение

Страховка на случай, если пропущен алерт от Watcher-бота или нужно заполнить форму
быстрее, чем вручную.

Бот открывает Google Form, заполняет все поля данными из профиля и нажимает Submit.
Получилось -- отчет со скриншотами в Telegram.
Не получилось -- ошибка в Telegram, дальше заполняешь руками.

Никакой интерактивности, никаких промежуточных шагов. Запустил -- жди результат.

---

## Сценарий использования

```
Watcher-бот: форма OPEN --> Auto-Bot запускается
                              |
                              +--> OK: форма заполнена, Submit нажат, отчет в Telegram
                              |
                              +--> FAIL: ошибка в Telegram, пользователь заполняет руками
```

---

## Как запускается

### Ручной запуск (основной)
```bash
python -m sws_auto_bot --url "https://forms.gle/example123"
```

### Автозапуск от Watcher-бота (планируется)
Watcher-бот при обнаружении статуса `OPEN` запускает Auto-Bot как subprocess.

### Тестовый прогон (без отправки)
```bash
python -m sws_auto_bot --url "https://forms.gle/example123" --test
```
Заполняет форму, делает скриншот, но НЕ нажимает Submit. Нужен для отладки на чужих формах.

### Первичная авторизация Google
```bash
python -m sws_auto_bot --login
```
Открывает браузер, пользователь логинится в Google со своего аккаунта,
профиль сохраняется в `data/chrome_profile/`. Делается один раз.

---

## Результаты исследования

### Google Forms не имеет reCAPTCHA

Google Forms не поддерживает reCAPTCHA как встроенную функцию.
Форма Best Opportunity исторически была полностью открытой, без логина и капчи.

Максимум, что может включить автор формы:
- "Ограничить 1 ответ" -- требует Google-аккаунт (решается через persistent profile)
- Ручная проверка типа "Сколько будет 2+3?" -- решается через словарь или LLM

### Выбранный подход: Playwright

| Подход | Вердикт | Почему |
|--------|---------|--------|
| POST на /formResponse | Запасной | Нужно знать entry.ID заранее. Ломается при любом изменении формы. |
| **Playwright (headless browser)** | **Основной** | Работает с любой формой. Читает лейблы. Имитирует человека. Скриншоты. |
| Pre-filled URL | Не подходит | Не отправляет форму, только заполняет. |

### ИИ: fallback, не основа

Основа -- детерминистический словарь синонимов на трех языках (RO/EN/RU) + fuzzy-matching.
LLM (Gemini/OpenAI API) подключается только для полей, которые словарь не распознал.

---

## Архитектура

```
sws-auto-bot
|
|-- config/
|   |-- profile.yaml        Персональные данные (gitignored)
|   |-- synonyms.yaml       Словарь синонимов полей (RO/EN/RU)
|
|-- src/
|   |-- analyzer.py          Парсинг структуры Google Form
|   |-- matcher.py           Сопоставление полей с профилем (словарь + fuzzy + LLM)
|   |-- filler.py            Заполнение полей + Submit
|   |-- reporter.py          Скриншоты, логи, Telegram-отчет
|   |-- browser.py           Playwright: запуск, persistent profile, антидетект
|   |-- config.py            Загрузка profile.yaml и synonyms.yaml
|   |-- __main__.py          CLI: --url, --test, --login
|
|-- data/
|   |-- chrome_profile/      Сохраненная Google-сессия (gitignored)
|
|-- logs/                    JSON-логи заполнения
|-- screenshots/             Скриншоты этапов
```

### Поток данных

```
[CLI / Watcher-бот]
        |
        v
  [browser.py] -- запуск Chromium с сохраненным профилем Google
        |
        v
  [analyzer.py] -- открывает форму, извлекает все поля:
        |          label, тип (text/radio/checkbox/dropdown/date), options, entry.ID
        |
        v
  [matcher.py] -- для каждого поля определяет, какие данные из profile.yaml подставить:
        |          1) Точное совпадение ключевого слова из synonyms.yaml
        |          2) Fuzzy-match (RapidFuzz, порог 80%)
        |          3) LLM fallback (если 1 и 2 не сработали)
        |
        v
  [filler.py] -- заполняет каждое поле с имитацией человека:
        |         - текст: посимвольный ввод (0.02-0.08с между символами)
        |         - radio/checkbox: клик по нужной опции
        |         - dropdown: открыть, найти, выбрать
        |         - задержка между полями: 0.3-0.8с
        |
        v
  [reporter.py] -- скриншот заполненной формы
        |
        v
  [filler.py] -- нажимает Submit
        |
        v
  [reporter.py] -- скриншот подтверждения
        |          -- JSON-лог (поле -> значение -> статус)
        |          -- Telegram-отчет со скриншотами
        v
      [DONE]
```

---

## Профиль пользователя (profile.yaml)

Заполняется один раз. Бот использует эти данные для любых форм.

```yaml
personal:
  first_name: "JOHN"
  last_name: "DOE"
  full_name: "JOHN DOE"
  date_of_birth: "01/01/2000"
  date_of_birth_parts:         # для форм, где дата разбита на 3 отдельных поля
    day: "01"
    month: "01"
    year: "2000"
  sex: "masculin"
  nationality: "moldoveneasca"
  city: "Chisinau"
  country: "Moldova"

documents:
  passport_number: "P00000000"
  passport_expiry: "01/01/2030"

contacts:
  phone: "+1234567890"
  whatsapp: "+1234567890"
  email: "user@example.com"

work:
  experience_agriculture: true
  experience_agriculture_text: "Da"      # текстовый вариант для radio-кнопок
  experience_uk: false
  experience_uk_text: "Nu"
  available_from: "April 2027"
  apply_alone: true
  apply_alone_text: "Singur"             # "Singur" / "In cuplu"
  english_level: "incepator"

about:
  ro: "Sunt o persoana responsabila, conditie fizica buna, pregatit pentru munca in agricultura."
  en: "Responsible person, good physical shape, ready for agricultural work."
  ru: "Ответственный, хорошая физическая форма, готов к работе на ферме."
```

---

## Словарь синонимов (synonyms.yaml)

Рабочий словарь, расширяется по мере тестирования на реальных формах.
Каждый ключ -- идентификатор поля в profile.yaml.
Значения -- массивы ключевых слов и regex-паттернов на трех языках.

```yaml
full_name:
  keywords: [nume, name, имя, фамилия, prenume, "full name", фио]
  patterns: ['nume\s*(si|și)?\s*prenume', '(first|last|full)\s*name']
  profile_key: personal.full_name

date_of_birth:
  keywords: [nasterii, nastere, birth, рождения, "data nasterii"]
  patterns: ['data\s*na[sș]terii', 'date\s*of\s*birth']
  profile_key: personal.date_of_birth

email:
  keywords: [email, e-mail, mail, почта, "adresa email"]
  patterns: ['e-?mail']
  profile_key: contacts.email

phone:
  keywords: [telefon, phone, whatsapp, телефон, numar]
  patterns: ['num[aă]r\s*(de\s*)?telefon', 'phone\s*number']
  profile_key: contacts.phone

passport:
  keywords: [pasaport, passport, паспорт, document, serie]
  patterns: ['num[aă]r\s*pa[sș]aport', 'passport\s*number']
  profile_key: documents.passport_number

sex:
  keywords: [sex, gen, пол, gender]
  patterns: ['^sex$', '^gen$', '^пол$']
  profile_key: personal.sex

nationality:
  keywords: [cetatenie, nationality, гражданство, nationalitate]
  patterns: ['cet[aă][tț]enie']
  profile_key: personal.nationality

city:
  keywords: [oras, city, город, localitate]
  patterns: ['ora[sș]']
  profile_key: personal.city

country:
  keywords: [tara, country, страна]
  patterns: ['[tț]ar[aă]']
  profile_key: personal.country

experience_agriculture:
  keywords: [experienta, experience, опыт, agricultura]
  patterns: ['experi[ei]n[tț][aă]']
  profile_key: work.experience_agriculture_text

experience_uk:
  keywords: ["lucrat in uk", "worked in uk", "работал в англии"]
  patterns: ['lucrat.*(uk|anglia|britanie)']
  profile_key: work.experience_uk_text

available_from:
  keywords: [cand, when, когда, disponibil, available, pleca]
  patterns: ['c[aâ]nd\s*(po[tț]i|e[sș]ti)', 'when.*available']
  profile_key: work.available_from

apply_type:
  keywords: [singur, cuplu, alone, couple]
  patterns: ['singur.*cuplu']
  profile_key: work.apply_alone_text

english_level:
  keywords: [engleza, english, английск, nivel, level]
  patterns: ['nivel.*englez', 'english.*level']
  profile_key: work.english_level

about:
  keywords: [despre, about, "о себе", detalii, details, informatii]
  patterns: ['despre\s*(tine|dvs)', 'about\s*you', 'detalii\s*suplimentare']
  profile_key: about.ro
```

---

## Маппинг полей: 3-уровневый

```
Уровень 1: Точное ключевое слово
  label "Nume si Prenume" содержит "nume" --> profile.personal.full_name

Уровень 2: Fuzzy-match (RapidFuzz, порог 80%)
  label "Numele dumneavoastra complet" --> fuzzy("nume") = 87% --> profile.personal.full_name

Уровень 3: LLM fallback
  label "Cum te cheama?" --> Gemini API: "вопрос про имя" --> profile.personal.full_name
```

---

## Нераспознанные поля

Если бот не смог сопоставить обязательное поле ни одним из трех уровней маппинга:

1. Бот прекращает работу.
2. В Telegram уходит сообщение: "Не удалось распознать поле [X]. Форма НЕ отправлена."
3. Скриншот формы на момент остановки прикладывается к сообщению.
4. Дальше заполняешь руками.

Необязательные поля пропускаются без остановки.

---

## Google-сессия (persistent browser profile)

Playwright поддерживает persistent browser context -- сохраненный профиль Chrome
со всеми куками и авторизацией.

Настройка (один раз):
1. `python -m sws_auto_bot --login`
2. Открывается обычный Chromium с UI.
3. Пользователь заходит в accounts.google.com под своим аккаунтом.
4. Закрывает браузер.
5. Профиль сохраняется в `data/chrome_profile/`.

Все последующие запуски бота используют этот профиль.
Если форма требует Google-аккаунт -- бот уже авторизован.

Папка `data/chrome_profile/` добавлена в `.gitignore`.

---

## Скриншоты и отчетность

Бот делает скриншоты на ключевых этапах. Все сохраняются в `screenshots/`.

| Этап | Файл | Описание |
|------|------|----------|
| 1 | `01_form_loaded.png` | Форма загружена (пустая, для контроля структуры) |
| 2 | `02_form_filled.png` | Все поля заполнены, кнопка Submit видна |
| 3 | `03_form_submitted.png` | Страница подтверждения после отправки |

JSON-лог `logs/fill_YYYY-MM-DD_HH-MM-SS.json`:
```json
{
  "url": "https://forms.gle/example123",
  "timestamp": "2026-10-15T14:30:00Z",
  "status": "submitted",
  "duration_sec": 23.4,
  "fields": [
    {"label": "Nume si Prenume", "matched_as": "full_name", "value": "JOHN DOE", "method": "keyword"},
    {"label": "Email", "matched_as": "email", "value": "user@example.com", "method": "keyword"}
  ],
  "screenshots": ["01_form_loaded.png", "02_form_filled.png", "03_form_submitted.png"],
  "google_session": "active"
}
```

Telegram-отчет: скриншот `02_form_filled.png` + скриншот `03_form_submitted.png` +
текстовый отчет (сколько полей, все ли заполнены, статус отправки).

---

## Антидетект

| Мера | Реализация |
|------|-----------|
| Случайные задержки между полями | 0.3-0.8с |
| Посимвольный ввод текста | 0.02-0.08с между символами |
| Реалистичный User-Agent | Актуальный Chrome UA (из persistent profile) |
| Viewport | 1920x1080 |
| Клики | В случайную точку внутри поля |
| Общее время заполнения | 15-45 секунд |

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| Браузер | Playwright (Python), Chromium |
| Маппинг полей | regex + RapidFuzz |
| LLM fallback | Google Gemini API (опционально) |
| Конфигурация | PyYAML |
| CLI | Click |
| Уведомления | aiohttp + Telegram Bot API |

---

## Подводные камни и решения

### 1. Протухание Google-сессии

**Проблема:** Google периодически инвалидирует сессии, особенно если видит автоматизацию.
Сессия, созданная через --login, может протухнуть через несколько недель. Бот запустится
в нужный момент, а Google попросит залогиниться заново.

**Решение:**
- Использовать `channel: "chrome"` (реальный Chrome, не bundled Chromium) -- Google
  меньше подозревает настоящий браузер.
- При каждом запуске бот первым делом проверяет, жива ли сессия: открывает
  `accounts.google.com` и ищет аватарку/имя пользователя. Если сессия протухла --
  немедленно шлет в Telegram: "Сессия Google протухла. Запусти --login."
- Добавить периодическую проверку сессии (cron раз в неделю или команда --check-session).
- Очистка lock-файлов профиля (SingletonLock, SingletonCookie, SingletonSocket) перед
  каждым запуском -- предотвращает проблемы с коррупцией профиля.

### 2. Google Forms DOM нестабилен

**Проблема:** Google Forms использует динамические CSS-классы (типа `Qr7Oae`, `whsOnd`),
которые меняются при обновлениях. Селекторы по классам ломаются.

**Решение:**
- Использовать только стабильные селекторы: `[role="listitem"]` для вопросов,
  `[role="heading"]` для лейблов, `[data-params]` для извлечения entry.ID.
- `page.get_by_label()` и `page.get_by_role()` вместо CSS-классов.
- Для извлечения entry.ID парсить атрибут `data-params` через regex
  (`data-params` содержит числовой ID в формате `[[2092238618,...]]`).
- Не хардкодить ни один CSS-класс. Вся логика через ARIA-роли и атрибуты.

### 3. Многостраничные формы

**Проблема:** Google Forms может быть разбита на несколько секций с кнопками
"Urmator" / "Next" / "Далее". Все поля не видны на одной странице.

**Решение:**
- После заполнения всех видимых полей проверять наличие кнопки "Next" / "Urmator".
- Если есть -- нажать, дождаться загрузки новой секции, проанализировать и заполнить.
- Цикл: заполнить секцию -> Next -> заполнить -> ... -> Submit.
- Скриншот каждой заполненной секции (не только последней).
- Лейблы кнопок на разных языках: "Urmator", "Urmatorul", "Next", "Далее", "Дальше".

### 4. Radio/Checkbox: выбор правильного варианта

**Проблема:** Для текстовых полей все просто -- вставил значение. Для radio/checkbox
нужно кликнуть правильный вариант. Варианты могут быть на румынском с диакритикой
("Da" / "Nu", "Masculin" / "Feminin", "incepator" / "mediu" / "avansat").

**Решение:**
- В profile.yaml хранить и boolean, и текстовое значение для radio-полей:
  `experience_agriculture: true` + `experience_agriculture_text: "Da"`.
- При заполнении radio-поля искать вариант через fuzzy-match значения из профиля
  по списку options: `"Da"` fuzzy-match `"Da, am experienta"` = 90%.
- Для пола: `"masculin"` -> fuzzy-match по вариантам `["Masculin", "Feminin"]`.
- Для чекбоксов: аналогично, но может быть выбрано несколько.

### 5. Dropdown-ы в Google Forms -- не настоящие select

**Проблема:** Google Forms рендерит dropdown как кастомный UI-компонент, а не как
стандартный `<select>`. Это значит -- нельзя просто вызвать `select_option()`.

**Решение:**
- Клик по контейнеру dropdown, чтобы раскрыть список.
- Дождаться появления вариантов (Playwright auto-wait).
- Найти нужный вариант по тексту (fuzzy-match).
- Кликнуть по нему.
- Верифицировать, что значение выбрано.

### 6. Верификация после Submit

**Проблема:** Как понять, что форма действительно отправлена? Нажатие Submit может:
a) Показать ошибки валидации ("This is a required question").
b) Показать страницу подтверждения ("Raspunsul dvs. a fost inregistrat").
c) Редирект на другую страницу.

**Решение:**
- После клика Submit подождать 3 секунды.
- Проверить наличие ошибок валидации: искать текст "required" / "obligatoriu" /
  "обязательный" на странице. Если найдены -- статус FAIL, скриншот с ошибками.
- Проверить наличие текста подтверждения: "raspunsul" / "response" / "recorded" /
  "inregistrat" / "ответ записан". Если найден -- статус OK.
- Проверить, изменился ли URL (редирект на /formResponse).
- Если ни одно условие не сработало -- статус UNKNOWN, скриншот отправляется
  в Telegram, а пользователь решает сам.

### 7. Форма уже закрыта к моменту заполнения

**Проблема:** Watcher-бот поймал OPEN, Auto-Bot запустился, но за эти 10-30 секунд
форму уже закрыли (или лимит ответов достигнут).

**Решение:**
- Первым делом после загрузки страницы проверить, не закрыта ли форма.
- Маркеры закрытия: URL содержит "closedform", текст содержит
  "nu mai accepta" / "no longer accepting" / "не принимает".
- Если закрыта -- немедленный FAIL в Telegram: "Форма уже закрыта."
- Скриншот закрытой формы прикладывается.

### 8. Сетевые ошибки и таймауты

**Проблема:** Форма может грузиться долго (DDoS от других ботов при открытии),
DNS может не резолвиться, Playwright может зависнуть.

**Решение:**
- Общий таймаут на всю операцию: 120 секунд. Если за это время не получилось --
  FAIL, скриншот текущего состояния.
- Таймаут на загрузку страницы: 30 секунд.
- Таймаут на каждое действие (клик, ввод): 10 секунд.
- Retry при сетевой ошибке: 1 повторная попытка с паузой 5 секунд.
- При любом необработанном исключении: скриншот, лог ошибки, Telegram-уведомление.

### 9. Конфликт first_name / last_name / full_name

**Проблема:** Форма может спрашивать отдельно "Имя" и "Фамилия", или одним полем
"Имя и Фамилия", или в обратном порядке "Фамилия и Имя". Бот не должен запутаться.

**Решение:**
- В synonyms.yaml три отдельных записи: `first_name`, `last_name`, `full_name`.
- Паттерн `"Nume si Prenume"` -> full_name (содержит оба слова).
- Паттерн только `"Prenume"` без "Nume" рядом -> first_name.
- Паттерн только `"Nume"` без "Prenume" рядом -> last_name.
- Порядок проверки: сначала full_name (составной паттерн), потом first/last (простые).
- profile.yaml хранит все три варианта: full_name, first_name, last_name.

### 10. Двойная отправка

**Проблема:** Бот отправил форму, пользователь не получил Telegram-отчет (задержка),
и руками тоже отправил. Две заявки от одного человека.

**Решение:**
- Это ожидаемая ситуация, не баг. Две заявки лучше, чем ноль.
- В Telegram-отчете четко указывать: "ФОРМА ОТПРАВЛЕНА. Если ты тоже отправил --
  не страшно, лишнюю заявку проигнорируют."
- Если форма требует Google-аккаунт с "Ограничить 1 ответ", вторая отправка
  физически невозможна -- Google сам откажет.

### 11. Форма внутри iframe

**Проблема:** Если форма встроена в стороннюю страницу через iframe, Playwright
не увидит поля через обычные селекторы.

**Решение:**
- Не актуально для нашего случая. Best Opportunity использует прямую ссылку на
  Google Forms. Но на всякий случай: при загрузке проверить наличие iframe,
  и если есть -- переключиться через `page.frame_locator('iframe')`.

### 12. file_upload поля

**Проблема:** Форма может требовать загрузку файла (CV, скан паспорта).

**Решение:**
- В profile.yaml добавить секцию `files:` с путями к файлам.
- При обнаружении поля типа file_upload использовать `input_file.set_input_files()`.
- Если файл не указан в профиле -- FAIL с сообщением "Форма требует загрузку файла,
  который не указан в профиле."
- В первой версии: не реализуем. Если форма потребует файл -- FAIL. Вероятность
  низкая: Best Opportunity исторически не просила файлы.

---

## План реализации

| # | Задача | Фаза |
|---|--------|------|
| 1 | Структура проекта, .gitignore, requirements.txt | 1 |
| 2 | profile.example.yaml, synonyms.yaml, config.py | 1 |
| 3 | browser.py -- Playwright, persistent profile, --login, проверка сессии | 1 |
| 4 | analyzer.py -- парсинг полей через data-params и ARIA-роли | 1 |
| 5 | matcher.py -- словарь + fuzzy-matching + приоритет full_name над first/last | 1 |
| 6 | filler.py -- заполнение + обработка radio/checkbox/dropdown + Submit | 1 |
| 7 | filler.py -- верификация после Submit (ошибки валидации / подтверждение) | 1 |
| 8 | filler.py -- поддержка многостраничных форм (кнопка Next) | 1 |
| 9 | reporter.py -- скриншоты, JSON-лог, Telegram | 1 |
| 10 | __main__.py -- CLI (--url, --test, --login, --check-session) | 1 |
| 11 | Тестирование на 5+ разных Google Forms | 1 |
| 12 | llm_fallback.py -- Gemini API для нераспознанных полей | 2 |
| 13 | Интеграция с Watcher-ботом (автозапуск при OPEN) | 2 |
| 14 | Загрузка файлов (file_upload) | 2 |
