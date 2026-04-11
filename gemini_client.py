import os
import json
from google import genai
from google.genai import types

class Mess2MasterAI:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found. Check your .env file.")
        self.client = genai.Client(api_key=api_key)
        # Fallback to gemini-1.5-flash if 2.0-flash is restricted in your region
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    def extract_tasks(self, files: list, notes: str, sem_start: str, sem_end: str):
        contents = []
        mime_map = {
            'pdf': 'application/pdf', 'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'txt': 'text/plain', 'mp3': 'audio/mpeg', 'wav': 'audio/wav',
            'm4a': 'audio/mp4', 'ogg': 'audio/ogg', 'png': 'image/png',
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'
        }

        for file in files:
            try:
                ext = file.filename.rsplit('.', 1)[-1].lower()
                mime = mime_map.get(ext, 'application/octet-stream')
                contents.append(types.Part.from_bytes(data=file.read(), mime_type=mime))
            except Exception as e:
                print(f"⚠️ Skipping file {file.filename}: {e}")

        prompt = f"""
        You are Mess2Master, an AI student project intelligence engine.
        SEMESTER CONTEXT: Start={sem_start}, End={sem_end}
        INPUT: {len(files)} file(s) + notes: "{notes[:500]}"

        OUTPUT STRICT JSON ONLY. NO MARKDOWN. NO EXTRA TEXT.
        {{
            "project_name": "string",
            "tasks": [
                {{"title": "string", "description": "string", "due_date": "YYYY-MM-DD (convert 'Week X' using semester dates)", "priority": "high|medium|low", "owner": "string or null"}}
            ],
            "gaps": [
                {{"issue": "string", "suggestion": "string"}}
            ],
            "sync_score": 0-100,
            "cross_insights": ["string"]
        }}

        RULES:
        1. Convert relative deadlines (e.g., "Week 4") to exact YYYY-MM-DD dates based on semester start.
        2. gaps: Identify missing rubric sections, unassigned critical roles, or timeline conflicts. Provide actionable suggestions.
        3. sync_score: Calculate 0-100 based on: clear deadlines (+30), assigned owners (+30), balanced workload (+20), explicit next steps (+20).
        4. cross_insights: Suggest templates, warn about deadline clashes, or recommend reuse of past work.
        5. Be specific. Prioritize high-impact academic tasks.
        """

        try:
            res = self.client.models.generate_content(
                model=self.model,
                contents=contents + [prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            raw = res.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].replace("json", "").strip()
            return json.loads(raw)
        except Exception as e:
            print(f"❌ AI Error: {e}")
            return self._fallback(sem_start)

    def _fallback(self, sem_start):
        return {
            "project_name": "Demo Project",
            "tasks": [{"title": "Setup Project Repository", "description": "Initialize git and README", "due_date": sem_start, "priority": "high", "owner": "Team Lead"}],
            "gaps": [{"issue": "Missing methodology section", "suggestion": "Schedule 1hr meeting to align on approach"}],
            "sync_score": 75,
            "cross_insights": ["Reuse literature review template from previous course"]
        }