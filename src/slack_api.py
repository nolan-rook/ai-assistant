from slack_sdk import WebClient
import os

from dotenv import load_dotenv
load_dotenv()

slack_client = None
bot_user_id = None

def init_slack_client():
    global bot_user_id, slack_client
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if slack_token is None:
        raise ValueError("SLACK_BOT_TOKEN environment variable not set")
    slack_client = WebClient(token=slack_token)
    bot_user_id = slack_client.auth_test()['user_id']