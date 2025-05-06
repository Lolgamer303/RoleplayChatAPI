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
    types.Content(role='user', parts=[types.Part(text='start')]),
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
    userId = request.args.get('userId', None)  # Optional field

    try:
        with Session(db.engine) as session:
            if userId:
                # If userId is provided, filter by it
                result = session.execute(
                    text("""SELECT id, name FROM "Campaign" WHERE "apiKeyId" = :id AND "userId" = :userId"""),
                    {'id': api_key_id, 'userId': userId}
                ).fetchall()
            else:
                # If userId is not provided, ignore the "userId" condition
                result = session.execute(
                    text("""SELECT id, name FROM "Campaign" WHERE "apiKeyId" = :id"""),
                    {'id': api_key_id}
                ).fetchall()
            campaigns = [{'id': str(row[0]), 'name': row[1]} for row in result]
    except Exception as e:
        print(f"Error fetching campaigns: {e}")
        return jsonify({'error': f"Database error: {e}"}), 500

    if len(campaigns) == 0:
        return jsonify({'message': 'You have no campaigns yet.'}), 204
    return jsonify(campaigns)

@app.route('/campaigns', methods=['POST'])
@require_api_key
def create_campaign():
    api_key_id = request.api_key_id
    name = request.json.get('name', '')
    book = request.json.get('book', '')
    prompt = request.json.get('prompt', default_prompt)
    
    userId = request.json.get('userId', None) # Optional field

    if not name or not book or not prompt:
        return jsonify({'error': 'Missing required fields.'}), 400

    try:
        with Session(db.engine) as session:
            new_campaign = {
                'id': str(uuid.uuid4()),  # Generate a UUID for the campaign ID
                'name': name,
                'book': book,
                'prompt': prompt,
                'userId': userId,  # Optional field
                'apiKeyId': api_key_id
            }
            session.execute(
                text("""INSERT INTO "Campaign" (id, name, book, prompt, "userId", "apiKeyId") 
                        VALUES (:id, :name, :book, :prompt, :userId, :apiKeyId)"""),
                new_campaign
            )
            session.commit()
    except Exception as e:
        print(f"Error creating campaign: {e}")
        return jsonify({'error': f"Database error: {e}"}), 500
    
    return jsonify({'status': 'success', 'message': 'Campaign created successfully.'}), 201

@app.route('/campaigns/<uuid:campaignid>', methods=['POST'])
@require_api_key
def campaign_chat(campaignid):
    api_key_id = request.api_key_id
    user_input = request.json.get('input', '')
    print(f"User input: {user_input}")

    try:
        with Session(db.engine) as session:
            # Step 1: Validate the campaign
            result = session.execute(
                text("""SELECT "apiKeyId" FROM "Campaign" WHERE id = :campaignid"""),
                {'campaignid': str(campaignid)}
            ).fetchone()
            if not result:
                return jsonify({'error': 'Campaign not found.'}), 404
            campaign_api_key_id = result[0]
            if campaign_api_key_id != api_key_id:
                return jsonify({'error': 'You do not have access.'}), 401

            # Step 2: Load the chat history for the campaign
            history_result = session.execute(
                text("""SELECT message, response FROM "Chat" WHERE "campaignId" = :campaignid ORDER BY "createdAt" DESC"""),
                {'campaignid': str(campaignid)}
            ).fetchall()
            
            if not history_result:
                history = base_context
                # Send only the base context to the AI and ignore the first user input
                response = client.models.generate_content(
                    model='gemini-2.0-flash', contents=history
                )
            else:
                history = []
                for row in history_result:
                    history.append(types.Content(role='user', parts=[types.Part(text=row[0])]))
                    history.append(types.Content(role='model', parts=[types.Part(text=row[1])]))
                    
                response = client.models.generate_content(
                    model='gemini-2.0-flash', contents=history + [types.Content(role='user', parts=[types.Part(text=user_input)])]
                )

            # Step 6: Store the new chat in the database
            storeChat(campaignid, user_input, response.text)

            # Step 7: Return the AI's response
            return jsonify({'response': response.text})

    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500

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
                return jsonify({'error': 'You do not have access'}), 401
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
    
@app.route('/campaigns/<uuid:campaignid>', methods=['PUT'])
@require_api_key
def edit_campaign_info(campaignid):
    api_key_id = request.api_key_id
    name = request.json.get('name', None)

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
                return jsonify({'error': 'You do not have access'}), 401
            if name:
                session.execute(
                    text("""UPDATE "Campaign" SET name = :name WHERE id = :campaignid"""),
                    {'name': name, 'campaignid': str(campaignid)}
                )
                session.commit()
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500
    return jsonify({'status': 'success', 'message': 'Campaign updated successfully.'}), 200
    
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
                return jsonify({'error': 'You do not have access'}), 401
            session.execute(
                text("""DELETE FROM "Campaign" WHERE id = :campaignid"""),
                {'campaignid': str(campaignid)}
            )
            session.commit()
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500
    
    return jsonify({'status': 'success', 'message': 'Campaign deleted successfully.'}), 200

@app.route('/campaigns/<uuid:campaignid>/chats', methods=['GET'])
@require_api_key
def get_chats(campaignid):
    api_key_id = request.api_key_id
    number = request.args.get('number', type=int)  # Optional field
    
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
                return jsonify({'error': 'You do not have access'}), 401

            # Fetch the chats for the campaign
            if number:
                result = session.execute(
                    text("""SELECT message, response FROM "Chat" WHERE "campaignId" = :campaignid ORDER BY "createdAt" DESC LIMIT :number"""),
                    {'campaignid': str(campaignid), 'number': number}
                ).fetchall()
            else:
                result = session.execute(
                    text("""SELECT message, response FROM "Chat" WHERE "campaignId" = :campaignid ORDER BY "createdAt" DESC"""),
                    {'campaignid': str(campaignid)}
                ).fetchall()

            chats = [{'message': row[0], 'response': row[1]} for row in result]
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500

    if len(chats) == 0:
        return jsonify({'message': 'No chats found for this campaign.'}), 204
    return jsonify(chats)
    
@app.route('/campaigns/<uuid:campaignid>/chats', methods=['DELETE'])
@require_api_key
def delete_chats(campaignid):
    api_key_id = request.api_key_id
    number = request.args.get('number', type=int)  # Optional field

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
                return jsonify({'error': 'You do not have access'}), 401

            # Delete the chats for the campaign
            if number:
                session.execute(
                    text("""DELETE FROM "Chat" WHERE "campaignId" = :campaignid ORDER BY "createdAt" DESC LIMIT :number"""),
                    {'campaignid': str(campaignid), 'number': number}
                )
            else:
                session.execute(
                    text("""DELETE FROM "Chat" WHERE "campaignId" = :campaignid"""),
                    {'campaignid': str(campaignid)}
                )
            session.commit()

            # Reset the AI's history to the base context
            session.execute(
                text("""INSERT INTO "Chat" (message, response, "campaignId") 
                        VALUES (:message, :response, :campaignId)"""),
                {
                    'message': default_prompt,
                    'response': 'Understood',
                    'campaignId': str(campaignid)
                }
            )
            session.commit()
    except Exception as e:
        return jsonify({'error': f"Database error: {e}"}), 500

    return jsonify({'status': 'success', 'message': 'Chats deleted and history reset successfully.'}), 200
    
# --- START ---
if __name__ == '__main__':
    app.run(debug=True)