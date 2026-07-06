import os
import logging
import json
import requests
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import db

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# httpx logs full request URLs at INFO level, which includes the bot token.
# Silence it to WARNING so the token never ends up in logs or screenshots.
logging.getLogger("httpx").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT_RAW = os.getenv("MQTT_PORT")
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")

# Required config — no hardcoded fallbacks. A missing value here fails loudly
# and specifically at startup rather than silently connecting to a wrong
# broker/port or limping along with a guessable default credential.
_REQUIRED = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
    "MQTT_BROKER": MQTT_BROKER,
    "MQTT_PORT": MQTT_PORT_RAW,
    "MQTT_USER": MQTT_USER,
    "MQTT_PASS": MQTT_PASS,
}


def _check_required_env():
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        raise RuntimeError(
            "🚫 Missing required .env variable(s): "
            + ", ".join(missing)
            + " — check your .env file against env.example."
        )


_check_required_env()
MQTT_PORT = int(MQTT_PORT_RAW)

WEEKLY_MEAL_PLAN = {
    "monday": ["Moong Daal - Mugdhon", "Yellow moong Daal", "Kitchdi - Ringru/KARI-Potatoe", "Khatta binda", "KIDNEY BEANS", "SARAGWO"],
    "tuesday": ["Chicken Curry", "Chicken Tikka", "Steamed Chicken", "Grilled Chicken with Mash", "BUTTER CHICKEN"],
    "wednesday": ["Chicken pie", "Pasta", "Sheppards pie", "Jacket potatoe", "LASAGNE"],
    "thursday": ["Fish Curry", "Grilled Fish", "Steamed Fish", "Home made Fish & Chips", "SPINACH + PANEER"],
    "friday": ["Daal Chawal", "Biryani", "Yakni", "Nihaari - Daleem", "Chinese Palau"],
    "saturday": ["Chinese", "Pizza", "Take out", "Sausages + mash"],
    "sunday": ["Chip - burger @ Home", "Noodles", "Kebab roll", "Take out"]
}


def parse_uk_date(date_str: str) -> str | None:
    cleaned = date_str.replace('.', '-').replace('/', '-')
    match = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{2,4})$", cleaned)
    if not match:
        return None
    day, month, year = match.groups()
    if len(year) == 2:
        year = f"20{year}"
    try:
        validated_date = datetime(int(year), int(month), int(day))
        return validated_date.strftime("%Y-%m-%d")
    except ValueError:
        return None


def trigger_ha_note_event(text: str, author: str):
    if not HA_URL or not HA_TOKEN:
        logger.warning("⚠️ HA_URL/HA_TOKEN not set. Skipping event trigger pipeline dispatch.")
        return
    try:
        url = f"{HA_URL}/api/events/telegram_note_posted"
        headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
        payload = {"message": text, "sender": author}
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        if response.status_code in [200, 201]:
            logger.info(f"🔔 Dispatched event to HA for author: {author}")
    except Exception as e:
        logger.error(f"Failed to forward event packet to HA: {e}")


def publish_to_dashboard(topic: str, payload_dict: dict):
    try:
        client = mqtt.Client()
        if MQTT_USER and MQTT_PASS:
            client.username_pw_set(MQTT_USER, MQTT_PASS)
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        client.publish(topic, json.dumps(payload_dict), qos=1, retain=True)
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        logger.error(f"MQTT Publish sequence failure on topic {topic}: {e}")


def publish_shopping():
    publish_to_dashboard("home/dashboard/shopping_list", {"items": db.get_shopping()})


def publish_meals():
    publish_to_dashboard("home/dashboard/meal_plan", {"meals": db.get_meals()})


def publish_notes():
    publish_to_dashboard("home/dashboard/daily_notes", {"notes": db.get_daily_notes()})


def publish_appointments():
    publish_to_dashboard("home/dashboard/manual_appointments", {"events": db.get_appointments()})


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Family Hub Bot is online!\n\n"
        "✨ *The bot understands natural family phrasing!*\n\n"
        "🛒 *Shopping:* `need milk`, `buy apples`, `get eggs`, `put bread on the list`\n"
        "📋 *Notes:* `note don't lock the door`, `sticky remember keys`\n"
        "📅 *Appointments:* `appt 12/07/26 14:00 Dentist`, `book 15.07.26 Car MOT`\n"
        "🍽️ *Meals:* `meals`, `meal today pizza`\n"
        "🗑️ *Removing:* `remove milk`, `delete note 1`, `cancel appt 2`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_text = update.message.text.strip()
        user_name = update.message.from_user.first_name or "Family Member"
        low_text = raw_text.lower().strip()

        # 1. AUTO-PRUNE EXPIRED APPOINTMENTS
        db.prune_expired_appointments()

        # 2. SHOPPING COMPLETED NATURAL TRIGGERS
        if low_text in [
            "shopping done", "been to shopping", "done shopping", "clear shopping",
            "cleared shopping", "finished shopping", "emptied shopping"
        ]:
            db.clear_shopping()
            await update.message.reply_text("🛒 Shopping list completely cleared. Hope you got everything!")
            publish_shopping()
            return

        # 3. FLEXIBLE VIEW LIST COMMAND ROUTERS
        if re.match(r"^(list|view|show|get)\s+(shop|item|grocer)", low_text) or low_text in ["whats on the list", "what are we buying"]:
            items = db.get_shopping()
            if not items:
                await update.message.reply_text("🛒 Your shopping list is empty.")
            else:
                msg = "🛒 *Current Shopping List:*\n" + "\n".join(f"• {i}" for i in items)
                await update.message.reply_text(msg, parse_mode="Markdown")
            return

        elif low_text in ["list notes", "view notes", "notes", "show notes", "sticky notes"]:
            current_notes = db.get_daily_notes()
            if not current_notes:
                await update.message.reply_text("📋 No sticky notes are active.")
            else:
                msg = "📋 *Active Family Notes:*\n" + "\n".join(
                    f"*{n['index']}•* [{n['time']}] {n['text']}" for n in current_notes
                )
                await update.message.reply_text(msg, parse_mode="Markdown")
            return

        elif re.match(r"^(list|view|show)\s+(appt|calendar|event|sched)", low_text) or low_text in ["whats on today", "any appointments"]:
            appts = db.get_appointments()
            if not appts:
                await update.message.reply_text("📅 No manually tracked appointments found.")
            else:
                msg = "📅 *Manual Calendar Events (Chronological):*\n"
                for a in appts:
                    when = f"[{a['date']} {a['time'] or ''}]" if a['date'] else "[Unscheduled]"
                    msg += f"*{a['index']}•* {when} {a['title']}\n"
                await update.message.reply_text(msg, parse_mode="Markdown")
            return

        elif re.match(r"^(list|view|show)\s+(meal|menu|food|dinner)", low_text) or low_text in ["meals", "whats for dinner", "menu", "food"]:
            current_overrides = db.get_meals()
            now_dt = datetime.now()
            tom_dt = now_dt + timedelta(days=1)

            day_today = now_dt.strftime("%A").lower()
            day_tomorrow = tom_dt.strftime("%A").lower()

            if "today" in current_overrides:
                today_display = f"⚠️ *Override:* {current_overrides['today']}"
            else:
                rotation = WEEKLY_MEAL_PLAN.get(day_today, ["None configured"])
                today_display = "\n".join(f"• {m}" for m in rotation)

            if "tomorrow" in current_overrides:
                tomorrow_display = f"⚠️ *Override:* {current_overrides['tomorrow']}"
            else:
                rotation = WEEKLY_MEAL_PLAN.get(day_tomorrow, ["None configured"])
                tomorrow_display = "\n".join(f"• {m}" for m in rotation)

            msg = (
                f"🍽️ *Family Menu Outlook*\n\n"
                f"📅 *TODAY ({day_today.capitalize()}):*\n{today_display}\n\n"
                f"📅 *TOMORROW ({day_tomorrow.capitalize()}):*\n{tomorrow_display}"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        if low_text.startswith("list ") or low_text.startswith("view ") or low_text.startswith("show "):
            await update.message.reply_text("❓ Unknown parameter. Try tracking with `shop`, `notes`, `meals`, or `appts`.")
            return

        # --- NATURAL LANGUAGE REGEX DICTIONARY ENGINE ---
        note_match = re.match(r"^(?:note|sticky|remind|remember)\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        buy_match = re.match(r"^(?:buy|add|get|shop|need|want)(?:\s+some|\s+to|\s+more)?\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        put_on_list_match = re.match(r"^put\b[,\s]*(.*)\s+on\s+(?:the\s+)?list", raw_text, re.IGNORECASE)
        remove_match = re.match(r"^(?:remove|delete|cancel|clear|drop)\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        meal_today_match = re.match(r"^(?:meal|dinner|food)\s+today\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        meal_tomorrow_match = re.match(r"^(?:meal|dinner|food)\s+tomorrow\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        appt_match = re.match(r"^(?:appt|appointment|book|schedule)\b[,\s]*(.*)", raw_text, re.IGNORECASE)

        # 4. STICKY NOTES ACTIONS
        if note_match:
            note_content = note_match.group(1).strip()
            if note_content:
                db.add_daily_note(note_content, user_name)
                await update.message.reply_text(f"📋 Note posted: \"{note_content}\"")
                trigger_ha_note_event(note_content, user_name)
                publish_notes()
            return
        elif low_text in ["clear notes", "clear sticky", "delete notes"]:
            db.clear_daily_notes()
            await update.message.reply_text("📋 Notes stack cleared.")
            publish_notes()
            return

        # 5. MEAL PLAN OVERRIDES
        elif meal_today_match:
            meal_content = meal_today_match.group(1).strip()
            if meal_content:
                db.set_meal("today", meal_content)
                await update.message.reply_text(f"🍽️ Today's meal override: \"{meal_content}\"")
                publish_meals()
            return
        elif meal_tomorrow_match:
            meal_content = meal_tomorrow_match.group(1).strip()
            if meal_content:
                db.set_meal("tomorrow", meal_content)
                await update.message.reply_text(f"🍽️ Tomorrow's meal override: \"{meal_content}\"")
                publish_meals()
            return

        # 6. REMOVE ACTION (handles cancel/delete/remove for appts, notes, or shopping)
        elif remove_match:
            remaining = remove_match.group(1).strip()

            # --- Remove Appointment ---
            appt_rem = re.match(r"^(?:appt|appointment|book|event)\b[,\s]*(.*)", remaining, re.IGNORECASE)
            if appt_rem:
                target = appt_rem.group(1).strip()
                if target.isdigit():
                    target_idx = int(target)
                    if db.delete_appointment_by_index(target_idx):
                        await update.message.reply_text(f"🗑️ Deleted appointment #{target_idx} from your calendar.")
                        publish_appointments()
                    else:
                        await update.message.reply_text(f"❓ Appointment #{target_idx} doesn't exist.")
                else:
                    if db.delete_appointment_by_text(target):
                        await update.message.reply_text(f"🗑️ Removed appointment matching: \"{target}\"")
                        publish_appointments()
                    else:
                        await update.message.reply_text(f"❓ Appointment '{target}' not found.")
                return

            # --- Remove Note ---
            note_rem = re.match(r"^(?:note|sticky)\b[,\s]*(.*)", remaining, re.IGNORECASE)
            if note_rem:
                raw_target = note_rem.group(1).strip()
                if raw_target.isdigit():
                    target_idx = int(raw_target)
                    if db.delete_note_by_index(target_idx):
                        await update.message.reply_text(f"🗑️ Deleted note #{target_idx}.")
                        publish_notes()
                    else:
                        await update.message.reply_text(f"❓ Note #{target_idx} doesn't exist.")
                else:
                    if db.delete_note_by_text(raw_target):
                        await update.message.reply_text("🗑️ Deleted note matching phrase.")
                        publish_notes()
                    else:
                        await update.message.reply_text("❓ Note matching phrase not found.")
                return

            # --- Remove Shopping Item ---
            if remaining:
                if db.delete_shopping_item(remaining):
                    await update.message.reply_text(f"🗑️ Removed '{remaining}'.")
                    publish_shopping()
                else:
                    await update.message.reply_text(f"❓ '{remaining}' not found.")
            return

        elif low_text in ["clear appointments", "clear calendar"]:
            db.clear_appointments()
            await update.message.reply_text("📅 Appointments cleared.")
            publish_appointments()
            return

        # 7. ADD APPOINTMENTS ENGINE
        elif appt_match:
            rest = appt_match.group(1).strip()
            parts = rest.split(maxsplit=2)
            date_val, time_val, title_val = None, None, None

            if len(parts) == 3:
                parsed_iso = parse_uk_date(parts[0])
                if parsed_iso:
                    date_val, time_val, title_val = parsed_iso, parts[1], parts[2]
                else:
                    title_val = rest
            elif len(parts) == 2:
                parsed_iso = parse_uk_date(parts[0])
                if parsed_iso:
                    date_val, title_val = parsed_iso, parts[1]
                else:
                    title_val = rest
            else:
                title_val = rest

            if title_val:
                db.add_appointment(title_val, date=date_val, time=time_val)
                publish_appointments()
                display_when = f"on {date_val}" if date_val else "unscheduled"
                if time_val:
                    display_when += f" at {time_val}"
                await update.message.reply_text(f"📅 Appointment added ({display_when}): \"{title_val}\"")
            return

        # 8. SHOPPING PARSER ENGINE
        item_to_add = None
        if buy_match:
            item_to_add = buy_match.group(1).strip()
        elif put_on_list_match:
            item_to_add = put_on_list_match.group(1).strip()

        if item_to_add:
            if db.add_shopping(item_to_add):
                await update.message.reply_text(f"🛒 Added '{item_to_add}'.")
                publish_shopping()
            else:
                await update.message.reply_text(f"❌ '{item_to_add}' is already on the list!")
            return

        logger.info(f"Ignored casual non-command text: '{raw_text}'")

    except Exception as e:
        logger.exception(f"Error handling message: {e}")
        try:
            await update.message.reply_text("⚠️ Something went wrong processing that message. Check the bot logs.")
        except Exception:
            pass


def main():
    db.init_db()
    logger.info(f"💾 SQLite database ready at {db.DB_PATH}")
    db.prune_expired_appointments()

    # Broadcast baseline data states instantly so the dashboard reflects
    # persisted state immediately after a restart, without waiting for the
    # next command.
    publish_shopping()
    publish_meals()
    publish_notes()
    publish_appointments()

    # Static placeholder for the prayer times card — not yet wired to a
    # real prayer-time source, so these values never change on their own.
    # publish_to_dashboard("home/dashboard/prayer_times", {
    #     "Fajr": "02:36 AM",
    #     "Dhuhr": "01:11 PM",
    #     "Asr": "06:51 PM",
    #     "Maghrib": "09:32 PM",
    #     "Isha": "10:37 PM"
    # })

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 System boot secured. Telegram Dispatch Bot listening for commands...")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.critical(f"Polling failed — is another instance already running? {e}")


if __name__ == '__main__':
    main()