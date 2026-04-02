import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SUBSCRIBERS_FILE = "subscribers.json"
MONITOR_INTERVAL = 15 * 60  # 15 minutes

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Subscribers storage ─────────────────────────────────────────────
def load_subscribers():
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_subscribers(data):
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Flight search ───────────────────────────────────────────────────
def search_flights(dep="TLV", region="any"):
    region_label = {
        "any": "любое направление",
        "europe": "Европа",
        "cis": "Россия и СНГ",
        "us": "США и Канада",
        "asia": "Азия",
    }.get(region, "любое направление")

    today = datetime.now().strftime("%Y-%m-%d")
    date_limit = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")

    prompt = f"""Ты — ИИ-агент по поиску авиабилетов. Сегодня {today}.

Найди доступные рейсы из аэропорта {dep} (Израиль), направление: {region_label}, ближайшие 14 дней (до {date_limit}), 1 пассажир.

Приоритет: Hive Airlines, Air Haifa. Также: El Al, Wizz Air, Ryanair, Turkish Airlines, Aegean, Transavia.

JSON-массив 3–6 рейсов:
{{"airline":"","emoji":"","from":"","to":"","city":"","date":"YYYY-MM-DD","time":"HH:MM","duration":"Xч Yм","stops":0,"price_usd":0,"book_url":""}}

Только валидный JSON-массив."""

    message = anthropic_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text.strip()
    clean = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

def flight_key(f):
    return f"{f['airline']}-{f['from']}-{f['to']}-{f['date']}"

def build_search_links(f):
    """Build direct search links for aggregators with route pre-filled."""
    dep = f["from"]
    dst = f["to"]
    date = f["date"].replace("-", "")      # YYYYMMDD for some sites
    date_iso = f["date"]                    # YYYY-MM-DD for others

    links = [
        (f"🔍 Kayak", f"https://www.kayak.com/flights/{dep}-{dst}/{date_iso}?sort=price_a"),
        (f"🌐 Skyscanner", f"https://www.skyscanner.net/transport/flights/{dep.lower()}/{dst.lower()}/{date}/"),
        (f"✈️ Google", f"https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI1LTExLTAxagcIARID{dep}cgcIARID{dst}"),
        (f"🎯 Momondo", f"https://www.momondo.com/flights/{dep}-{dst}/{date_iso}"),
    ]

    # Add direct airline link if known
    airline_lower = f["airline"].lower()
    if "el al" in airline_lower:
        links.append(("🇮🇱 El Al", "https://www.elal.com/"))
    elif "wizz" in airline_lower:
        links.append(("💜 Wizz Air", f"https://wizzair.com/#/booking/select-flight/{dep}/{dst}/{date_iso}/null/1/0/0/"))
    elif "ryanair" in airline_lower:
        links.append(("🟡 Ryanair", f"https://www.ryanair.com/en/booking/new-route/{dep}/{dst}/{date_iso}"))
    elif "turkish" in airline_lower:
        links.append(("🇹🇷 Turkish", "https://www.turkishairlines.com/"))
    elif "hive" in airline_lower:
        links.append(("🐝 Hive", "https://www.hiveairlines.com/"))
    elif "haifa" in airline_lower or "air haifa" in airline_lower:
        links.append(("🛩 Air Haifa", "https://www.airhaifa.com/"))

    return links

def format_flight_message(flights, new_keys=None, check_num=1):
    if not flights:
        return "😔 Рейсы не найдены. Проверю снова через 15 минут."

    new_keys = new_keys or set()
    lines = []
    now = datetime.now().strftime("%H:%M")

    if new_keys and check_num > 1:
        lines.append(f"🚨 *Новые рейсы из Израиля!* (проверка #{check_num}, {now})\n")
    else:
        lines.append(f"✈️ *Рейсы из Израиля* (проверка #{check_num}, {now})\n")

    sorted_flights = sorted(flights, key=lambda x: x["price_usd"])

    for f in sorted_flights:
        is_new = flight_key(f) in new_keys
        new_badge = " 🆕" if is_new else ""
        stops = "🟢 прямой" if f["stops"] == 0 else "🟡 1 стоп"

        # Build inline links string
        search_links = build_search_links(f)
        links_str = "  🔗 " + " · ".join(f"[{name}]({url})" for name, url in search_links)

        lines.append(
            f"{f['emoji']} *{f['airline']}*{new_badge}\n"
            f"  {f['from']} → {f['to']} ({f['city']})\n"
            f"  📅 {f['date']} {f['time']} · ⏱ {f['duration']} · {stops}\n"
            f"  💵 *от ${f['price_usd']}*\n"
            f"{links_str}\n"
        )

    lines.append(f"_⚠️ Цены ориентировочные — уточняй на сайте перед покупкой_")
    lines.append(f"_🔄 Следующая проверка через 15 минут_")
    return "\n".join(lines)

# ── Commands ────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subs = load_subscribers()

    keyboard = [
        [InlineKeyboardButton("✈️ Любое направление", callback_data="region_any")],
        [InlineKeyboardButton("🇪🇺 Европа", callback_data="region_europe"),
         InlineKeyboardButton("🌍 СНГ/Россия", callback_data="region_cis")],
        [InlineKeyboardButton("🇺🇸 США/Канада", callback_data="region_us"),
         InlineKeyboardButton("🌏 Азия", callback_data="region_asia")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if chat_id not in subs:
        await update.message.reply_text(
            "✈️ *Мониторинг рейсов из Израиля*\n\n"
            "Бот проверяет рейсы каждые 15 минут и уведомляет о новых вариантах.\n\n"
            "Выбери направление:",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    else:
        dep = subs[chat_id].get("dep", "TLV")
        region = subs[chat_id].get("region", "any")
        await update.message.reply_text(
            f"✅ Ты уже подписан!\n"
            f"Аэропорт: *{dep}* · Направление: *{region}*\n\n"
            "Команды:\n"
            "/status — текущий статус\n"
            "/search — найти прямо сейчас\n"
            "/stop — остановить мониторинг\n"
            "/add — добавить друга\n"
            "/direction — сменить направление",
            parse_mode="Markdown"
        )

async def handle_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.from_user.id)
    region = query.data.replace("region_", "")

    subs = load_subscribers()
    subs[chat_id] = {
        "chat_id": chat_id,
        "name": query.from_user.first_name or "User",
        "dep": "TLV",
        "region": region,
        "active": True,
        "check_count": 0,
        "last_flight_keys": []
    }
    save_subscribers(subs)

    region_names = {"any":"любое","europe":"Европа","cis":"СНГ/Россия","us":"США/Канада","asia":"Азия"}
    await query.edit_message_text(
        f"✅ *Подписка активирована!*\n\n"
        f"🛫 Аэропорт: TLV (Тель-Авив)\n"
        f"🌍 Направление: {region_names.get(region, region)}\n\n"
        f"Первая проверка через несколько секунд...\n\n"
        f"Команды:\n"
        f"/stop — остановить\n"
        f"/search — найти прямо сейчас\n"
        f"/add — добавить друга\n"
        f"/direction — сменить направление",
        parse_mode="Markdown"
    )

    # Immediate first search
    await asyncio.sleep(3)
    await do_search_for_user(context, chat_id, subs[chat_id], force=True)

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subs = load_subscribers()
    if chat_id in subs:
        subs[chat_id]["active"] = False
        save_subscribers(subs)
        await update.message.reply_text("⏸ Мониторинг остановлен. Напиши /start чтобы возобновить.")
    else:
        await update.message.reply_text("Ты не был подписан. Напиши /start чтобы начать.")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subs = load_subscribers()
    if chat_id not in subs or not subs[chat_id].get("active"):
        await update.message.reply_text("❌ Мониторинг не активен. Напиши /start")
        return
    s = subs[chat_id]
    region_names = {"any":"любое","europe":"Европа","cis":"СНГ/Россия","us":"США/Канада","asia":"Азия"}
    await update.message.reply_text(
        f"📊 *Статус мониторинга*\n\n"
        f"✅ Активен\n"
        f"🛫 Аэропорт: {s.get('dep','TLV')}\n"
        f"🌍 Направление: {region_names.get(s.get('region','any'), 'любое')}\n"
        f"🔄 Проверок выполнено: {s.get('check_count', 0)}\n"
        f"⏱ Интервал: каждые 15 минут",
        parse_mode="Markdown"
    )

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subs = load_subscribers()
    if chat_id not in subs:
        await update.message.reply_text("Сначала подпишись — напиши /start")
        return
    await update.message.reply_text("🔍 Ищу рейсы прямо сейчас...")
    await do_search_for_user(context, chat_id, subs[chat_id], force=True)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a friend by chat_id"""
    chat_id = str(update.effective_chat.id)
    subs = load_subscribers()

    if not context.args:
        await update.message.reply_text(
            "👥 *Как добавить друга:*\n\n"
            "1. Попроси друга написать боту @userinfobot\n"
            "2. Он пришлёт число — его chat\\_id\n"
            "3. Напиши: `/add 123456789`\n\n"
            "Друг начнёт получать те же уведомления что и ты.",
            parse_mode="Markdown"
        )
        return

    friend_id = context.args[0].strip()
    if not friend_id.lstrip("-").isdigit():
        await update.message.reply_text("❌ Неверный формат. chat_id должен быть числом.")
        return

    my_settings = subs.get(chat_id, {"dep": "TLV", "region": "any"})
    subs[friend_id] = {
        "chat_id": friend_id,
        "name": f"Друг от {subs.get(chat_id, {}).get('name', 'пользователя')}",
        "dep": my_settings.get("dep", "TLV"),
        "region": my_settings.get("region", "any"),
        "active": True,
        "check_count": 0,
        "last_flight_keys": [],
        "added_by": chat_id
    }
    save_subscribers(subs)

    await update.message.reply_text(
        f"✅ Друг с ID `{friend_id}` добавлен!\n"
        f"Он будет получать уведомления о рейсах.",
        parse_mode="Markdown"
    )
    try:
        await context.bot.send_message(
            chat_id=int(friend_id),
            text=f"✈️ Тебя добавили в мониторинг рейсов из Израиля!\n"
                 f"Ты будешь получать уведомления каждые 15 минут.\n\n"
                 f"Напиши /start чтобы настроить своё направление.\n"
                 f"Напиши /stop чтобы отписаться.",
        )
    except Exception:
        await update.message.reply_text("⚠️ Не смог написать другу — пусть сначала напишет боту /start")

async def direction_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("✈️ Любое направление", callback_data="region_any")],
        [InlineKeyboardButton("🇪🇺 Европа", callback_data="region_europe"),
         InlineKeyboardButton("🌍 СНГ/Россия", callback_data="region_cis")],
        [InlineKeyboardButton("🇺🇸 США/Канада", callback_data="region_us"),
         InlineKeyboardButton("🌏 Азия", callback_data="region_asia")],
    ]
    await update.message.reply_text(
        "🌍 Выбери новое направление:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ── Monitor job ─────────────────────────────────────────────────────
async def do_search_for_user(context, chat_id, user_data, force=False):
    if not user_data.get("active") and not force:
        return
    try:
        dep = user_data.get("dep", "TLV")
        region = user_data.get("region", "any")
        flights = search_flights(dep, region)

        last_keys = set(user_data.get("last_flight_keys", []))
        cur_keys = set(flight_key(f) for f in flights)
        new_keys = cur_keys - last_keys if last_keys else set()

        subs = load_subscribers()
        if chat_id in subs:
            subs[chat_id]["last_flight_keys"] = list(cur_keys)
            subs[chat_id]["check_count"] = subs[chat_id].get("check_count", 0) + 1
            check_num = subs[chat_id]["check_count"]
            save_subscribers(subs)
        else:
            check_num = 1

        # Send only if new flights found (after first check) or forced
        if force or new_keys:
            msg = format_flight_message(flights, new_keys, check_num)
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=msg,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

    except Exception as e:
        logger.error(f"Error searching for {chat_id}: {e}")
        if force:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text="⚠️ Не удалось получить данные. Попробую снова через 15 минут."
            )

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    subs = load_subscribers()
    for chat_id, user_data in subs.items():
        if user_data.get("active"):
            await do_search_for_user(context, chat_id, user_data)
            await asyncio.sleep(2)  # small delay between users

# ── Main ────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("direction", direction_cmd))
    app.add_handler(CallbackQueryHandler(handle_region, pattern="^region_"))

    # Schedule monitor every 15 minutes
    app.job_queue.run_repeating(monitor_job, interval=MONITOR_INTERVAL, first=60)

    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
