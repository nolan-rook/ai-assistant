from flask import Flask, request, jsonify
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler

from voiceflow_api import VoiceflowAPI
from utils import process_file

import re
import os
import random

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

import logging

logging.getLogger('slack_bolt.App').setLevel(logging.ERROR)

slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
bot_user_id = os.getenv("SLACK_BOT_USER_ID") 

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

# Route for Slack events
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return slack_handler.handle(request)

@bolt_app.event("message")
def handle_dm_events(event, say):
    # Check if the message is from the bot itself
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

        processing_messages = [
            "On it!", "Sure thing!", "Got it!", "One moment...", "I'm on it!", "Absolutely!",
            "Understood!", "Just a moment...", "Right away!", "Affirmative!", "No problem!",
            "Okay!", "On my way!", "Certainly!", "Acknowledged!", "Will do!", "You got it!",
            "I'm at it!", "Working on it!", "Already on it!", "On top of it!", "I've got this!",
            "Taking care of it!", "All over it!"
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
        print(combined_input)
        # Check if there's an ongoing conversation using the unique conversation_id
        if conversation_id in conversations:
            is_running, button_payloads = voiceflow.handle_user_input(conversation_id, combined_input)
        else:
            # Start a new conversation
            is_running, button_payloads = voiceflow.handle_user_input(conversation_id, {'type': 'launch'})

        # Store the conversation state using the unique conversation_id
        # Example of storing conversation details (adjust according to your actual logic)
        conversations[conversation_id] = {
            'channel': event['channel'],
            'user_id': event['user'],
            'thread_ts': event.get('thread_ts', event['ts']),  # Use thread_ts if available, otherwise event['ts']
            'button_payloads': button_payloads  # Assuming you're storing something like this for handling Voiceflow interactions
        }

        # Generate and send new blocks to Slack
        blocks, summary_text = create_message_blocks(voiceflow.get_responses(), button_payloads)
        say(blocks=blocks, text=summary_text, thread_ts=thread_ts)

@bolt_app.event("app_mention")
def handle_app_mention_events(event, say):
    # Check if the event is from the bot itself to avoid self-responses
    if event.get('user') == bot_user_id:
        return

    user_id = event['user']
    if 'thread_ts' in event:
        thread_ts = event['thread_ts']
    else:
        thread_ts = event['ts']
    user_input = event.get('text', '').strip().replace(f"<@{bot_user_id}>", "").strip()

    # Create a unique conversation ID using user_id and thread_ts
    conversation_id = f"{user_id}-{thread_ts}"
    
    processing_messages = [
        "On it!", "Sure thing!", "Got it!", "One moment...", "I'm on it!", "Absolutely!",
        "Understood!", "Just a moment...", "Right away!", "Affirmative!", "No problem!",
        "Okay!", "On my way!", "Certainly!", "Acknowledged!", "Will do!", "You got it!",
        "I'm at it!", "Working on it!", "Already on it!", "On top of it!", "I've got this!",
        "Taking care of it!", "All over it!"
    ]

    # Send a random processing message
    say(text=random.choice(processing_messages), thread_ts=thread_ts)

    combined_input = user_input

    # Process any file part of the message, if any
    files = event.get('files', [])
    if files:
        for file_info in files:
            file_url = file_info.get('url_private_download')
            file_type = file_info.get('filetype')
            if file_url:
                file_text = process_file(file_url, file_type)
                if file_text:
                    combined_input += "\n" + file_text

    print(combined_input)  # For debugging purposes

    # Handle conversation with Voiceflow
    if conversation_id in conversations:
        is_running, button_payloads = voiceflow.handle_user_input(conversation_id, combined_input)
    else:
        # Start a new conversation if not already ongoing
        is_running, button_payloads = voiceflow.handle_user_input(conversation_id, {'type': 'launch'})

    # Store or update the conversation state
    # Example of storing conversation details (adjust according to your actual logic)
    conversations[conversation_id] = {
        'channel': event['channel'],
        'user_id': event['user'],
        'thread_ts': event.get('thread_ts', event['ts']),  # Use thread_ts if available, otherwise event['ts']
        'button_payloads': button_payloads  # Assuming you're storing something like this for handling Voiceflow interactions
    }


    # Generate message blocks and summary text for Slack response
    blocks, summary_text = create_message_blocks(voiceflow.get_responses(), button_payloads)

    # Respond in the channel, replying in the thread if applicable
    say(blocks=blocks, text=summary_text, thread_ts=thread_ts)

@bolt_app.action(re.compile("voiceflow_button_"))
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