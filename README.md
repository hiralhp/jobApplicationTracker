# Job Application Tracker

A personal job application tracker with Gmail sync, built with Streamlit. Track companies, log applications, and automatically detect confirmation emails from your inbox.

---

## Getting Started

### 1. Check if Python is installed

Open a terminal:
- **Windows** → search for **PowerShell** in the Start menu
- **Mac** → open **Terminal** (Applications → Utilities → Terminal)

Run:
```
python --version
```

If you see something like `Python 3.11.2` you're good. If you get an error or a version below 3.9, install Python from **[python.org](https://www.python.org/downloads/)**.

> **Windows tip:** During installation, check **"Add Python to PATH"** before clicking Install.

---

### 2. Download the app

Click **Code → Download ZIP** at the top of this page and unzip it anywhere on your computer.

Or if you have Git:
```
git clone https://github.com/hiralhp/jobApplicationTracker.git
```

---

### 3. Install dependencies

In your terminal, navigate to the folder where you unzipped the app:
```
cd path/to/jobApplicationTracker
```

Then install the required packages:
```
pip install streamlit pandas google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

---

### 4. Run the app

```
streamlit run app.py
```

A browser window will open automatically at `http://localhost:8501`. Your data is stored locally in `job_tracker.db` — nobody else can see it.

---

## Gmail Sync

Once the app is running, open the **⚙️ Gmail Setup** tab for step-by-step instructions on connecting your Gmail account.

---

## Features

- **Companies tab** — track companies sorted into Need Action / Applied / Checked
- **Gmail Sync** — auto-detects application confirmation emails
- **Jobs tab** — track specific roles with statuses (To Do → Applied → Interviewing → Offer)
- **Add Company** — log companies with applied dates, careers URLs, and notes
