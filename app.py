from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
import os, json, time
from gemini_client import Mess2MasterAI

load_dotenv()
app = Flask(__name__)
ai = Mess2MasterAI()
DATA_FILE = "data/projects.json"

os.makedirs("data", exist_ok=True)

def load_data():
    """Load full data structure with semester + projects"""
    if not os.path.exists(DATA_FILE):
        default = {"semester": {}, "projects": []}
        with open(DATA_FILE, "w") as f: json.dump(default, f, indent=2)
        return default
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            # ✅ Migrate old format if needed
            if "projects" not in data and isinstance(data, list):
                data = {"semester": {}, "projects": data}
            return data
    except:
        return {"semester": {}, "projects": []}

def save_data(data):
    with open(DATA_FILE, "w") as f: json.dump(data, f, indent=2)

def generate_task_id(task_title):
    return f"ts_{int(time.time())}_{hash(task_title) % 10000}"

def merge_tasks(existing, new_tasks):
    """Merge new tasks into existing, preserving IDs and avoiding duplicates"""
    existing_map = {t.get("id") or t.get("task"): t for t in existing}
    
    for new in new_tasks:
        key = new.get("id") or new.get("task")
        if key in existing_map:
            # Update existing task fields
            existing_map[key].update({k: v for k, v in new.items() if k != "id"})
        else:
            # Add new task with ID
            if not new.get("id"):
                new["id"] = generate_task_id(new.get("task", ""))
            new["status"] = "pending"
            existing.append(new)
    
    return existing

@app.route("/")
def index():
    """Upload page + project selection"""
    data = load_data()
    projects = data.get("projects", [])
    project_names = [p.get("project_name") for p in projects]
    semester = data.get("semester", {})
    
    # ✅ Build master_tasks for preview (same logic as tasks_page)
    master_tasks = []
    for p in projects:
        for t in p.get("pending_tasks", []):
            t_copy = t.copy()  # Avoid mutating original
            t_copy["project"] = p["project_name"]
            t_copy["score"] = 3 if t_copy.get("priority")=="high" else 2 if t_copy.get("priority")=="medium" else 1
            master_tasks.append(t_copy)
    master_tasks.sort(key=lambda x: (-x["score"], x.get("deadline", "9999-12-31")))
    
    return render_template("index.html",
                         project_names=project_names,
                         semester=semester,
                         projects=projects,
                         master_tasks=master_tasks)

@app.route("/tasks")
def tasks_page():
    """Task board view"""
    data = load_data()
    projects = data.get("projects", [])
    
    # Build master queue
    all_pending = []
    for p in projects:
        for t in p.get("pending_tasks", []):
            t["project"] = p["project_name"]
            t["score"] = 3 if t.get("priority")=="high" else 2 if t.get("priority")=="medium" else 1
            all_pending.append(t)
    all_pending.sort(key=lambda x: (-x["score"], x.get("deadline", "9999-12-31")))
    
    return render_template("tasks.html", projects=projects, master_tasks=all_pending[:20])

@app.route("/api/semester", methods=["POST"])
def set_semester():
    """Save semester settings"""
    data = load_data()
    data["semester"] = {
        "start": request.json.get("start"),
        "end": request.json.get("end"),
        "break_week": int(request.json.get("break_week", 8))
    }
    save_data(data)
    return jsonify({"status": "success"})

@app.route("/api/semester/status", methods=["GET"])
def semester_status():
    """Check if semester is configured + return settings"""
    data = load_data()
    semester = data.get("semester", {})
    return jsonify({
        "configured": bool(semester.get("start")),  # True if start date exists
        "start": semester.get("start"),
        "end": semester.get("end"),
        "break_week": semester.get("break_week", 8)
    })

@app.route("/api/projects", methods=["GET"])
def list_projects():
    """List project names for dropdown"""
    data = load_data()
    return jsonify([p.get("project_name") for p in data.get("projects", [])])

@app.route("/process", methods=["POST"])
def process_upload():
    """Process input and merge tasks into selected project"""
    data = load_data()
    
    project_name = request.form.get("project_name")
    if not project_name:
        return jsonify({"error": "project_name required"}), 400
    
    files = request.files.getlist("files")
    notes = request.form.get("notes", "")
    semester = data.get("semester", {})
    sem_start = semester.get("start", "2026-01-12")
    sem_end = semester.get("end", "2026-05-15")
    break_week = semester.get("break_week", 8)
    
    # ✅ Get existing pending tasks for this project
    project = next((p for p in data["projects"] if p.get("project_name") == project_name), None)
    existing_pending = project.get("pending_tasks", []) if project else []
    
    # ✅ Call AI with merge context
    result = ai.extract_tasks(
        files, notes, sem_start, sem_end, 
        existing_pending=existing_pending,
        break_week=break_week
    )
    
    # ✅ Merge logic
    new_tasks = result.get("tasks", [])
    merged_tasks = merge_tasks(existing_pending, new_tasks)
    
    # ✅ Update or create project
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
    
    save_data(data)
    return jsonify({"status": "success", "project_name": project_name})

@app.route("/api/tasks/complete", methods=["POST"])
def complete_task():
    """Toggle task completion status"""
    data = load_data()
    req = request.json
    project_name = req.get("project_name")
    task_id = req.get("task_id")
    
    project = next((p for p in data["projects"] if p.get("project_name") == project_name), None)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    # Find and move task
    pending = project.get("pending_tasks", [])
    completed = project.get("completed_tasks", [])
    
    task = next((t for t in pending if t.get("id") == task_id), None)
    if task:
        task["status"] = "completed"
        pending = [t for t in pending if t.get("id") != task_id]
        completed.append(task)
        project["pending_tasks"] = pending
        project["completed_tasks"] = completed
        save_data(data)
        return jsonify({"status": "completed"})
    
    # Un-complete: move back to pending
    task = next((t for t in completed if t.get("id") == task_id), None)
    if task:
        task["status"] = "pending"
        completed = [t for t in completed if t.get("id") != task_id]
        pending.append(task)
        project["pending_tasks"] = pending
        project["completed_tasks"] = completed
        save_data(data)
        return jsonify({"status": "pending"})
    
    return jsonify({"error": "Task not found"}), 404

@app.route("/api/reset", methods=["POST"])
def reset_semester():
    """Clear all data for new semester"""
    save_data({"semester": {}, "projects": []})
    return jsonify({"status": "reset"})

if __name__ == "__main__":
    print("🚀 Mess2Master running on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)