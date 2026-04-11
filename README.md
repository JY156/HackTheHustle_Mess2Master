# 🚀 Mess2Master
> Turn chaotic project data into clear, actionable plans with Gemini AI | Hack The Hustle 2026

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![Gemini API](https://img.shields.io/badge/Gemini-API-green.svg)](https://ai.google.dev)
[![Flask](https://img.shields.io/badge/Flask-Lightweight-orange.svg)](https://flask.palletsprojects.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 🎯 The Problem
Student projects stall because critical information stays scattered across chat threads, rough notes, and assignment PDFs. Teams spend more energy managing coordination than actually creating work. This "coordination tax" leads to missed deadlines, duplicated efforts, and lost momentum.

## ✨ The Solution
**Mess2Master** ingests unstructured team data and uses **Google's Gemini AI** to instantly generate structured project intelligence:
- ✅ **Prioritized task lists** with deadlines & owner suggestions
- ✅ **Smart gap analysis** (flags missing sections or unassigned roles)
- ✅ **Calendar integration** via function calling
- ✅ **Resilient fallback system** ensuring reliable demo & production UX

## 🛠️ Features
| Feature | Description |
|---------|-------------|
| 📄 Multimodal Input | Upload PDFs, DOCs, TXT files + paste chat logs/meeting notes |
| 🧠 AI Task Extraction | Gemini converts messy text into structured JSON tasks |
| 🎯 Smart Prioritization | Auto-scores tasks by deadline proximity, rubric weight, & dependencies |
| 📅 Calendar Sync | One-click deadline creation (mock function calling for hackathon) |
| 🛡️ Graceful Degradation | Built-in fallback ensures UI never breaks during API limits |
| 📱 Modern UI | Responsive design, drag-and-drop upload, real-time loading states |

## 🧠 How It Works
           [Upload PDF + Paste Notes]
                       ↓
     [Flask Backend Receives Multipart Form]
                       ↓
     [Gemini API Processes Multimodal Input]
                       ↓
 [Structured JSON Output: Tasks, Deadlines, Gaps]
                       ↓
[Frontend Renders Priority Cards + Calendar Action]

## 📦 Tech Stack
- Backend: Python 3.9+, Flask, python-dotenv
- AI Engine: Google Gemini API (google-genai SDK), Structured JSON Output, Function Calling
- Frontend: HTML5, CSS3 (Modern Grid/Flexbox), Vanilla JavaScript
- Icons/Fonts: Font Awesome 6, Inter (Google Fonts)
- Deployment Ready: WSGI-compatible, environment-configured

## ▶️ Run Locally

### 1) Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure environment variables

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_api_key_here
# Optional model override
GEMINI_MODEL=gemini-2.0-flash
```

### 4) Start the app

```bash
python app.py
```

Open: `http://127.0.0.1:5000`

## ✅ Quick Testing Checklist

1. Homepage loads without errors.
2. Paste sample notes and submit with no files.
3. Upload at least one supported file (`pdf`, `docx`, `txt`, `png`, `jpg`) and submit.
4. Confirm tasks appear on the dashboard and are sorted by priority/date.
5. Confirm generated project data is saved to `data/projects.json`.

## 🧪 Optional Local API Test (PowerShell)

Use this to verify `/process` directly:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:5000/process -Method Post -Form @{
    notes = "Week 4: finish slides, assign presenter"
    sem_start = "2026-01-12"
    sem_end = "2026-05-15"
}
```

## 🤝 Team & Submission
- Built for: Hack The Hustle 2026 – "Boosting Productivity using Gemini"
- GitHub: https://github.com/JY156/HackTheHustle_GeminiFlow
- Pitch Deck:

## 📄 License
MIT License – Open for educational & hackathon use. See LICENSE for details.