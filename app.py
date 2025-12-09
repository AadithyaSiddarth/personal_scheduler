#!/usr/bin/env python3
"""
Flask UI for Time-Weighted Task Scheduler

Run:
  python app.py
Open in browser:
  http://127.0.0.1:5000
"""

from flask import Flask, render_template, request, redirect, url_for, send_file, flash
from pathlib import Path
from datetime import datetime, date, time, timedelta
import json, csv

APP_DIR = Path(__file__).parent
TASK_FILE = APP_DIR / "tasks.json"
DATEFMT = "%Y-%m-%d"

app = Flask(__name__)
app.secret_key = "dev-secret-key"  # change for production

# --- storage helpers ---
def load_tasks():
    if not TASK_FILE.exists():
        return {"tasks": []}
    return json.loads(TASK_FILE.read_text(encoding="utf-8"))

def save_tasks(data):
    TASK_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# --- scheduling logic (same as CLI) ---
def days_until_deadline(deadline_str):
    if not deadline_str:
        return None
    try:
        d = datetime.strptime(deadline_str, DATEFMT).date()
    except Exception:
        return None
    return max(0, (d - date.today()).days)

def compute_score(task, max_window=7):
    minutes = max(1, int(task.get("minutes", 0)))
    impact = float(task.get("impact", 1))
    days_left = days_until_deadline(task.get("deadline"))
    if days_left is None:
        urgency = 1.0
    else:
        urgency = 1.0 + max(0, (max_window - days_left) / max_window)
    score = (impact / minutes) * urgency
    return score

def format_time(t: time):
    return t.strftime("%H:%M")

def schedule_tasks_for_day(tasks, hours=8.0, start="09:00", allow_split=False, urgency_window=7):
    tasks = [t.copy() for t in tasks]
    for t in tasks:
        t["_score"] = compute_score(t, max_window=urgency_window)
    def sort_key(t):
        dl = t.get("deadline") or "9999-12-31"
        return (-t["_score"], dl, t["minutes"])
    tasks.sort(key=sort_key)

    total_minutes = int(hours * 60)
    start_hour, start_min = map(int, start.split(":"))
    current_minutes = 0
    schedule = []
    leftover_minutes = total_minutes

    for t in tasks:
        if leftover_minutes <= 0:
            break
        duration = int(t["minutes"])
        if duration <= leftover_minutes:
            start_time = (datetime.combine(date.today(), time(hour=start_hour, minute=start_min)) + timedelta(minutes=current_minutes)).time()
            end_time = (datetime.combine(date.today(), start_time) + timedelta(minutes=duration)).time()
            schedule.append({
                "title": t["title"],
                "start": format_time(start_time),
                "end": format_time(end_time),
                "minutes": duration,
                "impact": t["impact"],
                "deadline": t.get("deadline")
            })
            current_minutes += duration
            leftover_minutes -= duration
        else:
            if allow_split and leftover_minutes > 0:
                chunk = leftover_minutes
                start_time = (datetime.combine(date.today(), time(hour=start_hour, minute=start_min)) + timedelta(minutes=current_minutes)).time()
                end_time = (datetime.combine(date.today(), start_time) + timedelta(minutes=chunk)).time()
                schedule.append({
                    "title": t["title"] + " (part)",
                    "start": format_time(start_time),
                    "end": format_time(end_time),
                    "minutes": chunk,
                    "impact": t["impact"],
                    "deadline": t.get("deadline")
                })
                current_minutes += chunk
                leftover_minutes = 0
            # otherwise skip task

    return schedule

# --- routes ---
@app.route("/", methods=["GET"])
def index():
    data = load_tasks()
    tasks = sorted(data["tasks"], key=lambda t: (t.get("deadline") or "9999-12-31"))
    return render_template("index.html", tasks=tasks, schedule=None)

@app.route("/add", methods=["POST"])
def add_task():
    title = request.form.get("title", "").strip()
    minutes = request.form.get("minutes", "").strip()
    impact = request.form.get("impact", "").strip()
    deadline = request.form.get("deadline", "").strip() or None
    notes = request.form.get("notes", "").strip() or ""
    if not title or not minutes or not impact:
        flash("Title, minutes and impact are required.", "danger")
        return redirect(url_for("index"))
    try:
        minutes = int(minutes)
        impact = float(impact)
    except Exception:
        flash("Minutes must be integer and impact numeric.", "danger")
        return redirect(url_for("index"))
    data = load_tasks()
    task = {
        "id": int(datetime.now().timestamp() * 1000),
        "title": title,
        "minutes": minutes,
        "impact": impact,
        "deadline": deadline,
        "notes": notes
    }
    data["tasks"].append(task)
    save_tasks(data)
    flash(f'Added task: "{title}"', "success")
    return redirect(url_for("index"))

@app.route("/remove/<task_id>", methods=["POST"])
def remove_task(task_id):
    data = load_tasks()
    before = len(data["tasks"])
    data["tasks"] = [t for t in data["tasks"] if str(t["id"]) != str(task_id)]
    save_tasks(data)
    after = len(data["tasks"])
    flash(f"Removed {before-after} task(s).", "info")
    return redirect(url_for("index"))

@app.route("/schedule", methods=["POST"])
def schedule_route():
    hours = float(request.form.get("hours", "8"))
    start = request.form.get("start", "09:00")
    allow_split = bool(request.form.get("allow_split"))
    urgency_window = int(request.form.get("urgency_window", "7"))
    data = load_tasks()
    schedule = schedule_tasks_for_day(data["tasks"], hours=hours, start=start, allow_split=allow_split, urgency_window=urgency_window)
    tasks = sorted(data["tasks"], key=lambda t: (t.get("deadline") or "9999-12-31"))
    return render_template("index.html", tasks=tasks, schedule=schedule, hours=hours, start=start)

@app.route("/export_tasks")
def export_tasks():
    data = load_tasks()
    out = APP_DIR / "tasks_export.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "minutes", "impact", "deadline", "notes"])
        for t in data["tasks"]:
            w.writerow([t["id"], t["title"], t["minutes"], t["impact"], t.get("deadline") or "", t.get("notes") or ""])
    return send_file(out, as_attachment=True)

@app.route("/export_schedule")
def export_schedule_route():
    # simple export of last generated schedule if needed; here we recompute with defaults
    data = load_tasks()
    schedule = schedule_tasks_for_day(data["tasks"], hours=8.0, start="09:00", allow_split=False)
    out = APP_DIR / f"schedule_{date.today().isoformat()}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["start", "end", "title", "minutes", "impact", "deadline"])
        for s in schedule:
            w.writerow([s["start"], s["end"], s["title"], s["minutes"], s["impact"], s["deadline"] or ""])
    return send_file(out, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)
