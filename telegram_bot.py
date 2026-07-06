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

# Path to external meal configuration file
MEAL_PLAN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meal_plan.json")

def load_weekly_meal_plan():
    try:
        if os.path.exists(MEAL_PLAN_PATH):
            with open(MEAL_PLAN_PATH, "r", encoding="utf-8") as f:
                logger.info("🍽️ Successfully loaded external weekly meal plan configuration.")
                return json.load(f)
        else:
            logger.warning("⚠️ meal_plan.json not found! Falling back to empty menu defaults.")
            return {}
    except Exception as e:
        logger.error(f"❌ Failed to parse meal_plan.json: {e}")
        return {}

WEEKLY_MEAL_PLAN = load_weekly_meal_plan()


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
        "🛒 *Shopping:* `need milk`, `buy apples`, `get eggs`, `add to groceries bread`\n"
        "📋 *Notes:* `note don't lock the door`, `memo fix the tap`, `sticky remember keys`\n"
        "📅 *Appointments:* `appt 12/07/26 14:00 Dentist`, `schedule Sunday Family Dinner`\n"
        "🍽️ *Meals:* `meals`, `menu today pizza`, `eat friday pasta`\n"
        "🗑️ *Removing:* `remove milk`, `bought bread`, `delete note 1`, `cancel appt 2`"
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
            "cleared shopping", "finished shopping", "emptied shopping", "clear shopping list"
        ]:
            db.clear_shopping()
            await update.message.reply_text("🛒 Shopping list completely cleared. Hope you got everything!")
            publish_shopping()
            return

        # 3. FLEXIBLE VIEW LIST COMMAND ROUTERS
        if re.match(r"^(list|view|show|get)\s+(shop|item|grocer)", low_text) or low_text in ["whats on the list", "what are we buying", "what's on the list"]:
            items = db.get_shopping()
            if not items:
                await update.message.reply_text("🛒 Your shopping list is empty.")
            else:
                msg = "🛒 *Current Shopping List:*\n" + "\n".join(f"• {i}" for i in items)
                await update.message.reply_text(msg, parse_mode="Markdown")
            return

        elif low_text in ["list notes", "view notes", "notes", "show notes", "sticky notes", "memos"]:
            current_notes = db.get_daily_notes()
            if not current_notes:
                await update.message.reply_text("📋 No sticky notes are active.")
            else:
                msg = "📋 *Active Family Notes:*\n" + "\n".join(
                    f"*{n['index']}•* [{n['time']}] {n['text']}" for n in current_notes
                )
                await update.message.reply_text(msg, parse_mode="Markdown")
            return

        elif re.match(r"^(list|view|show)\s+(appt|calendar|event|sched)", low_text) or low_text in ["whats on today", "any appointments", "schedule", "calendar"]:
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

        elif re.match(r"^(list|view|show)\s+(meal|menu|food|dinner)", low_text) or low_text in ["meals", "whats for dinner", "what's for dinner", "menu", "food", "meal plan"]:
            current_overrides = db.get_meals()
            now_dt = datetime.now()
            tom_dt = now_dt + timedelta(days=1)

            day_today = now_dt.strftime("%A").lower()
            day_tomorrow = tom_dt.strftime("%A").lower()

            if "today" in current_overrides:
                today_display = f"⚠️ *Override:* {current_overrides['today']}"
            elif day_today in current_overrides:
                today_display = f"⚠️ *Override ({day_today.capitalize()}):* {current_overrides[day_today]}"
            else:
                rotation = WEEKLY_MEAL_PLAN.get(day_today, ["None configured"])
                today_display = "\n".join(f"• {m}" for m in rotation)

            if "tomorrow" in current_overrides:
                tomorrow_display = f"⚠️ *Override:* {current_overrides['tomorrow']}"
            elif day_tomorrow in current_overrides:
                tomorrow_display = f"⚠️ *Override ({day_tomorrow.capitalize()}):* {current_overrides[day_tomorrow]}"
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
        note_match = re.match(r"^(?:note|sticky|remind|remember|memo)\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        buy_match = re.match(r"^(?:buy|add\s+to\s+shopping\s+list|add\s+to\s+shopping|add\s+to\s+grocery|add\s+to\s+groceries|add|get|shop|need|want)(?:\s+some|\s+to|\s+more)?\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        put_on_list_match = re.match(r"^put\b[,\s]*(.*)\s+on\s+(?:the\s+)?list", raw_text, re.IGNORECASE)
        remove_match = re.match(r"^(?:remove|delete|cancel|clear|drop|bought)\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        
        # Enhanced Meal Parser supporting ad-hoc day-specific inputs (e.g. "menu monday pizza", "eat fri pasta")
        meal_match = re.match(r"^(?:meal|dinner|food|menu|eat)\s+(today|tomorrow|monday|mon|tuesday|tue|wednesday|wed|thursday|thu|friday|fri|saturday|sat|sunday|sun)\b[,\s]*(.*)", raw_text, re.IGNORECASE)
        appt_match = re.match(r"^(?:appt|appointment|book|schedule|event|calendar)\b[,\s]*(.*)", raw_text, re.IGNORECASE)

        # 4. STICKY NOTES ACTIONS
        if note_match and not low_text.startswith("note row") and not low_text.startswith("notes"):
            note_content = note_match.group(1).strip()
            if note_content:
                db.add_daily_note(note_content, user_name)
                await update.message.reply_text(f"📋 Note posted: \"{note_content}\"")
                trigger_ha_note_event(note_content, user_name)
                publish_notes()
                return
        elif low_text in ["clear notes", "clear sticky", "delete notes", "clear notes stack"]:
            db.clear_daily_notes()
            await update.message.reply_text("📋 Notes stack cleared.")
            publish_notes()
            return

        # 5. MEAL PLAN OVERRIDES
        elif meal_match:
            day_target = meal_match.group(1).lower()
            meal_content = meal_match.group(2).strip()
            
            # Map abbreviations back to full days for consistent indexing if not 'today'/'tomorrow'
            day_map = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}
            if day_target in day_map:
                day_target = day_map[day_target]

            if meal_content:
                db.set_meal(day_target, meal_content)
                await update.message.reply_text(f"🍽️ Overwrote {day_target.capitalize()} plan: \"{meal_content}\"")
                publish_meals()
            return
        elif low_text in ["clear menu", "clear meal plan", "reset menu", "delete menu"]:
            with db._connect() as conn:
                conn.execute("DELETE FROM meal_overrides")
                conn.commit()
            await update.message.reply_text("🍽️ Meal overrides cleared! Reverted back to the default rotation schedule.")
            publish_meals()
            return

        # 6. REMOVE ACTION (handles cancel/delete/remove for appts, notes, or shopping)
        elif remove_match:
            remaining = remove_match.group(1).strip()

            # --- Remove Appointment ---
            appt_rem = re.match(r"^(?:appt|appointment|book|event|schedule|calendar)\b[,\s]*(.*)", remaining, re.IGNORECASE)
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
            note_rem = re.match(r"^(?:note|sticky|memo)\b[,\s]*(.*)", remaining, re.IGNORECASE)
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
                    # Fallback context: check if they passed just a plain digit id to drop an item
                    await update.message.reply_text(f"❓ '{remaining}' not found.")
            return

        elif low_text in ["clear appointments", "clear calendar", "clear schedule"]:
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

        # 8. SHOPPING PARSER ENGINE & STRUCTURAL LIST PREFIX SYMBOLS
        item_to_add = None
        
        # Check if line begins with a bullet point marker block
        if raw_text.startswith(('-', '*', '▫️', '•')):
            item_to_add = re.sub(r"^[-\*▫️•]\s*", "", raw_text).strip()
        elif buy_match:
            item_to_add = buy_match.group(1).strip()
        elif put_on_list_match:
            item_to_add = put_on_list_match.group(1).strip()
        
        # Fallback to general capture rule if text does not fit alternative command structures
        elif len(raw_text) > 1 and not raw_text.startswith('/'):
            item_to_add = raw_text

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

    publish_shopping()
    publish_meals()
    publish_notes()
    publish_appointments()

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