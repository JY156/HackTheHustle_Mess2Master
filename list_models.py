# list_models.py
from google import genai
import os
from pathlib import Path
from dotenv import load_dotenv  # ← ADD THIS

BASE_DIR = Path(__file__).resolve().parent

# Load .env file FIRST
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ GEMINI_API_KEY not found. Check your .env file.")
    exit(1)

client = genai.Client(api_key=api_key)

try:
    models = client.models.list()
    print(f"✅ Found {len(models)} model(s) total")
    print("✅ Available models (supporting generateContent):")
    found = False
    for m in models:
        supported = getattr(m, 'supported_generation_methods', [])
        if "generateContent" in supported:
            found = True
            print(f"  • {m.name}")
    if not found:
        print("  (none)")
except Exception as e:
    print(f"❌ Error: {e}")