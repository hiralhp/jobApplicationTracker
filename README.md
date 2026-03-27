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

## Setting Up Gmail Sync (Optional)

Gmail sync scans your inbox for application confirmation emails and automatically marks companies as applied. Setup is free and takes about 10 minutes.

### Step 1 — Enable the Gmail API

1. Go to **[Google Cloud Console](https://console.cloud.google.com/)** and create a new project
2. Go to **APIs & Services → Library**, search for **Gmail API**, and click **Enable**

### Step 2 — Create credentials

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Set **Application type** to **Desktop app**, give it any name, click **Create**
3. Click **Download JSON** and save the file as **`credentials.json`** inside the app folder

### Step 3 — Configure the consent screen

1. Go to **APIs & Services → OAuth consent screen**, choose **External**, click **Create**
2. Enter an app name (e.g. *Job Tracker*) and your email address — leave everything else blank
3. On the **Scopes** page click **Save and Continue** without adding anything
4. On the **Test users** page add your Gmail address, then save
5. You don't need to publish the app — keeping it in testing mode is fine

### Step 4 — Authorize

1. Run the app (`streamlit run app.py`)
2. Open the **Gmail Sync** tab and click **Sync Gmail**
3. A browser window will open — sign in with your Google account and approve access
4. Done. A `token.json` file is saved so you won't need to authorize again

---

## Keeping Your Data Private

These files are personal and should never be shared or committed to GitHub:

| File | What it is |
|------|------------|
| `credentials.json` | Your Google OAuth credentials |
| `token.json` | Your Gmail auth token |
| `job_tracker.db` | Your personal tracker data |

These are already listed in `.gitignore` so they won't be accidentally committed if you use Git.

---

## Features

- **Companies tab** — track companies sorted into Need Action / Applied / Checked
- **Gmail Sync** — auto-detects application confirmation emails
- **Jobs tab** — track specific roles with statuses (To Do → Applied → Interviewing → Offer)
- **Add Company** — log companies with applied dates, careers URLs, and notes
