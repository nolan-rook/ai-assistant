from fastapi import FastAPI, Request, Response, status
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from src.voiceflow_api import VoiceflowAPI
from src.utils import process_file, extract_webpage_content, create_message_blocks

import re
import os
import asyncio
import logging

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.extras import Json

from cachetools import TTLCache
import hashlib

# Initialize a cache with a time-to-live (TTL) of 60 seconds
processed_events = TTLCache(maxsize=1000, ttl=60)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger('slack_bolt.AsyncApp').setLevel(logging.ERROR)

slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
bot_user_id = os.getenv("SLACK_BOT_USER_ID")
database_url = os.getenv("DATABASE_URL")

# Database connection function
def get_db_connection():
    conn = psycopg2.connect(database_url)
    return conn

logging.info(f"Bot User ID from environment: {bot_user_id}")

# Install the Slack app and get xoxb- token in advance
bolt_app = AsyncApp(token=slack_bot_token, signing_secret=slack_signing_secret)

# FastAPI app to handle webhook routes
app = FastAPI()
slack_handler = AsyncSlackRequestHandler(bolt_app)

# Initialize the Voiceflow API client
voiceflow = VoiceflowAPI()

@app.post("/slack/events")
async def slack_events(request: Request):
    return await slack_handler.handle(request)

@bolt_app.event("app_home_opened")
async def handle_app_home_opened(body, logger):
    logger.info("App home opened event received")
    # Add additional logic here if needed


async def process_message(event, say):
    user_id = event.get('user')
    channel_id = event.get('channel')
    thread_ts = event.get('thread_ts', event['ts'])
    user_input = event.get('text', '').strip()

    logging.info(f"Processing message from user {user_id} in channel {channel_id}, thread {thread_ts}")
    
    async def send_response(user_input):
        if 'app_mention' in event['type']:
            user_input = re.sub(r"<@U[A-Z0-9]+>", "", user_input, count=1).strip()

        conversation_id = f"{channel_id}-{thread_ts}"
        logging.info(f"Processing in conversation {conversation_id}")

        combined_input = user_input
        files = event.get('files', [])

        if files:
            for file_info in files:
                file_url = file_info.get('url_private_download')
                file_type = file_info.get('filetype')
                if file_url:
                    result = await process_file(file_url, file_type)  # Call only once per file
                    if result and file_type == 'mp4':
                        await bolt_app.client.files_upload(
                            channels=channel_id,
                            file=result,
                            title="Transcription",
                            filetype='text',
                            filename='transcription.txt'
                        )
                        return
                    elif result:  # This handles other file types that may have text to append
                        combined_input += "\n" + result

        urls = re.findall(r'<http[s]?://[^>]+>', user_input)
        for url in urls:
            url = url[1:-1]  # Strip angle brackets
            try:
                webpage_text = extract_webpage_content(url)
                if webpage_text:
                    combined_input += "\n" + webpage_text
            except Exception as e:
                logging.error(f"Error reading URL {url}: {str(e)}")
                combined_input += "\n[Note: A URL was not loaded properly and has been skipped.]"                   
        # Database interaction to fetch or create conversation                        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT button_payloads, transcript_created FROM conversations WHERE conversation_id = %s",
                    (conversation_id,)
                )
                existing_conversation = cur.fetchone()

                if existing_conversation:
                    button_payloads, transcript_created = existing_conversation
                    if not transcript_created:
                        # Create transcript if it hasn't been created yet
                        transcript_response = await voiceflow.create_transcript(conversation_id)
                        logging.info(f"Transcript created: {transcript_response}")
                        cur.execute(
                            "UPDATE conversations SET transcript_created = TRUE WHERE conversation_id = %s",
                            (conversation_id,)
                        )

                    # Process user input with Voiceflow
                    voiceflow_task = asyncio.create_task(voiceflow.handle_user_input(conversation_id, combined_input))
                    try:
                        is_running, button_payloads = await asyncio.wait_for(asyncio.shield(voiceflow_task), timeout=5.0)
                    except asyncio.TimeoutError:
                        await say(text="Just a moment...", thread_ts=thread_ts)
                    finally:
                        is_running, button_payloads = await voiceflow_task

                    # Update conversation with new button payloads
                    cur.execute(
                        "UPDATE conversations SET button_payloads = %s WHERE conversation_id = %s",
                        (Json(button_payloads), conversation_id)
                    )
                else:
                    # Create transcript for new conversation
                    transcript_response = await voiceflow.create_transcript(conversation_id)
                    logging.info(f"Transcript created: {transcript_response}")

                    # Launch new conversation in Voiceflow
                    voiceflow_task_launch = asyncio.create_task(voiceflow.handle_user_input(conversation_id, {'type': 'launch'}))
                    try:
                        is_running, button_payloads = await asyncio.wait_for(asyncio.shield(voiceflow_task_launch), timeout=5.0)
                    except asyncio.TimeoutError:
                        await say(text="Just a moment...", thread_ts=thread_ts)
                    finally:
                        is_running, button_payloads = await voiceflow_task_launch

                    if is_running:
                        # Continue conversation with user input
                        voiceflow_task_input = asyncio.create_task(voiceflow.handle_user_input(conversation_id, combined_input))
                        try:
                            is_running, button_payloads = await asyncio.wait_for(asyncio.shield(voiceflow_task_input), timeout=5.0)
                        except asyncio.TimeoutError:
                            await say(text="Just a moment...", thread_ts=thread_ts)
                        finally:
                            is_running, button_payloads = await voiceflow_task_input

                    # Insert new conversation into database
                    cur.execute(
                        "INSERT INTO conversations (conversation_id, user_id, channel_id, thread_ts, button_payloads, transcript_created) VALUES (%s, %s, %s, %s, %s, TRUE)",
                        (conversation_id, user_id, channel_id, thread_ts, Json(button_payloads))
                    )
        blocks, summary_text = create_message_blocks(voiceflow.get_responses(), button_payloads)
        logging.info(f"Sending blocks: {blocks}, summary_text: {summary_text}, thread_ts: {thread_ts}")
        await say(blocks=blocks, text=summary_text, thread_ts=thread_ts)
    
    try:
        await send_response(user_input)
    except Exception as e:
        logging.error(f"Error processing message: {e}")
        await say(text="An error occurred while processing your request.", thread_ts=thread_ts)

@bolt_app.event("app_mention")
async def handle_app_mention_events(event, say):
    event_id = hashlib.sha256(f"{event['user']}-{event['channel']}-{event['ts']}".encode()).hexdigest()
    if event_id in processed_events:
        return

    processed_events[event_id] = True
    if event.get('user') == bot_user_id:
        return

    # Check if the event has already been processed by handle_message_events
    if event.get('channel_type') != 'im' and not bool(event.get('thread_ts')):
        await process_message(event, say)
    
@bolt_app.event("message")
async def handle_message_events(event, say):
    event_id = hashlib.sha256(f"{event['user']}-{event['channel']}-{event['ts']}".encode()).hexdigest()
    if event_id in processed_events:
        return

    processed_events[event_id] = True
    # Ignore messages from the bot itself to avoid loops
    if event.get('user') == bot_user_id:
        return
    
    if event.get('channel_type') == 'im':
        await process_message(event, say)
    else:
        # Extract the necessary identifiers from the event
        thread_ts = event.get('thread_ts', event.get('ts'))
        is_threaded = bool(event.get('thread_ts'))
        channel_id = event.get('channel')

        # Check if the message is part of a thread that the bot is involved in
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM conversations WHERE conversation_id = %s",
                    (f"{channel_id}-{thread_ts}",)
                )
                conversation_exists = cur.fetchone()

        if is_threaded and conversation_exists:
            # Process the message as part of the ongoing conversation
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

    # Database interaction to fetch and update conversation
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT button_payloads FROM conversations WHERE conversation_id = %s",
                (conversation_id,)
            )
            existing_conversation = cur.fetchone()

            if existing_conversation:
                button_payloads = existing_conversation[0]
                button_payload = button_payloads.get(str(button_index + 1))

                if button_payload:
                    # Process the button action to advance the conversation
                    is_running, new_button_payloads = await voiceflow.handle_user_input(conversation_id, button_payload)
                    cur.execute(
                        "UPDATE conversations SET button_payloads = %s WHERE conversation_id = %s",
                        (Json(new_button_payloads), conversation_id)
                    )

                    # Update the message to remove the buttons
                    try:
                        original_blocks = body['message'].get('blocks', [])
                        updated_blocks = [block for block in original_blocks if block['type'] != 'actions']
                        await client.chat_update(
                            channel=channel_id,
                            ts=message_ts,
                            blocks=updated_blocks
                        )
                    except Exception as e:
                        logger.error(f"Failed to update message: {e}")

                    # Send a new message reflecting the next stage in the conversation
                    if is_running:
                        blocks, summary_text = create_message_blocks(voiceflow.get_responses(), new_button_payloads)
                        await client.chat_postMessage(channel=channel_id, blocks=blocks, text=summary_text, thread_ts=thread_ts)
                else:
                    # Respond in the correct thread if the choice wasn't understood
                    await client.chat_postMessage(channel=channel_id, text="Sorry, I didn't understand that choice.", thread_ts=thread_ts)
            else:
                # Respond in the correct thread if no conversation was found
                await client.chat_postMessage(channel=channel_id, text="Sorry, I couldn't find your conversation.", thread_ts=thread_ts)

async def notify_user_completion(conversation_id, document_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, channel_id, thread_ts FROM conversations WHERE conversation_id = %s",
                (conversation_id,)
            )
            conversation_details = cur.fetchone()

    if conversation_details:
        user_id, channel_id, thread_ts = conversation_details

        # Construct the notification message, tagging the user
        completion_message = f"Hey <@{user_id}>! 🎉 I've just finished crafting your requested document. Take a peek at the following link https://docs.google.com/document/d/{document_id} and let us know your thoughts!"

        # Use the correct Bolt app instance to send the message
        try:
            await bolt_app.client.chat_postMessage(
                channel=channel_id, 
                text=completion_message, 
                thread_ts=thread_ts  # Ensure the message is sent as a reply in the thread
            )
        except Exception as e:
            logging.info(f"Error sending completion notification: {e}")

# Correctly define the /task-completed endpoint within Flask app context
@app.post("/task-completed")
async def task_completed(request: Request):
    data = await request.json()
    conversation_id = data.get('conversation_id')
    document_id = data.get('document_id')
    if conversation_id:
        await notify_user_completion(conversation_id, document_id)
        return {"status": "success"}
    else:
        return {"status": "error", "message": "Missing conversation_id"}

async def notify_user_start(conversation_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT channel_id, thread_ts FROM conversations WHERE conversation_id = %s",
                (conversation_id,)
            )
            conversation_details = cur.fetchone()

    if conversation_details:
        channel_id, thread_ts = conversation_details

        # Construct the start notification message, tagging the user
        start_message = "Thankyou, I will start working on it. I will notify you when I'm done. It will take around 10-15 minutes."

        # Use the correct Bolt app instance to send the message
        try:
            await bolt_app.client.chat_postMessage(
                channel=channel_id, 
                text=start_message, 
                thread_ts=thread_ts  # Ensure the message is sent as a reply in the thread
            )
        except Exception as e:
            logging.info(f"Error sending start notification: {e}")

@app.post("/task-started")
async def task_started(request: Request):
    data = await request.json()
    conversation_id = data.get('conversation_id')
    if conversation_id:
        await notify_user_start(conversation_id)
        return {"status": "success", "message": "Task start notification sent"}
    else:
        return {"status": "error", "message": "Missing conversation_id"}