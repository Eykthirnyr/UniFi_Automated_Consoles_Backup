#!/usr/bin/env python3
import sys
import subprocess

###############################################################################
# 1) Check/install dependencies
###############################################################################
REQUIRED_PACKAGES = [
    "flask",
    "flask_apscheduler",
    "requests",
    "selenium",
    "webdriver_manager"
]

def check_and_install_dependencies():
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            print(f"[INFO] Missing '{pkg}'. Installing...")
            subprocess.run([sys.executable, "-m", "pip", "install", pkg], check=True)

check_and_install_dependencies()

###############################################################################
# Now safe to import
###############################################################################
import os
import json
import time
import threading
import zipfile
import io
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, render_template_string, flash, send_from_directory, send_file
from flask_apscheduler import APScheduler

# Selenium + WebDriver
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Wait utilities
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

###############################################################################
# CONFIG
###############################################################################

APP_FOLDER = os.path.join(os.getcwd(), "unifi_app")
os.makedirs(APP_FOLDER, exist_ok=True)

APPDATA_JSON = os.path.join(APP_FOLDER, "appdata.json")
COOKIES_JSON = os.path.join(APP_FOLDER, "cookies.json")

BACKUP_ROOT = os.path.join(APP_FOLDER, "backups")
os.makedirs(BACKUP_ROOT, exist_ok=True)

DOWNLOAD_DIR = os.path.join(APP_FOLDER, "chrome_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

SECRET_KEY = "REPLACE_WITH_A_STRONG_SECRET_KEY"

appdata = {}

current_task_status = {
    "running": False,
    "step": "",
    "start_time": None
}

###############################################################################
# FLASK + SCHEDULER SETUP
###############################################################################

class Config:
    SCHEDULER_API_ENABLED = True

app = Flask(__name__)
app.config.from_object(Config)
app.config["SECRET_KEY"] = SECRET_KEY

scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

###############################################################################
# LOAD / SAVE APPDATA
###############################################################################
def load_appdata():
    global appdata
    if not os.path.exists(APPDATA_JSON):
        appdata = {
            "master_logged_in": False,
            "consoles": [],
            "logs": [],
            "schedule": {
                "backup_enabled": True,
                "backup_value": 1,
                "backup_unit": "days",
                "check_enabled": True,
                "check_value": 4,
                "check_unit": "hours",
            }
        }
        save_appdata()
    else:
        with open(APPDATA_JSON, "r", encoding="utf-8") as f:
            appdata = json.load(f)

        # Ensure all needed keys exist
        if "master_logged_in" not in appdata:
            appdata["master_logged_in"] = False
        if "consoles" not in appdata:
            appdata["consoles"] = []
        if "logs" not in appdata:
            appdata["logs"] = []
        if "schedule" not in appdata:
            appdata["schedule"] = {}
        for key, default_val in [
            ("backup_enabled", True),
            ("backup_value", 1),
            ("backup_unit", "days"),
            ("check_enabled", True),
            ("check_value", 4),
            ("check_unit", "hours")
        ]:
            if key not in appdata["schedule"]:
                appdata["schedule"][key] = default_val

        save_appdata()

def save_appdata():
    with open(APPDATA_JSON, "w", encoding="utf-8") as f:
        json.dump(appdata, f, indent=2)

def add_log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    appdata["logs"].append({"timestamp": now, "message": msg})
    # Keep only last 100 logs
    appdata["logs"] = appdata["logs"][-100:]
    save_appdata()

###############################################################################
# TASK LOCK
###############################################################################
def is_task_running():
    return current_task_status["running"]

def start_task(step_msg):
    current_task_status["running"] = True
    current_task_status["step"] = step_msg
    current_task_status["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def end_task():
    current_task_status["running"] = False
    current_task_status["step"] = ""
    current_task_status["start_time"] = None

###############################################################################
# SELENIUM DRIVER
###############################################################################
def get_selenium_driver():
    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--start-maximized")

    # Force Chrome to download to DOWNLOAD_DIR
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True
    }
    chrome_options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def remove_old_cookie():
    if os.path.exists(COOKIES_JSON):
        os.remove(COOKIES_JSON)
        add_log("Removed old cookies.json manually.")

def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_JSON, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)
    add_log("Cookies saved to cookies.json")

def load_cookies(driver):
    if os.path.exists(COOKIES_JSON):
        with open(COOKIES_JSON, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        driver.get("https://unifi.ui.com/")
        time.sleep(2)
        for c in cookies:
            try:
                driver.add_cookie({
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c["domain"],
                    "path": c["path"]
                })
            except:
                pass
        add_log("Cookies loaded from cookies.json")

###############################################################################
# MANUAL LOGIN
###############################################################################
def manual_login_browser():
    start_task("Manual server-side login")
    driver = get_selenium_driver()
    try:
        driver.get("https://unifi.ui.com/")
        add_log("Opened https://unifi.ui.com for manual login. User must do credentials, MFA, etc...")

        success = False
        for _ in range(120):  # up to 2 mins for user to finish
            time.sleep(1)
            url_ = driver.current_url.lower()
            if "unifi.ui.com" in url_ and "/login" not in url_ and "/mfa" not in url_:
                success = True
                break

        if success:
            save_cookies(driver)
            appdata["master_logged_in"] = True
            add_log("Manual login success.")
        else:
            add_log("Timeout => user never left /login or /mfa => not logged in.")
    except Exception as e:
        add_log(f"Manual login error: {e}")
    finally:
        driver.quit()
        end_task()
        save_appdata()

###############################################################################
# BACKUP RETRIEVAL
###############################################################################
def download_backup_for_console(console):
    start_task(f"Backup for '{console['name']}'")
    driver = get_selenium_driver()
    try:
        driver.get("https://unifi.ui.com/")
        time.sleep(3)
        load_cookies(driver)
        time.sleep(3)

        driver.get(console["backup_url"])
        time.sleep(5)

        curr_url = driver.current_url.lower()
        if "/login" in curr_url or "/mfa" in curr_url:
            console["last_backup_status"] = "Fail: forced login => cookies expired?"
            add_log(f"Backup fail for '{console['name']}': forced login => cookies invalid.")
            # Mark not logged in
            appdata["master_logged_in"] = False
            save_appdata()
            return

        # Attempt to click the main "Download" button
        main_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@name='backupDownload']"))
        )
        main_btn.click()
        time.sleep(3)

        # Attempt to click the second "Download" in the pop-up
        second_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[@name='backupDownload' and contains(@class, 'css-network-qhqpn7')]"
            ))
        )
        second_btn.click()

        # Wait for file to appear in DOWNLOAD_DIR
        found_file = None
        for _ in range(60):
            possible = [
                f for f in os.listdir(DOWNLOAD_DIR)
                if (f.endswith(".unf") or f.endswith(".tar.gz")) and not f.endswith(".crdownload")
            ]
            if possible:
                possible.sort(
                    key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)),
                    reverse=True
                )
                found_file = possible[0]
                break
            time.sleep(1)

        if not found_file:
            console["last_backup_status"] = "Fail: no .unf or .tar.gz found after 60s"
            add_log("No newly downloaded .unf or .tar.gz file found in chrome_downloads folder.")
        else:
            # Move to date folder, rename with consoleName_ prefix
            day_folder = os.path.join(BACKUP_ROOT, datetime.now().strftime("%Y-%m-%d"))
            os.makedirs(day_folder, exist_ok=True)

            original_path = os.path.join(DOWNLOAD_DIR, found_file)
            new_name = f"{console['name']}_{found_file}"
            final_path = os.path.join(day_folder, new_name)
            os.rename(original_path, final_path)

            console["last_backup_status"] = "Success"
            console["last_backup_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            add_log(f"Backup success for '{console['name']}' => {new_name}")

    except Exception as e:
        console["last_backup_status"] = f"Fail: {e}"
        add_log(f"Backup fail for '{console['name']}': {e}")
    finally:
        driver.quit()
        end_task()
        save_appdata()

###############################################################################
# SCHEDULED CONNECTIVITY CHECK
###############################################################################
def scheduled_connectivity_check():
    if is_task_running():
        add_log("Connectivity check skipped => another task running.")
        return
    if not appdata["master_logged_in"]:
        add_log("Connectivity check => not logged in => skip.")
        return

    start_task("ConnectivityCheck")
    driver = get_selenium_driver()
    try:
        driver.get("https://unifi.ui.com/")
        time.sleep(2)
        load_cookies(driver)
        time.sleep(2)

        driver.get("https://unifi.ui.com/")
        time.sleep(3)
        curr_url = driver.current_url.lower()
        if "/login" in curr_url or "/mfa" in curr_url:
            add_log("Connectivity check => cookies invalid => forced login => set not logged in.")
            appdata["master_logged_in"] = False
            save_appdata()
        else:
            add_log("Connectivity check => success => still logged in.")
    except Exception as e:
        # If there's an error, also set not logged in
        add_log(f"Connectivity check => error => {e} => mark not logged in.")
        appdata["master_logged_in"] = False
        save_appdata()
    finally:
        driver.quit()
        end_task()

###############################################################################
# SCHEDULED BACKUP JOB
###############################################################################
def scheduled_backup_job():
    if is_task_running():
        add_log("Scheduled backup job skipped => another task is running.")
        return
    if not appdata.get("master_logged_in", False):
        add_log("Scheduled backup canceled => not logged in.")
        return

    add_log("Scheduled backup started...")
    for c in appdata["consoles"]:
        if is_task_running():
            add_log("Another task started => skipping remainder.")
            return
        download_backup_for_console(c)
        time.sleep(2)
    add_log("Scheduled backup complete.")

###############################################################################
# INIT SCHEDULE JOBS
###############################################################################
def init_schedule_jobs():
    # Remove old jobs if they exist
    if scheduler.get_job("BackupJob"):
        scheduler.remove_job("BackupJob")
    if scheduler.get_job("ConnectivityCheckJob"):
        scheduler.remove_job("ConnectivityCheckJob")

    s = appdata["schedule"]

    # SCHEDULE BACKUP JOB (if enabled)
    if s["backup_enabled"]:
        b_val = s["backup_value"]
        b_unit = s["backup_unit"]  # "minutes", "hours", "days"
        if b_unit == "minutes":
            scheduler.add_job(
                id="BackupJob",
                func=scheduled_backup_job,
                trigger="interval",
                minutes=b_val
            )
        elif b_unit == "hours":
            scheduler.add_job(
                id="BackupJob",
                func=scheduled_backup_job,
                trigger="interval",
                hours=b_val
            )
        elif b_unit == "days":
            scheduler.add_job(
                id="BackupJob",
                func=scheduled_backup_job,
                trigger="interval",
                days=b_val
            )

    # SCHEDULE CONNECTIVITY CHECK JOB (if enabled)
    if s["check_enabled"]:
        c_val = s["check_value"]
        c_unit = s["check_unit"]
        if c_unit == "minutes":
            scheduler.add_job(
                id="ConnectivityCheckJob",
                func=scheduled_connectivity_check,
                trigger="interval",
                minutes=c_val
            )
        elif c_unit == "hours":
            scheduler.add_job(
                id="ConnectivityCheckJob",
                func=scheduled_connectivity_check,
                trigger="interval",
                hours=c_val
            )
        elif c_unit == "days":
            scheduler.add_job(
                id="ConnectivityCheckJob",
                func=scheduled_connectivity_check,
                trigger="interval",
                days=c_val
            )

###############################################################################
# LOAD & INIT
###############################################################################
load_appdata()
init_schedule_jobs()

###############################################################################
# ADD: Helper function to format time deltas
###############################################################################
def format_timedelta(td):
    """Return a string like '1 day, 02:03:45' from a timedelta object."""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return "N/A"

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days == 1:
        parts.append("1 day")
    elif days > 1:
        parts.append(f"{days} days")
    parts.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
    return ", ".join(parts)

###############################################################################
# HTML TEMPLATE
###############################################################################
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
  <title>UniFi Automated Consoles Backup</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    body { font-family: sans-serif; margin: 20px; max-width: 1100px; margin: 20px auto; }
    .section { margin-bottom: 20px; padding: 10px; border: 1px solid #ccc; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 6px; vertical-align: top; }
    .log-list { max-height: 200px; overflow-y: auto; background: #fafafa; padding: 5px; }
    .console-url {
      max-width: 300px;
      word-wrap: break-word;
      white-space: pre-wrap;
    }
    .status-circle {
      width: 14px; 
      height: 14px; 
      border-radius: 7px; 
      display: inline-block;
      margin-left: 10px;
    }
    label { font-weight: bold; }
  </style>
  <script>
    function autoRefresh() {
      const running = "{{ current_task.running }}";
      if (running === "True") {
        setTimeout(() => {
          window.location.reload();
        }, 5000);
      }
    }
    window.onload = autoRefresh;
  </script>
</head>
<body>
  <h1>UniFi Automated Consoles Backup</h1>

  <div>
    {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      <ul>
      {% for category, message in messages %}
        <li><strong>{{ category }}:</strong> {{ message }}</li>
      {% endfor %}
      </ul>
    {% endif %}
    {% endwith %}
  </div>

  <div class="section">
    <h2>Current Task 
      {% if current_task.running %}
        - {{ current_task.step }}
      {% endif %}
    </h2>
    {% if current_task.running %}
      <p>Started: {{ current_task.start_time }}</p>
      <p>This page auto-refreshes every 5s while a task is running.</p>
    {% else %}
      <p>No task is running.</p>
    {% endif %}
  </div>

  <!-- ADDED: Next Backup Task Section -->
  <div class="section">
    <h2>Next Backup Task</h2>
    <p>
      <strong>Time Remaining:</strong> {{ time_remaining_backup }}
    </p>
  </div>

  <div class="section">
    <h2>Login Status
      {% if appdata.master_logged_in %}
        <span class="status-circle" style="background-color: green;" title="Logged in"></span>
      {% else %}
        <span class="status-circle" style="background-color: red;" title="Not logged in"></span>
      {% endif %}
    </h2>
    {% if appdata.master_logged_in %}
      <p>Already logged in. If cookies expire, re-login below.</p>
    {% else %}
      <p>Not logged in. Please do a manual server-side login.</p>
    {% endif %}
    <form method="POST" action="{{ url_for('manual_relogin') }}">
      <button type="submit">Re-Login / Clear Old Cookies</button>
    </form>
  </div>

  <div class="section">
    <h2>Schedules</h2>
    <p><em>Minimum 15 minutes if using minutes.</em></p>
    <form method="POST" action="{{ url_for('update_schedule') }}">
      <div style="display: flex; gap: 40px;">
        <div>
          <label>Enable Backup</label>
          <input type="checkbox" name="backup_enabled" value="1" 
            {% if appdata.schedule.backup_enabled %}checked{% endif %} />
          <br/><br/>
          <label>Backup Interval:</label><br/>
          <input type="number" name="backup_value" min="1" value="{{ appdata.schedule.backup_value }}" style="width: 80px;" />
          <select name="backup_unit">
            <option value="minutes" {% if appdata.schedule.backup_unit == 'minutes' %}selected{% endif %}>Minutes</option>
            <option value="hours"   {% if appdata.schedule.backup_unit == 'hours' %}selected{% endif %}>Hours</option>
            <option value="days"    {% if appdata.schedule.backup_unit == 'days' %}selected{% endif %}>Days</option>
          </select>
        </div>
        <div>
          <label>Enable Connectivity Check</label>
          <input type="checkbox" name="check_enabled" value="1"
            {% if appdata.schedule.check_enabled %}checked{% endif %} />
          <br/><br/>
          <label>Check Interval:</label><br/>
          <input type="number" name="check_value" min="1" value="{{ appdata.schedule.check_value }}" style="width: 80px;" />
          <select name="check_unit">
            <option value="minutes" {% if appdata.schedule.check_unit == 'minutes' %}selected{% endif %}>Minutes</option>
            <option value="hours"   {% if appdata.schedule.check_unit == 'hours' %}selected{% endif %}>Hours</option>
            <option value="days"    {% if appdata.schedule.check_unit == 'days' %}selected{% endif %}>Days</option>
          </select>
        </div>
      </div>
      <br/>
      <button type="submit">Update Schedules</button>
    </form>
  </div>

  <div class="section">
    <h2>Consoles</h2>
    <table>
      <tr>
        <th>Name</th>
        <th>Backup URL</th>
        <th>Last Backup Status</th>
        <th>Last Backup Time</th>
        <th>Actions</th>
      </tr>
      {% for c in appdata.consoles %}
      <tr>
        <td>{{ c.name }}</td>
        <td class="console-url">{{ c.backup_url }}</td>
        <td>{{ c.last_backup_status or 'None' }}</td>
        <td>{{ c.last_backup_time or 'Never' }}</td>
        <td>
          <form method="POST" action="{{ url_for('manual_backup', cid=c.id) }}" style="display:inline;">
            <button type="submit">Backup Now</button>
          </form>
          <form method="POST" action="{{ url_for('remove_console', cid=c.id) }}" style="display:inline;">
            <button type="submit">Remove</button>
          </form>
          <form method="GET" action="{{ url_for('download_latest_backup', cid=c.id) }}" style="display:inline;">
            <button type="submit">Download Latest</button>
          </form>
          <form method="GET" action="{{ url_for('console_history', cid=c.id) }}" style="display:inline;">
            <button type="submit">View History</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </table>
    <h3>Add Console</h3>
    <form method="POST" action="{{ url_for('add_console') }}">
      <p>
        <label>Name:</label><br/>
        <input type="text" name="name" required />
      </p>
      <p>
        <label>Backup URL:</label><br/>
        <input type="text" name="backup_url" required style="width: 400px;" />
      </p>
      <button type="submit">Add Console</button>
    </form>
    <br/>
    <form method="GET" action="{{ url_for('download_today_backups') }}">
      <button type="submit">Download All Today's Backups (ZIP)</button>
    </form>
  </div>

  <div class="section">
    <h2>Logs (Last 100)</h2>
    <div class="log-list">
      <ul>
      {% for entry in appdata.logs %}
        <li>[{{ entry.timestamp }}] - {{ entry.message }}</li>
      {% endfor %}
      </ul>
    </div>
  </div>

  <p style="margin-top:40px; text-align:center; font-size:0.9em; color:#888;">
    Made by <strong>Clément GHANEME</strong> (01/2025) - 
    <a href="https://clement.business/" target="_blank">https://clement.business/</a><br/>
    Use at your own risk. Not responsible for any damages or data losses.
  </p>
</body>
</html>
"""

###############################################################################
# ROUTES
###############################################################################
@app.route("/")
def dashboard():
    # Retrieve the Backup Job from APScheduler
    backup_job = scheduler.get_job("BackupJob")
    time_remaining_backup = "N/A"

    # If we have a backup job and a valid next run time, compute the remaining
    if backup_job and backup_job.next_run_time:
        now = datetime.now(backup_job.next_run_time.tzinfo)
        delta = backup_job.next_run_time - now
        time_remaining_backup = format_timedelta(delta)

    return render_template_string(
        HTML_TEMPLATE,
        appdata=appdata,
        current_task=current_task_status,
        time_remaining_backup=time_remaining_backup
    )

@app.route("/manual_relogin", methods=["POST"])
def manual_relogin():
    remove_old_cookie()
    appdata["master_logged_in"] = False
    save_appdata()

    def do_login():
        manual_login_browser()

    t = threading.Thread(target=do_login)
    t.start()
    flash("Forcing re-login. Old cookie removed. Ephemeral browser will open on the server side.")
    return redirect(url_for("dashboard"))

@app.route("/add_console", methods=["POST"])
def add_console():
    name = request.form.get("name", "").strip()
    curl = request.form.get("backup_url", "").strip()
    if not name or not curl:
        flash("Name and Backup URL are required", "danger")
        return redirect(url_for("dashboard"))

    new_id = max((c["id"] for c in appdata["consoles"]), default=0) + 1
    console_obj = {
        "id": new_id,
        "name": name,
        "backup_url": curl,
        "last_backup_status": "Unknown",
        "last_backup_time": None
    }
    appdata["consoles"].append(console_obj)
    save_appdata()
    flash(f"Console '{name}' added.", "success")
    return redirect(url_for("dashboard"))

@app.route("/remove_console/<int:cid>", methods=["POST"])
def remove_console(cid):
    found = False
    for c in appdata["consoles"]:
        if c["id"] == cid:
            appdata["consoles"].remove(c)
            found = True
            break
    if found:
        save_appdata()
        flash("Console removed.", "success")
    else:
        flash("Console not found.", "danger")
    return redirect(url_for("dashboard"))

@app.route("/manual_backup/<int:cid>", methods=["POST"])
def manual_backup(cid):
    if not appdata.get("master_logged_in", False):
        flash("Not logged in. Please do the manual login first.", "danger")
        return redirect(url_for("dashboard"))

    if is_task_running():
        flash("Another task is running. Wait until it finishes.", "danger")
        return redirect(url_for("dashboard"))

    console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
    if not console:
        flash("Console not found.", "danger")
        return redirect(url_for("dashboard"))

    def do_backup():
        download_backup_for_console(console)

    t = threading.Thread(target=do_backup)
    t.start()
    flash(f"Backup for '{console['name']}' started in background...", "info")
    return redirect(url_for("dashboard"))

@app.route("/update_schedule", methods=["POST"])
def update_schedule():
    s = appdata["schedule"]

    # Backup
    s["backup_enabled"] = ("backup_enabled" in request.form)
    s["backup_value"] = int(request.form.get("backup_value", "1"))
    s["backup_unit"] = request.form.get("backup_unit", "days")

    # Check
    s["check_enabled"] = ("check_enabled" in request.form)
    s["check_value"] = int(request.form.get("check_value", "4"))
    s["check_unit"] = request.form.get("check_unit", "hours")

    # Basic validation: if using minutes, ensure >= 15
    if s["backup_unit"] == "minutes" and s["backup_value"] < 15:
        s["backup_value"] = 15
        flash("Backup interval set to minimum of 15 minutes.", "warning")
    if s["check_unit"] == "minutes" and s["check_value"] < 15:
        s["check_value"] = 15
        flash("Check interval set to minimum of 15 minutes.", "warning")

    save_appdata()
    init_schedule_jobs()
    flash("Schedules updated.", "success")
    return redirect(url_for("dashboard"))

@app.route("/download_latest_backup/<int:cid>", methods=["GET"])
def download_latest_backup(cid):
    console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
    if not console:
        flash("Console not found.", "danger")
        return redirect(url_for("dashboard"))

    if not console.get("last_backup_time"):
        flash(f"No backup found for '{console['name']}'", "danger")
        return redirect(url_for("dashboard"))

    # The last_backup_time has format "YYYY-MM-DD HH:MM:SS"
    backup_day = console["last_backup_time"].split(" ")[0]  # "YYYY-MM-DD"
    folder_path = os.path.join(BACKUP_ROOT, backup_day)

    # Attempt to find a file that starts with "<consoleName>_" in that folder
    files = [
        f for f in os.listdir(folder_path)
        if f.startswith(console["name"] + "_")
    ]
    if not files:
        flash("No matching backup file found in the last backup folder.", "danger")
        return redirect(url_for("dashboard"))

    # Might be more than one, so let's pick the most recently modified
    files.sort(
        key=lambda x: os.path.getmtime(os.path.join(folder_path, x)),
        reverse=True
    )
    latest_file = files[0]

    return send_from_directory(folder_path, latest_file, as_attachment=True)

@app.route("/download_today_backups", methods=["GET"])
def download_today_backups():
    today_str = datetime.now().strftime("%Y-%m-%d")
    folder_path = os.path.join(BACKUP_ROOT, today_str)
    if not os.path.exists(folder_path):
        flash("No backups found for today.", "danger")
        return redirect(url_for("dashboard"))

    file_list = os.listdir(folder_path)
    if not file_list:
        flash("No backups found for today.", "danger")
        return redirect(url_for("dashboard"))

    # Create an in-memory zip
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w") as zf:
        for f in file_list:
            full_path = os.path.join(folder_path, f)
            if os.path.isfile(full_path):
                zf.write(full_path, arcname=f)

    mem_zip.seek(0)
    zip_filename = f"Backups_{today_str}.zip"
    return send_file(mem_zip,
                     as_attachment=True,
                     download_name=zip_filename,
                     mimetype="application/zip")

@app.route("/console_history/<int:cid>", methods=["GET"])
def console_history(cid):
    console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
    if not console:
        return f"<p>Console with ID {cid} not found.</p>"

    console_name = console["name"]

    # We will scan the last 30 days of subfolders inside BACKUP_ROOT
    # and find all files for that console.
    files_list = []
    today = datetime.now().date()
    earliest = today - timedelta(days=30)

    for day_offset in range(31):
        day = today - timedelta(days=day_offset)
        if day < earliest:
            break
        day_str = day.strftime("%Y-%m-%d")
        folder_path = os.path.join(BACKUP_ROOT, day_str)
        if not os.path.exists(folder_path):
            continue

        for fname in os.listdir(folder_path):
            if fname.startswith(console_name + "_"):
                fpath = os.path.join(folder_path, fname)
                if os.path.isfile(fpath):
                    mtime = os.path.getmtime(fpath)
                    dt_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                    files_list.append({
                        "date_folder": day_str,
                        "filename": fname,
                        "datetime": dt_str
                    })

    # Sort from newest to oldest
    files_list.sort(key=lambda x: x["datetime"], reverse=True)

    html_parts = [
        f"<h2>History for Console: {console_name}</h2>",
        f"<p>Showing backups from last 30 days (if any).</p>",
        "<table border='1' cellpadding='5' cellspacing='0'>",
        "<tr><th>Date Folder</th><th>File</th><th>Backup Time</th><th>Action</th></tr>"
    ]
    for item in files_list:
        link = url_for("download_specific_backup",
                       date_folder=item["date_folder"],
                       filename=item["filename"])
        html_parts.append(
            f"<tr>"
            f"<td>{item['date_folder']}</td>"
            f"<td>{item['filename']}</td>"
            f"<td>{item['datetime']}</td>"
            f"<td><a href='{link}'>Download</a></td>"
            f"</tr>"
        )
    html_parts.append("</table>")

    if not files_list:
        html_parts.append("<p>No backups found for this console in the last 30 days.</p>")

    html_parts.append(
        f"<p><a href='{url_for('dashboard')}'>Back to Dashboard</a></p>"
    )

    return "".join(html_parts)

@app.route("/download_backup/<date_folder>/<path:filename>", methods=["GET"])
def download_specific_backup(date_folder, filename):
    folder_path = os.path.join(BACKUP_ROOT, date_folder)
    return send_from_directory(folder_path, filename, as_attachment=True)

###############################################################################
# MAIN
###############################################################################
if __name__ == "__main__":
    load_appdata()
    init_schedule_jobs()
    app.run(debug=True, host="0.0.0.0", port=5000)
