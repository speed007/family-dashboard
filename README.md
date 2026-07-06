# Fixes applied - read this before deploying

## Round 3: four real deployment gremlins

- **`ha_automations.yaml` had a top-level `automation:` key wrapping
  everything.** Harmless if you paste individual blocks (as the how-to
  guide tells you to), but if you ever copied the *whole file* verbatim
  into an existing `automations.yaml` (referenced via `automation:
  !include automations.yaml` in `configuration.yaml`), you'd end up with
  one `automation:` key nested inside another and HA would fail to load
  it. **Fix:** the file is now a flat list, top to bottom - the same shape
  HA expects `automations.yaml` to already be. You can now safely use the
  whole file as a drop-in replacement for your `automations.yaml`, or
  paste any individual `- id: ...` block into an existing one at the same
  indentation level.

- **Appointment time parsing had no fallback for unparseable input.** The
  Jinja `strptime()` calls in `telegram_appointment_add` had no default
  value, so a time string that doesn't match either expected pattern (say,
  a stray `"4.30pm"` with a dot instead of a colon slipping past the bot's
  own regex) would throw a template rendering error and the appointment
  event creation would fail outright, rather than degrading gracefully.
  **Fix:** both `strptime()` calls now pass a third argument - a fallback
  default of 9:00 AM - so a malformed time no longer aborts the whole
  automation; worst case, the appointment lands with a default time
  instead of the one you typed, which you can then just edit in HA's
  calendar.

- **The shopping-list REST call used the pre-`todo`-platform pattern.**
  `get_shopping_list_items()` read `shopping_list.shopping_list`'s state
  attributes directly. HA's `shopping_list` integration has been backed by
  a `todo.shopping_list` entity under the `todo` platform for a while now,
  and how much of the item list still surfaces via the old entity's state
  attributes has shifted across HA versions - some setups will get an
  empty or incomplete list back from that call. **Fix:** the bot now tries
  the documented `todo.get_items` service first, and only falls back to
  reading `shopping_list.shopping_list`'s attributes if that fails (e.g.
  on an older HA version that predates the `todo` migration). Worth
  confirming which path your instance actually takes -
  **Developer Tools → Actions → `todo.get_items`**, target
  `todo.shopping_list`, is a quick way to check it exists on your setup.

- **Sequential, blocking HTTP calls inside an `async` handler.**
  `clear_shopping_list()` looped over every item and made one blocking
  `requests.post()` call after another - not wrapped in a thread, called
  directly inside `async def handle_message`. Since
  `python-telegram-bot`'s `Application` runs on a single asyncio event
  loop, any synchronous call made directly inside an `async def` blocks
  that *entire* loop for its duration - not just the reply to that one
  message, but every other message and the 30s MQTT publish job too, for
  as long as the HTTP calls take. A long shopping list made this
  meaningfully worse (more sequential round-trips), but the underlying
  problem exists even for a short list. **Fix:** `send_list` and
  `shopping_done` now run the HA calls via `asyncio.to_thread(...)` so
  they execute off the event loop, and `clear_shopping_list()` removes
  items through a small thread pool (5 at a time) instead of one at a
  time, so a long list clears faster too.

None of these require new manual setup steps or `.env` changes - just
redeploy `telegram_bot.py` and `ha_automations.yaml`.

---


## Round 2: dashboard topic race condition (read this if you deployed an earlier version)

Two bugs were found and fixed after the first round of fixes shipped:

- **`home/dashboard/shopping_list` and `home/dashboard/calendar_events` had
  two writers fighting over them.** `telegram_bot.py`'s
  `publish_dashboard_snapshot()` job publishes both topics every 30s from
  the bot's own SQLite tables - but `ha_automations.yaml` *also* had
  `publish_shopping_list` and `publish_calendar_events` automations
  publishing the same two topics from HA's own `shopping_list.shopping_list`
  and `calendar.family_events` entities, on their own timers. This is the
  exact bug that was already caught and avoided for `home/dashboard/meal_plan`
  in the first round, but it slipped through for these two topics. Whichever
  writer landed last would win, and the shopping-list version used a
  different payload shape (`{name, complete, id}` objects instead of the
  plain strings the bot sends), which could break `dashboard.jsx`'s
  rendering when HA's write won. **Fix:** removed both automations from
  `ha_automations.yaml`; the bot's snapshot job is now the single source of
  truth for all three dashboard topics, consistent with meal_plan. No
  action needed on your end beyond re-deploying the updated
  `ha_automations.yaml`.

- **`shopping done` only cleared HA's list, not the bot's own dedup
  table.** The bot's SQLite `shopping_items` table (used to reject
  same-day duplicates and to source the dashboard snapshot above) was
  never touched by the `shopping_done` / `clear the list` command - only
  Home Assistant's `shopping_list` entity was. In practice this meant the
  dashboard could keep showing "cleared" items until midnight, and
  re-adding an item you'd just cleared earlier the same day would get
  rejected as a duplicate. **Fix:** `shopping_done` now also deletes
  today's rows from the bot's `shopping_items` table
  (`DatabaseManager.clear_shopping_items()`), so both sides empty
  together. No config changes needed - just redeploy `telegram_bot.py`.

---


This is a corrected version of the family dashboard project. Below is what
changed and, more importantly, two manual one-time steps you still need to
do yourself (they can't be baked into the files).

## 1. Create the Mosquitto password file (required)

`mosquitto.conf` now requires authentication (`allow_anonymous false`)
instead of allowing anyone on your network to read/write your home
automation topics. Before starting the stack, generate a password file:

```bash
mkdir -p mosquitto/config
docker run --rm -v "$(pwd)/mosquitto/config:/mosquitto/config" \
  eclipse-mosquitto mosquitto_passwd -b -c /mosquitto/config/passwd mqtt_user YOUR_MQTT_PASSWORD
```

Replace `mqtt_user` and `YOUR_MQTT_PASSWORD` with whatever you put in your
`.env` as `MQTT_USER` / `MQTT_PASS`. This creates `mosquitto/config/passwd`,
which the broker reads on startup (already wired up in `mosquitto.conf` via
`password_file /mosquitto/config/passwd`).

## 2. Set the dashboard's MQTT connection details (required)

`dashboard.jsx` now reads its MQTT broker address/credentials from
`process.env.REACT_APP_MQTT_BROKER_WS` etc. instead of having them
hardcoded in the source. Create React App only exposes env vars prefixed
`REACT_APP_`, and only at **build** time - so before `npm run build`,
create a `.env` file inside the `dashboard/` folder (the one created by
`npx create-react-app dashboard`, not the bot's `.env`):

```
REACT_APP_MQTT_BROKER_WS=ws://192.168.1.XXX:9001
REACT_APP_MQTT_USER=mqtt_user
REACT_APP_MQTT_PASS=YOUR_MQTT_PASSWORD
```

Then `npm run build` as before. If you skip this, the dashboard falls back
to the placeholder `ws://YOUR_MQTT_IP:9001` and won't connect.

## What was fixed (summary)

- **NLP parser rewritten** (`telegram_bot.py`): the original regex patterns
  mis-captured text (e.g. "dentist Tuesday" parsed the title as just the
  letter "t"). It's now intent-classify-then-extract, and passes every
  example message from the Quick Start guide.
- **Bot now reads real env vars**: previously `MQTT_BROKER`,
  `TELEGRAM_BOT_TOKEN`, etc. were hardcoded constants in the script, so
  editing `.env` did nothing. `docker-compose.yml` also previously hardcoded
  placeholder values directly instead of loading `.env` - fixed with
  `env_file: - .env`.
- **Missing `Dockerfile` added** - `docker-compose.yml` referenced one that
  was never included.
- **SQLite connection leak fixed**: `add_shopping_item` didn't close its
  connection when rejecting a duplicate, which could exhaust connections /
  cause "database is locked" errors over time. Also added WAL mode + a busy
  timeout for resilience.
- **Dashboard meal plan now actually works**: the React dashboard had a
  meal-plan handler that called an empty stub function, so the meal columns
  were permanently stuck on "—". The bot now publishes real meal data on a
  30-second timer (`publish_dashboard_snapshot`) to the exact topic the
  dashboard subscribes to, and the dashboard renders it.
- **Fixed a topic mismatch**: Home Assistant was publishing meal updates to
  `home/mealplan/updated`, a topic nothing subscribed to. Removed (the bot's
  snapshot job is now the single source of truth for that topic, to avoid
  two writers fighting over it).
- **ESPHome YAML fixed**: the original had a Home-Assistant-style
  `automation:` block (`mqtt.publish:`, `logger.log:` as top-level
  actions) pasted into the ESPHome device config - this doesn't compile in
  ESPHome. Replaced with native `on_value`/`on_press`/`on_release` triggers
  on the sensors themselves, and ESPHome's `delayed_off` filter for the
  5-minute no-presence timeout instead of trying to replicate HA's
  `for: minutes:` condition.
- **Mosquitto now requires authentication** instead of `allow_anonymous
  true`, which made the MQTT_USER/MQTT_PASS used everywhere else pointless.
- **Calendar event time parsing in `ha_automations.yaml`** replaced
  fragile string-replace tricks on "2pm"/"2:30pm" with proper
  `strptime`/`strftime` Jinja filters. Worth testing against your actual HA
  version since Jinja helper availability can vary.
- **`requirements.txt`**: added the `[job-queue]` extra to
  `python-telegram-bot`, needed for the new scheduled dashboard-sync job.

## New features added

- **"send list" / "send shopping list" / "show the list"**: bot fetches the
  live shopping list from Home Assistant (`GET /api/states/shopping_list.shopping_list`)
  and posts it in the Telegram group, with ✅ for already-completed items.
- **"shopping done" / "clear the list"**: bot removes every item from HA's
  shopping list. Note that HA's `shopping_list` integration has no single
  "clear all" service - only `clear_completed_items`, which only removes
  items already checked off - so this calls `shopping_list.remove_item`
  once per item to actually empty it.
- **Alexa appointment announcements**: two new automations in
  `ha_automations.yaml` (`alexa_appointment_day_before`,
  `alexa_appointment_hour_before`) use HA's native `calendar` trigger with
  a time offset to announce upcoming appointments through your Echo via
  the Alexa Media Player integration. **You must update
  `media_player.kitchen_echo`** in both automations to your actual Echo's
  entity ID. Two things to know:
  - HA only re-reads calendars every ~15 minutes, so the actual
    announcement can land up to ~15 minutes off the offset.
  - These use the generic `notify.alexa_media` service with an explicit
    `target:`, not a per-device service name - per-device notify services
    (e.g. `notify.alexa_media_kitchen`) have been reported to disappear or
    rename across Alexa Media Player versions, so the generic form is the
    more stable choice.

These three features required adding `HA_URL` and `HA_TOKEN` (already
present as placeholders in `.env.example`, now actually used) - make sure
your Home Assistant long-lived access token is set there before deploying.

## Known limitations / things to verify yourself

- The Alexa announcement automations use documented HA + Alexa Media
  Player patterns, but I had no live HA instance or real Echo to test
  them against - confirm the `notify.alexa_media` service name and
  `target:` entity format match what's actually registered on your system
  (Developer Tools > Actions, search "alexa_media").
- The HA calendar trigger's offset is specified as `"-24:00:00"` /
  `"-01:00:00"` (HH:MM:SS) rather than a `days:` field, since day-scale
  offset support in the classic string format has varied across HA
  versions in community reports. If your HA version supports the
  structured `offset: {days: 1, ...}` form, that's equivalent and you can
  switch to it if you prefer.
- The HA Jinja template fix for appointment times uses `strptime` -
  confirm this works in your HA version before relying on it; if it
  doesn't, the calendar event will still get created but with a
  best-effort time.
- The NLP parser is much improved but still rule-based, not a real NLU
  model - unusual phrasing the rules don't anticipate will still go
  unmatched (it'll just silently not reply, same as before).
- I wasn't able to run a live ESPHome compile or a live HA instance to
  verify the YAML end-to-end (no network access in this environment) - the
  YAML is syntactically valid and uses documented ESPHome/HA patterns, but
  test it on real hardware before fully trusting it.