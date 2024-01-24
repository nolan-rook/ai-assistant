from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from voiceflow_api import VoiceflowAPI

import os
# Load environment variables
from dotenv import load_dotenv
load_dotenv()
import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from dotenv import load_dotenv
load_dotenv()
slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
slack_app_token = os.getenv("SLACK_APP_TOKEN")
# Install the Slack app and get xoxb- token in advance
app = App(token=slack_bot_token)
# Initialize the Voiceflow API client
voiceflow = VoiceflowAPI()

# Stores the ongoing conversations with Voiceflow
conversations = {}

def create_message_blocks(text_responses, button_payloads):
    print("Button Payloads:", button_payloads)
    blocks = []

    # Add text responses as section blocks
    for text in text_responses:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text
            }
        })

    # Add buttons as section blocks with accessories
    for idx, (button_value, button_payload) in enumerate(button_payloads.items()):
        button_text = button_payload['payload']['label']  # Use 'label' instead of 'name'
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{button_text}"
            },
            "accessory": {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": button_text,
                    "emoji": True
                },
                "value": button_value,
                "action_id": "voiceflow_button"
            }
        })

    return blocks



@app.event("message")
def handle_dm_events(event, say):
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
        blocks = create_message_blocks(voiceflow.get_responses(), button_payloads)
        say(blocks=blocks)


@app.action("voiceflow_button")
def handle_button_click(ack, body, client, say):
    ack()
    action_value = body['actions'][0]['value']
    user_id = body['user']['id']

    if user_id in conversations:
        # Retrieve the payload for the button pressed
        button_payloads = conversations[user_id]['button_payloads']
        button_payload = button_payloads.get(action_value)

        if button_payload:
            # Handle the button press
            is_running, new_button_payloads = voiceflow.handle_user_input(button_payload)
            conversations[user_id]['button_payloads'] = new_button_payloads

            # Generate and send new blocks to Slack
            blocks = create_message_blocks(voiceflow.get_responses(), new_button_payloads)
            say(blocks=blocks)
        else:
            say(text="Sorry, I didn't understand that choice.")
    else:
        say(text="Sorry, I couldn't find your conversation.")


if __name__ == "__main__":
    SocketModeHandler(app, slack_app_token).start()