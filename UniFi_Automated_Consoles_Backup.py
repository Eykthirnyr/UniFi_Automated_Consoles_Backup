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
from datetime import datetime
from flask import Flask, request, redirect, url_for, render_template_string, flash
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

CONSECUTIVE_CHECK_FAILS = 0  # for cookie expiration

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
                # user sets intervals in hours
                "backup_interval_hours": 24,
                "check_interval_hours": 4
            }
        }
        save_appdata()
    else:
        with open(APPDATA_JSON, "r", encoding="utf-8") as f:
            appdata = json.load(f)
        if "master_logged_in" not in appdata:
            appdata["master_logged_in"] = False
        if "consoles" not in appdata:
            appdata["consoles"] = []
        if "logs" not in appdata:
            appdata["logs"] = []
        if "schedule" not in appdata:
            appdata["schedule"] = {
                "backup_interval_hours": 24,
                "check_interval_hours": 4
            }
        save_appdata()

def save_appdata():
    with open(APPDATA_JSON, "w", encoding="utf-8") as f:
        json.dump(appdata, f, indent=2)

def add_log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    appdata["logs"].append({"timestamp": now, "message": msg})
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
        add_log("Opened https://unifi.ui.com for manual login. User must do credentials, MFA, trust device...")

        success = False
        for _ in range(120):
            time.sleep(1)
            url_ = driver.current_url.lower()
            if "unifi.ui.com" in url_ and "/login" not in url_ and "/mfa" not in url_:
                success = True
                break

        if success:
            save_cookies(driver)
            appdata["master_logged_in"] = True
            add_log("Manual login success, ephemeral browser closed.")
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

        add_log(f"Navigating to {console['backup_url']} for backup.")
        driver.get(console["backup_url"])
        time.sleep(5)

        curr_url = driver.current_url.lower()
        if "/login" in curr_url or "/mfa" in curr_url:
            console["last_backup_status"] = "Fail: cookies invalid => forced login page"
            add_log(f"Backup fail for '{console['name']}': forced login => cookies expired?")
            return

        # Click main "Download"
        main_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@name='backupDownload']"))
        )
        main_btn.click()
        time.sleep(3)

        # Click second "Download" in the popup
        second_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@name='backupDownload' and contains(@class, 'css-network-qhqpn7')]"))
        )
        second_btn.click()

        # Wait for file to appear in DOWNLOAD_DIR (not .crdownload)
        found_file = None
        for _ in range(60):
            possible = [f for f in os.listdir(DOWNLOAD_DIR)
                        if (f.endswith(".unf") or f.endswith(".tar.gz")) and not f.endswith(".crdownload")]
            if possible:
                possible.sort(key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)), reverse=True)
                found_file = possible[0]
                break
            time.sleep(1)

        if not found_file:
            console["last_backup_status"] = "Fail: no .unf or .tar.gz found after 60s"
            add_log("No newly downloaded .unf or .tar.gz file found in the chrome_downloads folder.")
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
            add_log(f"Backup success for '{console['name']}' => renamed => {new_name}")

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
    global CONSECUTIVE_CHECK_FAILS
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
        time.sleep(3)
        load_cookies(driver)
        time.sleep(3)

        curr_url = driver.current_url.lower()
        if "/login" in curr_url or "/mfa" in curr_url:
            CONSECUTIVE_CHECK_FAILS += 1
            add_log(f"Connectivity check => forced login => fails={CONSECUTIVE_CHECK_FAILS}")
        else:
            CONSECUTIVE_CHECK_FAILS = 0
            add_log("Connectivity check => success => still logged in.")
    except Exception as e:
        CONSECUTIVE_CHECK_FAILS += 1
        add_log(f"Connectivity check error => {e} => fails={CONSECUTIVE_CHECK_FAILS}")
    finally:
        driver.quit()
        end_task()

    if CONSECUTIVE_CHECK_FAILS >= 2:
        appdata["master_logged_in"] = False
        add_log("Cookies Expired => fails >= 2 => set not logged in.")
        save_appdata()

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
# INIT SCHEDULE JOBS (now that the scheduled_* functions are defined)
###############################################################################
def init_schedule_jobs():
    if scheduler.get_job("BackupJob"):
        scheduler.remove_job("BackupJob")
    if scheduler.get_job("ConnectivityCheckJob"):
        scheduler.remove_job("ConnectivityCheckJob")

    b_int = appdata["schedule"]["backup_interval_hours"]
    c_int = appdata["schedule"]["check_interval_hours"]

    # If user sets 24 => daily at 0. If user sets 6 => "*/6"
    # We'll interpret "*/N" if N < 24, else once a day at midnight.
    if b_int < 24:
        backup_expr = f"*/{b_int}"
    else:
        backup_expr = "0"  # run once a day at midnight

    if c_int < 24:
        check_expr = f"*/{c_int}"
    else:
        check_expr = "0"

    scheduler.add_job(
        id="BackupJob",
        func=scheduled_backup_job,
        trigger="cron",
        hour=backup_expr
    )

    scheduler.add_job(
        id="ConnectivityCheckJob",
        func=scheduled_connectivity_check,
        trigger="cron",
        hour=check_expr
    )

###############################################################################
# LOAD & INIT SCHEDULE
###############################################################################
load_appdata()
init_schedule_jobs()

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
  <title>UniFi Automated Consoles Backup</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    body { font-family: sans-serif; margin: 20px; max-width: 900px; margin: 20px auto; }
    .section { margin-bottom: 20px; padding: 10px; border: 1px solid #ccc; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 6px; vertical-align: top; }
    .log-list { max-height: 200px; overflow-y: auto; background: #fafafa; padding: 5px; }

    /* For very long Backup URLs */
    .console-url {
      max-width: 300px;
      word-wrap: break-word;
      white-space: pre-wrap;
    }

    .form-inline label {
      display: inline-block;
      width: 120px;
      text-align: right;
      margin-right: 8px;
    }
    .form-inline input {
      width: 250px;
      margin-bottom: 5px;
    }

    .status-circle {
      width: 14px; 
      height: 14px; 
      border-radius: 7px; 
      display: inline-block;
      margin-left: 10px;
    }
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

  <div class="section">
    <h2>Login Status
      {% if appdata.master_logged_in %}
        {% if cookies_expired %}
          <span class="status-circle" style="background-color: yellow;" title="Cookies expired?"></span>
        {% else %}
          <span class="status-circle" style="background-color: green;" title="Logged in"></span>
        {% endif %}
      {% else %}
        <span class="status-circle" style="background-color: red;" title="Not logged in"></span>
      {% endif %}
    </h2>
    {% if appdata.master_logged_in %}
      {% if cookies_expired %}
        <p><strong>Cookies might be expired</strong> => please re-login.</p>
      {% else %}
        <p>Already logged in. If cookies expire, re-login below.</p>
      {% endif %}
    {% else %}
      <p>Not logged in. Please do a manual server-side login.</p>
    {% endif %}
    <form method="POST" action="{{ url_for('manual_relogin') }}">
      <button type="submit">Re-Login / Clear Old Cookies</button>
    </form>
  </div>

  <div class="section">
    <h2>Schedules</h2>
    <p>Backup Interval (Hours): {{ appdata.schedule.backup_interval_hours }}</p>
    <p>Connectivity Check Interval (Hours): {{ appdata.schedule.check_interval_hours }}</p>
    <form method="POST" action="{{ url_for('update_schedule') }}">
      <label>Backup Interval (hrs):</label>
      <input type="number" name="backup_interval_hours" min="1" max="168" value="{{ appdata.schedule.backup_interval_hours }}" />
      <br/><br/>
      <label>Check Interval (hrs):</label>
      <input type="number" name="check_interval_hours" min="1" max="168" value="{{ appdata.schedule.check_interval_hours }}" />
      <br/><br/>
      <button type="submit">Update Schedule</button>
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
        </td>
      </tr>
      {% endfor %}
    </table>
    <h3>Add Console</h3>
    <form method="POST" action="{{ url_for('add_console') }}" class="form-inline">
      <p>
        <label>Name:</label>
        <input type="text" name="name" required />
      </p>
      <p>
        <label>Backup URL:</label>
        <input type="text" name="backup_url" required />
      </p>
      <button type="submit">Add Console</button>
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

@app.route("/")
def dashboard():
    global CONSECUTIVE_CHECK_FAILS
    cookies_exp = False
    if appdata["master_logged_in"] and CONSECUTIVE_CHECK_FAILS >= 2:
        cookies_exp = True

    # no direct next_run_time for "*/X" triggers, but let's see if it shows one anyway
    job = scheduler.get_job("BackupJob")
    next_backup_time = "Unknown"
    if job and job.next_run_time:
        next_backup_time = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")

    return render_template_string(
        HTML_TEMPLATE,
        appdata=appdata,
        current_task=current_task_status,
        cookies_expired=cookies_exp,
        next_backup_time=next_backup_time
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
    # We interpret user input for intervals in hours
    b_int = int(request.form.get("backup_interval_hours", "24"))
    c_int = int(request.form.get("check_interval_hours", "4"))

    appdata["schedule"]["backup_interval_hours"] = b_int
    appdata["schedule"]["check_interval_hours"] = c_int
    save_appdata()

    init_schedule_jobs()
    flash("Schedule updated. Next backups/connectivity checks will use these intervals.", "success")
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    load_appdata()
    init_schedule_jobs()
    app.run(debug=True, host="0.0.0.0", port=5000)
