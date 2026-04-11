import requests
import os
from dotenv import load_dotenv
from pathlib import Path

NOTION_API_VERSION = "2022-06-28"
BASE_DIR = Path(__file__).resolve().parent

class NotionClient:
    def __init__(self):
        # Always refresh from project .env so edits take effect immediately.
        load_dotenv(BASE_DIR / ".env", override=True)
        load_dotenv(BASE_DIR.parent / ".env", override=False)

        self.token = os.getenv("NOTION_TOKEN")
        self.db_id = os.getenv("NOTION_DATABASE_ID")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        }
        self.api_url = "https://api.notion.com/v1"

    def _fetch_database_properties(self):
        response = requests.get(
            f"{self.api_url}/databases/{self.db_id}",
            headers=self.headers,
            timeout=10,
        )
        if response.status_code != 200:
            try:
                msg = response.json().get("message", response.text)
            except Exception:
                msg = response.text
            raise RuntimeError(f"Failed to read Notion database schema: {msg}")
        data = response.json()
        return data.get("properties", {})

    def sync_tasks(self, tasks, project_name):
        """
        Post tasks to Notion database.
        
        Args:
            tasks: List of task dictionaries with title, priority, due_date, project
            project_name: Name of the project (for grouping)
            
        Returns:
            dict with status, synced_count, and errors
        """
        if not self.token or not self.db_id:
            return {"status": "error", "message": "Notion credentials not configured"}

        synced_count = 0
        errors = []

        try:
            db_properties = self._fetch_database_properties()
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "synced_count": 0,
                "total_tasks": len(tasks),
                "errors": [str(e)],
            }

        for task in tasks:
            try:
                page_data = self._build_page_payload(task, project_name, db_properties)
                response = requests.post(
                    f"{self.api_url}/pages",
                    json=page_data,
                    headers=self.headers,
                    timeout=10,
                )

                if response.status_code == 200:
                    synced_count += 1
                    print(f"✅ Synced task: {task.get('title', 'Untitled')}")
                else:
                    error_msg = response.json().get("message", response.text)
                    errors.append(f"{task.get('title')}: {error_msg}")
                    print(f"❌ Notion sync failed for {task.get('title')}: {error_msg}")
            except Exception as e:
                errors.append(f"{task.get('title')}: {str(e)}")
                print(f"❌ Exception syncing task: {e}")

        return {
            "status": "success" if synced_count > 0 else "error",
            "message": "" if synced_count > 0 else (errors[0] if errors else "No tasks were synced to Notion"),
            "synced_count": synced_count,
            "total_tasks": len(tasks),
            "errors": errors,
        }

    def _find_property_name(self, properties, prop_type, preferred_names=None):
        preferred_names = preferred_names or []
        for name in preferred_names:
            if name in properties and properties[name].get("type") == prop_type:
                return name
        for name, meta in properties.items():
            if meta.get("type") == prop_type:
                return name
        return None

    def _build_page_payload(self, task, project_name, db_properties):
        """
        Build Notion page creation payload.
        Assumes Notion database has these properties:
        - Title (or Name)
        - Priority (select/multi-select)
        - Due Date (date)
        - Project (text)
        - Status (select, default: "To-Do")
        """
        priority = task.get("priority", "medium").lower()
        due_date = task.get("due_date")
        title = task.get("title", "Untitled Task")

        title_prop = self._find_property_name(db_properties, "title", ["Title", "Name", "Task"])
        if not title_prop:
            raise RuntimeError("No title property found in Notion database. Add a Title/Name field.")

        properties = {
            title_prop: {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": title},
                    }
                ]
            }
        }

        priority_prop = self._find_property_name(db_properties, "select", ["Priority"])
        if priority_prop:
            properties[priority_prop] = {
                "select": {
                    "name": priority.capitalize(),
                }
            }

        project_prop = self._find_property_name(db_properties, "rich_text", ["Project", "Course", "Module"])
        if project_prop:
            properties[project_prop] = {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": project_name},
                    }
                ]
            }

        status_prop = self._find_property_name(db_properties, "select", ["Status"])
        if status_prop and status_prop != priority_prop:
            properties[status_prop] = {
                "select": {
                    "name": "To-Do",
                }
            }

        date_prop = self._find_property_name(db_properties, "date", ["Due Date", "Deadline", "Date"])
        if due_date and date_prop:
            properties[date_prop] = {
                "date": {
                    "start": due_date,
                }
            }

        return {
            "parent": {
                "database_id": self.db_id,
            },
            "properties": properties,
        }
