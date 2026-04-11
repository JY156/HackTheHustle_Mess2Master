import os
import json
from google import genai
from google.genai import types

# --- Function Calling Setup ---
CREATE_EVENT_DECLARATION = {
    "name": "create_calendar_event",
    "description": "Create a Google Calendar event for a project deadline",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Event title"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "description": {"type": "string", "description": "Event details"}
        },
        "required": ["title", "date"]
    }
}

def create_calendar_event(title: str, date: str, description: str = "") -> dict:
    """Mock function for hackathon demo"""
    return {
        "status": "created",
        "event": {"title": title, "date": date, "description": description},
        "message": f"Added '{title}' to your calendar for {date}"
    }

# --- Main AI Client ---
class Mess2MasterAI:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found. Check your .env file.")
        self.client = genai.Client(api_key=api_key)
        # Option 1
        self.model = "gemini-2.0-flash"

        # Option 2
        # self.model = "gemini-1.5-flash"

        # Option 3
        # self.model = "gemini-1.5-pro"

    def extract_tasks(self, files: list, notes: str, create_calendar: bool = False):
        """Core function: Turn messy input → structured tasks"""
        
        # 1. Build multimodal content safely
        contents = []
        for file in files:
            try:
                if file and file.filename:
                    ext = file.filename.lower().split('.')[-1]
                    mime = 'application/pdf' if ext == 'pdf' else 'image/jpeg'
                    contents.append(types.Part.from_bytes(data=file.read(), mime_type=mime))
            except Exception as e:
                print(f"⚠️ Skipping file: {e}")

        # 2. Build Prompt
        prompt = f"""
        You are a student project coordinator. Extract actionable tasks.
        NOTES: "{notes[:500] if notes else 'No notes provided'}"

        OUTPUT JSON ONLY (no markdown, no extra text):
        {{
            "project_name": "string",
            "tasks": [
                {{
                    "title": "string",
                    "description": "string",
                    "due_date": "YYYY-MM-DD or null",
                    "priority": "high|medium|low",
                    "estimated_hours": 0,
                    "owner_suggestion": "string or null"
                }}
            ],
            "critical_deadlines": ["YYYY-MM-DD"],
            "next_3_actions": ["action1", "action2", "action3"],
            "potential_gaps": ["missing element 1"]
        }}
        """

        # 3. Configure Generation
        config = types.GenerateContentConfig(response_mime_type="application/json")
        if create_calendar:
            config.tools = [types.Tool(function_declarations=[CREATE_EVENT_DECLARATION])]

        # 4. Call Gemini
        try:
            payload = contents + [prompt] if contents else [prompt]
            response = self.client.models.generate_content(
                model=self.model,
                contents=payload,
                config=config
            )
        except Exception as e:
            print(f"❌ Gemini API Error: {e}")
            return self._get_fallback()

        # 5. Parse Response Safely
        try:
            raw_text = response.text.strip()
            # Remove markdown code blocks if Gemini wraps them
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            raw_text = raw_text.strip()

            result = json.loads(raw_text)

            # Handle calendar function call
            if create_calendar and response.candidates and response.candidates[0].content.parts:
                first_part = response.candidates[0].content.parts[0]
                if hasattr(first_part, "function_call") and first_part.function_call.name == "create_calendar_event":
                    cal_result = create_calendar_event(**first_part.function_call.args)
                    result["calendar_result"] = cal_result

            return result
        except Exception as e:
            print(f"⚠️ JSON Parse Error: {e}")
            return self._get_fallback()

    def _get_fallback(self):
        return {
            "project_name": "Demo Project",
            "tasks": [{"title": "Sample Task", "description": "API fallback active", "due_date": "2026-04-20", "priority": "high", "estimated_hours": 2, "owner_suggestion": "Team"}],
            "critical_deadlines": [],
            "next_3_actions": ["Check API key", "Verify PDF format", "Retry"],
            "potential_gaps": []
        }