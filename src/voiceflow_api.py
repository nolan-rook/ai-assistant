import requests
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
        self.user_id = os.getenv('VOICEFLOW_USER_ID', 'default_user')
        self.last_message = None
        self.all_responses = []

    def interact(self, request):
        """Interact with the Voiceflow API and handle the response."""
        response = requests.post(
            url=f"{self.runtime_endpoint}/state/{self.version_id}/user/{self.user_id}/interact",
            json={'request': request},
            headers={'Authorization': self.api_key},
        )
        response.raise_for_status()  # Raise an exception for HTTP errors
        self.all_responses = []
        return self.parse_response(response.json())

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

    def handle_user_input(self, user_input):
        """Handles user input by sending text or button payload to Voiceflow."""
        # Check if the input is a dictionary, which indicates a button payload
        if isinstance(user_input, dict):
            # User input is a button payload
            return self.interact(user_input)
        else:
            # User input is regular text
            return self.interact({'type': 'text', 'payload': user_input})

    def get_last_response(self):
        """Return the last message from Voiceflow."""
        return self.last_message
    
    def get_responses(self):
        """Return all text/speak responses from the current interaction with Voiceflow."""
        return self.all_responses

    def handle_button_input(self, button_text):
        """Handles the interaction with Voiceflow when a button is pressed."""
        # Construct the payload expected by Voiceflow for a button press
        button_payload = {'type': 'text', 'payload': button_text}
        return self.interact(button_payload)
