from flask import Flask, request, jsonify
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler

from voiceflow_api import VoiceflowAPI
from utils import process_file, extract_webpage_content

import re
import os
import random

import time
from threading import Timer

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

def send_delayed_message(say, delay, thread_ts, message="Just a moment..."):
    def delayed_action():
        # Send the processing message
        say(text=message, thread_ts=thread_ts)
    
    # Create a Timer object that waits for the specified delay before executing the delayed_action
    timer = Timer(delay, delayed_action)
    timer.start()
    return timer

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

    timer_thread = send_delayed_message(say, 5, thread_ts)
    
    if 'app_mention' in event['type']:
        user_input = re.sub(r"<@U[A-Z0-9]+>", "", user_input, count=1).strip()

    conversation_id = f"{channel_id}-{thread_ts}"
    
    logging.info(f"Processing in conversation {conversation_id}")
    
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
        try:
            webpage_text = extract_webpage_content(url)
            if webpage_text:
                combined_input += "\n" + webpage_text
        except Exception as e:
            logging.error(f"Error reading URL {url}: {str(e)}")
            # Optionally, you could append a message indicating the URL was skipped
            combined_input += "\n[Note: A URL was not loaded properly and has been skipped.]"


    print(combined_input)  # For debugging
    
    if conversation_id in conversations:
        is_running, button_payloads = voiceflow.handle_user_input(conversation_id, combined_input)
    else:
        is_running, button_payloads = voiceflow.handle_user_input(conversation_id, {'type': 'launch'})
        if is_running:
            is_running, button_payloads = voiceflow.handle_user_input(conversation_id, combined_input)

    # Check if the timer thread is still alive and cancel if so
    if timer_thread.is_alive():
        timer_thread.cancel()

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
        # print(button_payload)
        # # Detect if this is the "Blog posts" action
        # if button_payload['payload']['label'] == "Blog posts":
        #     trigger_id = body['trigger_id']
        #     # Define the modal content here
        #     modal = {
        #         "type": "modal",
        #         "callback_id": "blog_posts_modal",
        #         "title": {"type": "plain_text", "text": "Blog Posts"},
        #         "blocks": [
        #             {
        #                 "type": "input",
        #                 "block_id": "blog_input",
        #                 "element": {"type": "plain_text_input", "action_id": "blog_text", "multiline": True},
        #                 "label": {"type": "plain_text", "text": "Enter blog content"}
        #             }
        #         ],
        #         "submit": {"type": "plain_text", "text": "Submit"}
        #     }
        #     # Open the modal
        #     client.views_open(trigger_id=trigger_id, view=modal)
        #     return  # Exit the function to prevent further processing for blog posts action

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

@bolt_app.view("blog_posts_modal")
def handle_modal_submission(ack, body, view, client):
    ack()
    # Extract the input data
    blog_content = view['state']['values']['blog_input']['blog_text']['value']
    # Here you can process the blog content, for example, saving it to a database or posting somewhere

    # Assuming you have the user's ID and the channel ID you want to post the confirmation to
    user_id = body['user']['id']
    # Optionally, you can use a specific channel ID if you want the confirmation to be public
    # channel_id = 'C1234567890' # Example channel ID

    # Construct the confirmation message
    confirmation_message = "Thank you for submitting your blog post. We've received it and are processing your request."

    # Send the confirmation message to the user
    try:
        client.chat_postMessage(
            channel=user_id, # Direct message to the user
            # For public confirmation, use 'channel=channel_id' instead of 'channel=user_id'
            text=confirmation_message
        )
    except Exception as e:
        print(f"Error sending confirmation message: {e}")
        
def notify_user_completion(conversation_id, document_id):
    conversation_details = conversations.get(conversation_id)
    if conversation_details:
        channel_id = conversation_details['channel']
        # This assumes you have stored 'user_id' when the conversation started
        user_id = conversation_details['user_id'] 
        thread_ts = conversation_details['thread_ts']  # The thread timestamp for replying in thread

        # Construct the notification message, tagging the user
        completion_message = f"Hey <@{user_id}>! 🎉 I've just finished crafting your requested document. Take a peek at the following link https://docs.google.com/document/d/{document_id} and let us know your thoughts!"

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
    document_id = data.get('document_id')
    if conversation_id:
        # Assuming you implement a way to notify users, for example:
        notify_user_completion(conversation_id, document_id)
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Missing conversation_id"}), 400
          
# Start your app
if __name__ == "__main__":
    flask_app.run(host='0.0.0.0', port=3000)