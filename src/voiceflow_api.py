import aiohttp
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class VoiceflowAPI:
    def __init__(self):
        self.api_key = os.getenv('VOICEFLOW_API_KEY')
        if self.api_key is None:
            raise ValueError("VOICEFLOW_API_KEY environment variable not set")
        self.runtime_endpoint = os.getenv('VOICEFLOW_RUNTIME_ENDPOINT', 'https://general-runtime.voiceflow.com')
        self.version_id = os.getenv('VOICEFLOW_VERSION_ID', 'production')
        self.project_id = os.getenv('VOICEFLOW_PROJECT_ID')
        self.last_message = None
        self.all_responses = []

    async def interact(self, conversation_id, request):
        """Interact with the Voiceflow API and handle the response."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url=f"{self.runtime_endpoint}/state/{self.version_id}/user/{conversation_id}/interact",
                json={'request': request},
                headers={'Authorization': self.api_key},
            ) as response:
                response.raise_for_status()  # Raise an exception for HTTP errors
                self.all_responses = []
                return self.parse_response(await response.json())

    async def create_transcript(self, conversation_id):
        """Create a transcript using the Voiceflow Transcript API."""
        url = "https://api.voiceflow.com/v2/transcripts"
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "versionID": self.version_id,
            "sessionID": conversation_id,
            "projectID": self.project_id
        }
        async with aiohttp.ClientSession() as session:
            async with session.put(url, json=payload, headers=headers) as response:
                response.raise_for_status()  # Raise an exception for HTTP errors
                return await response.json()

    def parse_response(self, response_data):
        """Parse the response data from Voiceflow."""
        button_payloads = {}
        should_continue = True

        for trace in response_data:
            if trace['type'] == 'speak' or trace['type'] == 'text':
                message = trace['payload']['message']
                self.last_message = message
                self.all_responses.append(message)  # Store the message
            elif trace['type'] == 'choice':
                for idx, choice in enumerate(trace['payload']['buttons']):
                    button_text = choice['name']
                    button_payloads[str(idx + 1)] = choice['request']
            elif trace['type'] == 'end':
                should_continue = False

        return should_continue, button_payloads

    async def handle_user_input(self, conversation_id, user_input):
        """Handles user input by sending text or button payload to Voiceflow."""
        if isinstance(user_input, dict):
            # User input is a button payload
            return await self.interact(conversation_id, user_input)
        else:
            # User input is regular text
            return await self.interact(conversation_id, {'type': 'text', 'payload': user_input})

    def get_last_response(self):
        """Return the last message from Voiceflow."""
        return self.last_message
    
    def get_responses(self):
        """Return all text/speak responses from the current interaction with Voiceflow."""
        return self.all_responses