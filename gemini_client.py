import os
import json
import time
from google import genai
from google.genai import types

class Mess2MasterAI:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found. Check your .env file.")
        self.client = genai.Client(api_key=api_key)
        # ✅ Use available model
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    def extract_tasks(self, files: list, notes: str, sem_start: str, sem_end: str, 
                      existing_pending: list = None, break_week: int = 8):
        """
        Extract/merge tasks with semester-aware date conversion.
        existing_pending: list of current pending tasks (for merging)
        break_week: week number where 1-week break occurs (shifts later dates)
        """
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

        existing_json = json.dumps(existing_pending[:5] if existing_pending else [], indent=2)
        
        prompt = f"""
        You are Mess2Master, an academic task intelligence engine.
        
        SEMESTER CONTEXT:
        - Start: {sem_start}, End: {sem_end}
        - 1-week break between Week 7 and Week 8 (shift Week 8+ deadlines by +7 days)
        
        EXISTING PENDING TASKS (preserve these, do NOT overwrite):
        {existing_json}
        
        NEW INPUT: {len(files)} file(s) + notes: "{notes[:400]}"
        
        OUTPUT STRICT JSON ONLY. NO MARKDOWN. NO EXTRA TEXT.
        {{
            "project_name": "string",
            "tasks": [
                {{
                    "id": "string (use timestamp or existing ID)",
                    "task": "string",
                    "deadline": "YYYY-MM-DD",
                    "priority": "high|medium|low",
                    "status": "pending",
                    "owner": "string or null"
                }}
            ],
            "gaps": [{{"issue": "string", "suggestion": "string"}}],
            "sync_score": 0-100,
            "cross_insights": ["string"]
        }}
        
        CRITICAL RULES:
        1. MERGE, don't replace: Keep all existing task IDs. Only update fields if input explicitly changes them.
        2. ADD new tasks with unique IDs: Use format "ts_" + current timestamp (e.g., "ts_1713023400").
        3. Convert "Week X" to dates: Calculate from semester start. If Week >= break_week, add 7 days.
        4. NEVER mark tasks as completed via AI. Status must always be "pending".
        5. REMOVE tasks only if input says "cancelled" or "removed".
        6. Be specific: "Implement login UI" not "Do frontend".
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
            result = json.loads(raw)
            
            # ✅ Ensure all tasks have IDs
            for task in result.get("tasks", []):
                if not task.get("id"):
                    task["id"] = f"ts_{int(time.time())}_{hash(task['task']) % 10000}"
                task["status"] = "pending"  # Enforce status
            
            return result
        except Exception as e:
            print(f"❌ AI Error: {e}")
            return self._fallback(sem_start)

    def _fallback(self, sem_start):
        ts = int(time.time())
        return {
            "project_name": "Demo Project",
            "tasks": [{
                "id": f"ts_{ts}",
                "task": "Setup Project Repository",
                "deadline": sem_start,
                "priority": "high",
                "status": "pending",
                "owner": "Team Lead"
            }],
            "gaps": [{"issue": "Missing methodology section", "suggestion": "Schedule 1hr meeting to align on approach"}],
            "sync_score": 75,
            "cross_insights": ["Reuse literature review template from previous course"]
        }