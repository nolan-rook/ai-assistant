import asyncio
import logging
import os
import re
import json
from contextlib import asynccontextmanager
from typing import Dict

from databases import Database
from fastapi import FastAPI, Request, BackgroundTasks
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp

from src.voiceflow_api import VoiceflowAPI
from src.utils import process_file, extract_webpage_content

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger('slack_bolt.AsyncApp').setLevel(logging.ERROR)

slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
bot_user_id = os.getenv("SLACK_BOT_USER_ID")

logging.info(f"Bot User ID from environment: {bot_user_id}")

# Initialize the Voiceflow API client
voiceflow = VoiceflowAPI()

# Database initialization
DATABASE_URL = os.getenv("DATABASE_URL")
database = Database(DATABASE_URL)

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    await database.connect()
    await database.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id VARCHAR(255) PRIMARY KEY,
            user_id VARCHAR(255),
            channel_id VARCHAR(255),
            thread_ts VARCHAR(255),
            button_payloads JSONB
        );
    """)
    try:
        yield
    finally:
        await database.disconnect()
    
# Initialize the Slack app
bolt_app = AsyncApp(token=slack_bot_token, signing_secret=slack_signing_secret)

# Initialize the FastAPI app
app = FastAPI(lifespan=app_lifespan)

async def send_delayed_message(say, delay, thread_ts, message="Just a moment..."):
    await asyncio.sleep(delay)
    await say(text=message, thread_ts=thread_ts)

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

async def process_message(event, say):
    user_id = event.get('user')
    channel_id = event.get('channel')
    thread_ts = event.get('thread_ts', event['ts'])
    user_input = event.get('text', '').strip()

    logging.info(f"Processing message from user {user_id} in channel {channel_id}, thread {thread_ts}")

    background_tasks = BackgroundTasks()
    background_tasks.add_task(send_delayed_message, say, 5, thread_ts)

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

    conversation = await database.fetch_one("SELECT * FROM conversations WHERE conversation_id = :conversation_id", values={"conversation_id": conversation_id})
    if conversation:
        is_running, button_payloads = await voiceflow.handle_user_input(conversation_id, combined_input)
    else:
        is_running, button_payloads = await voiceflow.handle_user_input(conversation_id, {'type': 'launch'})
        if is_running:
            is_running, button_payloads = await voiceflow.handle_user_input(conversation_id, combined_input)

    # Convert button_payloads to JSON string
    button_payloads_json = json.dumps(button_payloads)

    await database.execute("""
        INSERT INTO conversations (conversation_id, user_id, channel_id, thread_ts, button_payloads)
        VALUES (:conversation_id, :user_id, :channel_id, :thread_ts, :button_payloads)
        ON CONFLICT (conversation_id) DO UPDATE SET
            user_id = :user_id,
            channel_id = :channel_id,
            thread_ts = :thread_ts,
            button_payloads = :button_payloads
    """, values={
        "conversation_id": conversation_id,
        "user_id": user_id,
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "button_payloads": button_payloads_json  # Use the JSON string
    })

    blocks, summary_text = create_message_blocks(await voiceflow.get_responses(), button_payloads)
    logging.info(f"Sending blocks: {blocks}, summary_text: {summary_text}, thread_ts: {thread_ts}")
    await say(blocks=blocks, text=summary_text, thread_ts=thread_ts)

@bolt_app.event("app_mention")
async def handle_app_mention_events(event, say):
    logging.info(f"Received app_mention event: {event}")
    if event.get('user') == bot_user_id:
        return

    thread_ts = event.get('thread_ts', event['ts'])
    channel_id = event['channel']

    await database.execute("""
        INSERT INTO conversations (conversation_id, channel_id, thread_ts)
        VALUES (:conversation_id, :channel_id, :thread_ts)
        ON CONFLICT (conversation_id) DO NOTHING
    """, values={
        "conversation_id": f"{channel_id}-{thread_ts}",
        "channel_id": channel_id,
        "thread_ts": thread_ts
    })

    await process_message(event, say)

@bolt_app.event("message")
async def handle_message_events(event, say):
    logging.info(f"Received message event: {event}")
    if event.get('user') == bot_user_id:
        return

    if event.get('channel_type') == 'im':
        await process_message(event, say)

    thread_ts = event.get('thread_ts', event.get('ts'))
    is_threaded = bool(event.get('thread_ts'))

    if is_threaded:
        conversation = await database.fetch_one("SELECT * FROM conversations WHERE thread_ts = :thread_ts", values={"thread_ts": thread_ts})
        if conversation:
            await process_message(event, say)

@bolt_app.action(re.compile("voiceflow_button_"))
async def handle_voiceflow_button(ack, body, client, say, logger):
    await ack()  # Acknowledge the action
    action_id = body['actions'][0]['action_id']
    user_id = body['user']['id']
    channel_id = body['channel']['id']
    message_ts = body['message']['ts']  # Timestamp of the original message
    thread_ts = body['message'].get('thread_ts', body['message']['ts'])

    # Create a unique conversation ID using user_id and thread_ts
    conversation_id = f"{channel_id}-{thread_ts}"

    # Extract the index from the action_id
    button_index = int(action_id.split("_")[-1])

    conversation = await database.fetch_one("SELECT * FROM conversations WHERE conversation_id = :conversation_id", values={"conversation_id": conversation_id})
    if conversation:
        # Get the button_payloads field from the conversation object
        button_payloads_json = conversation.button_payloads

        if button_payloads_json:
            # Parse the JSON string back into a dictionary
            button_payloads = json.loads(button_payloads_json)
            button_payload = button_payloads.get(str(button_index + 1))

            if button_payload:
                # Process the button action to advance the conversation
                is_running, new_button_payloads = await voiceflow.handle_user_input(conversation_id, button_payload)

                # Convert the new button payloads to JSON string
                new_button_payloads_json = json.dumps(new_button_payloads)

                # Update the conversation in the database with the new button payloads
                await database.execute("""
                    UPDATE conversations
                    SET button_payloads = :button_payloads
                    WHERE conversation_id = :conversation_id
                """, values={
                    "button_payloads": new_button_payloads_json,
                    "conversation_id": conversation_id
                })

                # Update the message to remove the buttons
                try:
                    original_blocks = body['message'].get('blocks', [])
                    updated_blocks = [block for block in original_blocks if block['type'] != 'actions']
                    await client.chat_update(
                        channel=channel_id,
                        ts=message_ts,
                        text="Selected an option",  # Add a fallback text
                        blocks=updated_blocks
                    )
                except Exception as e:
                    logger.error(f"Failed to update message: {e}")

                # Send a new message reflecting the next stage in the conversation
                if is_running:
                    blocks, summary_text = create_message_blocks(await voiceflow.get_responses(), new_button_payloads)
                    await client.chat_postMessage(channel=channel_id, blocks=blocks, text=summary_text, thread_ts=thread_ts)

            else:
                # Respond in the correct thread if the choice wasn't understood
                await client.chat_postMessage(channel=channel_id, text="Sorry, I didn't understand that choice.", thread_ts=thread_ts)
        else:
            # Respond in the correct thread if no button payloads were found
            await client.chat_postMessage(channel=channel_id, text="Sorry, I couldn't find the button options for this conversation.", thread_ts=thread_ts)
    else:
        # Respond in the correct thread if no conversation was found
        await client.chat_postMessage(channel=channel_id, text="Sorry, I couldn't find your conversation.", thread_ts=thread_ts)

async def notify_user_completion(conversation_id, document_id):
    conversation = await database.fetch_one("SELECT * FROM conversations WHERE conversation_id = :conversation_id", values={"conversation_id": conversation_id})
    if conversation:
        channel_id = conversation['channel_id']
        user_id = conversation['user_id']
        thread_ts = conversation['thread_ts']

        completion_message = f"Hey <@{user_id}>! 🎉 I've just finished crafting your requested document. Take a peek at the following link https://docs.google.com/document/d/{document_id} and let us know your thoughts!"

        try:
            await bolt_app.client.chat_postMessage(
                channel=channel_id,
                text=completion_message,
                thread_ts=thread_ts
            )
        except Exception as e:
            print(f"Error sending completion notification: {e}")

@app.post("/task-completed")
async def task_completed(data: Dict):
    conversation_id = data.get('conversation_id')
    document_id = data.get('document_id')
    if conversation_id:
        await notify_user_completion(conversation_id, document_id)
        return {"status": "success"}
    else:
        return {"status": "error", "message": "Missing conversation_id"}

async def notify_user_start(conversation_id):
    conversation = await database.fetch_one("SELECT * FROM conversations WHERE conversation_id = :conversation_id", values={"conversation_id": conversation_id})
    if conversation:
        channel_id = conversation['channel_id']
        thread_ts = conversation['thread_ts']

        start_message = "Thank you, I will start working on it. I will notify you when I'm done. It will take around 10-15 minutes."

        try:
            await bolt_app.client.chat_postMessage(
                channel=channel_id,
                text=start_message,
                thread_ts=thread_ts
            )
        except Exception as e:
            print(f"Error sending start notification: {e}")

@app.post("/task-started")
async def task_started(data: Dict):
    conversation_id = data.get('conversation_id')
    if conversation_id:
        await notify_user_start(conversation_id)
        return {"status": "success", "message": "Task start notification sent"}
    else:
        return {"status": "error", "message": "Missing conversation_id"}

# Mount the Slack request handler
slack_handler = AsyncSlackRequestHandler(bolt_app)

@app.post("/slack/events")
async def slack_events(req: Request):
    return await slack_handler.handle(req)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ["PORT"]))