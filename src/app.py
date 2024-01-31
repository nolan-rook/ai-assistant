from slack_bolt import App
from slack_bolt.oauth.oauth_settings import OAuthSettings

import psycopg2
from slack_sdk.oauth.installation_store import InstallationStore
from slack_sdk.oauth.installation_store.models import Installation
from slack_sdk.oauth.installation_store.models import Bot
from slack_sdk.oauth.state_store import OAuthStateStore

import uuid

import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PostgresInstallationStore(InstallationStore):
    def __init__(self, database_url):
        self.conn = psycopg2.connect(database_url)

    def save(self, installation: Installation):
        logger.info(f"Saving installation data for team: {installation.team_id}")
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO installations 
                    (client_id, enterprise_id, team_id, bot_token, bot_user_id) 
                    VALUES (%s, %s, %s, %s, %s) 
                    ON CONFLICT (team_id) DO UPDATE 
                    SET bot_token = EXCLUDED.bot_token, bot_user_id = EXCLUDED.bot_user_id
                    """, 
                    (installation.client_id, installation.enterprise_id, installation.team_id, installation.bot.token, installation.bot_user_id))
                self.conn.commit()
                logger.info("Installation data saved successfully.")
        except Exception as e:
            logger.exception("Failed to save installation data.", exc_info=True)

    def find_installation(self, *, enterprise_id=None, team_id=None, user_id=None):
        with self.conn.cursor() as cur:
            cur.execute("SELECT bot_token, bot_user_id FROM installations WHERE team_id = %s", (team_id,))
            row = cur.fetchone()
            if row is not None:
                return Installation(client_id=self.client_id, enterprise_id=enterprise_id, team_id=team_id, bot=Bot(token=row[0], bot_user_id=row[1]))
            else:
                return None
    def find_bot_user_id(self, team_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT bot_user_id FROM installations WHERE team_id = %s", (team_id,))
            row = cur.fetchone()
            return row[0] if row else None


class PostgresOAuthStateStore(OAuthStateStore):
    def __init__(self, database_url):
        self.conn = psycopg2.connect(database_url)

    def issue(self):
        state = str(uuid.uuid4())
        logger.info(f"Issuing new state: {state}")
        try:
            with self.conn.cursor() as cur:
                # Store the state value in the database
                cur.execute("INSERT INTO oauth_states (state) VALUES (%s)", (state,))
                self.conn.commit()
                logger.info("State issued and stored successfully.")
            return state
        except Exception as e:
            logger.exception("Failed to issue new state.", exc_info=True)

    def consume(self, state: str):
        logger.info(f"Consuming state: {state}")
        try:
            if self.is_valid(state):
                with self.conn.cursor() as cur:
                    cur.execute("DELETE FROM oauth_states WHERE state = %s", (state,))
                    self.conn.commit()
                    logger.info("State consumed successfully.")
        except Exception as e:
            logger.exception("Failed to consume state.", exc_info=True)

    def is_valid(self, state: str):
        with self.conn.cursor() as cur:
            # Check if the state exists in the database
            cur.execute("SELECT state FROM oauth_states WHERE state = %s", (state,))
            return cur.fetchone() is not None

from voiceflow_api import VoiceflowAPI
from utils import process_file

import re
import os

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

logging.getLogger('slack_bolt.App').setLevel(logging.ERROR)

# Instantiate custom installation and state stores
installation_store = PostgresInstallationStore(database_url=os.getenv("DATABASE_URL"))
state_store = PostgresOAuthStateStore(database_url=os.getenv("DATABASE_URL"))

# OAuth settings with custom stores
oauth_settings = OAuthSettings(
    client_id=os.getenv("SLACK_CLIENT_ID"),
    client_secret=os.getenv("SLACK_CLIENT_SECRET"),
    scopes=["app_mentions:read", "channels:history", "chat:write", "im:history", "im:read", "im:write", "files:read", "files:write", "mpim:history", "mpim:read", "mpim:write", "users.profile:read", "users:read"],
    redirect_uri="https://sea-turtle-app-q8k8p.ondigitalocean.app/slack/oauth_redirect",
    installation_store=installation_store,
    state_store=state_store
)

app = App(signing_secret=os.getenv("SLACK_SIGNING_SECRET"), oauth_settings=oauth_settings)

# Initialize the Voiceflow API client
voiceflow = VoiceflowAPI()

# Stores the ongoing conversations with Voiceflow
conversations = {}

def create_message_blocks(text_responses, button_payloads):
    blocks = []
    summary_text = "Select an option:"  # Fallback text for notifications
    # Add text responses as section blocks
    for text in text_responses:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text
            }
        })
    blocks.append({"type": "divider"})
    # Prepare buttons with unique action_ids
    buttons = []
    for idx, (button_value, button_payload) in enumerate(button_payloads.items()):
        button_text = button_payload['payload']['label']
        buttons.append({
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": button_text,
                "emoji": True
            },
            "value": button_value,
            "action_id": f"voiceflow_button_{idx}"  # Unique action_id for each button
        })

    # Add buttons in one section
    if buttons:
        blocks.append({
            "type": "actions",
            "elements": buttons
        })

    return blocks, summary_text

@app.event("message")
def handle_dm_events(event, say):
    # Check if the message is from the bot itself
    team_id = event.get('team_id')
    bot_user_id = installation_store.find_bot_user_id(team_id)
    if event.get('user') == bot_user_id:
        return  # Ignore the event if it's from the bot
    if event.get('channel_type') == 'im':
        user_id = event['user']
        # Check if the message is part of a thread
        if 'thread_ts' in event:
            thread_ts = event['thread_ts']
        else:
            thread_ts = event['ts']
        user_input = event.get('text', '').strip()

        # Create a unique conversation ID using user_id and thread_ts
        conversation_id = f"{user_id}-{thread_ts}"

        # Send a processing message
        say(text="Processing your request...", thread_ts=thread_ts)
        
        combined_input = user_input

        # Fetch files from the event, if any
        files = event.get('files', [])

        # Process any file part of the message
        if files:
            for file_info in files:
                file_url = file_info.get('url_private_download')
                file_type = file_info.get('filetype')
                if file_url:
                    file_text = process_file(file_url, file_type)
                    if file_text:
                        combined_input += "\n" + file_text
        print(combined_input)
        # Check if there's an ongoing conversation using the unique conversation_id
        if conversation_id in conversations:
            is_running, button_payloads = voiceflow.handle_user_input(conversation_id, combined_input)
        else:
            # Start a new conversation
            is_running, button_payloads = voiceflow.handle_user_input(conversation_id, {'type': 'launch'})

        # Store the conversation state using the unique conversation_id
        conversations[conversation_id] = {'channel': event['channel'], 'button_payloads': button_payloads}

        # Generate and send new blocks to Slack
        blocks, summary_text = create_message_blocks(voiceflow.get_responses(), button_payloads)
        say(blocks=blocks, text=summary_text, thread_ts=thread_ts)
        
@app.action(re.compile("voiceflow_button_"))
def handle_voiceflow_button(ack, body, client, say, logger):
    ack()  # Acknowledge the action
    action_id = body['actions'][0]['action_id']
    user_id = body['user']['id']

    # Determine the thread timestamp
    # Use 'thread_ts' from the message if available, otherwise fall back to 'ts' of the action
    thread_ts = body['message'].get('thread_ts', body['message']['ts'])

    # Create a unique conversation ID using user_id and thread_ts
    conversation_id = f"{user_id}-{thread_ts}"

    # Extract the index from the action_id
    button_index = int(action_id.split("_")[-1])

    # Check if there's an ongoing conversation using the unique conversation_id
    if conversation_id in conversations:
        button_payloads = conversations[conversation_id]['button_payloads']
        button_payload = button_payloads.get(str(button_index + 1))

        if button_payload:
            is_running, new_button_payloads = voiceflow.handle_user_input(conversation_id, button_payload)
            conversations[conversation_id]['button_payloads'] = new_button_payloads

            # Generate and send new blocks to Slack, ensuring to respond in the correct thread
            blocks, summary_text = create_message_blocks(voiceflow.get_responses(), new_button_payloads)
            say(blocks=blocks, text=summary_text, thread_ts=thread_ts)
        else:
            # Respond in the correct thread if the choice wasn't understood
            say(text="Sorry, I didn't understand that choice.", thread_ts=thread_ts)
    else:
        # Respond in the correct thread if no conversation was found
        say(text="Sorry, I couldn't find your conversation.", thread_ts=thread_ts)

          
# Start your app
if __name__ == "__main__":
    app.start(port=int(os.getenv("PORT", 3000)))