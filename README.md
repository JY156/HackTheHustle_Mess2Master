# 🚀 Mess2Master

> Turn chaotic project data into clear, actionable plans with Gemini AI | Hack The Hustle 2026

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![Gemini API](https://img.shields.io/badge/Gemini-API-green.svg)](https://ai.google.dev)
[![Flask](https://img.shields.io/badge/Flask-Lightweight-orange.svg)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🎯 The Problem

Student projects stall because critical information stays scattered across chat threads, rough notes, and assignment PDFs. Teams spend more energy managing coordination than actually creating work.

This **"coordination tax"** leads to:

* Missed deadlines
* Duplicated efforts
* Lost momentum

---

## ✨ The Solution

**Mess2Master** ingests unstructured team data and uses **Google Gemini AI** to generate structured project intelligence instantly.

```
📥 Messy Input (PDFs, voice, chats)

                ↓

🧠 AI Processing (Gemini multimodal)

                ↓

📋 Structured Output (Tasks, deadlines, gaps)
```

### Key Capabilities

* ✅ **Prioritized task lists** with deadlines & assignee suggestions
* ✅ **Smart gap analysis** (missing sections, unassigned roles)
* ✅ **Calendar integration** (Google Calendar links)
* ✅ **Voice-first meetings** with live transcription
* ✅ **Resilient fallback system** for reliable UX

---

## 🛠️ Features

| Feature                      | Description                                     |
| ---------------------------- | ----------------------------------------------- |
| 📄 **Multimodal Input**      | Upload PDFs, DOCs, TXT, MP3, WAV or paste notes |
| 🎤 **Voice Meeting Capture** | Record meetings → extract tasks automatically   |
| 🧠 **AI Task Extraction**    | Converts messy text into structured JSON tasks  |
| 🎯 **Smart Prioritization**  | Scores tasks by urgency, weight, dependencies   |
| 📅 **Calendar Sync**         | One-click Google Calendar integration           |
| 🔗 **Notion Integration**    | Sync tasks or copy to clipboard                 |
| 🛡️ **Graceful Degradation** | Multi-model fallback chain                      |
| 📊 **Gap Detection**         | Identifies missing work, roles, conflicts       |
| 📱 **Responsive UI**         | Clean, modern, drag-and-drop interface          |

---

## 🧠 How It Works

1. **Upload** → PDFs, voice notes, or meeting text
2. **Process** → AI extracts tasks + detects gaps
3. **Display** → Master Priority List + Project Cards
4. **Act** → Calendar sync, Notion export, mark complete
5. **Iterate** → Add more inputs → AI merges safely

---

## 📦 Tech Stack

| Layer         | Technology                                 |
| ------------- | ------------------------------------------ |
| **Backend**   | Python 3.9+, Flask, python-dotenv          |
| **AI Engine** | Google Gemini API (`google-genai`)         |
| **Speech**    | Web Speech API                             |
| **Frontend**  | HTML5, CSS3, Vanilla JavaScript            |
| **Storage**   | Local JSON (`data/mess2master_state.json`) |

---

## 🚀 Quick Start (5 Minutes)

```bash
# 1. Clone repository
git clone https://github.com/JY156/HackTheHustle_GeminiFlow.git
cd HackTheHustle_GeminiFlow

# 2. Create virtual environment
python -m venv .venv

# 3. Activate environment
# Windows:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Configure environment variables
cp .env.example .env
# Add your GEMINI_API_KEY

# 6. Start the app
python app.py

# 7. Open in browser
http://127.0.0.1:5000
```

---

## 🎮 Usage

### Run App

```bash
python app.py
```

### Workflow

1. **First Visit** → Set semester dates
2. **Upload** → Files or meeting notes
3. **Select Project** → Existing or new
4. **Generate** → AI creates tasks
5. **Act**

   * 🗓️ Add to Google Calendar
   * 🔗 Sync to Notion
   * ✅ Mark tasks complete

---

## 🎤 Voice Meeting Feature (Chrome/Edge)

1. Click **"Start Listening"**
2. Accept privacy modal (audio stays local)
3. Conduct meeting normally
4. Click **"Stop & Extract"**
5. Tasks are automatically generated

---

## 🔑 Key Concepts

### Multimodal Input Processing

* **PDFs** → PyPDF2 + Gemini fallback
* **Voice** → Web Speech API → transcript → AI
* **Text** → Direct prompt processing

### Model Fallback Chain

If the primary model fails:

1. Automatically switches to backup models
2. Ensures uninterrupted UX

### Semester-Aware Date Parsing

* Converts **"Week 4" → actual date**
* Supports semester boundaries & break weeks

### Merge-Safe Task Management

* Unique task IDs (`ts_<timestamp>_<hash>`)
* Updates tasks without overwriting
* Separates pending vs completed

### Gap Detection

Identifies:

* Missing deliverables
* Unassigned roles
* Timeline conflicts
* Undefined technical decisions

---

## 🤝 Team & Submission

* **Event:** Hack The Hustle 2026 – *Boosting Productivity using Gemini*
* **GitHub:** https://github.com/JY156/HackTheHustle_GeminiFlow
* **Pitching Deck:** https://canva.link/0ginb13i6sv7wpt

---

## 📄 License

MIT License — free for educational and hackathon use.
