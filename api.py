from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from google import genai
from google.genai import types
from sqlalchemy import text
from sqlalchemy.orm import Session
from functools import wraps
import uuid
import os

app = Flask(__name__)

# ENV CONFIG
API_KEY = os.environ.get('API_KEY')
DATABASE_URL = os.environ.get('DATABASE_URL')
if not API_KEY:
    raise ValueError("No API_KEY found for Flask application")
if not DATABASE_URL:
    raise ValueError("No DATABASE_URL found for Flask application")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# GENAI CLIENT SETUP
client = genai.Client(api_key=API_KEY)

with open('default_prompt.txt', 'r') as file:
    default_prompt = file.read()

base_context = [
    types.Content(role='user', parts=[types.Part(text=default_prompt)]),
    types.Content(role='model', parts=[types.Part(text='Understood')]),
]

# --- API KEY AUTH (NOW FROM HEADER) ---
def verify_api_key():
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None, "Missing Authorization header."

    if not auth_header.startswith('Bearer '):
        return None, "Invalid Authorization header format."

    api_key = auth_header.replace('Bearer ', '').strip()

    try:
        with Session(db.engine) as session:
            result = session.execute(
                text("""SELECT id FROM "ApiKey" WHERE key = :key"""),
                {'key': api_key}
            ).fetchone()
            if not result:
                return None, "Invalid API key."
            return result[0], None
    except Exception as e:
        return None, f"Database error: {e}"
    
# --- DECORATORS ---
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key_id, error = verify_api_key()
        if error:
            return jsonify({'error': error}), 401
        request.api_key_id = api_key_id  # attach to request context
        return f(*args, **kwargs)
    return decorated

# --- DATABSE STORE FUNCTIONS ---

def storeChat(campaign_id, user_input, response):
    try:
        with Session(db.engine) as session:
            # Check if the campaign exists
            result = session.execute(
                text("""SELECT id FROM "Campaign" WHERE id = :campaign_id"""),
                {'campaign_id': str(campaign_id)}
            ).fetchone()
            if not result:
                return jsonify({'error': 'Campaign not found.'}), 404
            # Insert the new chat into the "Chat" table
            new_chat = {
                'message': user_input,
                'response': response,
                'campaignId': campaign_id
            }
            session.execute(
                text("""INSERT INTO "Chat" (message, response, "campaignId") 
                        VALUES (:message, :response, :campaignId)"""),
                new_chat
            )
            session.commit()
            return jsonify({'success': 'Chat stored successfully.'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- ROUTES ---

@app.route('/campaigns', methods=['GET'])
@require_api_key
def get_campaigns():
    api_key_id = request.api_key_id

    try:
        with Session(db.engine) as session:
            result = session.execute(
                text("""SELECT id, name FROM "Campaign" WHERE "apiKeyId" = :id"""),
                {'id': api_key_id}
            ).fetchall()
            campaigns = [{'id': str(row[0]), 'name': row[1]} for row in result]
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500
    
    if len(campaigns) == 0:
        return jsonify({'message': 'You have no campaigns yet.'})
    return jsonify(campaigns)

@app.route('/campaigns', methods=['POST'])
@require_api_key
def create_campaign():
    api_key_id = request.api_key_id
    name = request.json.get('name', '')
    book = request.json.get('book', '')
    prompt = request.json.get('prompt', default_prompt)

    if not name or not book or not prompt:
        return jsonify({'error': 'Missing required fields.'}), 400

    try:
        with Session(db.engine) as session:
            new_campaign = {
                'id': str(uuid.uuid4()),  # Generate a UUID for the campaign ID
                'name': name,
                'book': book,
                'prompt': prompt,
                'apiKeyId': api_key_id
            }
            session.execute(
                text("""INSERT INTO "Campaign" (id, name, book, prompt, "apiKeyId") VALUES (:id, :name, :book, :prompt, :apiKeyId)"""),
                new_campaign
            )
            session.commit()
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500
    
    return jsonify({'status': 'success', 'message': 'Campaign created successfully.'}), 201

@app.route('/campaigns/<uuid:campaignid>', methods=['POST'])
@require_api_key
def campaign_chat(campaignid):
    api_key_id = request.api_key_id
    try: 
        with Session(db.engine) as session:
            result = session.execute(
                text("""SELECT "apiKeyId" FROM "Campaign" WHERE id = :campaignid"""),
                {'campaignid': str(campaignid)}
            ).fetchone()
            if not result:
                return jsonify({'error': 'Campaign not found.'}), 404
            campaign_api_key_id = result[0]
            if campaign_api_key_id != api_key_id:
                return jsonify({'status': 'error', 'message': 'You do not have access.'}), 403
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500
    user_input = request.json.get('input', '')
    if user_input not in ['1', '2', '3', '4', '5']:
        return jsonify({'error': 'Invalid input. Please select a number from 1 to 5.'}), 400

    chat = client.chats.create(
        model='gemini-2.0-flash',
        history=base_context,
    )
    response = chat.send_message(user_input)

    storeChat(campaignid, user_input, response.text)

    return jsonify({'response': response.text})

@app.route('/campaigns/<uuid:campaignid>', methods=['GET'])
@require_api_key
def get_campaign_info(campaignid):
    api_key_id = request.api_key_id
    try: 
        with Session(db.engine) as session:
            result = session.execute(
                text("""SELECT "apiKeyId", book, prompt, name, "createdAt" FROM "Campaign" WHERE id = :campaignid"""),
                {'campaignid': str(campaignid)}
            ).fetchone()
            if not result:
                return jsonify({'error': 'Campaign not found.'}), 404
            campaign_api_key_id = result[0]
            if campaign_api_key_id != api_key_id:
                return jsonify({'error': 'You do not have access'}), 403
            book = result[1]
            prompt = result[2]
            name = result[3]
            created_at = result[4]
            return jsonify({
                'book': book,
                'prompt': prompt,
                'name': name,
                'created_at': created_at
            })
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500
    
@app.route('/campaigns/<uuid:campaignid>', methods=['DELETE'])
@require_api_key
def delete_campaign(campaignid):
    api_key_id = request.api_key_id
    try: 
        with Session(db.engine) as session:
            result = session.execute(
                text("""SELECT "apiKeyId" FROM "Campaign" WHERE id = :campaignid"""),
                {'campaignid': str(campaignid)}
            ).fetchone()
            if not result:
                return jsonify({'error': 'Campaign not found.'}), 404
            campaign_api_key_id = result[0]
            if campaign_api_key_id != api_key_id:
                return jsonify({'error': 'You do not have access'}), 403
            session.execute(
                text("""DELETE FROM "Campaign" WHERE id = :campaignid"""),
                {'campaignid': str(campaignid)}
            )
            session.commit()
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500
    
    return jsonify({'status': 'success', 'message': 'Campaign deleted successfully.'}), 200
    
# --- START ---
if __name__ == '__main__':
    app.run(debug=True)
