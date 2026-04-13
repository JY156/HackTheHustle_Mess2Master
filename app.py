from flask import Flask, request, jsonify, render_template, redirect
from dotenv import load_dotenv
import os, json, time, threading
import re
from datetime import datetime
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
WEB_ALERT_DUE_SOON_HOURS = int(os.getenv("WEB_ALERT_DUE_SOON_HOURS", "24"))
WEB_ALERT_STALLED_HOURS = int(os.getenv("WEB_ALERT_STALLED_HOURS", "48"))
GUIDANCE_CACHE = {}
GUIDANCE_CACHE_TTL_SEC = int(os.getenv("GUIDANCE_CACHE_TTL_SEC", "3600"))

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


def task_signature(task):
    """Build a stable semantic key for deduping equivalent tasks across reprocessing."""
    title = str(task.get("title") or task.get("task") or "").strip().lower()
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[^a-z0-9 ]", "", title)
    deadline = str(task.get("deadline") or task.get("due_date") or "").strip()
    return (title, deadline)

# === Helper: Merge Tasks by ID ===
def merge_tasks(existing, new_tasks):
    """Merge new tasks into existing, preserving IDs and avoiding duplicates"""
    existing_map = {t.get("id"): t for t in existing if t.get("id")}
    signature_map = {task_signature(t): t for t in existing if (t.get("title") or t.get("task"))}
    
    for new in new_tasks:
        key = new.get("id")
        if key and key in existing_map:
            # Update existing task fields (except id)
            existing_map[key].update({k: v for k, v in new.items() if k != "id"})
            signature_map[task_signature(existing_map[key])] = existing_map[key]
        else:
            # Merge by semantic equivalence to prevent duplicate cards on repeat voice processing.
            sig = task_signature(new)
            match = signature_map.get(sig)
            if match:
                match.update({k: v for k, v in new.items() if k != "id"})
                match["status"] = "pending"
                continue

            # Add new task with guaranteed ID
            if not new.get("id"):
                new["id"] = f"ts_{int(time.time())}_{hash(new.get('title',''))%10000}"
            new["status"] = "pending"
            existing.append(new)
            existing_map[new["id"]] = new
            signature_map[task_signature(new)] = new
    return existing

def find_task(data, project_name, task_id):
    project = next((p for p in data.get("projects", []) if p.get("project_name") == project_name), None)
    if not project:
        return None, None
    for bucket_name in ("pending_tasks", "completed_tasks"):
        for task in project.get(bucket_name, []):
            if task.get("id") == task_id:
                return project, task
    return project, None


def guidance_cache_key(project_name, task):
    return "|".join([
        str(project_name or ""),
        str(task.get("id") or ""),
        str(task.get("title") or task.get("task") or ""),
        str(task.get("deadline") or task.get("due_date") or ""),
        str(task.get("priority") or ""),
    ])


def get_cached_guidance(key):
    item = GUIDANCE_CACHE.get(key)
    if not item:
        return None
    if (time.time() - item.get("ts", 0)) > GUIDANCE_CACHE_TTL_SEC:
        GUIDANCE_CACHE.pop(key, None)
        return None
    return item.get("payload")


def set_cached_guidance(key, payload):
    GUIDANCE_CACHE[key] = {"ts": time.time(), "payload": payload}


def notion_dashboard_url():
    direct = (os.getenv("NOTION_DATABASE_URL") or "").strip()
    if direct:
        return direct
    db_id = (os.getenv("NOTION_DATABASE_ID") or "").replace("-", "").strip()
    return f"https://www.notion.so/{db_id}" if db_id else None


def build_display_gaps(project):
    """Combine AI gaps with deterministic task hygiene risks for UI display."""
    base_gaps = list(project.get("gaps") or [])
    pending_tasks = project.get("pending_tasks") or []

    unassigned_count = 0
    missing_deadline_count = 0
    for task in pending_tasks:
        owner = str(task.get("owner") or "").strip().lower()
        deadline = str(task.get("deadline") or task.get("due_date") or "").strip()
        if not owner or owner == "unassigned":
            unassigned_count += 1
        if not deadline:
            missing_deadline_count += 1

    if unassigned_count:
        label = "task" if unassigned_count == 1 else "tasks"
        base_gaps.append({
            "issue": f"{unassigned_count} pending {label} without assignee",
            "suggestion": "Assign each task to a specific teammate to avoid ownership gaps.",
        })

    if missing_deadline_count:
        label = "task" if missing_deadline_count == 1 else "tasks"
        base_gaps.append({
            "issue": f"{missing_deadline_count} pending {label} without deadline",
            "suggestion": "Set a clear due date so priorities and reminders stay reliable.",
        })

    deduped = []
    seen = set()
    for gap in base_gaps:
        issue = str((gap or {}).get("issue") or "").strip()
        suggestion = str((gap or {}).get("suggestion") or "").strip()
        key = (issue.lower(), suggestion.lower())
        if not issue or key in seen:
            continue
        seen.add(key)
        deduped.append({"issue": issue, "suggestion": suggestion})

    return deduped


def parse_deadline_for_alert(task):
    raw = str(task.get("deadline") or task.get("due_date") or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                return dt.replace(hour=23, minute=59)
            return dt
        except ValueError:
            continue
    return None


def parse_created_at_from_id(task):
    task_id = str(task.get("id") or "")
    parts = task_id.split("_")
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(parts[1]))
    except Exception:
        return None


def build_project_alerts(project):
    alerts = []
    now = datetime.now()
    for task in project.get("pending_tasks", []) or []:
        title = str(task.get("title") or task.get("task") or "Untitled").strip()
        task_id = str(task.get("id") or "").strip()
        owner = str(task.get("owner") or "").strip().lower()
        unassigned = (not owner) or owner == "unassigned"
        if not unassigned:
            continue

        deadline_dt = parse_deadline_for_alert(task)
        if deadline_dt:
            delta_hours = (deadline_dt - now).total_seconds() / 3600.0
            if delta_hours < 0:
                alerts.append({
                    "type": "overdue",
                    "title": title,
                    "task_id": task_id,
                    "urgency_rank": 3,
                    "metric": delta_hours,
                })
                continue
            if delta_hours <= WEB_ALERT_DUE_SOON_HOURS:
                alerts.append({
                    "type": "due_soon",
                    "title": title,
                    "task_id": task_id,
                    "urgency_rank": 2,
                    "metric": delta_hours,
                })
                continue

        created_at = parse_created_at_from_id(task)
        if created_at:
            age_hours = (now - created_at).total_seconds() / 3600.0
            if age_hours >= WEB_ALERT_STALLED_HOURS:
                alerts.append({
                    "type": "stalled",
                    "title": title,
                    "task_id": task_id,
                    "urgency_rank": 1,
                    "metric": -age_hours,
                })
    alerts.sort(key=lambda a: (-int(a.get("urgency_rank", 0)), float(a.get("metric", 0.0))))
    return alerts


def alert_anchor(task_id):
    clean = str(task_id or "").strip()
    return f"task-{clean}" if clean else None


def first_alert_target(project, external=False):
    alerts = project.get("alerts") or []
    if not alerts:
        return None
    anchor = alert_anchor(alerts[0].get("task_id"))
    if not anchor:
        return None
    return f"/tasks#{anchor}" if external else f"#{anchor}"


def first_global_alert_target(projects, external=False):
    best_item = None
    for project in projects:
        for alert in project.get("alerts") or []:
            task_id = str(alert.get("task_id") or "").strip()
            if not task_id:
                continue
            candidate = {
                "rank": int(alert.get("urgency_rank", 0)),
                "metric": float(alert.get("metric", 0.0)),
                "task_id": task_id,
            }
            if not best_item:
                best_item = candidate
                continue
            if (candidate["rank"], -candidate["metric"]) > (best_item["rank"], -best_item["metric"]):
                best_item = candidate

    if not best_item:
        return None
    anchor = alert_anchor(best_item["task_id"])
    if not anchor:
        return None
    return f"/tasks#{anchor}" if external else f"#{anchor}"


def project_alert_task_ids(project):
    ids = []
    for item in project.get("alerts") or []:
        task_id = str(item.get("task_id") or "").strip()
        if task_id:
            ids.append(task_id)
    return sorted(set(ids))

# === Routes ===

@app.route("/")
def index():
    """Upload page + project selection"""
    data = load_state()
    projects = data.get("projects", [])
    for project in projects:
        project["display_gaps"] = build_display_gaps(project)
        project["alerts"] = build_project_alerts(project)
        project["alert_count"] = len(project["alerts"])
        project["alert_task_ids"] = project_alert_task_ids(project)
        project["first_alert_target"] = first_alert_target(project, external=True)
    alert_count = sum(p.get("alert_count", 0) for p in projects)
    alert_target = first_global_alert_target(projects, external=True)
    project_names = [p.get("project_name") for p in projects]
    semester = data.get("semester", {})
    
    # Build master preview (null-safe sort)
    master_tasks = []
    for p in projects:
        for t in p.get("pending_tasks", []):
            t_copy = t.copy()
            t_copy["project"] = p["project_name"]
            t_copy["score"] = priority_score(t_copy.get("priority"))
            t_copy["needs_alert"] = str(t_copy.get("id") or "") in set(p.get("alert_task_ids") or [])
            master_tasks.append(t_copy)
    master_tasks.sort(key=lambda x: (-x["score"], safe_deadline(x)))
    
    return render_template("index.html",
                         project_names=project_names,
                         semester=semester,
                         projects=projects,
                         master_tasks=master_tasks[:5],
                         alert_count=alert_count,
                         alert_target=alert_target)  # Preview only

@app.route("/tasks")
def tasks_page():
    """Task board view with project tabs"""
    data = load_state()
    projects = data.get("projects", [])
    for project in projects:
        project["display_gaps"] = build_display_gaps(project)
        project["alerts"] = build_project_alerts(project)
        project["alert_count"] = len(project["alerts"])
        project["alert_task_ids"] = project_alert_task_ids(project)
        project["first_alert_target"] = first_alert_target(project, external=False)
    alert_count = sum(p.get("alert_count", 0) for p in projects)
    alert_target = first_global_alert_target(projects, external=False)
    
    # Build master queue (null-safe sort)
    all_pending = []
    for p in projects:
        alert_ids = set(p.get("alert_task_ids") or [])
        for t in p.get("pending_tasks", []):
            t_copy = t.copy()
            t_copy["project"] = p["project_name"]
            t_copy["score"] = priority_score(t_copy.get("priority"))
            t_copy["needs_alert"] = str(t_copy.get("id") or "") in alert_ids
            all_pending.append(t_copy)
    all_pending.sort(key=lambda x: (-x["score"], safe_deadline(x)))
    
    return render_template("tasks.html", 
                         projects=projects, 
                         master_tasks=all_pending[:20],
                         alert_count=alert_count,
                         alert_target=alert_target)

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
            # Keep most recently updated project at the end for homepage "recent" rendering.
            data["projects"] = [p for p in data["projects"] if p.get("project_name") != project_name] + [project]
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
            "fallback_reason": result.get("_meta", {}).get("fallback_reason"),
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

@app.route("/api/tasks/update", methods=["POST"])
def update_task():
    """Update editable task fields while preserving stored task identity."""
    data = load_state()
    req = request.json or {}
    project_name = req.get("project_name")
    task_id = req.get("task_id")

    if not project_name or not task_id:
        return jsonify({"error": "project_name and task_id are required"}), 400

    project, task = find_task(data, project_name, task_id)
    if not project or not task:
        return jsonify({"error": "Task not found"}), 404

    updates = {
        "title": (req.get("title") or task.get("title") or task.get("task") or "").strip(),
        "deadline": (req.get("deadline") or "").strip() or None,
        "owner": (req.get("owner") or "").strip() or None,
        "priority": (req.get("priority") or task.get("priority") or "medium").strip().lower(),
    }

    if updates["priority"] not in {"high", "medium", "low"}:
        updates["priority"] = "medium"

    task.update(updates)
    task["status"] = task.get("status") or "pending"

    save_state(data)
    return jsonify({
        "status": "success",
        "task": task,
        "project_name": project_name,
    })

@app.route("/api/tasks/delete", methods=["POST"])
def delete_task():
    """Delete a task from pending or completed buckets."""
    data = load_state()
    req = request.json or {}
    project_name = req.get("project_name")
    task_id = req.get("task_id")

    if not project_name or not task_id:
        return jsonify({"error": "project_name and task_id are required"}), 400

    project = next((p for p in data.get("projects", []) if p.get("project_name") == project_name), None)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    removed = False
    for bucket_name in ("pending_tasks", "completed_tasks"):
        bucket = project.get(bucket_name, [])
        new_bucket = [t for t in bucket if t.get("id") != task_id]
        if len(new_bucket) != len(bucket):
            project[bucket_name] = new_bucket
            removed = True

    if not removed:
        return jsonify({"error": "Task not found"}), 404

    save_state(data)
    return jsonify({"status": "deleted", "project_name": project_name, "task_id": task_id})


@app.route("/api/tasks/guidance", methods=["POST"])
def task_guidance():
    """Generate concise per-task guidance with caching for snappy UX."""
    req = request.json or {}
    project_name = req.get("project_name")
    task_id = req.get("task_id")

    if not project_name or not task_id:
        return jsonify({"error": "project_name and task_id are required"}), 400

    data = load_state()
    project, task = find_task(data, project_name, task_id)
    if not project or not task:
        return jsonify({"error": "Task not found"}), 404

    key = guidance_cache_key(project_name, task)
    cached = get_cached_guidance(key)
    if cached:
        return jsonify({"status": "success", **cached, "cached": True})

    try:
        result = ai.generate_task_guidance(task)
        payload = {
            "guidance": result.get("guidance", ""),
            "fallback": bool(result.get("fallback", False)),
            "used_model": result.get("used_model"),
            "task_id": task_id,
            "project_name": project_name,
        }
        set_cached_guidance(key, payload)
        return jsonify({"status": "success", **payload, "cached": False})
    except Exception as e:
        return jsonify({"error": f"Unable to generate guidance: {str(e)}"}), 500

@app.route("/sync-notion", methods=["POST"])
def sync_notion():
    """Sync tasks to Notion. Always returns JSON, even on crashes."""
    try:
        # Check credentials first
        if not os.getenv("NOTION_TOKEN") or not os.getenv("NOTION_DATABASE_ID"):
            return jsonify({
                "status": "fallback",
                "message": "Notion not configured. Use clipboard export instead.",
                "clipboard_markdown": generate_notion_markdown()
            })
        
        data = load_state()
        projects = data.get("projects", [])
        if not projects:
            return jsonify({"status": "error", "message": "No projects to sync"}), 400
        
        # Check if specific project requested
        req_data = request.get_json(silent=True) or {}
        target_project_name = req_data.get("project_name")
        
        if target_project_name:
            project = next((p for p in projects if p.get("project_name") == target_project_name), None)
            if not project:
                return jsonify({"status": "error", "message": f"Project '{target_project_name}' not found"}), 404
            projects_to_sync = [project]
        else:
            projects_to_sync = projects

        synced_count = 0
        errors = []
        
        for proj in projects_to_sync:
            all_tasks = proj.get("pending_tasks", []) + proj.get("completed_tasks", [])
            if not all_tasks: continue
            
            try:
                # Import here to catch missing file gracefully
                from notion_client import NotionClient
                client = NotionClient()
                result = client.sync_tasks(all_tasks, proj.get("project_name", "Untitled"))
                
                if result.get("status") == "success":
                    synced_count += result.get("synced_count", 0)
                else:
                    errors.extend(result.get("errors", []))
            except ImportError:
                errors.append("Notion client not installed. Run: pip install requests")
            except Exception as e:
                errors.append(f"{proj.get('project_name')}: {str(e)}")

        if synced_count > 0:
            return jsonify({
                "status": "success", 
                "synced_count": synced_count, 
                "total_tasks": len([t for p in projects_to_sync for t in p.get("pending_tasks", [])]),
                "errors": errors,
                "dashboard_url": notion_dashboard_url(),
            })
        else:
            # Graceful fallback to clipboard
            return jsonify({
                "status": "fallback",
                "message": "Notion API failed. Checklist copied to clipboard.",
                "clipboard_markdown": generate_notion_markdown()
            })
            
    except Exception as e:
        # 🔥 NEVER return HTML. Always return JSON.
        print(f"❌ /sync-notion CRASH: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}",
            "clipboard_markdown": generate_notion_markdown()
        }), 500

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