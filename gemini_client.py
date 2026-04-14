import os
import json
import time
import re
from datetime import date, timedelta
from io import BytesIO
from PyPDF2 import PdfReader
from google import genai
from google.genai import types

class Mess2MasterAI:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found. Check your .env file.")
        self.client = genai.Client(api_key=api_key)
        
        # ✅ Model fallback chain prioritizing 1.5 to avoid current 2.5 quota limits
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.model_fallbacks = [
            "gemini-2.5-flash",      # Standard model
            "gemini-2.5-flash-lite", # High daily quota, but currently exhausted
        ]

    def generate_task_guidance(self, task: dict):
        """Generate concise, actionable execution guidance for a single task."""
        title = (task.get("title") or task.get("task") or "Untitled Task").strip()
        priority = (task.get("priority") or "medium").strip().lower()
        deadline = (task.get("deadline") or task.get("due_date") or "TBD").strip()
        owner = (task.get("owner") or "Unassigned").strip()

        prompt = f"""
        You are a practical student productivity coach.

        Task title: {title}
        Priority: {priority}
        Deadline: {deadline}
        Assignee: {owner}

        Return plain text only, no markdown.
        Keep it concise and specific.
        Output exactly this structure:
        Start now:
        1) ...
        2) ...
        3) ...
        Optional snippet:
        ...

        Rules:
        - Max 3 steps.
        - Each step must be concrete and actionable.
        - Add optional snippet only when useful (coding tasks, command, or outline).
        - Keep total under 120 words.
        """

        last_error = None
        for model_name in dict.fromkeys(self.model_fallbacks):
            try:
                # 🚀 Reverted to simple generation for small tasks
                res = self.client.models.generate_content(
                    model=model_name,
                    contents=[prompt],
                    config=types.GenerateContentConfig(
                        max_output_tokens=260,
                        temperature=0.25,
                    )
                )
                text = (res.text or "").strip()
                if text:
                    return {"guidance": text, "used_model": model_name, "fallback": False}
            except Exception as e:
                last_error = e
                error_text = str(e)
                if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                    break

        fallback_text = self._fallback_task_guidance(title, priority, deadline)
        return {
            "guidance": fallback_text,
            "used_model": None,
            "fallback": True,
            "fallback_reason": str(last_error) if last_error else "model_unavailable",
        }

    def _fallback_task_guidance(self, title: str, priority: str, deadline: str) -> str:
        lower = (title or "").lower()

        if any(k in lower for k in ["report", "proposal", "essay", "documentation"]):
            return (
                "Start now:\n"
                "1) Draft a 5-line outline: objective, scope, method, result, next step.\n"
                "2) Write section 1 fully before polishing anything else.\n"
                "3) Set a 30-minute review block to tighten wording and references.\n"
                "Optional snippet:\n"
                "Outline: Intro -> Method -> Findings -> Conclusion"
            )

        if any(k in lower for k in ["flask", "backend", "api", "endpoint", "server"]):
            return (
                "Start now:\n"
                "1) Create one endpoint that returns a test JSON response.\n"
                "2) Wire request/response schema and validate one happy path.\n"
                "3) Add one failing test case before adding more features.\n"
                "Optional snippet:\n"
                "@app.get('/health') -> {'status':'ok'}"
            )

        if any(k in lower for k in ["presentation", "slides", "pitch"]):
            return (
                "Start now:\n"
                "1) Lock your 3-slide story: problem, solution, proof.\n"
                "2) Add one concrete demo screenshot or metric per slide.\n"
                "3) Rehearse a 90-second narration and trim extra details.\n"
                "Optional snippet:\n"
                "Slide order: Pain -> Demo -> Impact"
            )

        urgency = "high-priority" if priority == "high" else "standard"
        return (
            "Start now:\n"
            f"1) Define the first 20-minute {urgency} milestone for '{title}'.\n"
            "2) Complete one tangible output (draft, commit, or checklist).\n"
            f"3) Reserve a follow-up block before deadline ({deadline}).\n"
            "Optional snippet:\n"
            "Done criteria: one visible output + next action assigned"
        )

    def extract_tasks(self, files: list, notes: str, sem_start: str, sem_end: str,
                      existing_pending: list = None, break_week: int = 8):
        contents = []
        diagnostics = {
            "pdf_text_extracted": False,
            "used_model": None,
            "fallback": False,
            "fallback_reason": None,
        }
        
        mime_map = {
            'pdf': 'application/pdf', 'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'txt': 'text/plain', 'mp3': 'audio/mpeg', 'wav': 'audio/wav',
            'm4a': 'audio/mp4', 'ogg': 'audio/ogg', 'png': 'image/png',
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'
        }

        # === File Processing ===
        for file in files:
            try:
                filename = getattr(file, "filename", "") or "uploaded_file"
                ext = filename.rsplit('.', 1)[-1].lower() if "." in filename else ""
                file_bytes = file.read()
                
                # 1. FORCE PDF CHECK FIRST
                pdf_extracted = False
                try:
                    pdf_reader = PdfReader(BytesIO(file_bytes))
                    extracted_pages = []
                    for page in pdf_reader.pages[:5]:
                        page_text = page.extract_text() or ""
                        if page_text.strip():
                            extracted_pages.append(page_text.strip())
                    
                    extracted_text = "\n\n".join(extracted_pages).strip()
                    if extracted_text:
                        diagnostics["pdf_text_extracted"] = True
                        contents.append(
                            f"FILE: {filename}\nTYPE: pdf\nEXTRACTED_TEXT:\n{extracted_text[:12000]}"
                        )
                        pdf_extracted = True
                except Exception:
                    pass

                if pdf_extracted:
                    continue

                # 2. Standard MIME Check
                mime = getattr(file, "mimetype", None)
                if not mime or mime == 'application/octet-stream':
                    mime = mime_map.get(ext, 'text/plain')

                supported_mimes = [
                    'application/pdf', 'text/plain', 'text/csv', 'text/html', 'text/markdown', 
                    'image/png', 'image/jpeg', 'image/webp', 'image/heic', 'image/heif',
                    'audio/wav', 'audio/mp3', 'audio/aiff', 'audio/aac', 'audio/ogg', 'audio/flac'
                ]

                if mime not in supported_mimes:
                    print(f"⚠️ Skipping file {filename}: Unsupported format ({mime})")
                    continue 

                if len(file_bytes) > 2_000_000:
                    file_bytes = file_bytes[:2_000_000]

                contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime))
            except Exception as e:
                print(f"⚠️ Skipping file {getattr(file, 'filename', 'unknown')}: {e}")

        existing_json = json.dumps(existing_pending[:5] if existing_pending else [], indent=2)
        
        # 🚀 sync_score removed from prompt
        prompt = f"""
        You are Mess2Master, an academic task intelligence engine.
        
        SEMESTER CONTEXT:
        - Start: {sem_start}, End: {sem_end}
        - 1-week break between Week {break_week} and Week {break_week+1} (shift Week {break_week}+ deadlines by +7 days)
        
        EXISTING PENDING TASKS (PRESERVE THESE - DO NOT OVERWRITE):
        {existing_json}
        
        NEW INPUT: {len(files)} file(s) + notes: "{notes}"
        
        OUTPUT STRICT JSON ONLY. NO MARKDOWN. NO EXTRA TEXT.
        {{
            "project_name": "string",
            "tasks": [
                {{
                    "id": "string (use existing ID or 'ts_' + timestamp)",
                    "title": "string",
                    "description": "string",
                    "deadline": "YYYY-MM-DD or null",
                    "due_date_source": "explicit|suggested|null",
                    "priority": "high|medium|low",
                    "status": "pending",
                    "owner": "string or null",
                    "follow_up": "string or null"
                }}
            ],
            "gaps": [{{"issue": "string", "suggestion": "string"}}],
            "cross_insights": ["string"]
        }}
        
        CRITICAL RULES:
        1. MERGE, don't replace: Keep all existing task IDs. Only update fields if input explicitly changes them.
        2. ADD new tasks with unique IDs: Use format "ts_" + current timestamp (e.g., "ts_1713023400").
        3. Convert "Week X" to dates: Calculate from semester start. If Week >= {break_week}, add 7 days.
        4. NEVER mark tasks as completed via AI. Status must always be "pending".
        5. REMOVE tasks only if input explicitly says "cancelled" or "removed".
        6. If deadline is not explicit, leave deadline null and due_date_source null. Do not guess dates.
        7. Be specific: "Implement login UI with email/password" not "Do frontend".
        8. Extract ALL explicit assignments and deliverables. If a schedule lists recurring deliverables (e.g., weekly lab reports, code submissions), extract each week's deliverables as separate tasks. Do not artificially limit the total number of tasks.
        """

        last_error = None
        tried_models = []

        for model_name in dict.fromkeys(self.model_fallbacks):
            tried_models.append(model_name)
            
            max_attempts = 2 if "lite" in model_name or "8b" in model_name else 1 
            
            for attempt in range(max_attempts):
                try:
                    # 🚀 JSON SCHEMA PLACED HERE
                    extraction_schema = {
                        "type": "OBJECT",
                        "properties": {
                            "project_name": {"type": "STRING"},
                            "tasks": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "id": {"type": "STRING"},
                                        "title": {"type": "STRING"},
                                        "description": {"type": "STRING"},
                                        "deadline": {"type": "STRING", "nullable": True},
                                        "due_date_source": {"type": "STRING"},
                                        "priority": {"type": "STRING"},
                                        "status": {"type": "STRING"},
                                        "owner": {"type": "STRING", "nullable": True},
                                        "follow_up": {"type": "STRING", "nullable": True}
                                    }
                                }
                            },
                            "gaps": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "issue": {"type": "STRING"},
                                        "suggestion": {"type": "STRING"}
                                    }
                                }
                            },
                            "cross_insights": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"}
                            }
                        },
                        "required": ["project_name", "tasks", "gaps", "cross_insights"]
                    }

                    res = self.client.models.generate_content(
                        model=model_name,
                        contents=contents + [prompt],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=extraction_schema, # 🚀 THIS FORCES PERFECT JSON
                            max_output_tokens=8190,            # 🚀 GIVES IT ROOM TO BREATHE
                            temperature=0.2,
                        )
                    )
                    diagnostics["used_model"] = model_name
                    
                    raw = (res.text or "").strip()
                    if raw.startswith("```"):
                        raw = raw.split("```")[1].replace("json", "").strip()
                    
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError as e:
                        start = raw.find("{")
                        end = raw.rfind("}")
                        if start != -1 and end != -1 and end > start:
                            try:
                                parsed = json.loads(raw[start:end + 1])
                            except json.JSONDecodeError:
                                print(f"⚠️ JSON typo from {model_name} (Attempt {attempt+1}/{max_attempts}). Retrying...")
                                last_error = e
                                continue
                        else:
                            print(f"⚠️ JSON typo from {model_name} (Attempt {attempt+1}/{max_attempts}). Retrying...")
                            last_error = e
                            continue
                    
                    if isinstance(parsed, dict) and "tasks" in parsed:
                        for task in parsed["tasks"]:
                            if not task.get("id"):
                                task["id"] = f"ts_{int(time.time())}_{hash(task.get('title', '')) % 10000}"
                            task["status"] = "pending"  
                            task["deadline"] = task.get("deadline") or task.get("due_date") or None
                            task["due_date"] = task.get("due_date") or task.get("deadline") or None
                            owner = task.get("owner")
                            if isinstance(owner, str) and not owner.strip():
                                task["owner"] = None
                            if not task.get("due_date_source"):
                                task["due_date_source"] = "suggested" if task.get("deadline") else "null"

                        heuristic_tasks = self._extract_tasks_from_notes(notes, sem_start)
                        parsed["tasks"] = self._merge_task_lists(parsed["tasks"], heuristic_tasks)
                        
                        parsed["_meta"] = diagnostics
                        return parsed
                        
                except Exception as e:
                    last_error = e
                    error_text = str(e)
                    print(f"❌ AI Error with {model_name}: {error_text}")
                    
                    if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text or "spending cap" in error_text.lower():
                        diagnostics["used_model"] = model_name
                        return self._fallback(sem_start, notes=notes, reason=error_text, diagnostics=diagnostics)
                    
                    if "503" in error_text or "404" in error_text:
                        break

        print(f"❌ AI Error after trying {tried_models}: {last_error}")
        return self._fallback(sem_start, notes=notes, reason=str(last_error), diagnostics=diagnostics)

    def _fallback(self, sem_start, notes="", reason=None, diagnostics=None):
        extracted = self._extract_tasks_from_notes(notes, sem_start)
        
        if not extracted:
            extracted = self._regex_date_fallback(notes, sem_start)

        if not extracted:
            ts = int(time.time())
            extracted = [{
                "id": f"ts_{ts}_dummy",
                "title": "Review syllabus and assign deadlines manually",
                "description": "AI models were unavailable and no clear dates were found in the text.",
                "deadline": sem_start,
                "due_date_source": "suggested",
                "priority": "high",
                "status": "pending",
                "owner": None,
                "follow_up": "Check original PDF for schedule"
            }]

        # 🚀 sync_score removed from fallback payload
        payload = {
            "project_name": "Mess2Master Fallback (Regex Mode)",
            "tasks": extracted[:20], 
            "gaps": [{"issue": "AI was unavailable. Tasks were generated via Regex.", "suggestion": "Review descriptions to ensure they make sense."}],
            "cross_insights": []
        }
        
        payload["_meta"] = diagnostics or {}
        payload["_meta"]["fallback"] = True
        payload["_meta"]["fallback_reason"] = reason or "All models failed"
        
        return payload

    def _extract_tasks_from_notes(self, notes: str, sem_start: str) -> list:
        text = (notes or "").strip()
        if not text:
            return []

        default_year = self._year_from_sem_start(sem_start)
        normalized = re.sub(r"\s+", " ", text)
        normalized = re.sub(r"(?i)\b(next|also|wait|first|then|finally|meanwhile|additionally|and also|oh and)\b", r". \1", normalized)
        normalized = re.sub(
            r"\s+(?=[A-Z][a-z]+(?:,)?\s+(?:can you|please|you'll|you have|you've|you can|we need|we have|take|draft|design|write|prepare|check|confirm|handle|since))",
            ". ",
            normalized,
        )
        clauses = [c.strip(" \t\n\"'") for c in re.split(r"(?<=[.!?])\s+", normalized) if c.strip()]
        tasks = []
        seen = set()
        last_task = None

        for clause in clauses:
            owner = None
            action = None
            deadline_iso = None
            due_source = "null"

            m_owner = re.search(
                r"\b(?P<owner>[A-Z][a-z]+)(?:,)?\s*(?:since[^,]*,?\s*)?(?:can you|please|you(?:'ll| will)|you've[^,]*,?\s*so please|you have|you can|take|draft|design|write|prepare|check|confirm|handle)\s+(?P<action>.+)",
                clause,
                flags=re.IGNORECASE,
            )
            if m_owner:
                owner = m_owner.group("owner")
                action = m_owner.group("action")

                if action and re.fullmatch(r"(?:handle|take|do)?(?:\s+(?:that|this|it|the same))?[?.!]*", action.strip(), flags=re.IGNORECASE):
                    if last_task and not last_task.get("owner"):
                        last_task["owner"] = owner
                    continue

            if not action:
                m_need = re.search(r"\b(?:we need to|we have to|please)\s+(?P<action>.+)", clause, flags=re.IGNORECASE)
                if m_need:
                    action = m_need.group("action")

            if not action and "peer evaluation" in clause.lower() and "due" in clause.lower():
                action = "Submit peer evaluation forms"

            if not action and ("haven't assigned anyone to" in clause.lower() or "not assigned" in clause.lower()):
                m_gap = re.search(r"(?:haven't assigned anyone to|not assigned to)\s+(?P<action>.+)", clause, flags=re.IGNORECASE)
                action = m_gap.group("action") if m_gap else clause

            if not action:
                if last_task and not last_task.get("deadline"):
                    inferred_date, inferred_source = self._extract_deadline(clause, default_year)
                    if inferred_date:
                        last_task["deadline"] = inferred_date
                        last_task["due_date_source"] = inferred_source
                continue

            deadline_iso, due_source = self._extract_deadline(clause, default_year)

            action = re.sub(r"\b(?:by|before|due|deadline)\b.+$", "", action, flags=re.IGNORECASE).strip(" .,")
            action = re.sub(r"\b(?:that|soon|should work)\b$", "", action, flags=re.IGNORECASE).strip(" .,")
            action = re.sub(r"\b(?:i'?m|we should|let'?s|okay that'?s it|that'?s it|but no one'?s assigned.*|wait|also|oh and).*$", "", action, flags=re.IGNORECASE).strip(" .,")
            if not action:
                continue

            action_variants = self._split_compound_action(action)
            for variant in action_variants:
                title = self._title_from_action(variant)
                dedupe_key = (title.lower(), (deadline_iso or ""), (owner or "").lower())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                task = {
                    "id": f"ts_{int(time.time())}_{len(tasks)}",
                    "title": title,
                    "description": variant,
                    "deadline": deadline_iso,
                    "due_date_source": due_source,
                    "priority": "high" if deadline_iso else "medium",
                    "status": "pending",
                    "owner": owner,
                    "follow_up": None if owner else "Assign owner"
                }
                tasks.append(task)
                last_task = task

        return tasks

    def _split_compound_action(self, action: str) -> list[str]:
        text = re.sub(r"\s+", " ", (action or "")).strip(" .,")
        if not text:
            return []

        if re.search(r"\bor\b", text, flags=re.IGNORECASE):
            parts = [part.strip(" .,") for part in re.split(r"\bor\b", text, flags=re.IGNORECASE) if part.strip()]
            if len(parts) >= 2 and all(len(part) >= 4 for part in parts):
                return parts

        return [text]

    def _merge_task_lists(self, primary_tasks: list, secondary_tasks: list) -> list:
        merged = []
        seen = set()

        for source in (primary_tasks or [], secondary_tasks or []):
            for task in source:
                title = self._title_from_action(str(task.get("title") or task.get("description") or ""))
                deadline = task.get("deadline") or task.get("due_date") or ""
                owner = (task.get("owner") or "").strip().lower()
                key = (title.lower(), deadline, owner)
                if key in seen:
                    continue
                seen.add(key)

                normalized = dict(task)
                normalized["title"] = title
                normalized["deadline"] = normalized.get("deadline") or normalized.get("due_date")
                normalized["status"] = "pending"
                if not normalized.get("due_date_source"):
                    normalized["due_date_source"] = "suggested" if normalized.get("deadline") else "null"
                merged.append(normalized)

        return merged

    def _extract_deadline(self, text: str, default_year: int):
        month_pattern = re.search(
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?",
            text,
            flags=re.IGNORECASE,
        )
        if month_pattern:
            month_name = month_pattern.group(1).lower()
            day = int(month_pattern.group(2))
            year = int(month_pattern.group(3)) if month_pattern.group(3) else default_year
            month = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }[month_name]
            try:
                return date(year, month, day).isoformat(), "explicit"
            except ValueError:
                return None, "null"

        mid_month = re.search(r"\bmid[-\s]+(january|february|march|april|may|june|july|august|september|october|november|december)\b", text, flags=re.IGNORECASE)
        if mid_month:
            month_name = mid_month.group(1).lower()
            month = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }[month_name]
            return date(default_year, month, 15).isoformat(), "suggested"

        weekday_pattern = re.search(r"\b(?:this\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text, flags=re.IGNORECASE)
        if weekday_pattern and re.search(r"\b(by|before|due)\b", text, flags=re.IGNORECASE):
            target_name = weekday_pattern.group(1).lower()
            target_weekday = {
                "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6,
            }[target_name]
            today = date.today()
            delta = (target_weekday - today.weekday()) % 7
            delta = 7 if delta == 0 else delta
            return (today + timedelta(days=delta)).isoformat(), "suggested"

        return None, "null"

    def _title_from_action(self, action: str) -> str:
        cleaned = re.sub(r"\s+", " ", action).strip(" .")
        cleaned = cleaned[:120]
        if not cleaned:
            return "Untitled task"
        if len(cleaned) <= 62:
            return cleaned[0].upper() + cleaned[1:]
        short = cleaned[:62].rsplit(" ", 1)[0].strip()
        return (short or cleaned[:62]) + "..."

    def _year_from_sem_start(self, sem_start: str) -> int:
        try:
            return int((sem_start or "")[:4])
        except Exception:
            return date.today().year
    
    def _regex_date_fallback(self, notes: str, sem_start: str) -> list:
        """A rock-solid Regex fallback that hunts for dates in text when the AI fails."""
        tasks = []
        if not notes:
            return tasks

        lines = re.split(r'[\n\r]+|\.\s+', notes)
        
        date_pattern = re.compile(
            r'\b(?:Week\s+\d+|[0-3]?\d/[01]?\d/\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})\b', 
            re.IGNORECASE
        )
        
        default_year = self._year_from_sem_start(sem_start)
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line or len(line) < 10: 
                continue
                
            date_match = date_pattern.search(line)
            if date_match:
                iso_date, due_source = self._extract_deadline(line, default_year)
                
                if not iso_date:
                    due_source = "regex_guess"
                
                tasks.append({
                    "id": f"ts_{int(time.time())}_regex_{i}",
                    "title": self._title_from_action(line),
                    "description": line[:200],
                    "deadline": iso_date,
                    "due_date_source": due_source,
                    "priority": "high",
                    "status": "pending",
                    "owner": None,
                    "follow_up": f"Auto-extracted via Regex based on: {date_match.group()}"
                })
                
        return tasks
    
    def _fallback(self, sem_start, notes="", reason=None, diagnostics=None):
        """Graceful fallback that extracts practical tasks from plain text or dates."""
        
        # 🚀 STEP 1: Try your original conversation/action extraction
        extracted = self._extract_tasks_from_notes(notes, sem_start)
        
        # 🚀 STEP 2: If conversation extraction failed, try the new Date Regex!
        if not extracted:
            extracted = self._regex_date_fallback(notes, sem_start)

        # 🚀 STEP 3: If absolutely everything failed, yield the dummy task
        if not extracted:
            ts = int(time.time())
            extracted = [{
                "id": f"ts_{ts}_dummy",
                "title": "Review syllabus and assign deadlines manually",
                "description": "AI models were unavailable and no clear dates were found in the text.",
                "deadline": sem_start,
                "due_date_source": "suggested",
                "priority": "high",
                "status": "pending",
                "owner": None,
                "follow_up": "Check original PDF for schedule"
            }]

        payload = {
            "project_name": "Mess2Master Fallback (Regex Mode)",
            "tasks": extracted[:20], # Allow up to 20 regex tasks
            "gaps": [{"issue": "AI was unavailable. Tasks were generated via Regex.", "suggestion": "Review descriptions to ensure they make sense."}],
            "cross_insights": []
        }
        
        payload["_meta"] = diagnostics or {}
        payload["_meta"]["fallback"] = True
        payload["_meta"]["fallback_reason"] = reason or "All models failed"
        
        return payload

    def _extract_tasks_from_notes(self, notes: str, sem_start: str) -> list:
        text = (notes or "").strip()
        if not text:
            return []

        default_year = self._year_from_sem_start(sem_start)
        normalized = re.sub(r"\s+", " ", text)
        normalized = re.sub(r"(?i)\b(next|also|wait|first|then|finally|meanwhile|additionally|and also|oh and)\b", r". \1", normalized)
        normalized = re.sub(
            r"\s+(?=[A-Z][a-z]+(?:,)?\s+(?:can you|please|you'll|you have|you've|you can|we need|we have|take|draft|design|write|prepare|check|confirm|handle|since))",
            ". ",
            normalized,
        )
        clauses = [c.strip(" \t\n\"'") for c in re.split(r"(?<=[.!?])\s+", normalized) if c.strip()]
        tasks = []
        seen = set()
        last_task = None

        for clause in clauses:
            owner = None
            action = None
            deadline_iso = None
            due_source = "null"

            m_owner = re.search(
                r"\b(?P<owner>[A-Z][a-z]+)(?:,)?\s*(?:since[^,]*,?\s*)?(?:can you|please|you(?:'ll| will)|you've[^,]*,?\s*so please|you have|you can|take|draft|design|write|prepare|check|confirm|handle)\s+(?P<action>.+)",
                clause,
                flags=re.IGNORECASE,
            )
            if m_owner:
                owner = m_owner.group("owner")
                action = m_owner.group("action")

                # "Sarah, can you handle that" should assign previous open task owner.
                if action and re.fullmatch(r"(?:handle|take|do)?(?:\s+(?:that|this|it|the same))?[?.!]*", action.strip(), flags=re.IGNORECASE):
                    if last_task and not last_task.get("owner"):
                        last_task["owner"] = owner
                    continue

            if not action:
                m_need = re.search(r"\b(?:we need to|we have to|please)\s+(?P<action>.+)", clause, flags=re.IGNORECASE)
                if m_need:
                    action = m_need.group("action")

            if not action and "peer evaluation" in clause.lower() and "due" in clause.lower():
                action = "Submit peer evaluation forms"

            if not action and ("haven't assigned anyone to" in clause.lower() or "not assigned" in clause.lower()):
                m_gap = re.search(r"(?:haven't assigned anyone to|not assigned to)\s+(?P<action>.+)", clause, flags=re.IGNORECASE)
                action = m_gap.group("action") if m_gap else clause

            if not action:
                # Allow standalone date clause to enrich previous task.
                if last_task and not last_task.get("deadline"):
                    inferred_date, inferred_source = self._extract_deadline(clause, default_year)
                    if inferred_date:
                        last_task["deadline"] = inferred_date
                        last_task["due_date_source"] = inferred_source
                continue

            deadline_iso, due_source = self._extract_deadline(clause, default_year)

            # Clean up trailing chatter that is not part of the action
            action = re.sub(r"\b(?:by|before|due|deadline)\b.+$", "", action, flags=re.IGNORECASE).strip(" .,")
            action = re.sub(r"\b(?:that|soon|should work)\b$", "", action, flags=re.IGNORECASE).strip(" .,")
            action = re.sub(r"\b(?:i'?m|we should|let'?s|okay that'?s it|that'?s it|but no one'?s assigned.*|wait|also|oh and).*$", "", action, flags=re.IGNORECASE).strip(" .,")
            if not action:
                continue

            action_variants = self._split_compound_action(action)
            for variant in action_variants:
                title = self._title_from_action(variant)
                dedupe_key = (title.lower(), (deadline_iso or ""), (owner or "").lower())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                task = {
                    "id": f"ts_{int(time.time())}_{len(tasks)}",
                    "title": title,
                    "description": variant,
                    "deadline": deadline_iso,
                    "due_date_source": due_source,
                    "priority": "high" if deadline_iso else "medium",
                    "status": "pending",
                    "owner": owner,
                    "follow_up": None if owner else "Assign owner"
                }
                tasks.append(task)
                last_task = task

        return tasks

    def _split_compound_action(self, action: str) -> list[str]:
        text = re.sub(r"\s+", " ", (action or "")).strip(" .,")
        if not text:
            return []

        if re.search(r"\bor\b", text, flags=re.IGNORECASE):
            parts = [part.strip(" .,") for part in re.split(r"\bor\b", text, flags=re.IGNORECASE) if part.strip()]
            if len(parts) >= 2 and all(len(part) >= 4 for part in parts):
                return parts

        return [text]

    def _merge_task_lists(self, primary_tasks: list, secondary_tasks: list) -> list:
        merged = []
        seen = set()

        for source in (primary_tasks or [], secondary_tasks or []):
            for task in source:
                title = self._title_from_action(str(task.get("title") or task.get("description") or ""))
                deadline = task.get("deadline") or task.get("due_date") or ""
                owner = (task.get("owner") or "").strip().lower()
                key = (title.lower(), deadline, owner)
                if key in seen:
                    continue
                seen.add(key)

                normalized = dict(task)
                normalized["title"] = title
                normalized["deadline"] = normalized.get("deadline") or normalized.get("due_date")
                normalized["status"] = "pending"
                if not normalized.get("due_date_source"):
                    normalized["due_date_source"] = "suggested" if normalized.get("deadline") else "null"
                merged.append(normalized)

        return merged

    def _extract_deadline(self, text: str, default_year: int):
        month_pattern = re.search(
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?",
            text,
            flags=re.IGNORECASE,
        )
        if month_pattern:
            month_name = month_pattern.group(1).lower()
            day = int(month_pattern.group(2))
            year = int(month_pattern.group(3)) if month_pattern.group(3) else default_year
            month = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }[month_name]
            try:
                return date(year, month, day).isoformat(), "explicit"
            except ValueError:
                return None, "null"

        mid_month = re.search(r"\bmid[-\s]+(january|february|march|april|may|june|july|august|september|october|november|december)\b", text, flags=re.IGNORECASE)
        if mid_month:
            month_name = mid_month.group(1).lower()
            month = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }[month_name]
            return date(default_year, month, 15).isoformat(), "suggested"

        weekday_pattern = re.search(r"\b(?:this\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text, flags=re.IGNORECASE)
        if weekday_pattern and re.search(r"\b(by|before|due)\b", text, flags=re.IGNORECASE):
            target_name = weekday_pattern.group(1).lower()
            target_weekday = {
                "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6,
            }[target_name]
            today = date.today()
            delta = (target_weekday - today.weekday()) % 7
            delta = 7 if delta == 0 else delta
            return (today + timedelta(days=delta)).isoformat(), "suggested"

        return None, "null"

    def _title_from_action(self, action: str) -> str:
        cleaned = re.sub(r"\s+", " ", action).strip(" .")
        cleaned = cleaned[:120]
        if not cleaned:
            return "Untitled task"
        if len(cleaned) <= 62:
            return cleaned[0].upper() + cleaned[1:]
        short = cleaned[:62].rsplit(" ", 1)[0].strip()
        return (short or cleaned[:62]) + "..."

    def _year_from_sem_start(self, sem_start: str) -> int:
        try:
            return int((sem_start or "")[:4])
        except Exception:
            return date.today().year
    
    def _regex_date_fallback(self, notes: str, sem_start: str) -> list:
        """A rock-solid Regex fallback that hunts for dates in text when the AI fails."""
        tasks = []
        if not notes:
            return tasks

        # Split the text into individual lines or sentences
        lines = re.split(r'[\n\r]+|\.\s+', notes)
        
        # Regex pattern looking for:
        # 1. Week X (e.g., "Week 4")
        # 2. DD/MM/YYYY or D/M/YY (e.g., "01/04/2026", "8/4/26")
        # 3. Month DD (e.g., "April 1st", "Jan 15")
        date_pattern = re.compile(
            r'\b(?:Week\s+\d+|[0-3]?\d/[01]?\d/\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})\b', 
            re.IGNORECASE
        )
        
        default_year = self._year_from_sem_start(sem_start)
        
        for i, line in enumerate(lines):
            line = line.strip()
            # Skip empty lines or lines that are too short to be a real task
            if not line or len(line) < 10: 
                continue
                
            date_match = date_pattern.search(line)
            if date_match:
                # We found a date! Try to formally parse it using your existing method
                iso_date, due_source = self._extract_deadline(line, default_year)
                
                # If your extractor couldn't parse the exact ISO format, we'll flag it
                if not iso_date:
                    due_source = "regex_guess"
                
                tasks.append({
                    "id": f"ts_{int(time.time())}_regex_{i}",
                    "title": self._title_from_action(line),
                    "description": line[:200], # Save the raw line so the user knows what it was
                    "deadline": iso_date,
                    "due_date_source": due_source,
                    "priority": "high",
                    "status": "pending",
                    "owner": None,
                    "follow_up": f"Auto-extracted via Regex based on: {date_match.group()}"
                })
                
        return tasks