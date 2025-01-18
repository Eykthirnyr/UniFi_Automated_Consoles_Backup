# UniFi Automated Consoles Backup

## Overview

`UniFi Automated Consoles Backup` is a self-contained Python application designed to automate the backup process for UniFi consoles. The application uses a combination of Flask (for a web GUI) and Selenium (for web interaction) to handle login, backup retrieval, and storage. This is achieved with a single Python file, making deployment and maintenance simple.

The app is designed to run on **Windows** and requires **Google Chrome** as the browser for automation tasks.

---

### Conceptual Synopsis

The motivation behind this script is to automate backups for multiple UniFi sites, addressing a key limitation of relying solely on UniFi's cloud saves. For large-scale deployments, especially in scenarios where significant changes are made daily, traditional cloud saves can lead to substantial data loss if a rollback is required. Operators may forget to make a manual save before implementing changes in production, potentially risking the loss of hours or even a full day's work. This script ensures backups are taken as frequently as every hour, minimizing the risk of losing critical data during such rollbacks.

### Challenges and Limitations

Ubiquiti currently does not provide an API to directly access or automate backups, which significantly complicates the development of such a tool. As a workaround, this script controls a Chrome browser to simulate user actions like logging in and navigating to the backup page. While functional, this approach has several drawbacks:
- **Fragility:** Any changes to the UniFi HTML structure can break the automation and require updates to the script.
- **Security Concerns:** Credentials are never stored, but cookies are saved locally for subsequent automated logins. This is still not an ideal security measure. By all means, you should restrict file access as much as possible to this.
- **System-Specific Operations:** The initial login to extract cookies must be performed directly on the machine or virtual machine hosting the script. This limitation ensures cookies are correctly tied to the environment running the automation.

### Design Decisions

To enhance accessibility and usability, the interface is designed as a lightweight web GUI:
1. **Remote Control:** The web GUI allows users to manage the script from a browser, eliminating the need to install the script on every machine or device. It can even be accessed from smartphones.
2. **Centralized Monitoring:** Multiple users can simultaneously check the backup status, view logs, and configure settings without client-side installations.
3. **Automation and Safeguards:** The script is designed to handle common user input errors, avoid conflicting operations, and maintain robust scheduling for tasks. 

While not perfect due to the reliance on browser automation, this tool provides a practical solution for UniFi backup automation given the current limitations of Ubiquiti's platform.


## Features

1. **Automated Console Backups**
   - Schedule backups for multiple UniFi consoles at user-defined intervals.
   - Save backups in a structured folder hierarchy (`YYYY-MM-DD/<ConsoleName>_backupName.unf`).

2. **Web GUI**
   - Accessible interface for adding consoles, configuring schedules, and managing backups.
   - Real-time logs and status updates.

3. **Resilient Login System**
   - Manual login handled in a controlled Chrome instance.
   - Cookies are saved to maintain authenticated sessions.
   - Automatic detection of expired cookies and user prompts for re-login.

4. **Error Prevention**
   - Validates user input for console names and backup URLs.
   - Prevents overlapping or conflicting tasks to ensure reliability.

---

## Requirements

### System Requirements
- **Operating System**: Windows 10 or later.
- **Python**: Version 3.9 or newer.
- **Google Chrome**: Latest stable version.

### Python Dependencies
The following Python packages are required:
- Flask
- Flask-APScheduler
- Requests
- Selenium
- WebDriver-Manager

The script will automatically check for and install missing dependencies upon execution.

---

## Using the Application

### Adding a Console
1. Navigate to the **Add Console** section in the web GUI.
2. Provide:
   - **Console Name**: A unique identifier for the console (e.g., `MainOfficeConsole`).
   - **Backup URL**: The direct URL to the console's backup page (e.g., `https://unifi.ui.com/consoles/<ID>/network/default/settings/system/backups`).
3. Click **Add Console** to save.

### Scheduling Backups
1. In the **Schedules** section:
   - Set the **Backup Interval (hours)** to define how often backups are retrieved.
   - Set the **Connectivity Check Interval (hours)** to determine how frequently the connection status is verified.
2. Click **Update Schedule** to save changes.

### Managing Backups
- **Initiate a Backup**: Click **Backup Now** for a console in the list.
- **View Logs**: Scroll through the **Logs** section to monitor activity.
- **Retrieve Backups**:
  - Backups are stored in `unifi_app/backups/YYYY-MM-DD/`.
  - Each file is prefixed with the console name.

---

## Safeguards and Error Handling

### Input Validation
- **Console Name**: Ensures uniqueness and non-empty input.
- **Backup URL**: Checks for valid URLs.

### Task Management
- Prevents overlapping tasks by disallowing concurrent executions.
- Skips tasks if invalid or expired cookies are detected.

### Recovery from Errors
- Logs detailed error messages for troubleshooting.
- Prompts for re-login if cookies expire.

---

## Security and Limitations

### Known Limitations
1. **Session Cookies**:
   - Authentication relies on cookies saved during manual login.
   - Expired cookies require user intervention to re-login.

2. **Network and Firewall**:
   - Ensure the local machine can access UniFi consoles.
   - Configure firewalls to allow outbound connections to UniFi's web services.

3. **Browser Control**:
   - Google Chrome is controlled via Selenium for automation tasks. Ensure Chrome is installed and accessible.

### Recommendations
- **Secure Network Configuration**:
  - Restrict access to sensitive network locations.
  - Use firewalls to control outbound/inbound traffic.

- **Local Machine Security**:
  - Keep the host system updated.
  - Run the application in a secure environment.

- **Error Handling**:
  - Regularly monitor logs for issues.
  - Test connectivity after changes to the UniFi environment.

---

## How It Works

### Self-Contained Design
- The application is entirely contained in a single Python script.
- Dependencies are installed automatically upon execution.
- No additional setup or external services are required.

### Backup Workflow
1. **Login**:
   - Manual login occurs in a controlled Chrome instance.
   - Cookies are saved for session persistence.

2. **Backup Retrieval**:
   - Selenium navigates to the backup URL.
   - Files are downloaded and renamed with a `<ConsoleName>_` prefix.
   - Backups are stored in a date-based folder hierarchy.

3. **Scheduled Jobs**:
   - Flask-APScheduler runs periodic tasks for backups and connectivity checks.

---

## Disclaimer
This software is provided "as is" without any warranty. Use it at your own risk. The developer is not responsible for data loss, system damage, or any other issues resulting from its use.

For more information, visit [Clément GHANEME's Website](https://clement.business/).
