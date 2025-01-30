#!/usr/bin/env python3
import sys
import subprocess

REQUIRED_PACKAGES = [
    "flask",
    "flask_apscheduler",
    "requests",
    "selenium",
    "webdriver_manager",
    "psutil",
    "zoneinfo"
]

def check_and_install_dependencies():
    for pkg in REQUIRED_PACKAGES:
        if pkg == "zoneinfo":
            continue
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
import queue
import zipfile
import io
import requests
import psutil

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import deque

from flask import (
    Flask, request, redirect, url_for,
    render_template_string, flash,
    send_from_directory, send_file,
    Response
)
from flask_apscheduler import APScheduler

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

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

DEFAULT_TZ = "UTC"
AVAILABLE_TIMEZONES = [
    "UTC",
    "Europe/Paris",
    "Europe/Berlin",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Tokyo",
    "Australia/Sydney"
]

appdata = {}

###############################################################################
# TIME & TIMEZONE
###############################################################################
def get_user_timezone() -> ZoneInfo:
    tz_name = appdata.get("tz_choice", DEFAULT_TZ)
    try:
        return ZoneInfo(tz_name)
    except:
        return ZoneInfo("UTC")

def localize_utc_str_to_user_tz(utc_str: str) -> str:
    try:
        dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S")
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone(get_user_timezone())
        return dt_local.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return utc_str

###############################################################################
# ROLLING CONSOLE LOG
###############################################################################
console_log_buffer = deque(maxlen=2000)

def log_console(message: str):
    tstamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{tstamp_utc}] {message}"
    print(line)
    console_log_buffer.append(line)

###############################################################################
# FLASK + SCHEDULER
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
            },
            "tz_choice": DEFAULT_TZ
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
        if "tz_choice" not in appdata:
            appdata["tz_choice"] = DEFAULT_TZ
        save_appdata()

def save_appdata():
    with open(APPDATA_JSON, "w", encoding="utf-8") as f:
        json.dump(appdata, f, indent=2)

def add_app_log(msg):
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    entry = {"timestamp": now_utc_str, "message": msg}
    appdata["logs"].append(entry)
    appdata["logs"] = appdata["logs"][-100:]
    save_appdata()

###############################################################################
# TASK QUEUE & STATUS
###############################################################################
task_queue = queue.Queue()

current_task_status = {
    "running": False,
    "step": "",
    "start_time": None
}

def is_task_running():
    return current_task_status["running"]

def start_task(step_msg):
    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    current_task_status["running"] = True
    current_task_status["step"] = step_msg
    current_task_status["start_time"] = now_utc_str

def end_task():
    current_task_status["running"] = False
    current_task_status["step"] = ""
    current_task_status["start_time"] = None

###############################################################################
# CLEANUP leftover Chrome
###############################################################################
def kill_leftover_chrome_processes():
    log_console("[Cleanup] Checking leftover Chrome/ChromeDriver processes...")
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline_str = " ".join(proc.cmdline()).lower()
            name_str = (proc.name() or "").lower()
            if ("chrome" in name_str or "chromedriver" in name_str) or \
               ("chrome" in cmdline_str or "chromedriver" in cmdline_str):
                log_console(f"[Cleanup] Killing leftover process PID={proc.pid} ({name_str}).")
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

def cleanup_leftover_chrome():
    if is_task_running():
        return
    if not task_queue.empty():
        return
    kill_leftover_chrome_processes()

###############################################################################
# WORKER LOOP
###############################################################################
def worker_loop():
    while True:
        task_name, func, args, kwargs = task_queue.get()
        start_task(task_name)
        add_app_log(f"Worker: Starting task '{task_name}'")
        log_console(f"[Worker] Starting task '{task_name}'")

        try:
            func(*args, **kwargs)
        except Exception as e:
            add_app_log(f"Task '{task_name}' => ERROR: {e}")
            log_console(f"[Worker] Task '{task_name}' => EXCEPTION: {e}")

        end_task()
        task_queue.task_done()
        cleanup_leftover_chrome()

worker_thread = threading.Thread(target=worker_loop, daemon=True)
worker_thread.start()

###############################################################################
# SELENIUM
###############################################################################
def get_selenium_driver():
    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--start-maximized")

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
        add_app_log("Removed old cookies.json manually.")
        log_console("Removed old cookies.json manually.")

def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_JSON, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)
    add_app_log("Cookies saved to cookies.json")
    log_console("Cookies saved to cookies.json")

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
        add_app_log("Cookies loaded from cookies.json")
        log_console("Cookies loaded from cookies.json")

###############################################################################
# MANUAL LOGIN
###############################################################################
def manual_login_browser_logic():
    log_console("Starting manual_login_browser_logic() ...")
    driver = get_selenium_driver()
    try:
        driver.get("https://unifi.ui.com/")
        add_app_log("Opened unifi.ui.com for manual login (2 min).")

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
            add_app_log("Manual login success => master_logged_in=True.")
            log_console("Manual login => success => cookies saved.")
        else:
            add_app_log("Manual login => timed out => user never left /login or /mfa.")
            log_console("Manual login => timed out => still on /login or /mfa.")
    finally:
        driver.quit()
        save_appdata()

###############################################################################
# ATTEMPT SINGLE BACKUP WITH REAL DOWNLOAD
###############################################################################
def attempt_console_backup(console):
    """
    Attempt to download a real backup for this console, store the real file.
    """
    name = console["name"]
    driver = get_selenium_driver()
    try:
        driver.get("https://unifi.ui.com/")
        time.sleep(2)
        load_cookies(driver)
        time.sleep(2)

        if not appdata.get("master_logged_in", False):
            console["last_backup_status"] = "Fail: Not logged in"
            add_app_log(f"Backup => '{name}' => Not logged in => fail.")
            return False

        driver.get(console["backup_url"])
        time.sleep(5)

        # Check if forced login
        curr_url = driver.current_url.lower()
        if "/login" in curr_url or "/mfa" in curr_url:
            console["last_backup_status"] = "Fail: forced login => cookies invalid"
            add_app_log(f"Backup => '{name}' => forced login => set master_logged_in=False")
            appdata["master_logged_in"] = False
            save_appdata()
            kill_leftover_chrome_processes()
            return False

        # Click main "Download" button
        main_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, "//button[@name='backupDownload']"))
        )
        main_btn.click()
        time.sleep(3)

        # Click second "Download"
        second_btn = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[@name='backupDownload' and contains(@class, 'css-network-qhqpn7')]"
            ))
        )
        second_btn.click()

        # Wait up to 60s for .unf or .tar.gz
        found_file = None
        for _ in range(60):
            possible = [
                f for f in os.listdir(DOWNLOAD_DIR)
                if (f.endswith(".unf") or f.endswith(".tar.gz")) and not f.endswith(".crdownload")
            ]
            if possible:
                possible.sort(key=lambda x: os.path.getmtime(os.path.join(DOWNLOAD_DIR, x)), reverse=True)
                found_file = possible[0]
                break
            time.sleep(1)

        if not found_file:
            console["last_backup_status"] = "Fail: no .unf or .tar.gz after 60s"
            add_app_log(f"Backup => '{name}' => no .unf/.tar.gz => fail.")
            return False

        # Move file to BACKUP_ROOT/<UTC-date> folder
        utc_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        folder_path = os.path.join(BACKUP_ROOT, utc_date_str)
        os.makedirs(folder_path, exist_ok=True)

        oldpath = os.path.join(DOWNLOAD_DIR, found_file)
        new_name = f"{name}_{found_file}"
        newpath = os.path.join(folder_path, new_name)
        os.rename(oldpath, newpath)

        console["last_backup_status"] = "Success"
        console["last_backup_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        add_app_log(f"Backup => '{name}' => success => {new_name}")
        return True

    except Exception as e:
        console["last_backup_status"] = f"Fail: {e}"
        add_app_log(f"Backup => '{name}' => exception => {e}")
        kill_leftover_chrome_processes()
        return False
    finally:
        driver.quit()
        save_appdata()

###############################################################################
# SCHEDULED CONNECTIVITY CHECK
###############################################################################
def scheduled_connectivity_check_logic():
    log_console("scheduled_connectivity_check_logic => start")
    if not appdata.get("master_logged_in", False):
        add_app_log("Connectivity check => not logged in => skip.")
        return

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
            add_app_log("Connectivity check => forced login => set not logged in.")
            appdata["master_logged_in"] = False
            save_appdata()
        else:
            add_app_log("Connectivity check => success => still logged in.")
    finally:
        driver.quit()

###############################################################################
# SCHEDULED BACKUP => pass1 -> pass2 -> pass3
###############################################################################
def scheduled_backup_job_logic():
    if not appdata.get("master_logged_in", False):
        add_app_log("Scheduled backup => canceled => not logged in.")
        return

    add_app_log("Scheduled backup => pass#1 for all consoles.")
    pass1_fail = []
    all_cons = appdata["consoles"]
    for c in all_cons:
        current_task_status["step"] = f"ScheduledBackup => Pass1 => {c['name']}"
        ok = attempt_console_backup(c)
        if not ok:
            pass1_fail.append(c["id"])

    if pass1_fail:
        current_task_status["step"] = "ScheduledBackup => Wait10s => pass2"
        time.sleep(10)
        pass2_fail = []
        for cid in pass1_fail:
            c = next((x for x in all_cons if x["id"] == cid), None)
            if not c:
                continue
            current_task_status["step"] = f"ScheduledBackup => Pass2 => {c['name']}"
            ok2 = attempt_console_backup(c)
            if not ok2:
                pass2_fail.append(c["id"])
            else:
                c["last_backup_status"] = "Succeeded after retry"
                add_app_log(f"{c['name']} => pass2 => succeeded after retry")

        if pass2_fail:
            current_task_status["step"] = "ScheduledBackup => Wait10s => pass3"
            time.sleep(10)
            pass3_fail = []
            for cid in pass2_fail:
                c = next((x for x in all_cons if x["id"] == cid), None)
                if not c:
                    continue
                current_task_status["step"] = f"ScheduledBackup => Pass3 => {c['name']}"
                ok3 = attempt_console_backup(c)
                if not ok3:
                    pass3_fail.append(c["id"])
                else:
                    c["last_backup_status"] = "Succeeded after retry"
                    add_app_log(f"{c['name']} => pass3 => succeeded after retry")

            if pass3_fail:
                for cid in pass3_fail:
                    c = next((x for x in all_cons if x["id"] == cid), None)
                    if c:
                        c["last_backup_status"] = "Failed after 3 tries"
                        add_app_log(f"{c['name']} => failed after 3 tries.")
        else:
            log_console("No fails remain after pass2 => skipping pass3.")
    else:
        log_console("No fails => skipping pass2/pass3.")

    add_app_log("Scheduled backup => complete => all passes done.")
    current_task_status["step"] = "ScheduledBackup => Done"
    save_appdata()

###############################################################################
# APSCHEDULER JOBS
###############################################################################
def scheduled_connectivity_check_job():
    log_console("APScheduler => scheduled_connectivity_check_job triggered")
    task_queue.put(("ConnectivityCheck", scheduled_connectivity_check_logic, [], {}))

def scheduled_backup_job():
    log_console("APScheduler => scheduled_backup_job triggered")

    if is_task_running() and current_task_status["step"].startswith("ScheduledBackup =>"):
        add_app_log("Conflict: a scheduled backup is already running => skip new one.")
        return
    for item in list(task_queue.queue):
        if item[0].startswith("ScheduledBackup =>"):
            add_app_log("Conflict: a scheduled backup is queued => skip new one.")
            return

    task_queue.put(("ScheduledBackup => Pass1 => allConsoles", scheduled_backup_job_logic, [], {}))

###############################################################################
# INIT SCHEDULE JOBS
###############################################################################
def init_schedule_jobs():
    if scheduler.get_job("BackupJob"):
        scheduler.remove_job("BackupJob")
    if scheduler.get_job("ConnectivityCheckJob"):
        scheduler.remove_job("ConnectivityCheckJob")

    s = appdata["schedule"]

    if s["backup_enabled"]:
        b_val = s["backup_value"]
        b_unit = s["backup_unit"]
        if b_unit == "minutes":
            scheduler.add_job("BackupJob", scheduled_backup_job, trigger="interval", minutes=b_val)
        elif b_unit == "hours":
            scheduler.add_job("BackupJob", scheduled_backup_job, trigger="interval", hours=b_val)
        elif b_unit == "days":
            scheduler.add_job("BackupJob", scheduled_backup_job, trigger="interval", days=b_val)

    if s["check_enabled"]:
        c_val = s["check_value"]
        c_unit = s["check_unit"]
        if c_unit == "minutes":
            scheduler.add_job(
                "ConnectivityCheckJob", scheduled_connectivity_check_job,
                trigger="interval", minutes=c_val
            )
        elif c_unit == "hours":
            scheduler.add_job(
                "ConnectivityCheckJob", scheduled_connectivity_check_job,
                trigger="interval", hours=c_val
            )
        elif c_unit == "days":
            scheduler.add_job(
                "ConnectivityCheckJob", scheduled_connectivity_check_job,
                trigger="interval", days=c_val
            )

load_appdata()
init_schedule_jobs()

###############################################################################
# HELPER: Format Timedelta
###############################################################################
def format_timedelta(td):
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
# SSE
###############################################################################
@app.route("/status_stream")
def status_stream():
    def event_stream():
        while True:
            data = {}
            data["current_task"] = current_task_status.copy()
            data["queue_size"] = task_queue.qsize()
            data["master_logged_in"] = appdata.get("master_logged_in", False)

            # logs newest first
            logs_reversed = reversed(appdata["logs"])
            data_logs = []
            for entry in logs_reversed:
                local_ts = localize_utc_str_to_user_tz(entry["timestamp"])
                data_logs.append({"timestamp": local_ts, "message": entry["message"]})
            data["logs"] = data_logs

            # consoles
            data_consoles = []
            for c in appdata["consoles"]:
                local_time = ""
                if c.get("last_backup_time"):
                    local_time = localize_utc_str_to_user_tz(c["last_backup_time"])
                data_consoles.append({
                    "id": c["id"],
                    "name": c["name"],
                    "backup_url": c.get("backup_url", ""),
                    "status": c.get("last_backup_status", ""),
                    "time": local_time
                })
            data["consoles"] = data_consoles

            # next backup
            backup_job = scheduler.get_job("BackupJob")
            next_backup_str = "N/A"
            next_backup_seconds = 0
            if backup_job and backup_job.next_run_time:
                now_ = datetime.now(backup_job.next_run_time.tzinfo)
                delta = backup_job.next_run_time - now_
                sec_ = int(delta.total_seconds())
                if sec_ < 0:
                    sec_ = 0
                next_backup_seconds = sec_
                next_backup_str = format_timedelta(delta)

            data["next_backup_time_str"] = next_backup_str
            data["next_backup_time_seconds"] = next_backup_seconds

            yield "event: message\n" + "data: " + json.dumps(data) + "\n\n"
            time.sleep(1)

    return Response(event_stream(), mimetype="text/event-stream")

###############################################################################
# HTML Template
###############################################################################
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
  <title>UniFi Automated Consoles Backup</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    body {
      font-family: sans-serif;
      margin: 20px;
      max-width: 1100px;
      margin: 20px auto;
    }
    .section {
      margin-bottom: 20px;
      padding: 10px;
      border: 1px solid #ccc;
    }
    table {
      border-collapse: collapse;
      width: 100%;
    }
    th, td {
      border: 1px solid #ddd;
      padding: 6px;
      vertical-align: top;
    }
    .log-list {
      max-height: 200px;
      overflow-y: auto;
      background: #fafafa;
      padding: 5px;
    }
    .console-url {
      max-width: 200px;
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
    label {
      font-weight: bold;
    }
    .actions-col {
      width: 300px; /* wide enough for 4 buttons side by side */
    }
    .status-col {
      width: 200px;
    }
    .flash-message {
      padding: 8px;
      margin: 5px 0;
      border: 1px solid #ccc;
      background: #fffae6;
    }
  </style>
  <script>
    let evtSource = null;

    function autoRemoveFlashMessages() {
      setTimeout(() => {
        const flashEls = document.querySelectorAll(".flash-message");
        flashEls.forEach(el => el.remove());
      }, 120000); // 2 min
    }

    function initSSE() {
      evtSource = new EventSource("/status_stream");
      evtSource.onmessage = function(e) {
        if (!e.data) return;
        const data = JSON.parse(e.data);
        updateUI(data);
      };
    }

    function updateUI(data) {
      const running = data.current_task.running;
      const step = data.current_task.step;
      const startTime = data.current_task.start_time || "";
      const queueSize = data.queue_size;

      const taskSection = document.getElementById("task-section");
      let taskHtml = "";
      if (running) {
        taskHtml += `<p>Task Running: <strong>${step}</strong></p>`;
        taskHtml += `<p>Started: ${startTime}</p>`;
      } else if (queueSize > 0) {
        taskHtml += `<p>Task(s) queued, waiting for worker...</p>`;
      } else {
        taskHtml += `<p>No task is running or queued.</p>`;
      }
      taskSection.innerHTML = taskHtml;

      const nextStr = data.next_backup_time_str || "N/A";
      document.getElementById("time-remaining").textContent = nextStr;

      // logs (newest first)
      const logsUl = document.getElementById("logs-ul");
      logsUl.innerHTML = "";
      const logsList = data.logs || [];
      logsList.forEach(entry => {
        const li = document.createElement("li");
        li.textContent = `[${entry.timestamp}] - ${entry.message}`;
        logsUl.appendChild(li);
      });

      // login
      const loginStatus = data.master_logged_in;
      const loginSpan = document.getElementById("login-status-circle");
      const loginTxt = document.getElementById("login-status-text");
      if (loginStatus) {
        loginSpan.style.backgroundColor = "green";
        loginTxt.textContent = "Cookies are valid. If they expire, re-login below.";
      } else {
        loginSpan.style.backgroundColor = "red";
        loginTxt.textContent = "Not logged in. Please do a manual server-side login.";
      }

      const consolesTbody = document.getElementById("consoles-tbody");
      consolesTbody.innerHTML = "";
      const consoles = data.consoles || [];
      consoles.forEach(c => {
        const row = document.createElement("tr");

        const tdName = document.createElement("td");
        tdName.textContent = c.name;
        row.appendChild(tdName);

        const tdUrl = document.createElement("td");
        tdUrl.className = "console-url";
        tdUrl.textContent = c.backup_url || "";
        row.appendChild(tdUrl);

        const tdStatus = document.createElement("td");
        tdStatus.className = "status-col";
        tdStatus.textContent = c.status || "None";
        row.appendChild(tdStatus);

        const tdTime = document.createElement("td");
        tdTime.textContent = c.time || "Never";
        row.appendChild(tdTime);

        const tdActions = document.createElement("td");
        tdActions.className = "actions-col";
        tdActions.innerHTML = `
          <form method="POST" action="/manual_backup/${c.id}" style="display:inline;">
            <button type="submit">Backup Now</button>
          </form>
          <form method="POST" action="/remove_console/${c.id}" style="display:inline;">
            <button type="submit">Remove</button>
          </form>
          <form method="GET" action="/download_latest_backup/${c.id}" style="display:inline;">
            <button type="submit">Download Latest</button>
          </form>
          <form method="GET" action="/console_history/${c.id}" style="display:inline;">
            <button type="submit">View History</button>
          </form>
        `;
        row.appendChild(tdActions);

        consolesTbody.appendChild(row);
      });
    }

    window.onload = function() {
      initSSE();
      autoRemoveFlashMessages();
    }
  </script>
</head>
<body>
  <h1>UniFi Automated Consoles Backup</h1>

  <div>
    {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for category, message in messages %}
        <div class="flash-message">
          <strong>{{ category }}:</strong> {{ message }}
        </div>
      {% endfor %}
    {% endif %}
    {% endwith %}
  </div>

  <div class="section" id="task-section">
    <!-- SSE updated -->
  </div>

  <!-- "Start Schedule Now" button -->
  <div class="section">
    <h2>Manual Start of Scheduled Backup</h2>
    <p>If you want to override the timer and run a scheduled backup right now:</p>
    <form method="POST" action="{{ url_for('start_schedule_now') }}">
      <button type="submit">Start Schedule Now</button>
    </form>
  </div>

  <div class="section">
    <h2>Next Backup Task</h2>
    <p>
      <strong>Time Remaining:</strong>
      <span id="time-remaining">N/A</span>
    </p>
  </div>

  <div class="section">
    <h2>Login Status
      <span
        id="login-status-circle"
        class="status-circle"
        style="background-color: red;"
        title="Not logged in"
      ></span>
    </h2>
    <p id="login-status-text">
      Not logged in. Please do a manual server-side login.
    </p>
    <form method="POST" action="{{ url_for('manual_relogin') }}">
      <button type="submit">Re-Login / Clear Old Cookies</button>
    </form>
  </div>

  <div class="section">
    <h2>Schedules & Time Zone</h2>
    <form method="POST" action="{{ url_for('update_schedule') }}">
      <div style="display: flex; gap: 40px;">
        <div>
          <label>Enable Backup</label>
          <input type="checkbox" name="backup_enabled" value="1"
            {% if appdata.schedule.backup_enabled %}checked{% endif %} />
          <br/><br/>
          <label>Backup Interval:</label><br/>
          <input type="number" name="backup_value" min="1"
            value="{{ appdata.schedule.backup_value }}"
            style="width: 80px;" />
          <select name="backup_unit">
            <option value="minutes"
              {% if appdata.schedule.backup_unit == 'minutes' %}selected{% endif %}>Minutes
            </option>
            <option value="hours"
              {% if appdata.schedule.backup_unit == 'hours' %}selected{% endif %}>Hours
            </option>
            <option value="days"
              {% if appdata.schedule.backup_unit == 'days' %}selected{% endif %}>Days
            </option>
          </select>
        </div>
        <div>
          <label>Enable Connectivity Check</label>
          <input type="checkbox" name="check_enabled" value="1"
            {% if appdata.schedule.check_enabled %}checked{% endif %} />
          <br/><br/>
          <label>Check Interval:</label><br/>
          <input type="number" name="check_value" min="1"
            value="{{ appdata.schedule.check_value }}"
            style="width: 80px;" />
          <select name="check_unit">
            <option value="minutes"
              {% if appdata.schedule.check_unit == 'minutes' %}selected{% endif %}>Minutes
            </option>
            <option value="hours"
              {% if appdata.schedule.check_unit == 'hours' %}selected{% endif %}>Hours
            </option>
            <option value="days"
              {% if appdata.schedule.check_unit == 'days' %}selected{% endif %}>Days
            </option>
          </select>
        </div>
        <div>
          <label>Time Zone:</label><br/>
          <select name="tz_choice">
            {% for tz in available_tzs %}
              <option value="{{ tz }}"
                {% if appdata.tz_choice == tz %}selected{% endif %}>{{ tz }}</option>
            {% endfor %}
          </select>
        </div>
      </div>
      <br/>
      <button type="submit">Update Schedules & Timezone</button>
    </form>
  </div>

  <div class="section">
    <h2>Consoles</h2>
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Backup URL</th>
          <th class="status-col">Last Backup Status</th>
          <th>Last Backup Time</th>
          <th class="actions-col">Actions</th>
        </tr>
      </thead>
      <tbody id="consoles-tbody">
      </tbody>
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
      <ul id="logs-ul">
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
    return render_template_string(
        HTML_TEMPLATE,
        appdata=appdata,
        available_tzs=AVAILABLE_TIMEZONES
    )

@app.route("/manual_relogin", methods=["POST"])
def manual_relogin():
    remove_old_cookie()
    appdata["master_logged_in"] = False
    save_appdata()
    task_queue.put(("ManualRelogin", manual_login_browser_logic, [], {}))
    flash("Forcing re-login. Old cookie removed. A Chrome window will open on the server side.", "info")
    return redirect(url_for("dashboard"))

@app.route("/start_schedule_now", methods=["POST"])
def start_schedule_now():
    # concurrency check
    if is_task_running() and current_task_status["step"].startswith("ScheduledBackup =>"):
        flash("Conflict: a scheduled backup is already running => skip new one.", "danger")
        return redirect(url_for("dashboard"))
    for item in list(task_queue.queue):
        if item[0].startswith("ScheduledBackup =>"):
            flash("Conflict: a scheduled backup is queued => skip new one.", "danger")
            return redirect(url_for("dashboard"))

    task_queue.put(("ScheduledBackup => Pass1 => allConsoles", scheduled_backup_job_logic, [], {}))
    flash("Scheduled backup started now (manual override).", "success")
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
        flash("Not logged in. Please do manual login first.", "danger")
        return redirect(url_for("dashboard"))

    console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
    if not console:
        flash("Console not found.", "danger")
        return redirect(url_for("dashboard"))

    task_queue.put((f"ManualBackup-{console['name']}", attempt_console_backup, [console], {}))
    flash(f"Backup for '{console['name']}' queued...", "info")
    return redirect(url_for("dashboard"))

@app.route("/update_schedule", methods=["POST"])
def update_schedule():
    s = appdata["schedule"]

    s["backup_enabled"] = ("backup_enabled" in request.form)
    s["backup_value"] = int(request.form.get("backup_value", "1"))
    s["backup_unit"] = request.form.get("backup_unit", "days")

    s["check_enabled"] = ("check_enabled" in request.form)
    s["check_value"] = int(request.form.get("check_value", "4"))
    s["check_unit"] = request.form.get("check_unit", "hours")

    if s["backup_unit"] == "minutes" and s["backup_value"] < 15:
        s["backup_value"] = 15
        flash("Backup interval set to minimum of 15 minutes.", "warning")
    if s["check_unit"] == "minutes" and s["check_value"] < 15:
        s["check_value"] = 15
        flash("Check interval set to minimum of 15 minutes.", "warning")

    tzc = request.form.get("tz_choice", DEFAULT_TZ)
    if tzc not in AVAILABLE_TIMEZONES:
        tzc = DEFAULT_TZ
    appdata["tz_choice"] = tzc

    save_appdata()
    init_schedule_jobs()
    flash("Schedules & Timezone updated.", "success")
    return redirect(url_for("dashboard"))

@app.route("/download_latest_backup/<int:cid>")
def download_latest_backup(cid):
    console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
    if not console:
        flash("Console not found.", "danger")
        return redirect(url_for("dashboard"))

    if not console.get("last_backup_time"):
        flash("No backup found for that console.", "danger")
        return redirect(url_for("dashboard"))

    day_str_utc = console["last_backup_time"].split(" ")[0]
    folder_path = os.path.join(BACKUP_ROOT, day_str_utc)
    if not os.path.exists(folder_path):
        flash("No matching backup folder found.", "danger")
        return redirect(url_for("dashboard"))

    files = [f for f in os.listdir(folder_path) if f.startswith(console["name"] + "_")]
    if not files:
        flash("No matching backup file found for that console's last backup time.", "danger")
        return redirect(url_for("dashboard"))

    files.sort(key=lambda x: os.path.getmtime(os.path.join(folder_path, x)), reverse=True)
    latest_file = files[0]
    return send_from_directory(folder_path, latest_file, as_attachment=True)

@app.route("/download_today_backups", methods=["GET"])
def download_today_backups():
    today_str_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    folder_path = os.path.join(BACKUP_ROOT, today_str_utc)
    if not os.path.exists(folder_path):
        flash("No backups found for today (UTC).", "danger")
        return redirect(url_for("dashboard"))

    file_list = os.listdir(folder_path)
    if not file_list:
        flash("No backups found for today (UTC).", "danger")
        return redirect(url_for("dashboard"))

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w") as zf:
        for f in file_list:
            full_path = os.path.join(folder_path, f)
            if os.path.isfile(full_path):
                zf.write(full_path, arcname=f)

    mem_zip.seek(0)
    zip_filename = f"Backups_{today_str_utc}.zip"
    return send_file(mem_zip,
                     as_attachment=True,
                     download_name=zip_filename,
                     mimetype="application/zip")

@app.route("/console_history/<int:cid>")
def console_history(cid):
    console = next((x for x in appdata["consoles"] if x["id"] == cid), None)
    if not console:
        return f"<p>Console with ID {cid} not found.</p>"

    console_name = console["name"]
    today_utc = datetime.now(timezone.utc).date()
    earliest = today_utc - timedelta(days=30)

    files_list = []
    for day_offset in range(31):
        day_ = today_utc - timedelta(days=day_offset)
        day_str = day_.strftime("%Y-%m-%d")
        folder_path = os.path.join(BACKUP_ROOT, day_str)
        if not os.path.exists(folder_path):
            continue
        for fname in os.listdir(folder_path):
            if fname.startswith(console_name + "_"):
                fpath = os.path.join(folder_path, fname)
                if os.path.isfile(fpath):
                    mtime = os.path.getmtime(fpath)
                    dt_utc = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    files_list.append({
                        "date_folder": day_str,
                        "filename": fname,
                        "datetime_utc": dt_utc
                    })

    files_list.sort(key=lambda x: x["datetime_utc"], reverse=True)

    html_parts = [
        f"<h2>History for Console: {console_name}</h2>",
        "<p>Showing backups from last 30 days (UTC-based folder dates).</p>",
        "<table border='1' cellpadding='5' cellspacing='0'>",
        "<tr><th>Date Folder (UTC)</th><th>File</th><th>Backup Time (UTC)</th><th>Action</th></tr>"
    ]
    for item in files_list:
        link = url_for("download_specific_backup",
                       date_folder=item["date_folder"],
                       filename=item["filename"])
        html_parts.append(
            f"<tr>"
            f"<td>{item['date_folder']}</td>"
            f"<td>{item['filename']}</td>"
            f"<td>{item['datetime_utc']}</td>"
            f"<td><a href='{link}'>Download</a></td>"
            f"</tr>"
        )
    html_parts.append("</table>")

    if not files_list:
        html_parts.append("<p>No backups found in the last 30 days for this console.</p>")

    html_parts.append(f"<p><a href='{url_for('dashboard')}'>Back to Dashboard</a></p>")
    return "".join(html_parts)

@app.route("/download_backup/<date_folder>/<path:filename>")
def download_specific_backup(date_folder, filename):
    folder_path = os.path.join(BACKUP_ROOT, date_folder)
    return send_from_directory(folder_path, filename, as_attachment=True)

###############################################################################
# MAIN
###############################################################################
if __name__ == "__main__":
    load_appdata()
    init_schedule_jobs()
    log_console("Starting Flask with real file download logic, reversing logs, etc.")
    app.run(debug=True, host="0.0.0.0", port=5000)
