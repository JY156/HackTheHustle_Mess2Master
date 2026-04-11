import os
import json
import time
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
        
        # ✅ Model fallback chain (Main's robustness)
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.model_fallbacks = [
            self.model,
            "gemini-2.5-flash-lite",
            "models/gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "models/gemini-2.5-flash",
        ]

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
                ext = file.filename.rsplit('.', 1)[-1].lower()
                mime = mime_map.get(ext, 'application/octet-stream')
                file_bytes = file.read()

                # Special handling for PDFs: extract text first (better for task extraction)
                if ext == 'pdf':
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
                if ext != 'pdf' and len(file_bytes) > 2_000_000:
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
                        if not task.get("due_date_source"):
                            task["due_date_source"] = "suggested" if task.get("deadline") else "null"
                    
                    parsed["_meta"] = diagnostics
                    return parsed
                    
            except Exception as e:
                last_error = e
                error_text = str(e)
                print(f"❌ AI Error with {model_name}: {error_text}")
                
                # Stop on quota errors (don't waste retries)
                if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text or "spending cap" in error_text.lower():
                    diagnostics["used_model"] = model_name
                    return self._fallback(sem_start, reason=error_text, diagnostics=diagnostics)
                # Continue to next model on 404/not found
                if "404" in error_text or "NOT_FOUND" in error_text or "no longer available" in error_text:
                    continue

        # All models failed → fallback
        print(f"❌ AI Error after trying {tried_models}: {last_error}")
        return self._fallback(sem_start, reason=str(last_error), diagnostics=diagnostics)

    def _fallback(self, sem_start, reason=None, diagnostics=None):
        """Graceful fallback that returns valid JSON structure"""
        ts = int(time.time())
        payload = {
            "project_name": "Demo Project",
            "tasks": [{
                "id": f"ts_{ts}",
                "title": "Setup Project Repository",
                "description": "Initialize git and README",
                "deadline": sem_start,
                "due_date_source": "explicit",
                "priority": "high",
                "status": "pending",
                "owner": "Team Lead",
                "follow_up": "Confirm repository name and assign first owner"
            }],
            "gaps": [{"issue": "Missing methodology section", "suggestion": "Schedule 1hr meeting to align on approach"}],
            "sync_score": 75,
            "cross_insights": ["Reuse literature review template from previous course"]
        }
        payload["_meta"] = diagnostics or {}
        payload["_meta"]["fallback"] = True
        payload["_meta"]["fallback_reason"] = reason or "All models failed"
        return payload