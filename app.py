from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
import os
import json
from gemini_client import Mess2MasterAI
from notion_client import NotionClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(BASE_DIR), ".env"))
app = Flask(__name__)
ai = Mess2MasterAI()
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "projects.json")

os.makedirs(DATA_DIR, exist_ok=True)

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
    try:
        files = request.files.getlist("files")
        notes = request.form.get("notes", "")
        sem_start = request.form.get("sem_start", "2026-01-12")
        sem_end = request.form.get("sem_end", "2026-05-15")

        received_files = []
        for file in files:
            file_bytes = file.read()
            received_files.append({"name": file.filename, "size": len(file_bytes)})
            file.stream.seek(0)

        print(f"📥 Received files: {received_files}")
        print(f"📝 Notes preview: {notes[:120]}")

        result = ai.extract_tasks(files, notes, sem_start, sem_end)

        save_projects([result])

        return jsonify({
            "status": "success",
            "project_name": result.get("project_name"),
            "received_files": received_files,
            "fallback": bool(result.get("_meta", {}).get("fallback")),
            "fallback_reason": result.get("_meta", {}).get("fallback_reason"),
            "used_model": result.get("_meta", {}).get("used_model"),
            "pdf_text_extracted": bool(result.get("_meta", {}).get("pdf_text_extracted")),
        })
    except Exception as e:
        print(f"❌ /process error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/sync-notion", methods=["POST"])
def sync_notion():
    try:
        projects = load_projects()
        if not projects:
            return jsonify({"status": "error", "message": "No projects to sync"}), 400
        
        project = projects[0]
        tasks = project.get("tasks", [])
        
        if not tasks:
            return jsonify({"status": "error", "message": "No tasks to sync"}), 400
        
        notion = NotionClient()
        result = notion.sync_tasks(tasks, project.get("project_name", "Untitled Project"))

        if result.get("status") != "success":
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        print(f"❌ /sync-notion error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == "__main__":
    print("🚀 Mess2Master running on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)