# list_models.py
from google import genai
import os
from dotenv import load_dotenv  # ← ADD THIS

# Load .env file FIRST
load_dotenv()  # ← ADD THIS

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ GEMINI_API_KEY not found. Check your .env file.")
    exit(1)

client = genai.Client(api_key=api_key)

try:
    models = client.models.list()
    print("✅ Available models (supporting generateContent):")
    for m in models:
        if hasattr(m, 'supported_generation_methods') and "generateContent" in m.supported_generation_methods:
            print(f"  • {m.name}")
except Exception as e:
    print(f"❌ Error: {e}")