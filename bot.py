import os
import re
import json
from html import escape as html_escape
from io import BytesIO
from datetime import date
from datetime import datetime, timedelta
from urllib.parse import quote

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from gemini_client import Mess2MasterAI
from notion_client import NotionClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(BASE_DIR), ".env"))

ai = Mess2MasterAI()
TRIGGERS = [
    "deadline",
    "task",
    "assign",
    "need to",
    "finish",
    "due",
    "submit",
]
CHAT_STATE = {}
CONFIDENCE_THRESHOLD = 0.65
STRONG_SIGNAL_WORDS = ["due", "by", "friday", "monday", "assign", "need to", "finish", "submit"]
TASK_SPLIT_RE = re.compile(r"\s+(?:and also|also|and then|plus|,\s+and|;|\.|\n)\s+", re.I)


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


class InMemoryUpload:
    """Minimal file-like wrapper compatible with Mess2MasterAI.extract_tasks."""

    def __init__(self, filename: str, data: bytes, mimetype: str | None = None):
        self.filename = filename
        self.mimetype = mimetype
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


def extract_tasks_from_message(message_text: str, default_owner: str) -> list[dict]:
    tasks = []
    for clause in split_task_clauses(message_text):
        title = simplify_task_title(clause)
        if not title:
            continue

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


def build_actions_markup(task: dict) -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton("✅ Sync to Notion", callback_data="sync_notion")]
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

    # Follow-up mode: interpret normal replies as updates to the latest task card.
    state = CHAT_STATE.get(chat_id) if chat_id else None
    if state and apply_followup_updates(notes, state.get("tasks", [])):
        state["task_keys"] = {task_key(task) for task in state["tasks"]}
        await context.bot.edit_message_text(
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
        lowered = notes.lower()
        if not any(word in lowered for word in TRIGGERS):
            return
    default_owner = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "Unassigned"
    if chat_id and message.message_id:
        await set_reaction_safe(context, chat_id, message.message_id, "🧐")

    try:
        result = await build_ai_result(notes=notes, uploads=[])
        local_tasks = extract_tasks_from_message(notes, default_owner)

        if local_tasks:
            result["tasks"] = local_tasks
            result["project_name"] = result.get("project_name") or simplify_task_title(notes)
        elif result.get("tasks"):
            inferred_title = simplify_task_title(notes)
            inferred_due = infer_due_date(notes)
            inferred_time = infer_due_time(notes)
            for task in result["tasks"][:3]:
                if inferred_title:
                    task["title"] = inferred_title
                if inferred_due:
                    task["due_date"] = f"{inferred_due} {inferred_time}" if inferred_time else inferred_due
                task["owner"] = task.get("owner") or default_owner

        if chat_id and message.message_id:
            await set_reaction_safe(context, chat_id, message.message_id, "✅")

        if is_group_chat:
            confidence = estimate_confidence(result, notes)
            tasks = result.get("tasks", [])
            if not tasks:
                return
            if confidence < CONFIDENCE_THRESHOLD and not has_strong_signal(notes):
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
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state["message_id"],
                    text=format_task_list_message("✨ Mess2Master: Task Detected", state["tasks"], notes),
                    text=format_task_list_message("✨ Mess2Master: Task Detected", state["tasks"], notes),
                    parse_mode=ParseMode.HTML,
                    reply_markup=build_actions_markup(state["tasks"][-1]),
                )
                return

            if state and key in state["task_keys"]:
                return

            sent = await message.reply_text(
                format_task_list_message("✨ Mess2Master: Task Detected", tasks, notes),
                format_task_list_message("✨ Mess2Master: Task Detected", tasks, notes),
                parse_mode=ParseMode.HTML,
                reply_markup=build_actions_markup(tasks[0]),
            )
            CHAT_STATE[chat_id] = {
                "message_id": sent.message_id,
                "tasks": tasks,
                "task_keys": {task_key(task) for task in tasks},
            }
            return

        if local_tasks:
            result["tasks"] = local_tasks
        await message.reply_text(format_private_response(result), parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"Sync error: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.document:
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
        if notes:
            local_tasks = extract_tasks_from_message(notes, "Unassigned")
            if local_tasks:
                result["tasks"] = local_tasks
            elif result.get("tasks"):
                inferred_title = simplify_task_title(notes)
                inferred_due = infer_due_date(notes)
                inferred_time = infer_due_time(notes)
                for task in result["tasks"][:3]:
                    if inferred_title:
                        task["title"] = inferred_title
                    if inferred_due:
                        task["due_date"] = f"{inferred_due} {inferred_time}" if inferred_time else inferred_due
        await message.reply_text(format_private_response(result), parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"File processing error: {e}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
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

    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("Telegram bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
