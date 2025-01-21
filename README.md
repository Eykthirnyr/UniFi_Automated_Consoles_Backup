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



# Getting Started

### 1. **Deploying the App**
1. Deploy the app on a VM within your company’s hypervisor (e.g., with IP `192.168.1.50`).
2. Ensure the VM has Python and Google Chrome installed.

### 2. **Running the Script**
1. Run the script:  
   ```bash
   python UniFi_Automated_Consoles_Backup.py
   ```
2. The script will automatically:
   - Install all required dependencies.
   - Create the `unifi_app` folder structure, including:
     - `backups/`: Stores organized backups.
     - `cookies.json`: Stores session data.
     - `logs/`: Logs of all app activities.

---

### 3. **Accessing the Web GUI**
1. On any device in your network (e.g., your laptop), open a web browser.
2. Navigate to the VM’s IP address on **port 5000**:  
   ```  
   http://192.168.1.50:5000  
   ```
3. This web GUI serves as the primary interface for managing the application.

---

### 4. **First-Time Login**
1. In the web GUI, click the **"Login"** button.
2. A Chrome browser window will open on the VM.
3. Complete the UniFi login process in the Chrome window:
   - Enter your **email** and **password**.
   - Complete **MFA** if prompted.
   - Trust the device if required.
4. Once logged in:
   - Cookies are saved to maintain the session.
   - The browser window will close automatically.
5. The GUI will now display a **connected status**.

---

### 5. **Adding Consoles**
1. Navigate to the **Add Console** section in the GUI.
2. Provide the following:
   - **Console Name**: A friendly identifier for the console (e.g., `MainOfficeConsole`).
   - **Backup URL**: The direct URL to the console’s backup page  
     (e.g., `https://unifi.ui.com/consoles/<ID>/network/default/settings/system/backups`).
3. Click **Add Console** to save the details.

---

### 6. **Configuring Schedules**
1. Go to the **Schedules** section in the GUI.
2. Set the intervals in hours:
   - **Backup Interval**: How often backups are taken.
   - **Connection Check Interval**: How often the app checks if the console is still connected.
3. Save your changes by clicking **Update Schedule**.

---

### 7. **Managing Backups**
1. **Start a Backup**:
   - Click **Backup Now** for a specific console in the list.
2. **View Logs**:
   - Monitor logs in the **Logs** section to check activity and troubleshoot.
3. **Access Backups**:
   - Backups are stored in `unifi_app/backups/YYYY-MM-DD/` on the VM.
   - Each backup file is prefixed with the console name for easy identification.

---

### 8. **Re-Login (If Needed)**
1. If the connection fails twice (e.g., expired cookies), the GUI will notify you to re-login.
2. To re-login:
   - Click the **Login** button in the GUI.
   - Complete the login process again in the Chrome window.
   - Upon success, new cookies will be saved, and the session will resume.

---

### 9. **Security Tips**
1. Restrict GUI access to trusted IPs using your firewall.
2. Ensure that **port 5000** is open for devices needing access to the GUI.
3. Isolate the VM in a secure VLAN or subnet.
4. Use a reverse proxy (e.g., NGINX) to enable HTTPS for the GUI.
5. Ensure `cookies.json` is stored securely, as it contains sensitive session data.


---

### Screenshots



![Dashboard](https://github.com/user-attachments/assets/6d1fc56c-f7a3-40cf-bb5c-dd0a8773c025)

![Sites List](https://github.com/user-attachments/assets/81abbdf3-9d1a-4a81-9b7e-d288abe12fd0)

![URL](https://github.com/user-attachments/assets/9b2c4448-198f-47e8-91ce-65bcf771812a)

![Folders Hierarchy](https://github.com/user-attachments/assets/fc453bd1-f454-451b-b618-bb1d766ac867)

# Changelog

## 21/05/2025

### Added
- Ability to enable or disable "Check Interval (hrs)" in the schedule settings.
- Support for setting "Backup Interval" and "Check Interval" in minutes, hours, or days (minimum 15 minutes enforced).
- A "Download Latest Backup" button for each console to download the most recent backup directly from the web interface.
- A "Download All Today's Backups (ZIP)" button to download all backups from the current day as a single ZIP file.
- A "View History" button for each console, displaying a timeline of backups made for that console (last 30 days) with the ability to download individual backups from the timeline.

### Fixed
- Resolved `TypeError: send_file() got an unexpected keyword argument 'attachment_filename'` in the "Download All Today's Backups (ZIP)" functionality by replacing `attachment_filename` with `download_name`.

### Updated
- Connectivity checks now verify access to `https://unifi.ui.com/` with the current cookies and update the login status if access is denied.


## Disclaimer
This software is provided "as is" without any warranty. Use it at your own risk. I'm not responsible for data loss, system damage, or any other issues resulting from its use.

Made by [Clément GHANEME](https://clement.business/) 01/2025.
