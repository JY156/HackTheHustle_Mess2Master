import os
import json
import time
import re
from datetime import date, timedelta, datetime, timezone
from io import BytesIO
from PyPDF2 import PdfReader
from google import genai
from google.genai import types


QUOTA_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gemini_quota_state.json")

class Mess2MasterAI:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found. Check your .env file.")
        self.client = genai.Client(api_key=api_key)
        
        # ✅ Model fallback chain (Main's robustness)
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.model_fallbacks = [
            self.model,
            "gemini-2.5-flash-lite",
            "models/gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "models/gemini-2.5-flash",
        ]

    def _load_quota_state(self):
        try:
            if not os.path.exists(QUOTA_STATE_FILE):
                return {}
            with open(QUOTA_STATE_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_quota_state(self, payload: dict):
        try:
            os.makedirs(os.path.dirname(QUOTA_STATE_FILE), exist_ok=True)
            with open(QUOTA_STATE_FILE, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception:
            # Quota guard should never break the main flow.
            pass

    def _quota_cooldown_active(self):
        state = self._load_quota_state()
        until = float(state.get("cooldown_until", 0) or 0)
        if until and time.time() < until:
            return True, state
        return False, state

    def _next_utc_midnight(self):
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).date()
        return datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc).timestamp()

    def _extract_retry_seconds(self, error_text: str) -> int:
        match = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", error_text, flags=re.IGNORECASE)
        if match:
            try:
                return max(1, int(float(match.group(1))))
            except Exception:
                pass

        match = re.search(r"'retryDelay':\s*'([0-9]+)s'", error_text, flags=re.IGNORECASE)
        if match:
            try:
                return max(1, int(match.group(1)))
            except Exception:
                pass

        return 60

    def _register_quota_cooldown(self, error_text: str, model_name: str):
        error_lower = (error_text or "").lower()
        retry_seconds = self._extract_retry_seconds(error_text)

        # Daily free-tier quota exhaustion should stop retries until the next UTC day.
        if "generate_content_free_tier_requests" in error_lower or "generaterequestspersdayperprojectpermodel-freetier" in error_lower:
            cooldown_until = max(time.time() + retry_seconds, self._next_utc_midnight())
        else:
            cooldown_until = time.time() + retry_seconds

        self._save_quota_state({
            "cooldown_until": cooldown_until,
            "model": model_name,
            "reason": error_text[:1000],
            "updated_at": time.time(),
        })

    def _quota_fallback_result(self, reason: str):
        return {
            "guidance": self._fallback_task_guidance("Task", "medium", "TBD"),
            "used_model": None,
            "fallback": True,
            "fallback_reason": reason,
        }

    def _normalize_owner(self, owner):
        raw = str(owner or "").strip()
        if not raw:
            return None

        lower = raw.lower().strip(" .,!?:;\"'")
        invalid = {
            "someone", "somebody", "anyone", "anybody", "everyone", "team",
            "could", "can", "will", "should", "must", "also", "then", "next",
            "i", "im", "i'm", "me", "we", "us", "you", "unassigned", "none", "null"
        }
        if lower in invalid:
            return None

        if re.fullmatch(r"[a-zA-Z][a-zA-Z\-']{1,30}", raw):
            return raw.capitalize()

        return raw

    def generate_task_guidance(self, task: dict):
        """Generate concise, actionable execution guidance for a single task."""
        title = (task.get("title") or task.get("task") or "Untitled Task").strip()
        priority = (task.get("priority") or "medium").strip().lower()
        deadline = (task.get("deadline") or task.get("due_date") or "TBD").strip()
        owner = (task.get("owner") or "Unassigned").strip()

        quota_active, quota_state = self._quota_cooldown_active()
        if quota_active:
            return {
                "guidance": self._fallback_task_guidance(title, priority, deadline),
                "used_model": None,
                "fallback": True,
                "fallback_reason": quota_state.get("reason") or "Gemini quota cooldown active",
            }

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
                res = self.client.models.generate_content(
                    model=model_name,
                    contents=[prompt],
                    config=types.GenerateContentConfig(
                        max_output_tokens=260,
                        temperature=0.25,
                    ),
                )
                text = (res.text or "").strip()
                if text:
                    return {"guidance": text, "used_model": model_name, "fallback": False}
            except Exception as e:
                last_error = e
                error_text = str(e)
                if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
                    self._register_quota_cooldown(error_text, model_name)
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
        """
        Extract/merge tasks with:
        - Multimodal input (PDF text extraction + native Gemini support)
        - Semester-aware date conversion (Week X → YYYY-MM-DD)
        - Merge-safe output (preserves existing task IDs)
        - Graceful fallback chain + diagnostics
        """
        contents = []
        diagnostics = {
            "pdf_text_extracted": False,
            "used_model": None,
            "fallback": False,
            "fallback_reason": None,
        }

        quota_active, quota_state = self._quota_cooldown_active()
        if quota_active:
            diagnostics["fallback"] = True
            diagnostics["fallback_reason"] = quota_state.get("reason") or "Gemini quota cooldown active"
            return self._fallback(sem_start, notes=notes, reason=diagnostics["fallback_reason"], diagnostics=diagnostics)
        
        mime_map = {
            'pdf': 'application/pdf', 'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'txt': 'text/plain', 'mp3': 'audio/mpeg', 'wav': 'audio/wav',
            'm4a': 'audio/mp4', 'ogg': 'audio/ogg', 'png': 'image/png',
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'
        }

        # === File Processing: PDF Text Extraction + Multimodal Fallback ===
        for file in files:
            try:
                filename = getattr(file, "filename", "") or ""
                ext = filename.rsplit('.', 1)[-1].lower() if "." in filename else ""
                mime = getattr(file, "mimetype", None) or mime_map.get(ext, 'application/octet-stream')
                file_bytes = file.read()
                is_pdf = (ext == 'pdf') or (mime == 'application/pdf')

                # Special handling for PDFs: extract text first (better for task extraction)
                if is_pdf:
                    try:
                        pdf_reader = PdfReader(BytesIO(file_bytes))
                        extracted_pages = []
                        for page in pdf_reader.pages[:5]:  # Limit to first 5 pages
                            page_text = page.extract_text() or ""
                            if page_text.strip():
                                extracted_pages.append(page_text.strip())
                        
                        extracted_text = "\n\n".join(extracted_pages).strip()
                        if extracted_text:
                            diagnostics["pdf_text_extracted"] = True
                            # Send extracted text as plain content (more reliable for task parsing)
                            contents.append(
                                f"FILE: {file.filename}\nTYPE: pdf\nEXTRACTED_TEXT:\n{extracted_text[:12000]}"
                            )
                            continue  # Skip adding raw PDF bytes if text extraction succeeded
                    except Exception as pdf_error:
                        print(f"⚠️ PDF text extraction failed for {file.filename}: {pdf_error}")
                        # Fallback: send raw PDF bytes for Gemini's native PDF understanding

                # Truncate large non-PDF files to avoid token limits
                if not is_pdf and len(file_bytes) > 2_000_000:
                    file_bytes = file_bytes[:2_000_000]

                contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime))
            except Exception as e:
                print(f"⚠️ Skipping file {file.filename}: {e}")

        # === Prepare Merge Context ===
        existing_json = json.dumps(existing_pending[:5] if existing_pending else [], indent=2)
        
        # === Merge-Aware Prompt (Model's structure + Main's specificity) ===
        prompt = f"""
        You are Mess2Master, an academic task intelligence engine.
        
        SEMESTER CONTEXT:
        - Start: {sem_start}, End: {sem_end}
        - 1-week break between Week {break_week} and Week {break_week+1} (shift Week {break_week}+ deadlines by +7 days)
        
        EXISTING PENDING TASKS (PRESERVE THESE - DO NOT OVERWRITE):
        {existing_json}
        
        NEW INPUT: {len(files)} file(s) + notes: "{notes[:400]}"
        
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
            "sync_score": 0-100,
            "cross_insights": ["string"]
        }}
        
        CRITICAL RULES:
        1. MERGE, don't replace: Keep all existing task IDs. Only update fields if input explicitly changes them.
        2. ADD new tasks with unique IDs: Use format "ts_" + current timestamp (e.g., "ts_1713023400").
        3. Convert "Week X" to dates: Calculate from semester start. If Week >= {break_week}, add 7 days.
        4. NEVER mark tasks as completed via AI. Status must always be "pending".
        5. REMOVE tasks only if input explicitly says "cancelled" or "removed".
        6. If deadline is not explicit, set due_date_source="suggested" and provide reasonable estimate.
        7. Be specific: "Implement login UI with email/password" not "Do frontend".
        8. Keep tasks concise: 3-8 tasks max, 1-3 sentence descriptions.
        """

        # === Model Fallback Chain (Main's reliability) ===
        last_error = None
        tried_models = []

        for model_name in dict.fromkeys(self.model_fallbacks):  # Remove duplicates
            tried_models.append(model_name)
            try:
                res = self.client.models.generate_content(
                    model=model_name,
                    contents=contents + [prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        max_output_tokens=800,
                        temperature=0.2,
                    )
                )
                diagnostics["used_model"] = model_name
                
                # Parse JSON with markdown cleanup
                raw = (res.text or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1].replace("json", "").strip()
                
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    # Fallback: extract JSON substring
                    start = raw.find("{")
                    end = raw.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        parsed = json.loads(raw[start:end + 1])
                    else:
                        raise ValueError("Model returned non-JSON output")
                
                # ✅ Enforce task schema + IDs
                if isinstance(parsed, dict) and "tasks" in parsed:
                    for task in parsed["tasks"]:
                        if not task.get("id"):
                            task["id"] = f"ts_{int(time.time())}_{hash(task.get('title', '')) % 10000}"
                        task["status"] = "pending"  # Enforce status
                        task["deadline"] = task.get("deadline") or task.get("due_date")  # Field name compatibility
                        task["owner"] = self._normalize_owner(task.get("owner"))
                        if not task.get("due_date_source"):
                            task["due_date_source"] = "suggested" if task.get("deadline") else "null"

                    # Voice meeting notes often need more deterministic splitting than the model gives.
                    heuristic_tasks = self._extract_tasks_from_notes(notes, sem_start)
                    parsed["tasks"] = self._merge_task_lists(parsed["tasks"], heuristic_tasks)
                    
                    parsed["_meta"] = diagnostics
                    return parsed
                    
            except Exception as e:
                last_error = e
                error_text = str(e)
                print(f"❌ AI Error with {model_name}: {error_text}")
                
                # Stop on quota errors (don't waste retries)
                if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text or "spending cap" in error_text.lower():
                    self._register_quota_cooldown(error_text, model_name)
                    diagnostics["used_model"] = model_name
                    return self._fallback(sem_start, notes=notes, reason=error_text, diagnostics=diagnostics)
                # Continue to next model on 404/not found
                if "404" in error_text or "NOT_FOUND" in error_text or "no longer available" in error_text:
                    continue

        # All models failed → fallback
        print(f"❌ AI Error after trying {tried_models}: {last_error}")
        return self._fallback(sem_start, notes=notes, reason=str(last_error), diagnostics=diagnostics)

    def _fallback(self, sem_start, notes="", reason=None, diagnostics=None):
        """Graceful fallback that still extracts practical tasks from plain transcript text."""
        extracted = self._extract_tasks_from_notes(notes, sem_start)
        if not extracted:
            ts = int(time.time())
            extracted = [{
                "id": f"ts_{ts}",
                "title": "Review meeting transcript and assign assignees",
                "description": "AI fallback mode could not parse clear action lines. Confirm assignees and deadlines manually.",
                "deadline": sem_start,
                "due_date_source": "suggested",
                "priority": "high",
                "status": "pending",
                "owner": None,
                "follow_up": "Share one sentence per task: Assignee + Action + Date"
            }]

        dynamic_gaps = self._build_dynamic_gaps(extracted)

        payload = {
            "project_name": "Mess2Master Fallback",
            "tasks": extracted[:10],
            "gaps": dynamic_gaps,
            "sync_score": 70,
            "cross_insights": []
        }
        payload["_meta"] = diagnostics or {}
        payload["_meta"]["fallback"] = True
        payload["_meta"]["fallback_reason"] = reason or "All models failed"
        return payload

    def _build_dynamic_gaps(self, tasks: list) -> list:
        """Generate risk gaps from actual task quality instead of static fallback text."""
        pending = [t for t in (tasks or []) if str(t.get("status") or "pending").lower() != "completed"]
        gaps = []

        unassigned = 0
        no_deadline = 0
        for task in pending:
            owner = str(task.get("owner") or "").strip().lower()
            deadline = str(task.get("deadline") or task.get("due_date") or "").strip()
            if not owner or owner == "unassigned":
                unassigned += 1
            if not deadline:
                no_deadline += 1

        if unassigned:
            label = "task" if unassigned == 1 else "tasks"
            gaps.append({
                "issue": f"{unassigned} pending {label} without assignee",
                "suggestion": "Assign each task to a specific teammate to avoid ownership gaps."
            })

        if no_deadline:
            label = "task" if no_deadline == 1 else "tasks"
            gaps.append({
                "issue": f"{no_deadline} pending {label} without deadline",
                "suggestion": "Set a clear due date so priorities and reminders stay reliable."
            })

        return gaps

    def _extract_tasks_from_notes(self, notes: str, sem_start: str) -> list:
        text = (notes or "").strip()
        if not text:
            return []

        default_year = self._year_from_sem_start(sem_start)
        normalized = re.sub(r"\s+", " ", text)
        # Do not split on words like "next" or "also" because they often belong to deadlines/actions.
        normalized = re.sub(r"(?i)\b(wait|first|then|finally|meanwhile|additionally|and also|oh and)\b", r". \1", normalized)
        normalized = re.sub(
            r"\s+(?=[A-Z][a-z]+(?:,)?\s+(?:will|can|could|should|must|needs?\s+to|has\s+to|can you|please|you'll|you have|you've|you can|we need|we have|take|draft|design|write|prepare|check|confirm|handle|since))",
            ". ",
            normalized,
        )
        clauses = [c.strip(" \t\n\"'") for c in re.split(r"(?<=[.!?])\s+", normalized) if c.strip()]
        tasks = []
        seen = set()
        last_task = None

        for clause in clauses:
            clause = re.sub(r"^(?:and|also|then|next|finally|meanwhile|additionally|oh and)\b[:\s,.-]*", "", clause, flags=re.IGNORECASE).strip()
            if not clause:
                continue

            owner = None
            action = None
            deadline_iso = None
            due_source = "null"

            m_owner = re.search(
                r"\b(?P<owner>[A-Za-z][a-z]+)(?:,)?\s*(?:since[^,]*,?\s*)?(?:can you|please|you(?:'ll| will)|you've[^,]*,?\s*so please|you have|you can|take|draft|design|write|prepare|check|confirm|handle)\s+(?P<action>.+)",
                clause,
                flags=re.IGNORECASE,
            )
            if m_owner:
                owner = m_owner.group("owner")
                action = m_owner.group("action")

            if not action:
                # Handles natural speech like "Sarah will build...", "Jason could write..."
                m_owner_simple = re.search(
                    r"\b(?P<owner>[A-Za-z][a-z]+)\s+(?:will|can|could|should|must|needs?\s+to|has\s+to)\s+(?P<action>.+)",
                    clause,
                    flags=re.IGNORECASE,
                )
                if m_owner_simple:
                    owner = m_owner_simple.group("owner")
                    action = m_owner_simple.group("action")

            # "Sarah, can you handle that" should assign previous open task owner.
            if action and re.fullmatch(r"(?:handle|take|do)?(?:\s+(?:that|this|it|the same))?[?.!]*", action.strip(), flags=re.IGNORECASE):
                if last_task and owner and not last_task.get("owner"):
                    last_task["owner"] = owner
                continue

            owner = self._normalize_owner(owner)

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
        merged_by_key = {}

        for source in (primary_tasks or [], secondary_tasks or []):
            for task in source:
                title = self._title_from_action(str(task.get("title") or task.get("description") or ""))
                deadline = task.get("deadline") or task.get("due_date") or ""
                key = (title.lower(), deadline)

                normalized = dict(task)
                normalized["title"] = title
                normalized["deadline"] = normalized.get("deadline") or normalized.get("due_date")
                normalized["status"] = "pending"
                normalized["owner"] = self._normalize_owner(normalized.get("owner"))
                if not normalized.get("due_date_source"):
                    normalized["due_date_source"] = "suggested" if normalized.get("deadline") else "null"

                if key in merged_by_key:
                    existing = merged_by_key[key]
                    # Prefer richer owner/follow-up info from whichever source has it.
                    if not existing.get("owner") and normalized.get("owner"):
                        existing["owner"] = normalized.get("owner")
                    if not existing.get("follow_up") and normalized.get("follow_up"):
                        existing["follow_up"] = normalized.get("follow_up")
                    if not existing.get("description") and normalized.get("description"):
                        existing["description"] = normalized.get("description")
                    continue

                merged_by_key[key] = normalized
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

        if re.search(r"\b(tomorrow|tmr)\b", text, flags=re.IGNORECASE) and re.search(r"\b(by|before|due)\b", text, flags=re.IGNORECASE):
            return (date.today() + timedelta(days=1)).isoformat(), "suggested"

        weekday_pattern = re.search(r"\b(?:(this|next)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text, flags=re.IGNORECASE)
        if weekday_pattern and re.search(r"\b(by|before|due)\b", text, flags=re.IGNORECASE):
            qualifier = (weekday_pattern.group(1) or "").lower()
            target_name = weekday_pattern.group(2).lower()
            target_weekday = {
                "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6,
            }[target_name]
            today = date.today()
            delta = (target_weekday - today.weekday()) % 7
            if qualifier == "next":
                delta = delta + 7 if delta != 0 else 7
            else:
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