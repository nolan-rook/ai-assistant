from flask import Flask, request, jsonify
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler

from voiceflow_api import VoiceflowAPI
from utils import process_file, extract_webpage_content

import re
import os
import random

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

logging.getLogger('slack_bolt.App').setLevel(logging.ERROR)

slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
bot_user_id = os.getenv("SLACK_BOT_USER_ID") 

logging.info(f"Bot User ID from environment: {bot_user_id}")

# Install the Slack app and get xoxb- token in advance
bolt_app = App(token=slack_bot_token,
          signing_secret=slack_signing_secret)

# Flask app to handle webhook routes
flask_app = Flask(__name__)
slack_handler = SlackRequestHandler(bolt_app)

# Initialize the Voiceflow API client
voiceflow = VoiceflowAPI()

# Stores the ongoing conversations with Voiceflow
conversations = {}

def create_message_blocks(text_responses, button_payloads):
    blocks = []
    summary_text = "Select an option:"  # Fallback text for notifications
    max_chars = 3000  # Maximum characters for a block of text

    # Function to split text into chunks of max_chars
    def split_text(text, max_length):
        for start in range(0, len(text), max_length):
            yield text[start:start + max_length]

    # Add text responses as section blocks
    for text in text_responses:
        if len(text) <= max_chars:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                }
            })
        else:
            # Split text into chunks and add each as a separate block
            for chunk in split_text(text, max_chars):
                blocks.append({"type": "divider"})
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": chunk
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

# Route for Slack events
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return slack_handler.handle(request)

def process_message(event, say):
    user_id = event.get('user')
    channel_id = event.get('channel')
    thread_ts = event.get('thread_ts', event['ts'])
    user_input = event.get('text', '').strip()
    
    logging.info(f"Processing message from user {user_id} in channel {channel_id}, thread {thread_ts}")

    if 'app_mention' in event['type']:
        user_input = re.sub(r"<@U[A-Z0-9]+>", "", user_input, count=1).strip()

    conversation_id = f"{channel_id}-{thread_ts}"
    
    logging.info(f"Processing in conversation {conversation_id}")
    
    processing_messages = [
        "Just a moment..."
    ]

    # Send a random processing message
    say(text=random.choice(processing_messages), thread_ts=thread_ts)
    
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
    # Extract URLs and remove the enclosing angle brackets
    urls = re.findall(r'<http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+[^>),\s]*>', user_input)
    for url in urls:
        # Remove the angle brackets from each URL
        url = url[1:-1]
        webpage_text = extract_webpage_content(url)
        if webpage_text:
            combined_input += "\n" + webpage_text

    print(combined_input)  # For debugging
    
    if conversation_id in conversations:
        is_running, button_payloads = voiceflow.handle_user_input(conversation_id, combined_input)
    else:
        is_running, button_payloads = voiceflow.handle_user_input(conversation_id, {'type': 'launch'})
        if is_running:
            is_running, button_payloads = voiceflow.handle_user_input(conversation_id, combined_input)

    conversations[conversation_id] = {
        'channel': event['channel'],
        'user_id': user_id,
        'thread_ts': thread_ts,
        'button_payloads': button_payloads
    }

    blocks, summary_text = create_message_blocks(voiceflow.get_responses(), button_payloads)
    logging.info(f"Sending blocks: {blocks}, summary_text: {summary_text}, thread_ts: {thread_ts}")
    say(blocks=blocks, text=summary_text, thread_ts=thread_ts)

@bolt_app.event("app_mention")
def handle_app_mention_events(event, say):
    logging.info(f"Received app_mention event: {event}")
    if event.get('user') == bot_user_id:
        return

    # Extract thread_ts to track the conversation; fall back to message ts if not in a thread
    thread_ts = event.get('thread_ts', event['ts'])
    channel_id = event['channel']

    # Mark this thread as an active conversation the bot is participating in
    conversations[thread_ts] = {'channel_id': channel_id, 'thread_ts': thread_ts}

    # Process the mention message
    process_message(event, say)

@bolt_app.event("message")
def handle_message_events(event, say):
    logging.info(f"Received message event: {event}")
    # Ignore messages from the bot itself to avoid loops
    if event.get('user') == bot_user_id:
        return
    
    if event.get('channel_type') == 'im':
        process_message(event, say)

    # Extract the necessary identifiers from the event
    thread_ts = event.get('thread_ts', event.get('ts'))
    is_threaded = bool(event.get('thread_ts'))

    # Check if the message is part of a thread that the bot is involved in
    if is_threaded and thread_ts in conversations:
        # Process the message as part of the ongoing conversation
        process_message(event, say)

@bolt_app.action(re.compile("voiceflow_button_"))
def handle_voiceflow_button(ack, body, client, say, logger):
    ack()  # Acknowledge the action
    action_id = body['actions'][0]['action_id']
    user_id = body['user']['id']
    channel_id = body['channel']['id']
    message_ts = body['message']['ts']  # Timestamp of the original message
    thread_ts = body['message'].get('thread_ts', body['message']['ts'])

    # Create a unique conversation ID using user_id and thread_ts
    conversation_id = f"{channel_id}-{thread_ts}"

    # Extract the index from the action_id
    button_index = int(action_id.split("_")[-1])

    if conversation_id in conversations:
        button_payloads = conversations[conversation_id]['button_payloads']
        button_payload = button_payloads.get(str(button_index + 1))

        if button_payload:
            # Process the button action to advance the conversation
            is_running, new_button_payloads = voiceflow.handle_user_input(conversation_id, button_payload)
            conversations[conversation_id]['button_payloads'] = new_button_payloads

            # Update the message to remove the buttons
            try:
                original_blocks = body['message'].get('blocks', [])
                updated_blocks = [block for block in original_blocks if block['type'] != 'actions']
                client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    blocks=updated_blocks
                )
            except Exception as e:
                logger.error(f"Failed to update message: {e}")

            # Send a new message reflecting the next stage in the conversation
            if is_running:
                blocks, summary_text = create_message_blocks(voiceflow.get_responses(), new_button_payloads)
                client.chat_postMessage(channel=channel_id, blocks=blocks, text=summary_text, thread_ts=thread_ts)
                
        else:
            # Respond in the correct thread if the choice wasn't understood
            client.chat_postMessage(channel=channel_id, text="Sorry, I didn't understand that choice.", thread_ts=thread_ts)
    else:
        # Respond in the correct thread if no conversation was found
        client.chat_postMessage(channel=channel_id, text="Sorry, I couldn't find your conversation.", thread_ts=thread_ts)

        
def notify_user_completion(conversation_id):
    conversation_details = conversations.get(conversation_id)
    if conversation_details:
        channel_id = conversation_details['channel']
        # This assumes you have stored 'user_id' when the conversation started
        user_id = conversation_details['user_id'] 
        thread_ts = conversation_details['thread_ts']  # The thread timestamp for replying in thread

        # Construct the notification message, tagging the user
        completion_message = f"Hey <@{user_id}>! 🎉 I've just finished crafting that blog post for you. Take a peek in the Google Docs folder and let us know your thoughts!"

        # Use the correct Bolt app instance to send the message
        try:
            bolt_app.client.chat_postMessage(
                channel=channel_id, 
                text=completion_message, 
                thread_ts=thread_ts  # Ensure the message is sent as a reply in the thread
            )
        except Exception as e:
            print(f"Error sending completion notification: {e}")

# Correctly define the /task-completed endpoint within Flask app context
@flask_app.route("/task-completed", methods=["POST"])
def task_completed():
    data = request.json
    conversation_id = data.get('conversation_id')
    if conversation_id:
        # Assuming you implement a way to notify users, for example:
        notify_user_completion(conversation_id)
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Missing conversation_id"}), 400
          
# Start your app
if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=3000)