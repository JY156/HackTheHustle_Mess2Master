from flask import Flask, request, jsonify, render_template, redirect
from dotenv import load_dotenv
import os, json, time, threading
from gemini_client import Mess2MasterAI
from notion_client import NotionClient  # Optional: wrapped in try/except

# === Config & Init ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(BASE_DIR), ".env"))

app = Flask(__name__)
ai = Mess2MasterAI()
DATA_FILE = os.path.join(BASE_DIR, "data", "mess2master_state.json")
os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

# === Thread-Safe JSON I/O (Prevent Race Conditions) ===
DATA_LOCK = threading.Lock()

def load_state():
    """Load state with lock + migration support"""
    with DATA_LOCK:
        if not os.path.exists(DATA_FILE):
            default = {"semester": {}, "projects": []}
            with open(DATA_FILE, "w") as f: json.dump(default, f, indent=2)
            return default
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                # ✅ Migrate old list format → new structure
                if isinstance(data, list):
                    data = {"semester": {}, "projects": data}
                # ✅ Migrate old task schema (due_date → deadline)
                for p in data.get("projects", []):
                    for t in p.get("tasks", []):
                        if "deadline" not in t and "due_date" in t:
                            t["deadline"] = t["due_date"]
                        if "id" not in t:
                            t["id"] = f"ts_{int(time.time())}_{hash(t.get('title',''))%10000}"
                        if "status" not in t:
                            t["status"] = "pending"
                    # Ensure split arrays exist
                    if "pending_tasks" not in p and "tasks" in p:
                        p["pending_tasks"] = [t for t in p["tasks"] if t.get("status") != "completed"]
                        p["completed_tasks"] = [t for t in p["tasks"] if t.get("status") == "completed"]
                return data
        except Exception as e:
            print(f"⚠️ Load state error: {e}")
            return {"semester": {}, "projects": []}

def save_state(data):
    """Save state with lock"""
    with DATA_LOCK:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)

# === Helper: Safe Sort Key (Prevent NoneType Crash) ===
def safe_deadline(task):
    """Return deadline string or far-future sentinel for null-safe sorting"""
    dl = task.get("deadline") or task.get("due_date")
    return dl if dl else "2099-12-31"

def priority_score(priority):
    return {"high": 3, "medium": 2, "low": 1}.get(priority, 1)

# === Helper: Merge Tasks by ID ===
def merge_tasks(existing, new_tasks):
    """Merge new tasks into existing, preserving IDs and avoiding duplicates"""
    existing_map = {t.get("id"): t for t in existing if t.get("id")}
    
    for new in new_tasks:
        key = new.get("id")
        if key and key in existing_map:
            # Update existing task fields (except id)
            existing_map[key].update({k: v for k, v in new.items() if k != "id"})
        else:
            # Add new task with guaranteed ID
            if not new.get("id"):
                new["id"] = f"ts_{int(time.time())}_{hash(new.get('title',''))%10000}"
            new["status"] = "pending"
            existing.append(new)
    return existing

# === Routes ===

@app.route("/")
def index():
    """Upload page + project selection"""
    data = load_state()
    projects = data.get("projects", [])
    project_names = [p.get("project_name") for p in projects]
    semester = data.get("semester", {})
    
    # Build master preview (null-safe sort)
    master_tasks = []
    for p in projects:
        for t in p.get("pending_tasks", []):
            t_copy = t.copy()
            t_copy["project"] = p["project_name"]
            t_copy["score"] = priority_score(t_copy.get("priority"))
            master_tasks.append(t_copy)
    master_tasks.sort(key=lambda x: (-x["score"], safe_deadline(x)))
    
    return render_template("index.html",
                         project_names=project_names,
                         semester=semester,
                         projects=projects,
                         master_tasks=master_tasks[:5])  # Preview only

@app.route("/tasks")
def tasks_page():
    """Task board view with project tabs"""
    data = load_state()
    projects = data.get("projects", [])
    
    # Build master queue (null-safe sort)
    all_pending = []
    for p in projects:
        for t in p.get("pending_tasks", []):
            t_copy = t.copy()
            t_copy["project"] = p["project_name"]
            t_copy["score"] = priority_score(t_copy.get("priority"))
            all_pending.append(t_copy)
    all_pending.sort(key=lambda x: (-x["score"], safe_deadline(x)))
    
    return render_template("tasks.html", 
                         projects=projects, 
                         master_tasks=all_pending[:20])

@app.route("/api/semester", methods=["POST"])
def set_semester():
    """Save semester settings"""
    data = load_state()
    data["semester"] = {
        "start": request.json.get("start"),
        "end": request.json.get("end"),
        "break_week": int(request.json.get("break_week", 8))
    }
    save_state(data)
    return jsonify({"status": "success"})

@app.route("/api/semester/status", methods=["GET"])
def semester_status():
    """Check if semester is configured"""
    data = load_state()
    semester = data.get("semester", {})
    return jsonify({
        "configured": bool(semester.get("start")),
        "start": semester.get("start"),
        "end": semester.get("end"),
        "break_week": semester.get("break_week", 8)
    })

@app.route("/api/projects", methods=["GET"])
def list_projects():
    """List project names for dropdown"""
    data = load_state()
    return jsonify([p.get("project_name") for p in data.get("projects", [])])

@app.route("/process", methods=["POST"])
def process_upload():
    """Process input and merge tasks into selected project"""
    try:
        data = load_state()
        project_name = request.form.get("project_name")
        if not project_name:
            return jsonify({"error": "project_name required"}), 400
        
        files = request.files.getlist("files")
        notes = request.form.get("notes", "")
        semester = data.get("semester", {})
        sem_start = semester.get("start", "2026-01-12")
        sem_end = semester.get("end", "2026-05-15")
        break_week = semester.get("break_week", 8)
        
        # Get existing pending tasks for merge context
        project = next((p for p in data["projects"] if p.get("project_name") == project_name), None)
        existing_pending = project.get("pending_tasks", []) if project else []
        
        # Call AI with merge context
        result = ai.extract_tasks(
            files, notes, sem_start, sem_end,
            existing_pending=existing_pending,
            break_week=break_week
        )
        
        # Merge logic
        new_tasks = result.get("tasks", [])
        merged_tasks = merge_tasks(existing_pending, new_tasks)
        
        # Update or create project
        if project:
            project["pending_tasks"] = merged_tasks
            project["gaps"] = result.get("gaps", [])
            project["sync_score"] = result.get("sync_score", 75)
            project["cross_insights"] = result.get("cross_insights", [])
        else:
            data["projects"].append({
                "project_name": project_name,
                "pending_tasks": merged_tasks,
                "completed_tasks": [],
                "gaps": result.get("gaps", []),
                "sync_score": result.get("sync_score", 75),
                "cross_insights": result.get("cross_insights", [])
            })
        
        save_state(data)
        
        # Return diagnostics for frontend transparency
        return jsonify({
            "status": "success",
            "project_name": project_name,
            "fallback": result.get("_meta", {}).get("fallback", False),
            "used_model": result.get("_meta", {}).get("used_model"),
            "pdf_extracted": result.get("_meta", {}).get("pdf_text_extracted", False)
        })
        
    except Exception as e:
        print(f"❌ /process error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/tasks/complete", methods=["POST"])
def complete_task():
    """Toggle task completion status"""
    data = load_state()
    req = request.json
    project_name = req.get("project_name")
    task_id = req.get("task_id")
    
    project = next((p for p in data["projects"] if p.get("project_name") == project_name), None)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    pending = project.get("pending_tasks", [])
    completed = project.get("completed_tasks", [])
    
    # Find in pending → move to completed
    task = next((t for t in pending if t.get("id") == task_id), None)
    if task:
        task["status"] = "completed"
        pending = [t for t in pending if t.get("id") != task_id]
        completed.append(task)
        project["pending_tasks"] = pending
        project["completed_tasks"] = completed
        save_state(data)
        return jsonify({"status": "completed"})
    
    # Find in completed → move back to pending
    task = next((t for t in completed if t.get("id") == task_id), None)
    if task:
        task["status"] = "pending"
        completed = [t for t in completed if t.get("id") != task_id]
        pending.append(task)
        project["pending_tasks"] = pending
        project["completed_tasks"] = completed
        save_state(data)
        return jsonify({"status": "pending"})
    
    return jsonify({"error": "Task not found"}), 404

@app.route("/sync-notion", methods=["POST"])
def sync_notion():
    """Optional Notion sync - wrapped in try/except to never crash demo"""
    try:
        # Check if Notion is configured
        if not os.getenv("NOTION_TOKEN") or not os.getenv("NOTION_DB_ID"):
            return jsonify({
                "status": "fallback",
                "message": "Notion not configured. Use clipboard export instead.",
                "clipboard_markdown": generate_notion_markdown()
            })
        
        data = load_state()
        projects = data.get("projects", [])
        if not projects:
            return jsonify({"status": "error", "message": "No projects to sync"}), 400
        
        # Sync first project (MVP scope)
        project = projects[0]
        tasks = project.get("pending_tasks", []) + project.get("completed_tasks", [])
        
        if not tasks:
            return jsonify({"status": "error", "message": "No tasks to sync"}), 400
        
        notion = NotionClient()
        result = notion.sync_tasks(tasks, project.get("project_name", "Untitled"))
        
        # Fallback if API fails
        if result.get("status") != "success":
            return jsonify({
                "status": "fallback",
                "message": "Notion API failed. Here's your checklist:",
                "clipboard_markdown": generate_notion_markdown(tasks)
            })
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ /sync-notion error: {e}")
        # Never crash - return markdown fallback
        return jsonify({
            "status": "fallback",
            "message": f"Notion sync error: {str(e)}. Use clipboard instead.",
            "clipboard_markdown": generate_notion_markdown()
        })

def generate_notion_markdown(tasks=None):
    """Generate Notion-compatible markdown checklist"""
    if tasks is None:
        data = load_state()
        tasks = []
        for p in data.get("projects", []):
            tasks.extend(p.get("pending_tasks", []))
    
    lines = [f"## 📋 Mess2Master Tasks - {time.strftime('%Y-%m-%d')}"]
    for t in tasks:
        status = "[x]" if t.get("status") == "completed" else "[ ]"
        prio = "🔴" if t.get("priority")=="high" else "🟡" if t.get("priority")=="medium" else "🟢"
        dl = t.get("deadline") or "TBD"
        lines.append(f"- {status} **{t.get('title')}** {prio} (Due: {dl})")
        if t.get("description"):
            lines.append(f"  > {t.get('description')[:100]}")
    return "\n".join(lines)

@app.route("/api/reset", methods=["POST"])
def reset_semester():
    """Clear all data for new semester"""
    save_state({"semester": {}, "projects": []})
    return jsonify({"status": "reset"})

# === Run ===
if __name__ == "__main__":
    print("🚀 Mess2Master running on http://127.0.0.1:5000")
    print(f"📁 Data file: {DATA_FILE}")
    app.run(debug=True, port=5000)