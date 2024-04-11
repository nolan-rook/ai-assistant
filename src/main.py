import os
import re
import logging
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, HTTPException
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_sdk.errors import SlackApiError
from databases import Database
from src.voiceflow_api import VoiceflowAPI
from src.utils import process_file, extract_webpage_content  # Ensure these are async too

# Load environment variables
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Slack bot credentials
slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
bot_user_id = os.getenv("SLACK_BOT_USER_ID")

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
            state TEXT,
            user_id VARCHAR(255),
            channel_id VARCHAR(255),
            thread_ts VARCHAR(255)
        );
    """)
    yield
    await database.disconnect()

app = FastAPI(lifespan=app_lifespan)
bolt_app = AsyncApp(token=slack_bot_token, signing_secret=slack_signing_secret)
slack_handler = AsyncSlackRequestHandler(bolt_app)

@app.post("/slack/events")
async def slack_events(request: Request):
    return await slack_handler.handle(request)

@bolt_app.event("message")
async def handle_message_events(event, say):
    if event.get('user') == bot_user_id:
        return

    user_id = event.get('user')
    channel_id = event.get('channel')
    thread_ts = event.get('thread_ts', event['ts'])
    user_input = event.get('text', '').strip()

    if 'app_mention' in event['type']:
        user_input = re.sub(r"<@U[A-Z0-9]+>", "", user_input, count=1).strip()

    conversation_id = f"{channel_id}-{thread_ts}"
    combined_input = await handle_user_input(event, user_input)

    try:
        delay_task = asyncio.create_task(send_delayed_message(say, 5, thread_ts))
        response = await process_voiceflow_interaction(conversation_id, combined_input, user_id=event["user"], channel_id=event["channel"], thread_ts=thread_ts)
        delay_task.cancel()  # Cancel the delayed message if processing finishes in time
        if response:
            await say(blocks=response["blocks"], text=response["summary_text"], thread_ts=thread_ts)
    except Exception as e:
        logging.error(f"Error processing voiceflow interaction: {str(e)}")
        if not delay_task.cancelled():
            delay_task.cancel()
        await say(text="Failed to process your request.", thread_ts=thread_ts)

async def send_delayed_message(say, delay, thread_ts, message="Just a moment..."):
    try:
        await asyncio.sleep(delay)
        await say(text=message, thread_ts=thread_ts)
    except SlackApiError as e:
        logging.error(f"Failed to send delayed message: {str(e)}")

async def handle_user_input(event, user_input):
    combined_input = user_input
    files = event.get('files', [])

    for file_info in files:
        file_url = file_info.get('url_private_download')
        file_type = file_info.get('filetype')
        if file_url:
            file_text = await process_file(file_url, file_type)
            combined_input += "\n" + file_text if file_text else ""

    urls = re.findall(r'<http[s]?://[^\s]+>', user_input)
    for url in urls:
        clean_url = url.strip('<>')
        webpage_text = await extract_webpage_content(clean_url)
        combined_input += "\n" + webpage_text if webpage_text else "[URL content could not be loaded]"

    return combined_input

async def process_voiceflow_interaction(conversation_id, input_text, user_id, channel_id, thread_ts):
    query = "SELECT state FROM conversations WHERE conversation_id = :conversation_id"
    result = await database.fetch_one(query=query, values={"conversation_id": conversation_id})
    state = result["state"] if result else "new"

    is_running, button_payloads = await voiceflow.handle_user_input(conversation_id, input_text if state != "new" else {'type': 'launch'})
    
    if is_running:
        await database.execute(
            "INSERT INTO conversations (conversation_id, state, user_id, channel_id, thread_ts) VALUES (:conversation_id, :state, :user_id, :channel_id, :thread_ts) ON CONFLICT (conversation_id) DO UPDATE SET state = :state",
            {"conversation_id": conversation_id, "state": "active", "user_id": user_id, "channel_id": channel_id, "thread_ts": thread_ts}
        )

    responses = voiceflow.get_responses()
    logging.info(f"Voiceflow responses: {responses}")

    blocks, summary_text = create_message_blocks(responses, button_payloads)
    return {"blocks": blocks, "summary_text": summary_text}

def create_message_blocks(text_responses, button_payloads):
    blocks = []
    # Iterate through all text responses and add them to message blocks
    for text in text_responses:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text
            }
        })

    # Add buttons if they exist
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": payload["label"], "emoji": True},
            "value": str(idx),
            "action_id": f"voiceflow_button_{idx}"
        } for idx, payload in enumerate(button_payloads.values(), start=1)
    ]

    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
        summary_text = "Select an option:"  # This text is used for notifications
    else:
        summary_text = text_responses[-1]  # Fallback to the last message if no buttons

    return blocks, summary_text

@app.post("/task-started")
async def task_started():
    data = await request.json
    conversation_id = data.get('conversation_id')
    await notify_user_start(conversation_id)
    return {"status": "success"}

@app.post("/task-completed")
async def task_completed(request: Request):
    data = await request.json()
    conversation_id = data.get('conversation_id')
    document_id = data.get('document_id')

    if not (conversation_id and document_id):
        raise HTTPException(status_code=400, detail="Missing conversation_id or document_id")

    await notify_user_completion(conversation_id, document_id)
    return {"status": "success"}

async def notify_user_start(conversation_id):
    query = "SELECT channel_id, user_id FROM conversations WHERE conversation_id = :conversation_id"
    result = await database.fetch_one(query, {"conversation_id": conversation_id})
    if result:
        channel_id = result['channel_id']
        thread_ts = result['thread_ts']  # The thread timestamp for replying in thread

        # Construct the start notification message, tagging the user
        start_message = "Thankyou, I will start working on it. I will notify you when I'm done. It will take around 10-15 minutes."
        await bolt_app.client.chat_postMessage(channel=channel_id, text=start_message, thread_ts=thread_ts)


async def notify_user_completion(conversation_id, document_id):
    query = "SELECT channel_id, user_id FROM conversations WHERE conversation_id = :conversation_id"
    result = await database.fetch_one(query, {"conversation_id": conversation_id})
    if result:
        channel_id = result["channel_id"]
        user_id = result["user_id"]
        thread_ts = result['thread_ts']
        message = f"Hey <@{user_id}>! Your document is ready: [Document Link](https://docs.google.com/document/d/{document_id})"
        await bolt_app.client.chat_postMessage(channel=channel_id, text=message, thread_ts=thread_ts)
