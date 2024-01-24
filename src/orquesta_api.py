from orquesta_sdk import Orquesta, OrquestaClientOptions
import os

from dotenv import load_dotenv
load_dotenv() 

client = None

def init_orquesta_client():
    global client
    api_key = os.getenv("ORQUESTA_API_KEY")
    options = OrquestaClientOptions(api_key=api_key, environment="production")
    client = Orquesta(options)