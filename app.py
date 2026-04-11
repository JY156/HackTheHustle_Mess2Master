from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Now import your modules
from gemini_client import Mess2MasterAI
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)
ai = Mess2MasterAI()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_upload():
    files = request.files.getlist('files')
    notes = request.form.get('notes', '')
    
    # Process with Gemini
    result = ai.extract_tasks(files, notes)
    
    # TODO: Add calendar integration here (Day 2)
    
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True, port=5000)