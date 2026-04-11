import os
import json
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
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.model_fallbacks = [
            self.model,
            "gemini-2.5-flash-lite",
            "models/gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "models/gemini-2.5-flash",
        ]

    def extract_tasks(self, files: list, notes: str, sem_start: str, sem_end: str):
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

        for file in files:
            try:
                filename = getattr(file, "filename", "") or ""
                ext = filename.rsplit('.', 1)[-1].lower() if "." in filename else ""
                mime = getattr(file, "mimetype", None) or mime_map.get(ext, 'application/octet-stream')
                file_bytes = file.read()
                is_pdf = (ext == 'pdf') or (mime == 'application/pdf')

                if is_pdf:
                    try:
                        pdf_reader = PdfReader(BytesIO(file_bytes))
                        if len(pdf_reader.pages) == 0:
                            print(f"⚠️ PDF has no pages: {file.filename}")
                            continue
                        extracted_pages = []
                        for page in pdf_reader.pages[:5]:
                            page_text = page.extract_text() or ""
                            if page_text.strip():
                                extracted_pages.append(page_text.strip())
                        extracted_text = "\n\n".join(extracted_pages).strip()
                        if extracted_text:
                            diagnostics["pdf_text_extracted"] = True
                            contents.append(
                                f"FILE: {file.filename}\nTYPE: pdf\nEXTRACTED_TEXT:\n{extracted_text[:12000]}"
                            )
                            continue
                    except Exception as pdf_error:
                        print(f"⚠️ PDF text extraction failed for {file.filename}: {pdf_error}")

                # Keep full PDF bytes; truncation can corrupt page structure.
                if not is_pdf and len(file_bytes) > 2_000_000:
                    file_bytes = file_bytes[:2_000_000]

                contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime))
            except Exception as e:
                print(f"⚠️ Skipping file {file.filename}: {e}")

        prompt = f"""
        You must use only the uploaded files and notes below.
        Do not use unrelated outside examples, tutorial templates, or prior knowledge.

        First infer the actual project/topic from the uploaded material.
        If the file is about software requirement engineering, plan that project.

        Extract a compact student project plan.
        Semester start: {sem_start}
        Semester end: {sem_end}
        Files: {len(files)}
        Notes: {notes[:250]}

        Return strict JSON only with this shape:
        {{
          "project_name": "string",
                    "tasks": [{{"title":"string","description":"string","due_date":"YYYY-MM-DD or null","due_date_source":"explicit|suggested|null","priority":"high|medium|low","owner":"string or null","follow_up":"string or null"}}],
          "gaps": [{{"issue":"string","suggestion":"string"}}],
          "sync_score": 0,
          "cross_insights": ["string"]
        }}

                Keep it short. Prefer 3 to 6 tasks.
                If files are uploaded, make each task description more specific and readable, using 1 to 3 short sentences.
                Prefer task titles that closely match the user's wording or the uploaded brief wording. Avoid generic titles unless the input is very vague.
                If a deadline is not explicitly stated in the file, provide a suggested due date and mark due_date_source as "suggested".
                If you can infer a follow-up action, include it in follow_up as a short practical sentence.
        """

        last_error = None
        tried_models = []

        for model_name in dict.fromkeys(self.model_fallbacks):
            tried_models.append(model_name)
            try:
                res = self.client.models.generate_content(
                    model=model_name,
                    contents=contents + [prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        max_output_tokens=700,
                        temperature=0.2,
                    )
                )
                diagnostics["used_model"] = model_name
                raw = (res.text or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1].replace("json", "").strip()

                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        parsed["_meta"] = diagnostics
                    return parsed
                except Exception:
                    start = raw.find("{")
                    end = raw.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        parsed = json.loads(raw[start:end + 1])
                        if isinstance(parsed, dict):
                            parsed["_meta"] = diagnostics
                        return parsed
                    raise ValueError("Model returned non-JSON output")
            except Exception as e:
                last_error = e
                error_text = str(e)
                print(f"❌ AI Error with {model_name}: {error_text}")
                if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text or "spending cap" in error_text.lower():
                    diagnostics["used_model"] = model_name
                    return self._fallback(sem_start, reason=error_text, diagnostics=diagnostics)
                if "404" in error_text or "NOT_FOUND" in error_text or "no longer available" in error_text:
                    continue

        print(f"❌ AI Error after trying {tried_models}: {last_error}")
        return self._fallback(sem_start, reason=str(last_error), diagnostics=diagnostics)

    def _fallback(self, sem_start, reason=None, diagnostics=None):
        payload = {
            "project_name": "Mess2Master Fallback",
            "tasks": [{"title": "Setup Project Repository", "description": "Initialize git and README", "due_date": sem_start, "due_date_source": "explicit", "priority": "high", "owner": "Team Lead", "follow_up": "Confirm repository name and assign the first owner."}],
            "gaps": [{"issue": "Missing methodology section", "suggestion": "Schedule 1hr meeting to align on approach"}],
            "sync_score": 75,
            "cross_insights": ["Reuse literature review template from previous course"]
        }
        payload["_meta"] = diagnostics or {}
        payload["_meta"]["fallback"] = True
        payload["_meta"]["fallback_reason"] = reason or "Unknown AI error"
        return payload