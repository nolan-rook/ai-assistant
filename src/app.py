from slack_bolt import App

from voiceflow_api import VoiceflowAPI

import re
import os

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
bot_user_id = os.getenv("SLACK_BOT_USER_ID") 

# Install the Slack app and get xoxb- token in advance
app = App(token=slack_bot_token,
          signing_secret=slack_signing_secret)

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
    if event.get('user') == bot_user_id:
        return  # Ignore the event if it's from the bot
    if event.get('channel_type') == 'im':
        user_id = event['user']
        user_input = event.get('text', '')

        # Check if there's an ongoing conversation
        if user_id in conversations:
            # There's an ongoing conversation, so handle the user's input
            is_running, button_payloads = voiceflow.handle_user_input(user_input)
        else:
            # This is the start of a new conversation
            # First, send a "launch" request to start the conversation
            is_running, button_payloads = voiceflow.handle_user_input({'type': 'launch'})
            # Then, send the user's actual message to Voiceflow
            if user_input.lower() != "hi":  # Check if the user input is not just a greeting to avoid double messages
                is_running, button_payloads = voiceflow.handle_user_input(user_input)

        # Store the conversation state
        conversations[user_id] = {'channel': event['channel'], 'button_payloads': button_payloads}

        # Generate and send new blocks to Slack
        blocks, summary_text = create_message_blocks(voiceflow.get_responses(), button_payloads)
        say(blocks=blocks, text=summary_text)


@app.action(re.compile("voiceflow_button_"))  # This will match any action_id starting with 'voiceflow_button_'
def handle_voiceflow_button(ack, body, client, say, logger):
    ack()  # Acknowledge the action
    action_id = body['actions'][0]['action_id']
    user_id = body['user']['id']

    # Extract the index from the action_id (e.g., 'voiceflow_button_0' -> 0)
    button_index = int(action_id.split("_")[-1])

    if user_id in conversations:
        # Retrieve the payload for the button pressed
        button_payloads = conversations[user_id]['button_payloads']
        # Fetch the corresponding button payload using the index
        button_payload = button_payloads.get(str(button_index + 1))

        if button_payload:
            # Handle the button press
            is_running, new_button_payloads = voiceflow.handle_user_input(button_payload)
            conversations[user_id]['button_payloads'] = new_button_payloads

            # Generate and send new blocks to Slack
            blocks, summary_text = create_message_blocks(voiceflow.get_responses(), new_button_payloads)
            say(blocks=blocks, text=summary_text)
        else:
            say(text="Sorry, I didn't understand that choice.")
    else:
        say(text="Sorry, I couldn't find your conversation.")
          
# Start your app
if __name__ == "__main__":
    app.start(port=int(os.getenv("PORT", 3000)))