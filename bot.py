import os
import re
import json
import time
import asyncio
from collections import deque
from html import escape as html_escape
from io import BytesIO
from datetime import date
from datetime import datetime, timedelta
from urllib.parse import quote

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from gemini_client import Mess2MasterAI
from notion_client import NotionClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(BASE_DIR), ".env"))
STATE_FILE = os.path.join(BASE_DIR, "data", "mess2master_state.json")
REMINDER_STATE_FILE = os.path.join(BASE_DIR, "data", "bot_reminder_state.json")

ai = Mess2MasterAI()
TRIGGERS = [
    "deadline",
    "task",
    "assign",
    "need to",
    "finish",
    "due",
    "submit",
    "@Mess2Master_Bot"
]
CHAT_STATE = {}
CHAT_CONTEXT = {}
CONTEXT_WINDOW_SIZE = int(os.getenv("CONTEXT_WINDOW_SIZE", "15"))
CONTEXT_LINE_MAX_CHARS = int(os.getenv("CONTEXT_LINE_MAX_CHARS", "120"))
CONTEXT_TOTAL_MAX_CHARS = int(os.getenv("CONTEXT_TOTAL_MAX_CHARS", "1800"))
CONFIDENCE_THRESHOLD = 0.65
STRONG_SIGNAL_WORDS = ["due", "by", "friday", "monday", "assign", "need to", "finish", "submit"]
TASK_SPLIT_RE = re.compile(r"\s+(?:and also|also|and then|plus|,\s+and|;|\.|\n)\s+", re.I)
AMBIGUOUS_TITLES = {"this", "that", "it", "do this", "do that", "follow up", "the task"}
ASSIGNEE_SUGGESTION_THRESHOLD = 3
REMINDER_SCAN_INTERVAL_SEC = int(os.getenv("REMINDER_SCAN_INTERVAL_SEC", "600"))
REMINDER_NEAR_DEADLINE_HOURS = int(os.getenv("REMINDER_NEAR_DEADLINE_HOURS", "24"))
REMINDER_STALLED_HOURS = int(os.getenv("REMINDER_STALLED_HOURS", "48"))
MAX_REMINDERS_PER_SCAN = int(os.getenv("MAX_REMINDERS_PER_SCAN", "3"))
WEB_APP_BASE_URL = (os.getenv("WEB_APP_BASE_URL") or "http://127.0.0.1:5000").rstrip("/")
SKILL_HINTS = {
    "writing": ["report", "draft", "write", "slides", "documentation"],
    "design": ["ui", "design", "wireframe", "frontend", "prototype"],
    "backend": ["api", "backend", "database", "schema", "auth"],
}
REMINDER_LOOP_TASK = None


def load_reminder_state() -> dict:
    if not os.path.exists(REMINDER_STATE_FILE):
        return {"subscribed_chats": [], "sent": {}}
    try:
        with open(REMINDER_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"subscribed_chats": [], "sent": {}}
            data.setdefault("subscribed_chats", [])
            data.setdefault("sent", {})
            return data
    except Exception:
        return {"subscribed_chats": [], "sent": {}}


def save_reminder_state(data: dict):
    os.makedirs(os.path.dirname(REMINDER_STATE_FILE), exist_ok=True)
    with open(REMINDER_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def is_unassigned(owner_value: str | None) -> bool:
    owner = str(owner_value or "").strip().lower()
    return (not owner) or owner == "unassigned"


def parse_task_deadline(task: dict) -> datetime | None:
    raw = str(task.get("deadline") or task.get("due_date") or "").strip()
    if not raw:
        return None

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                return dt.replace(hour=23, minute=59)
            return dt
        except ValueError:
            continue
    return None


def parse_task_created_at(task: dict) -> datetime | None:
    task_id = str(task.get("id") or "")
    m = re.match(r"^ts_(\d{9,12})", task_id)
    if not m:
        return None
    try:
        return datetime.fromtimestamp(int(m.group(1)))
    except Exception:
        return None


def project_slug(name: str) -> str:
    return re.sub(r"\s+", "-", (name or "").strip())


def website_project_url(project_name: str) -> str:
    slug = project_slug(project_name)
    return f"{WEB_APP_BASE_URL}/tasks#{quote(slug)}" if slug else f"{WEB_APP_BASE_URL}/tasks"


def reminder_assign_callback(task_key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "", (task_key or ""))[:50]
    return f"rem_assign:{safe}"


def build_reminder_markup(item: dict) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton("👤 Assign in Telegram", callback_data=reminder_assign_callback(item.get("task_key") or "")),
        InlineKeyboardButton("🌐 Open website project", url=website_project_url(item.get("project") or "")),
    ]
    return InlineKeyboardMarkup([row])


def find_task_in_website_state(task_key: str):
    state = load_website_state()
    for project in state.get("projects", []):
        for task in project.get("pending_tasks", []) or []:
            current_id = str(task.get("id") or "")
            if current_id and current_id == task_key:
                return project, task
    return None, None


def build_reminder_candidates(state_data: dict) -> list[dict]:
    now = datetime.now()
    reminders = []
    for project in state_data.get("projects", []):
        project_name = project.get("project_name") or "Untitled"
        for task in project.get("pending_tasks", []) or []:
            title = str(task.get("title") or task.get("task") or "Untitled").strip()
            owner = task.get("owner")
            deadline_dt = parse_task_deadline(task)
            created_at = parse_task_created_at(task)
            task_key_value = str(task.get("id") or f"{title}|{task.get('deadline') or task.get('due_date') or ''}")

            if is_unassigned(owner) and deadline_dt:
                delta_hours = (deadline_dt - now).total_seconds() / 3600.0
                if 0 <= delta_hours <= REMINDER_NEAR_DEADLINE_HOURS:
                    hours_left = max(1, int(round(delta_hours)))
                    reminders.append({
                        "rule": "unassigned_due_soon",
                        "task_key": task_key_value,
                        "task_id": str(task.get("id") or ""),
                        "project": project_name,
                        "title": title,
                        "message": f"⚠️ '{title}' is unassigned and due in about {hours_left}h. Assign now?",
                    })
                elif delta_hours < 0:
                    reminders.append({
                        "rule": "overdue_unassigned",
                        "task_key": task_key_value,
                        "task_id": str(task.get("id") or ""),
                        "project": project_name,
                        "title": title,
                        "message": f"🚨 '{title}' is overdue and still unassigned. Assign ownership immediately.",
                    })

            if is_unassigned(owner) and not deadline_dt and created_at:
                age_hours = (now - created_at).total_seconds() / 3600.0
                if age_hours >= REMINDER_STALLED_HOURS:
                    reminders.append({
                        "rule": "stalled_unassigned",
                        "task_key": task_key_value,
                        "task_id": str(task.get("id") or ""),
                        "project": project_name,
                        "title": title,
                        "message": f"🕒 '{title}' has no assignee and no deadline for over {REMINDER_STALLED_HOURS}h.",
                    })
    return reminders


async def run_reminder_scan(bot, force: bool = False) -> int:
    reminder_state = load_reminder_state()
    subscribed = [int(cid) for cid in reminder_state.get("subscribed_chats", [])]
    if not subscribed:
        return 0

    app_state = load_website_state()
    candidates = build_reminder_candidates(app_state)
    if not candidates:
        return 0

    sent_map = reminder_state.setdefault("sent", {})
    sent_count = 0

    for chat_id in subscribed:
        chat_items = []
        for item in candidates:
            item_key = item.get("task_id") or item.get("task_key")
            dedupe_key = f"{chat_id}|{item['rule']}|{item_key}"
            if (not force) and dedupe_key in sent_map:
                continue
            chat_items.append((dedupe_key, item))
            if len(chat_items) >= MAX_REMINDERS_PER_SCAN:
                break

        if not chat_items:
            continue

        try:
            now_iso = datetime.now().isoformat()
            for dedupe_key, item in chat_items:
                text = (
                    "<b>🧠 Proactive AI Reminder</b>\n"
                    f"<b>{html_escape(item['project'])}</b>\n"
                    f"{html_escape(item['message'])}\n\n"
                    "Take action now to prevent last-minute fire drills."
                )
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=build_reminder_markup(item),
                )
                sent_map[dedupe_key] = now_iso
                sent_count += 1
        except Exception as exc:
            print(f"Reminder send failed for chat {chat_id}: {exc}")

    save_reminder_state(reminder_state)
    return sent_count


async def reminder_loop(application):
    while True:
        try:
            await run_reminder_scan(application.bot)
        except Exception as exc:
            print(f"Reminder scan error: {exc}")
        await asyncio.sleep(max(60, REMINDER_SCAN_INTERVAL_SEC))


async def remind_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or not update.effective_message:
        return
    reminder_state = load_reminder_state()
    subscribed = set(int(cid) for cid in reminder_state.get("subscribed_chats", []))
    subscribed.add(chat.id)
    reminder_state["subscribed_chats"] = sorted(subscribed)
    save_reminder_state(reminder_state)
    await update.effective_message.reply_text(
        "✅ Proactive reminders enabled for this chat.\n"
        "I will alert on unassigned due-soon, overdue, and stalled tasks."
    )


async def remind_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or not update.effective_message:
        return
    reminder_state = load_reminder_state()
    reminder_state["subscribed_chats"] = [int(cid) for cid in reminder_state.get("subscribed_chats", []) if int(cid) != chat.id]
    save_reminder_state(reminder_state)
    await update.effective_message.reply_text("🛑 Proactive reminders disabled for this chat.")


async def remind_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or not update.effective_message:
        return
    reminder_state = load_reminder_state()
    subscribed = set(int(cid) for cid in reminder_state.get("subscribed_chats", []))
    enabled = chat.id in subscribed
    sent_total = len(reminder_state.get("sent", {}))
    await update.effective_message.reply_text(
        f"Reminder status: {'ON' if enabled else 'OFF'}\n"
        f"Scan interval: {REMINDER_SCAN_INTERVAL_SEC // 60} min\n"
        f"Near-deadline window: {REMINDER_NEAR_DEADLINE_HOURS}h\n"
        f"Total deduped alerts recorded: {sent_total}"
    )


async def remind_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    sent = await run_reminder_scan(context.bot, force=True)
    await update.effective_message.reply_text(f"✅ Manual reminder scan complete. Sent {sent} alert(s).")


async def on_post_init(application):
    global REMINDER_LOOP_TASK
    if REMINDER_LOOP_TASK is None or REMINDER_LOOP_TASK.done():
        REMINDER_LOOP_TASK = asyncio.create_task(reminder_loop(application))


async def on_post_shutdown(application):
    global REMINDER_LOOP_TASK
    if REMINDER_LOOP_TASK and not REMINDER_LOOP_TASK.done():
        REMINDER_LOOP_TASK.cancel()
        try:
            await REMINDER_LOOP_TASK
        except asyncio.CancelledError:
            pass
    REMINDER_LOOP_TASK = None


def normalize_alias_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def load_assignee_aliases() -> dict[str, str]:
    raw = (os.getenv("ASSIGNEE_ALIASES") or "").strip()
    aliases = {}
    if not raw:
        return aliases

    if raw.startswith("{") and raw.endswith("}"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    key = normalize_alias_key(str(k))
                    val = str(v).strip()
                    if key and val:
                        aliases[key] = val if val.startswith("@") else f"@{val}"
                return aliases
        except Exception:
            pass

    parts = [p.strip() for p in re.split(r"[;|]", raw) if p.strip()]
    for part in parts:
        if "=" in part:
            left, right = part.split("=", 1)
        elif ":" in part:
            left, right = part.split(":", 1)
        else:
            continue
        key = normalize_alias_key(left)
        val = right.strip()
        if key and val:
            aliases[key] = val if val.startswith("@") else f"@{val}"
    return aliases


ASSIGNEE_ALIASES = load_assignee_aliases()


def resolve_assignee(name: str) -> str:
    clean = (name or "").strip().rstrip(".,;:!? ")
    if not clean:
        return clean
    if clean.startswith("@"):
        return clean
    alias = ASSIGNEE_ALIASES.get(normalize_alias_key(clean))
    if alias:
        return alias
    # If not mapped and it's a single token, treat as a potential Telegram handle.
    if " " not in clean:
        return f"@{clean}"
    return clean


def get_context_buffer(chat_id: int):
    if chat_id not in CHAT_CONTEXT:
        CHAT_CONTEXT[chat_id] = deque(maxlen=CONTEXT_WINDOW_SIZE)
    return CHAT_CONTEXT[chat_id]


def add_context_message(chat_id: int, sender: str, text: str):
    buffer = get_context_buffer(chat_id)
    compact = shorten_text(text, 220)
    if compact:
        buffer.append({"sender": sender or "unknown", "text": compact})


def get_message_sender(message) -> str:
    user = message.from_user if message else None
    if not user:
        return "unknown"
    if user.username:
        return f"@{user.username}"

    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return full_name or "unknown"


def message_mentions_bot(message, bot_username: str | None) -> bool:
    if not message or not bot_username:
        return False

    def matches(text: str | None, entities) -> bool:
        content = text or ""
        if not content:
            return False

        for entity in entities or []:
            if getattr(entity, "type", "") != "mention":
                continue
            start = int(getattr(entity, "offset", 0))
            end = start + int(getattr(entity, "length", 0))
            token = content[start:end].strip().lower()
            if token.startswith("@") and token == f"@{bot_username.lower()}":
                return True

        return f"@{bot_username.lower()}" in content.lower()

    return matches(message.text, message.entities) or matches(message.caption, getattr(message, "caption_entities", None))


def build_rolling_context_notes(chat_id: int, current_sender: str, current_text: str, window_size: int = 15) -> str:
    entries = list(get_context_buffer(chat_id)) if chat_id else []
    if entries and entries[-1].get("sender") == current_sender and entries[-1].get("text") == shorten_text(current_text, 220):
        entries = entries[:-1]

    recent = entries[-max(1, window_size):]
    if not recent:
        return current_text

    context_lines = []
    total_chars = 0
    for entry in reversed(recent):
        sender = entry.get("sender") or "unknown"
        text = shorten_text(entry.get("text") or "", CONTEXT_LINE_MAX_CHARS)
        if not text:
            continue
        line = f"{sender}: {text}"
        if total_chars + len(line) > CONTEXT_TOTAL_MAX_CHARS:
            break
        context_lines.append(line)
        total_chars += len(line)

    context_lines.reverse()
    if not context_lines:
        return current_text

    context_block = "\n".join(context_lines)
    return (
        "Use this rolling group context before the current message. "
        "Prioritize concrete deadlines/owners and ignore small talk.\n\n"
        f"[Recent Group Context - last {len(context_lines)} messages]\n"
        f"{context_block}\n\n"
        "[Current Message]\n"
        f"{current_text}"
    )


def infer_topic_from_context(context_buffer: list[dict], current_text: str) -> str | None:
    current = (current_text or "").lower()
    for entry in reversed(context_buffer or []):
        candidate = simplify_task_title(entry.get("text") or "")
        if not candidate:
            continue
        if candidate.strip().lower() in AMBIGUOUS_TITLES:
            continue
        if candidate.lower() in current:
            continue
        return candidate
    return None


def suggest_assignee_from_context(task: dict, context_buffer: list[dict]) -> tuple[str | None, int]:
    title = (task.get("title") or "").lower()
    if not title:
        return None, 0

    # Prefer explicit handle/name mentions from recent context with matching task domain.
    scores: dict[str, int] = {}
    for entry in context_buffer or []:
        sender = entry.get("sender") or ""
        text = (entry.get("text") or "").lower()
        if not text:
            continue

        domain_bonus = 0
        for _, keywords in SKILL_HINTS.items():
            if any(k in title for k in keywords) and any(k in text for k in keywords):
                domain_bonus += 2

        ownership_phrase = any(p in text for p in ["i can", "i'll", "i will", "i like", "i'm good at", "i am good at"])
        mention_match = re.findall(r"@([a-z0-9_]{3,})", text, flags=re.I)
        for handle in mention_match:
            key = resolve_assignee(f"@{handle}")
            scores[key] = scores.get(key, 0) + 1 + domain_bonus

        if sender and sender != "unknown":
            key = resolve_assignee(sender)
            scores[key] = scores.get(key, 0) + (2 if ownership_phrase else 0) + domain_bonus

    if not scores:
        return None, 0
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0], int(best[1])


def attach_assignee_suggestions(tasks: list[dict], context_buffer: list[dict]):
    for task in tasks:
        owner = (task.get("owner") or "").strip().lower()
        if owner and owner != "unassigned":
            continue
        suggestion, confidence = suggest_assignee_from_context(task, context_buffer)
        task["needs_assignee"] = True
        if suggestion and confidence >= ASSIGNEE_SUGGESTION_THRESHOLD:
            task["assignee_suggestion"] = suggestion
            task["assignee_suggestion_confidence"] = confidence
        else:
            task["assignee_suggestion"] = None
            task["assignee_suggestion_confidence"] = confidence


class InMemoryUpload:
    """Minimal file-like wrapper compatible with Mess2MasterAI.extract_tasks."""

    def __init__(self, filename: str, data: bytes, mimetype: str | None = None):
        self.filename = filename
        self.mimetype = mimetype
        self.stream = BytesIO(data)

    def read(self):
        return self.stream.read()


async def build_ai_result(notes: str, uploads: list[InMemoryUpload]):
    sem_start = os.getenv("BOT_SEM_START", date.today().isoformat())
    sem_end = os.getenv("BOT_SEM_END", "2026-12-31")
    return ai.extract_tasks(files=uploads, notes=notes, sem_start=sem_start, sem_end=sem_end)


def shorten_text(text: str, limit: int = 120) -> str:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def infer_task_title(message_text: str) -> str:
    text = re.sub(r"^@\w+\s+", "", (message_text or "")).strip()
    text = re.sub(r"^(we\s+have|we\s+need|need\s+to|please|kindly)\s+", "", text, flags=re.I)
    split_markers = [" due ", " by ", " tomorrow", " today", " friday", " monday", " tuesday", " wednesday", " thursday", " saturday", " sunday"]
    cutoff = len(text)
    lowered = text.lower()
    for marker in split_markers:
        idx = lowered.find(marker)
        if idx != -1 and idx < cutoff:
            cutoff = idx
    title = text[:cutoff].strip(" .,!?:;-\n\t")
    title = re.sub(r"\s+", " ", title)
    if not title:
        title = text.strip()
    if len(title.split()) > 8:
        title = " ".join(title.split()[:8])
    return title.title()


def simplify_task_title(message_text: str) -> str:
    text = re.sub(r"^@\w+\s+", "", (message_text or "")).strip()
    lowered = text.lower()

    phrases = [
        "need to",
        "we need to",
        "we have to",
        "we have",
        "please",
        "kindly",
        "let's",
        "lets",
        "should",
        "must",
    ]
    for phrase in phrases:
        if lowered.startswith(phrase):
            text = text[len(phrase):].strip()
            lowered = text.lower()
            break

    title = re.split(r"\b(?:due|by|tomorrow|today|this friday|this monday|next monday|next friday)\b", text, flags=re.I)[0]
    title = re.split(r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}(?::\d{2})?\s?(?:am|pm))\b", title, maxsplit=1, flags=re.I)[0]
    title = re.sub(r"\b(?:we|i|you|they|everyone|team|group)\b", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" ,.!?:;-\n\t")
    if not title:
        return infer_task_title(message_text)

    words = title.split()
    if len(words) > 9:
        title = " ".join(words[:9])
    return title[:1].upper() + title[1:]


def explain_due_date_source(task: dict) -> str:
    source = (task.get("due_date_source") or "").lower()
    if source == "suggested":
        return " (suggested)"
    return ""


def infer_due_date(message_text: str) -> str | None:
    text = (message_text or "").lower()
    today = date.today()

    if "tomorrow" in text:
        due = today + timedelta(days=1)
    elif "today" in text:
        due = today
    else:
        explicit = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
        if explicit:
            day = int(explicit.group(1))
            month = int(explicit.group(2))
            year = int(explicit.group(3)) if explicit.group(3) else today.year
            if year < 100:
                year += 2000
            try:
                due = date(year, month, day)
            except ValueError:
                due = None
        else:
            due = None
            time_words = {"friday": 4, "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "saturday": 5, "sunday": 6}
            for name, idx in time_words.items():
                if name in text:
                    days_ahead = (idx - today.weekday()) % 7
                    if days_ahead == 0:
                        days_ahead = 7
                    due = today + timedelta(days=days_ahead)
                    break

    return due.isoformat() if due else None


def infer_due_time(message_text: str) -> str | None:
    text = (message_text or "").lower()
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s?(am|pm)\b", text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def split_task_clauses(message_text: str) -> list[str]:
    text = (message_text or "").strip()
    if not text:
        return []

    pieces = [part.strip() for part in TASK_SPLIT_RE.split(text) if part.strip()]
    if len(pieces) <= 1:
        return [text]

    task_like = [piece for piece in pieces if any(keyword in piece.lower() for keyword in ["due", "submit", "presentation", "report", "design", "testing", "fix", "deploy", "review", "assign", "finish", "prepare", "write"])]
    return task_like or pieces


def extract_tasks_from_message(message_text: str, default_owner: str, context_buffer: list[dict] | None = None) -> list[dict]:
    tasks = []
    inferred_topic = infer_topic_from_context(context_buffer or [], message_text)
    for clause in split_task_clauses(message_text):
        title = simplify_task_title(clause)
        if not title:
            continue

        if title.strip().lower() in AMBIGUOUS_TITLES and inferred_topic:
            title = inferred_topic

        due_date = infer_due_date(clause)
        due_time = infer_due_time(clause)
        if due_date and due_time:
            due_value = f"{due_date} {due_time}"
        else:
            due_value = due_date

        priority = "high" if has_strong_signal(clause) else "medium"
        tasks.append(
            {
                "title": title,
                "description": clause.strip(),
                "due_date": due_value,
                "priority": priority,
                "owner": default_owner,
            }
        )

    return tasks


def parse_owner_from_text(text: str) -> str | None:
    m = re.search(r"(?:assign(?:ed)?(?:\s+to)?|owner(?:\s+is)?|for)\s+(@?[A-Za-z0-9_][A-Za-z0-9_\s]{0,40})", text, flags=re.I)
    if m:
        name = m.group(1).strip().rstrip(".,;:!? ")
        return resolve_assignee(name)
    return None


def looks_like_followup(text: str) -> bool:
    lowered = (text or "").lower().strip()
    patterns = [
        r"^\d+\.",
        r"->\s*@?[a-z0-9_]+",
        r"\bassign\b",
        r"\bowner\b",
        r"\bdue\b",
        r"\bdeadline\b",
        r"\bmove\b",
        r"\bchange\b",
    ]
    return any(re.search(p, lowered) for p in patterns)


def should_show_details(task: dict) -> bool:
    title = (task.get("title") or "").strip()
    desc = (task.get("description") or "").strip()
    if not desc:
        return False

    title_norm = re.sub(r"\W+", " ", title).strip().lower()
    desc_norm = re.sub(r"\W+", " ", desc).strip().lower()
    desc_norm = re.sub(r"^(we have to|we need to|need to|please|kindly)\s+", "", desc_norm)
    if not title_norm:
        return len(desc) > 20

    if desc_norm == title_norm:
        return False
    if desc_norm.startswith(title_norm) and len(desc_norm) <= len(title_norm) + 32:
        return False
    return True


def apply_followup_updates(text: str, tasks: list[dict]) -> bool:
    if not text or not tasks:
        return False

    lowered = text.lower()
    changed = False

    # Example: "task 2 assign to lily" or "2 to lily"
    indexed_owner = re.search(r"(?:task\s*)?(\d+)\D{0,20}(?:assign(?:ed)?(?:\s+to)?|owner(?:\s+is)?|to)\s+(@?[A-Za-z0-9_][A-Za-z0-9_\s]{0,40})", text, flags=re.I)
    if indexed_owner:
        idx = int(indexed_owner.group(1)) - 1
        if 0 <= idx < len(tasks):
            owner_raw = indexed_owner.group(2).strip().rstrip(".,;:!? ")
            tasks[idx]["owner"] = resolve_assignee(owner_raw)
            changed = True

    # Example: "1. Submit slide -> SynYee"
    arrow_owner = re.search(r"^\s*(\d+)\D{0,80}->\s*(@?[A-Za-z0-9_][A-Za-z0-9_\s]{0,40})\s*$", text, flags=re.I)
    if arrow_owner:
        idx = int(arrow_owner.group(1)) - 1
        if 0 <= idx < len(tasks):
            owner_raw = arrow_owner.group(2).strip().rstrip(".,;:!? ")
            tasks[idx]["owner"] = resolve_assignee(owner_raw)
            changed = True

    # Example: "assign all to lily"
    all_owner = re.search(r"assign\s+(?:all|everyone|all tasks?)\s+to\s+(@?[A-Za-z0-9_][A-Za-z0-9_\s]{0,40})", text, flags=re.I)
    if all_owner:
        owner_raw = all_owner.group(1).strip().rstrip(".,;:!? ")
        owner = resolve_assignee(owner_raw)
        for task in tasks:
            task["owner"] = owner
        changed = True

    fallback_owner = parse_owner_from_text(text)
    if fallback_owner and not (indexed_owner or all_owner):
        # Apply to first task by default if user provides only one owner in free text.
        tasks[0]["owner"] = fallback_owner
        changed = True

    # Example: "task 2 deadline monday 5pm"
    indexed_due = re.search(r"(?:task\s*)?(\d+).{0,30}(?:deadline|due|by)\s+(.+)$", text, flags=re.I)
    if indexed_due:
        idx = int(indexed_due.group(1)) - 1
        due_phrase = indexed_due.group(2)
        due_date = infer_due_date(due_phrase)
        due_time = infer_due_time(due_phrase)
        if 0 <= idx < len(tasks) and due_date:
            tasks[idx]["due_date"] = f"{due_date} {due_time}" if due_time else due_date
            tasks[idx]["due_date_source"] = "explicit"
            changed = True

    # Example: "move deadline to friday 2pm" applies to first task by default.
    global_due = re.search(r"(?:deadline|due|by)\s+(.+)$", text, flags=re.I)
    if global_due and not indexed_due:
        due_phrase = global_due.group(1)
        due_date = infer_due_date(due_phrase)
        due_time = infer_due_time(due_phrase)
        if due_date:
            tasks[0]["due_date"] = f"{due_date} {due_time}" if due_time else due_date
            tasks[0]["due_date_source"] = "explicit"
            changed = True

    return changed


def estimate_confidence(result: dict, notes: str) -> float:
    tasks = result.get("tasks", [])
    if not tasks:
        return 0.0

    score = 0.25
    meta = result.get("_meta", {})
    if not meta.get("fallback"):
        score += 0.35
    if any(t.get("due_date") for t in tasks[:3]):
        score += 0.15
    if any((t.get("priority") or "").lower() == "high" for t in tasks[:3]):
        score += 0.1
    if len((notes or "").split()) >= 6:
        score += 0.1

    for gap in result.get("gaps", []):
        issue = (gap.get("issue") or "").lower()
        if "no files" in issue or "lack of specific project details" in issue:
            score -= 0.2

    return max(0.0, min(1.0, score))


def task_key(task: dict) -> str:
    return f"{(task.get('title') or '').strip().lower()}|{task.get('due_date') or 'tbd'}"


def has_strong_signal(text: str) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in STRONG_SIGNAL_WORDS)


def to_calendar_url(task: dict) -> str:
    title = quote(task.get("title") or "Mess2Master Task")
    title = quote(task.get("title") or "Mess2Master Task")
    due = task.get("due_date") or date.today().isoformat()
    if " " in due:
        date_part, time_part = due.split(" ", 1)
        start = f"{date_part.replace('-', '')}T{time_part.replace(':', '')}00"
        end = start
    else:
        date_part = due
        start = date_part.replace("-", "")
        end = start
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={title}&dates={start}/{end}&details=Task%20detected%20by%20Mess2Master"
        f"&text={title}&dates={start}/{end}&details=Task%20detected%20by%20Mess2Master"
    )


def notion_url() -> str | None:
    direct = os.getenv("NOTION_DATABASE_URL")
    if direct:
        return direct
    db_id = (os.getenv("NOTION_DATABASE_ID") or "").replace("-", "")
    return f"https://www.notion.so/{db_id}" if db_id else None


def load_website_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"semester": {}, "projects": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return {"semester": {}, "projects": data}
            return data
    except Exception:
        return {"semester": {}, "projects": []}


def save_website_state(data: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def website_task_id(seed: str) -> str:
    return f"ts_{int(time.time())}_{abs(hash(seed)) % 10000}"


def task_identity(task: dict) -> str:
    title = (task.get("title") or "").strip().lower()
    deadline = (task.get("deadline") or task.get("due_date") or "").strip().lower()
    return f"{title}|{deadline}"


def map_task_for_website(task: dict) -> dict:
    deadline = task.get("deadline") or task.get("due_date") or None
    title = (task.get("title") or "Untitled").strip()
    return {
        "id": task.get("id") or website_task_id(f"{title}|{deadline}"),
        "title": title,
        "description": (task.get("description") or "").strip(),
        "deadline": deadline,
        "priority": (task.get("priority") or "medium").lower(),
        "status": "pending",
        "owner": task.get("owner"),
        "follow_up": task.get("follow_up"),
    }


def sync_tasks_to_website(tasks: list[dict], project_name: str) -> dict:
    if not tasks:
        return {"status": "error", "message": "No tasks to sync", "synced_count": 0}

    clean_project = (project_name or "Telegram Inbox").strip()
    state = load_website_state()
    projects = state.setdefault("projects", [])
    project = next((p for p in projects if p.get("project_name") == clean_project), None)
    if not project:
        project = {
            "project_name": clean_project,
            "pending_tasks": [],
            "completed_tasks": [],
            "gaps": [],
            "sync_score": 75,
            "cross_insights": [],
        }
        projects.append(project)

    pending = project.setdefault("pending_tasks", [])
    completed = project.setdefault("completed_tasks", [])
    existing_keys = {task_identity(t) for t in pending + completed}

    synced_count = 0
    for task in tasks:
        mapped = map_task_for_website(task)
        key = task_identity(mapped)
        if key in existing_keys:
            continue
        pending.append(mapped)
        existing_keys.add(key)
        synced_count += 1

    save_website_state(state)
    return {
        "status": "success",
        "synced_count": synced_count,
        "project_name": clean_project,
    }


def build_actions_markup(task: dict) -> InlineKeyboardMarkup:
    row1 = [
        InlineKeyboardButton("✅ Sync to Notion", callback_data="sync_notion"),
        InlineKeyboardButton("🌐 Sync to Website", callback_data="sync_website"),
    ]
    row2 = [InlineKeyboardButton("📅 Add to Calendar", url=to_calendar_url(task))]
    row3 = [InlineKeyboardButton("👤 Add Assignee", callback_data="follow_up"), InlineKeyboardButton("❌ Dismiss", callback_data="dismiss_card")]
    return InlineKeyboardMarkup([row1, row2, row3])


def render_group_card(tasks: list[dict], source_text: str, username: str) -> str:
    heading = "<b>✨ Mess2Master: Task Detected</b>"
    heading = "<b>✨ Mess2Master: Task Detected</b>"
    if len(tasks) > 1:
        heading = f"<b>✨ Mess2Master: {len(tasks)} Tasks Detected</b>"
        heading = f"<b>✨ Mess2Master: {len(tasks)} Tasks Detected</b>"
    lines = [heading]
    for idx, task in enumerate(tasks, start=1):
        priority = (task.get("priority") or "medium").lower()
        icon = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
        title = html_escape(task.get("title") or "Untitled")
        due = html_escape(task.get("due_date") or "TBD")
        owner = html_escape(task.get("owner") or username)
        description = html_escape((task.get("description") or "").strip())
        follow_up = html_escape((task.get("follow_up") or "").strip())
        lines.append("")
        lines.append(f"<b>{idx}. 📝 Task:</b> {title}")
        lines.append(f"<b>📅 Deadline:</b> {due}{html_escape(explain_due_date_source(task))}")
        lines.append(f"<b>👤 Assignee:</b> {owner}")
        lines.append(f"<b>🎯 Priority:</b> {html_escape(priority.capitalize())} {icon}")
        if should_show_details(task):
            lines.append(f"<b>🧾 Details:</b> {description}")
        suggestion = task.get("assignee_suggestion")
        if suggestion and (not task.get("owner") or str(task.get("owner")).lower() == "unassigned"):
            lines.append(f"<b>🤝 Suggestion:</b> No assignee yet. Based on recent chat, assign to {html_escape(str(suggestion))}?")
        elif (not task.get("owner") or str(task.get("owner")).lower() == "unassigned"):
            lines.append("<b>👤 Assignee:</b> Need assignee")
        if follow_up:
            lines.append(f"<b>🔁 Follow-up:</b> {follow_up}")

    snippet = shorten_text("; ".join(task.get("description", "") for task in tasks if task.get("description")), 120)
    lines.append("")
    lines.append("<b>Detected from message:</b>")
    lines.append(f"<pre>{html_escape(snippet)}</pre>")
    return "\n".join(lines)


def format_private_response(result: dict) -> str:
    meta = result.get("_meta", {}) if isinstance(result, dict) else {}
    if meta.get("fallback"):
        reason = (meta.get("fallback_reason") or "Unknown AI error")
        brief = shorten_text(reason, 220)
        return (
            "<b>Mess2Master could not analyze this file yet.</b>\n"
            f"Reason: {html_escape(brief)}\n\n"
            "Try PDF/TXT/DOCX, or add a short caption describing the assignment scope."
        )

    tasks = result.get("tasks", [])[:3]
    if not tasks:
        return "I could not detect clear tasks yet. Add a due date and owner, or upload a brief."

    lines = [f"<b>{html_escape(result.get('project_name', 'Project'))}</b>", "", f"<b>Action Plan</b> · {len(result.get('tasks', []))} task(s) found"]
    for task in tasks:
        priority = (task.get("priority") or "medium").lower()
        icon = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
        title = html_escape(task.get("title") or "Untitled")
        due = html_escape(task.get("due_date") or "TBD")
        owner = html_escape(task.get("owner") or "Unassigned")
        follow_up = html_escape((task.get("follow_up") or "").strip())
        lines.append(f"• {icon} {title} — {due}{html_escape(explain_due_date_source(task))} — {owner}")
        if follow_up:
            lines.append(f"  ↳ {follow_up}")
    lines.append("")
    lines.append("<i>Next step: confirm owners and due dates in one message.</i>")
    return "\n".join(lines)


def format_task_list_message(title: str, tasks: list[dict], source_text: str) -> str:
    display_title = f"{title} · {len(tasks)} task(s)" if len(tasks) > 1 else title
    lines = [f"<b>{html_escape(display_title)}</b>"]
    for index, task in enumerate(tasks, start=1):
        priority = (task.get("priority") or "medium").lower()
        icon = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
        task_title = html_escape(task.get("title") or "Untitled")
        due_value = html_escape(task.get("due_date") or "TBD")
        owner_value = html_escape(task.get("owner") or "Unassigned")
        details = html_escape((task.get("description") or "").strip())
        follow_up = html_escape((task.get("follow_up") or "").strip())
        lines.append("")
        lines.append(f"<b>{index}. 📝 Task:</b> {task_title}")
        lines.append(f"<b>📅 Deadline:</b> {due_value}{html_escape(explain_due_date_source(task))}")
        lines.append(f"<b>👤 Assignee:</b> {owner_value}")
        lines.append(f"<b>🎯 Priority:</b> {html_escape(priority.capitalize())} {icon}")
        if should_show_details(task):
            lines.append(f"<b>🧾 Details:</b> {details}")
        suggestion = task.get("assignee_suggestion")
        if suggestion and (not task.get("owner") or str(task.get("owner")).lower() == "unassigned"):
            lines.append(f"<b>🤝 Suggestion:</b> No assignee yet. Based on recent chat, assign to {html_escape(str(suggestion))}?")
        elif (not task.get("owner") or str(task.get("owner")).lower() == "unassigned"):
            lines.append("<b>👤 Assignee:</b> Need assignee")
        if follow_up:
            lines.append(f"<b>🔁 Follow-up:</b> {follow_up}")

    summary = "; ".join(task.get("title", "") for task in tasks if task.get("title"))[:160]
    if summary:
        lines.append("")
        lines.append("<b>Detected tasks:</b>")
        lines.append(f"<pre>{html_escape(summary)}</pre>")
    return "\n".join(lines)


async def set_reaction_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, emoji: str):
    try:
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji)],
        )
    except Exception:
        pass


async def edit_message_text_safe(context: ContextTypes.DEFAULT_TYPE, **kwargs):
    try:
        await context.bot.edit_message_text(**kwargs)
    except BadRequest as exc:
        # Telegram returns this when content/markup is identical; safe to ignore.
        if "message is not modified" in str(exc).lower():
            return
        raise


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return

    notes = message.text or ""
    if not notes.strip():
        await message.reply_text("Send a short project update, or upload a file with optional caption.")
        return

    chat_type = (update.effective_chat.type if update.effective_chat else "private").lower()
    is_group_chat = chat_type in {"group", "supergroup"}
    chat_id = update.effective_chat.id if update.effective_chat else None
    sender_name = get_message_sender(message)
    bot_username = context.bot.username if context and context.bot else None
    mention_triggered = message_mentions_bot(message, bot_username)
    if chat_id:
        add_context_message(chat_id, sender_name, notes)

    if is_group_chat and not mention_triggered:
        return

    # Follow-up mode: interpret normal replies as updates to the latest task card.
    state = CHAT_STATE.get(chat_id) if chat_id else None
    if state and apply_followup_updates(notes, state.get("tasks", [])):
        state["task_keys"] = {task_key(task) for task in state["tasks"]}
        await edit_message_text_safe(
            context,
            chat_id=chat_id,
            message_id=state["message_id"],
            text=format_task_list_message("✨ Mess2Master: Task Updated", state["tasks"], notes),
            parse_mode=ParseMode.HTML,
            reply_markup=build_actions_markup(state["tasks"][0]),
        )
        if chat_id and message.message_id:
            await set_reaction_safe(context, chat_id, message.message_id, "✅")
        return

    # If we already have an active card and this message looks like a follow-up,
    # do not create new tasks from it.
    if state and looks_like_followup(notes):
        if chat_id and message.message_id:
            await set_reaction_safe(context, chat_id, message.message_id, "✅")
        return

    if is_group_chat:
        ai_notes = build_rolling_context_notes(chat_id, sender_name, notes, window_size=CONTEXT_WINDOW_SIZE)
    else:
        ai_notes = notes
    default_owner = "Unassigned" if is_group_chat else (f"@{message.from_user.username}" if message.from_user and message.from_user.username else "Unassigned")
    if chat_id and message.message_id:
        await set_reaction_safe(context, chat_id, message.message_id, "🧐")

    try:
        result = await build_ai_result(notes=ai_notes, uploads=[])
        context_buffer = list(get_context_buffer(chat_id)) if chat_id else []
        if result.get("tasks"):
            for task in result["tasks"]:
                if not task.get("owner"):
                    task["owner"] = "Unassigned"
                due_value = task.get("deadline") or task.get("due_date")
                task["deadline"] = due_value or None
                task["due_date"] = due_value or None
                if not task.get("due_date_source"):
                    task["due_date_source"] = "explicit" if due_value else "null"
            attach_assignee_suggestions(result["tasks"], context_buffer)
            result["project_name"] = result.get("project_name") or simplify_task_title(notes)

        if chat_id and message.message_id:
            await set_reaction_safe(context, chat_id, message.message_id, "✅")

        if is_group_chat:
            confidence = estimate_confidence(result, notes)
            tasks = result.get("tasks", [])
            if not tasks:
                if mention_triggered:
                    await message.reply_text(
                        "I couldn't extract structured tasks from that mention. Try adding a clear action, owner, or deadline."
                    )
                return
            if not mention_triggered and confidence < CONFIDENCE_THRESHOLD and not has_strong_signal(notes):
                return

            key = "|".join(sorted(task_key(task) for task in tasks))
            state = CHAT_STATE.get(chat_id)

            if state and key not in state["task_keys"]:
                existing_keys = state["task_keys"]
                new_tasks = [task for task in tasks if task_key(task) not in existing_keys]
                if not new_tasks:
                    return
                state["tasks"].extend(new_tasks)
                state["task_keys"].update(task_key(task) for task in new_tasks)
                await edit_message_text_safe(
                    context,
                    chat_id=chat_id,
                    message_id=state["message_id"],
                    text=format_task_list_message("✨ Mess2Master: Task Detected", state["tasks"], notes),
                    parse_mode=ParseMode.HTML,
                    reply_markup=build_actions_markup(state["tasks"][-1]),
                )
                return

            if state and key in state["task_keys"]:
                return

            sent = await message.reply_text(
                format_task_list_message("✨ Mess2Master: Task Detected", tasks, notes),
                parse_mode=ParseMode.HTML,
                reply_markup=build_actions_markup(tasks[0]),
            )
            CHAT_STATE[chat_id] = {
                "message_id": sent.message_id,
                "tasks": tasks,
                "task_keys": {task_key(task) for task in tasks},
                "project_name": result.get("project_name") or "Telegram Inbox",
            }
            return

        await message.reply_text(format_private_response(result), parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"Sync error: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.document:
        return

    chat_type = (update.effective_chat.type if update.effective_chat else "private").lower()
    is_group_chat = chat_type in {"group", "supergroup"}
    bot_username = context.bot.username if context and context.bot else None
    if is_group_chat and not message_mentions_bot(message, bot_username):
        return

    await message.reply_text("Reading your file...")
    try:
        telegram_file = await context.bot.get_file(message.document.file_id)
        raw = await telegram_file.download_as_bytearray()

        upload = InMemoryUpload(
            message.document.file_name or "upload.bin",
            bytes(raw),
            mimetype=message.document.mime_type,
        )
        upload = InMemoryUpload(
            message.document.file_name or "upload.bin",
            bytes(raw),
            mimetype=message.document.mime_type,
        )
        notes = message.caption or ""

        result = await build_ai_result(notes=notes, uploads=[upload])
        await message.reply_text(format_private_response(result), parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"File processing error: {e}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    if data.startswith("rem_assign:") and query.message:
        chat_id = query.message.chat_id
        reminder_task_key = data.split(":", 1)[1].strip()
        project, website_task = find_task_in_website_state(reminder_task_key)
        if not project or not website_task:
            await query.answer("Task no longer found. Refresh reminders with /remind_scan", show_alert=True)
            return

        task = {
            "id": website_task.get("id"),
            "title": website_task.get("title") or website_task.get("task") or "Untitled",
            "description": website_task.get("description") or "",
            "due_date": website_task.get("deadline") or website_task.get("due_date") or "",
            "owner": website_task.get("owner") or "Unassigned",
            "priority": website_task.get("priority") or "medium",
        }
        CHAT_STATE[chat_id] = {
            "message_id": query.message.message_id,
            "tasks": [task],
            "task_keys": {task_key(task)},
            "project_name": project.get("project_name") or "Telegram Inbox",
        }

        await edit_message_text_safe(
            context,
            chat_id=chat_id,
            message_id=query.message.message_id,
            text=format_task_list_message("✨ Assign This Task", [task], task.get("description") or ""),
            parse_mode=ParseMode.HTML,
            reply_markup=build_actions_markup(task),
        )
        await query.answer("Reply: assign to @username", show_alert=False)
        return

    if data == "dismiss_card" and query.message:
        chat_id = query.message.chat_id
        await query.message.delete()
        CHAT_STATE.pop(chat_id, None)
        await query.answer("Dismissed")
        return

    if data == "sync_notion" and query.message:
        chat_id = query.message.chat_id
        state = CHAT_STATE.get(chat_id)
        if not state or not state.get("tasks"):
            await query.answer("No tasks saved for this card.", show_alert=True)
            return

        try:
            notion = NotionClient()
            project_name = query.message.text.split("\n", 1)[0].replace("<b>", "").replace("</b>", "") if query.message.text else "Mess2Master"
            project_name = query.message.text.split("\n", 1)[0].replace("<b>", "").replace("</b>", "") if query.message.text else "Mess2Master"
            result = notion.sync_tasks(state["tasks"], project_name)
            if result.get("status") == "success":
                await query.answer(f"Synced {result.get('synced_count', 0)} task(s) to Notion")
            else:
                await query.answer(result.get("message", "Notion sync failed"), show_alert=True)
        except Exception as exc:
            await query.answer(f"Notion sync failed: {exc}", show_alert=True)
        return

    if data == "sync_website" and query.message:
        chat_id = query.message.chat_id
        state = CHAT_STATE.get(chat_id)
        if not state or not state.get("tasks"):
            await query.answer("No tasks saved for this card.", show_alert=True)
            return

        try:
            project_name = state.get("project_name") or "Telegram Inbox"
            result = sync_tasks_to_website(state["tasks"], project_name)
            if result.get("status") == "success":
                count = result.get("synced_count", 0)
                await query.answer(f"Synced {count} task(s) to website")
            else:
                await query.answer(result.get("message", "Website sync failed"), show_alert=True)
        except Exception as exc:
            await query.answer(f"Website sync failed: {exc}", show_alert=True)
        return

    if data == "follow_up" and query.message:
        chat_id = query.message.chat_id
        state = CHAT_STATE.get(chat_id)
        if not state or not state.get("tasks"):
            await query.answer("No saved task card to follow up on.", show_alert=True)
            return

        follow_lines = ["Reply with the owner for each task, for example:"]
        for index, task in enumerate(state["tasks"], start=1):
            title = task.get("title") or f"Task {index}"
            follow_lines.append(f"{index}. {title} -> owner name")
        follow_lines.append("You can also send one more detail if the deadline should be adjusted.")
        await query.answer("Follow-up prompt sent")
        await query.message.reply_text("\n".join(follow_lines))
        return

    if data == "notion_info":
        await query.answer("Set NOTION_DATABASE_URL in .env to enable one-tap Notion open.", show_alert=True)
        return

    await query.answer()


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is missing. Add it to your .env before running bot.py")

    app = ApplicationBuilder().token(token).post_init(on_post_init).post_shutdown(on_post_shutdown).build()
    app.add_handler(CommandHandler("remind_on", remind_on))
    app.add_handler(CommandHandler("remind_off", remind_off))
    app.add_handler(CommandHandler("remind_status", remind_status))
    app.add_handler(CommandHandler("remind_scan", remind_scan))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("Telegram bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
