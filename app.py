from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
import os
import json
from gemini_client import Mess2MasterAI

load_dotenv()
app = Flask(__name__)
ai = Mess2MasterAI()
DATA_FILE = "data/projects.json"

os.makedirs("data", exist_ok=True)

def load_projects():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f: json.dump([], f)
        return []
    try:
        with open(DATA_FILE, "r") as f: return json.load(f)
    except: return []

def save_projects(projects):
    with open(DATA_FILE, "w") as f: json.dump(projects, f, indent=2)

@app.route("/")
def dashboard():
    projects = load_projects()
    
    # Build master priority queue across all projects
    all_tasks = []
    for p in projects:
        for t in p.get("tasks", []):
            t["project"] = p["project_name"]
            t["score"] = 3 if t.get("priority") == "high" else 2 if t.get("priority") == "medium" else 1
            all_tasks.append(t)
            
    # Sort: High priority first, then earliest due date
    all_tasks.sort(key=lambda x: (-x["score"], x.get("due_date", "9999-12-31")))
    
    return render_template("index.html", projects=projects, master_tasks=all_tasks[:20])

@app.route("/process", methods=["POST"])
def process_upload():
    files = request.files.getlist("files")
    notes = request.form.get("notes", "")
    sem_start = request.form.get("sem_start", "2026-01-12")
    sem_end = request.form.get("sem_end", "2026-05-15")

    result = ai.extract_tasks(files, notes, sem_start, sem_end)

    projects = load_projects()
    existing = next((p for p in projects if p.get("project_name") == result.get("project_name")), None)
    if existing:
        existing.update(result)
    else:
        projects.append(result)
    save_projects(projects)

    return jsonify({"status": "success", "project_name": result.get("project_name")})

if __name__ == "__main__":
    print("🚀 Mess2Master running on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)