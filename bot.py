"""
Israel Flights Bot - Telegram bot for monitoring flights from Israel
"""
import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, ConversationHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.getenv("BOT_TOKEN", "8758062682:AAGf4Ny1gZvg_us0U9RxQ0ZG7k1Jpky17do")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "762315584"))
CHECK_INTERVAL = 15 * 60  # seconds

AIRPORTS = ["TLV", "ETM", "HFA"]

DIRECTIONS = {
    "any":    [],  # empty = all
    "europe": ["LHR","CDG","AMS","FCO","MAD","BCN","FRA","VIE","ATH","PRG","WAW","BUD","LIS","CPH","ARN","HEL","OSL","ZRH","MXP","DUB"],
    "cis":    ["SVO","DME","VKO","LED","KBP","TBS","EVN","ALA","TSE","TAS","MSQ","RIX","TLL","VNO"],
    "usa":    ["JFK","LAX","ORD","MIA","BOS","SFO","EWR","IAD","ATL","DFW"],
    "asia":   ["BKK","DXB","DOH","IST","SIN","KUL","HKT","CMB","DEL","BOM","NRT","ICN"],
}

PRIORITY_AIRLINES = {
    "5H": "Hive Airlines",
    "IH": "Air Haifa",
    "LY": "El Al",
    "W6": "Wizz Air",
    "FR": "Ryanair",
    "TK": "Turkish Airlines",
}

DATA_FILE = Path("data.json")

# ─── DATA HELPERS ────────────────────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "users": {str(ADMIN_CHAT_ID): {"active": False, "direction": "any", "airports": AIRPORTS}},
        "seen_flights": []
    }

def save_data(data: dict):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ─── LINK GENERATORS ─────────────────────────────────────────────────────────
def kayak_link(origin: str, dest: str, date: str) -> str:
    d = date.replace("-", "")
    return f"https://www.kayak.com/flights/{origin}-{dest}/{d}/1adults"

def skyscanner_link(origin: str, dest: str, date: str) -> str:
    d = date.replace("-", "")
    return f"https://www.skyscanner.net/transport/flights/{origin.lower()}/{dest.lower()}/{d[2:]}/"

def google_link(origin: str, dest: str, date: str) -> str:
    return f"https://www.google.com/flights?hl=ru#flt={origin}.{dest}.{date}*{dest}.{origin};c:ILS;e:1;s:0*0;sd:1;t:f"

def momondo_link(origin: str, dest: str, date: str) -> str:
    d = date.replace("-", "")
    return f"https://www.momondo.com/flight-search/{origin}-{dest}/{d}/1adults"

def airline_direct_link(iata: str, origin: str, dest: str, date: str) -> str:
    urls = {
        "LY": f"https://www.elal.com/en/Book-a-Flight/Pages/FlightSearch.aspx?origin={origin}&destination={dest}&departureDate={date}",
        "W6": f"https://wizzair.com/en-gb/flights/{origin}/{dest}/{date}/null/1/0/0/null",
        "FR": f"https://www.ryanair.com/en/us/booking/home/{origin}/{dest}/{date}/null/1/0/0/null",
        "TK": f"https://www.turkishairlines.com/en-int/flights/find-a-flight/?from={origin}&to={dest}&date={date}&adult=1&child=0&infant=0&searchType=S",
    }
    return urls.get(iata, "")

def build_search_links(origin: str, dest: str, date: str, airline_code: str = "") -> str:
    links = [
        f"[Kayak]({kayak_link(origin, dest, date)})",
        f"[Skyscanner]({skyscanner_link(origin, dest, date)})",
        f"[Google Flights]({google_link(origin, dest, date)})",
        f"[Momondo]({momondo_link(origin, dest, date)})",
    ]
    direct = airline_direct_link(airline_code, origin, dest, date)
    if direct:
        name = PRIORITY_AIRLINES.get(airline_code, airline_code)
        links.append(f"[{name}]({direct})")
    return "  •  ".join(links)

# ─── FLIGHT SEARCH (Amadeus sandbox or fallback) ──────────────────────────────
AMADEUS_TOKEN: str | None = None
AMADEUS_TOKEN_EXPIRES: datetime | None = None

async def get_amadeus_token(client: httpx.AsyncClient) -> str | None:
    global AMADEUS_TOKEN, AMADEUS_TOKEN_EXPIRES
    key    = os.getenv("AMADEUS_API_KEY", "")
    secret = os.getenv("AMADEUS_API_SECRET", "")
    if not key or not secret:
        return None
    if AMADEUS_TOKEN and AMADEUS_TOKEN_EXPIRES and datetime.utcnow() < AMADEUS_TOKEN_EXPIRES:
        return AMADEUS_TOKEN
    try:
        r = await client.post(
            "https://test.api.amadeus.com/v1/security/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": key, "client_secret": secret},
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json()
            AMADEUS_TOKEN = d["access_token"]
            AMADEUS_TOKEN_EXPIRES = datetime.utcnow() + timedelta(seconds=int(d["expires_in"]) - 60)
            return AMADEUS_TOKEN
    except Exception as e:
        logger.warning(f"Amadeus token error: {e}")
    return None

async def search_flights_amadeus(client: httpx.AsyncClient, origin: str, dest: str, date: str) -> list[dict]:
    token = await get_amadeus_token(client)
    if not token:
        return []
    try:
        r = await client.get(
            "https://test.api.amadeus.com/v2/shopping/flight-offers",
            params={
                "originLocationCode": origin,
                "destinationLocationCode": dest,
                "departureDate": date,
                "adults": 1,
                "max": 5,
                "currencyCode": "ILS",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception as e:
        logger.warning(f"Amadeus search error: {e}")
    return []

def generate_search_batches(direction: str, airports: list[str]) -> list[tuple[str, str, str]]:
    """Generate (origin, dest, date) tuples for the next 14 days."""
    batches = []
    today = datetime.utcnow().date()
    dest_list = DIRECTIONS.get(direction, [])

    for day_offset in range(1, 15):
        date = (today + timedelta(days=day_offset)).isoformat()
        for origin in airports:
            if dest_list:
                for dest in dest_list:
                    batches.append((origin, dest, date))
            else:
                # "any" direction — just return origin so we show search links
                batches.append((origin, "ANY", date))
    return batches

# ─── NOTIFICATION BUILDER ────────────────────────────────────────────────────
def format_flight_message(origin: str, dest: str, date: str,
                           price: float | None = None,
                           airline_code: str = "",
                           airline_name: str = "",
                           departure_time: str = "") -> str:
    flag = {"TLV": "🇮🇱", "ETM": "🇮🇱", "HFA": "🇮🇱"}.get(origin, "✈️")
    dest_label = dest if dest != "ANY" else "все направления"
    lines = [
        f"{flag} *{origin} → {dest_label}*",
        f"📅 Дата: {date}",
    ]
    if departure_time:
        lines.append(f"🕐 Вылет: {departure_time}")
    if airline_name:
        lines.append(f"✈️ Авиакомпания: {airline_name}")
    if price:
        lines.append(f"💰 Цена: {price:.0f} ₪")
    lines.append("")
    lines.append("🔍 *Поиск и бронирование:*")
    lines.append(build_search_links(origin, dest if dest != "ANY" else "anywhere", date, airline_code))
    return "\n".join(lines)

# ─── MONITORING TASK ─────────────────────────────────────────────────────────
async def run_monitoring(app: Application):
    await asyncio.sleep(5)  # wait for bot to start
    logger.info("Monitoring started")
    async with httpx.AsyncClient() as client:
        while True:
            try:
                data = load_data()
                active_users = {uid: u for uid, u in data["users"].items() if u.get("active")}
                if not active_users:
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                # Aggregate unique (direction, airports) combos to avoid duplicate API calls
                seen = set(data.get("seen_flights", []))
                new_seen = set()
                notifications: dict[str, list[str]] = {uid: [] for uid in active_users}

                for uid, user in active_users.items():
                    direction = user.get("direction", "any")
                    airports  = user.get("airports", AIRPORTS)
                    batches   = generate_search_batches(direction, airports)

                    # Limit to avoid hammering the API on free tier
                    for origin, dest, date in batches[:30]:
                        flight_key = f"{origin}-{dest}-{date}"
                        if flight_key in seen:
                            continue

                        # Try Amadeus first
                        flights = await search_flights_amadeus(client, origin, dest, date)
                        new_seen.add(flight_key)

                        if flights:
                            for offer in flights[:2]:  # top 2 offers
                                try:
                                    price = float(offer["price"]["total"])
                                    seg   = offer["itineraries"][0]["segments"][0]
                                    al    = seg["carrierCode"]
                                    dep   = seg["departure"]["at"][11:16]
                                    name  = PRIORITY_AIRLINES.get(al, al)
                                    is_priority = al in PRIORITY_AIRLINES
                                    prefix = "⭐ " if is_priority else ""
                                    msg = prefix + format_flight_message(origin, dest, date, price, al, name, dep)
                                    notifications[uid].append(msg)
                                except Exception:
                                    pass
                        else:
                            # No API — just send a search-link reminder for priority routes
                            if dest != "ANY":
                                msg = format_flight_message(origin, dest, date)
                                notifications[uid].append(msg)

                # Send notifications
                for uid, msgs in notifications.items():
                    if msgs:
                        header = f"✈️ *Найдено {len(msgs)} рейс(ов) из Израиля!*\n\n"
                        chunk = header
                        for m in msgs[:5]:  # max 5 per check
                            if len(chunk) + len(m) > 3800:
                                await app.bot.send_message(
                                    chat_id=int(uid), text=chunk,
                                    parse_mode="Markdown", disable_web_page_preview=True
                                )
                                chunk = ""
                            chunk += m + "\n\n─────────────\n\n"
                        if chunk.strip():
                            await app.bot.send_message(
                                chat_id=int(uid), text=chunk,
                                parse_mode="Markdown", disable_web_page_preview=True
                            )

                # Update seen
                data["seen_flights"] = list(seen | new_seen)[-500:]  # keep last 500
                save_data(data)

            except Exception as e:
                logger.error(f"Monitoring error: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL)

# ─── COMMAND HANDLERS ────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    data = load_data()
    if uid not in data["users"]:
        data["users"][uid] = {"active": False, "direction": "any", "airports": AIRPORTS}
    data["users"][uid]["active"] = True
    save_data(data)
    await update.message.reply_text(
        "✈️ *Israel Flights Bot запущен!*\n\n"
        "Буду проверять рейсы каждые 15 минут и присылать уведомления.\n\n"
        "📌 *Команды:*\n"
        "/stop — остановить мониторинг\n"
        "/status — текущий статус\n"
        "/direction — выбрать направление\n"
        "/search — найти рейсы прямо сейчас\n"
        "/add <chat_id> — добавить друга\n",
        parse_mode="Markdown"
    )

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    data = load_data()
    if uid in data["users"]:
        data["users"][uid]["active"] = False
        save_data(data)
    await update.message.reply_text("⏹ Мониторинг остановлен. /start чтобы возобновить.")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    data = load_data()
    user = data["users"].get(uid, {})
    active    = user.get("active", False)
    direction = user.get("direction", "any")
    airports  = user.get("airports", AIRPORTS)
    total_users = sum(1 for u in data["users"].values() if u.get("active"))
    text = (
        f"📊 *Статус:*\n\n"
        f"{'✅ Активен' if active else '❌ Остановлен'}\n"
        f"🌍 Направление: *{direction}*\n"
        f"🛫 Аэропорты: {', '.join(airports)}\n"
        f"👥 Активных пользователей: {total_users}\n"
        f"⏱ Проверка каждые 15 минут"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_direction(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🌍 Любое", callback_data="dir_any")],
        [InlineKeyboardButton("🇪🇺 Европа", callback_data="dir_europe"),
         InlineKeyboardButton("🇺🇿 СНГ",    callback_data="dir_cis")],
        [InlineKeyboardButton("🇺🇸 США",    callback_data="dir_usa"),
         InlineKeyboardButton("🌏 Азия",    callback_data="dir_asia")],
    ]
    await update.message.reply_text(
        "Выберите направление для мониторинга:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def direction_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    direction = query.data.replace("dir_", "")
    data = load_data()
    if uid not in data["users"]:
        data["users"][uid] = {"active": False, "direction": "any", "airports": AIRPORTS}
    data["users"][uid]["direction"] = direction
    save_data(data)
    labels = {"any": "Любое", "europe": "Европа", "cis": "СНГ", "usa": "США", "asia": "Азия"}
    await query.edit_message_text(f"✅ Направление установлено: *{labels.get(direction, direction)}*", parse_mode="Markdown")

async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = str(update.effective_chat.id)
    data = load_data()
    user = data["users"].get(uid, {"direction": "any", "airports": AIRPORTS})
    direction = user.get("direction", "any")
    airports  = user.get("airports", AIRPORTS)

    await update.message.reply_text("🔍 Ищу рейсы, подождите...")

    today = datetime.utcnow().date()
    dest_list = DIRECTIONS.get(direction, []) or ["LHR", "CDG", "AMS", "FCO", "IST", "DXB"]
    messages = []

    async with httpx.AsyncClient() as client:
        for origin in airports:
            for dest in dest_list[:4]:
                for offset in [3, 7, 14]:
                    date = (today + timedelta(days=offset)).isoformat()
                    flights = await search_flights_amadeus(client, origin, dest, date)
                    if flights:
                        try:
                            offer = flights[0]
                            price = float(offer["price"]["total"])
                            seg   = offer["itineraries"][0]["segments"][0]
                            al    = seg["carrierCode"]
                            dep   = seg["departure"]["at"][11:16]
                            name  = PRIORITY_AIRLINES.get(al, al)
                            messages.append(format_flight_message(origin, dest, date, price, al, name, dep))
                        except Exception:
                            pass
                    else:
                        messages.append(format_flight_message(origin, dest, date))
                    if len(messages) >= 6:
                        break
                if len(messages) >= 6:
                    break
            if len(messages) >= 6:
                break

    if not messages:
        # Fallback: just generate search links
        for origin in airports[:2]:
            for dest in (dest_list or ["LHR", "CDG"])[:3]:
                date = (today + timedelta(days=7)).isoformat()
                messages.append(format_flight_message(origin, dest, date))

    for msg in messages[:5]:
        await update.message.reply_text(
            msg, parse_mode="Markdown", disable_web_page_preview=True
        )

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /add <chat_id>")
        return
    try:
        new_uid = str(int(ctx.args[0]))
    except ValueError:
        await update.message.reply_text("Неверный chat_id.")
        return
    data = load_data()
    data["users"][new_uid] = {"active": True, "direction": "any", "airports": AIRPORTS}
    save_data(data)
    await update.message.reply_text(f"✅ Пользователь {new_uid} добавлен и активирован.")
    try:
        await ctx.bot.send_message(
            chat_id=int(new_uid),
            text="✈️ Вас добавили в Israel Flights Bot! Напишите /start чтобы настроить уведомления."
        )
    except Exception:
        pass

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("stop",      cmd_stop))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("direction", cmd_direction))
    app.add_handler(CommandHandler("search",    cmd_search))
    app.add_handler(CommandHandler("add",       cmd_add))
    app.add_handler(CallbackQueryHandler(direction_callback, pattern="^dir_"))

    loop = asyncio.get_event_loop()
    loop.create_task(run_monitoring(app))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
