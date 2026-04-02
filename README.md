# ✈️ Israel Flights Monitor Bot

Telegram-бот мониторинга рейсов из Израиля. Проверяет рейсы каждые 15 минут и присылает уведомления о новых вариантах.

## 🚀 Деплой на Render (пошаговая инструкция)

### Шаг 1 — Залей код на GitHub

1. Зайди на https://github.com → кнопка **"New repository"**
2. Название: `israel-flights-bot`
3. Нажми **"Create repository"**
4. На следующей странице выбери **"uploading an existing file"**
5. Перетащи все 3 файла: `bot.py`, `requirements.txt`, `render.yaml`
6. Нажми **"Commit changes"**

### Шаг 2 — Создай сервис на Render

1. Зайди на https://render.com → войди через Google
2. Нажми **"New +"** → выбери **"Blueprint"**
3. Подключи свой GitHub аккаунт
4. Выбери репозиторий `israel-flights-bot`
5. Render сам найдёт `render.yaml` и предложит создать сервис
6. Нажми **"Apply"**

### Шаг 3 — Добавь секреты (Environment Variables)

После создания сервиса:
1. Зайди в сервис → вкладка **"Environment"**
2. Добавь две переменные:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | токен от @BotFather |
| `ANTHROPIC_API_KEY` | ключ с console.anthropic.com |

3. Нажми **"Save Changes"** — сервис перезапустится

### Шаг 4 — Получи Anthropic API ключ

1. Зайди на https://console.anthropic.com
2. Слева → **"API Keys"** → **"Create Key"**
3. Скопируй ключ (начинается с `sk-ant-...`)
4. Вставь в Environment Variables на Render

### Шаг 5 — Проверь что всё работает

1. Найди своего бота в Telegram
2. Напиши `/start`
3. Выбери направление — бот сразу сделает первый поиск

---

## 📱 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Подписаться на мониторинг |
| `/stop` | Остановить мониторинг |
| `/search` | Найти рейсы прямо сейчас |
| `/status` | Статус подписки |
| `/direction` | Сменить направление |
| `/add 123456789` | Добавить друга по его chat_id |

## 👥 Как добавить друга

1. Попроси друга написать боту `@userinfobot` в Telegram
2. Он получит своё число (chat_id)
3. Напиши боту: `/add ЕГО_ЧИСЛО`
4. Друг автоматически начнёт получать уведомления
