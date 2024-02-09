import logging
from io import BytesIO
from pdfminer.high_level import extract_text
from docx import Document
from pptx import Presentation
import requests
from bs4 import BeautifulSoup
import os

def download_file(file_url):
    headers = {'Authorization': f'Bearer {os.getenv("SLACK_BOT_TOKEN")}'}
    response = requests.get(file_url, headers=headers, allow_redirects=True)
    if response.status_code == 200:
        logging.info(f"File downloaded successfully: {file_url}")
        logging.info(f"Response headers: {response.headers}")
        logging.info(f"First 100 bytes of file content: {response.content[:100]}")
        return response.content
    else:
        logging.error(f"Error downloading file: {response.status_code}, {response.text}")
        return None


def extract_text_from_pdf(file_content):
    try:
        file_stream = BytesIO(file_content)
        text = extract_text(file_stream)
        return text
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return None

def extract_text_from_docx(file_content):
    try:
        file_stream = BytesIO(file_content)
        doc = Document(file_stream)
        return "\n".join([paragraph.text for paragraph in doc.paragraphs])
    except Exception as e:
        logging.error(f"Error extracting text from DOCX: {e}")
        return None

def extract_text_from_pptx(file_content):
    try:
        file_stream = BytesIO(file_content)
        ppt = Presentation(file_stream)
        text = []
        for slide in ppt.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    text.append(shape.text)
        return "\n".join(text)
    except Exception as e:
        logging.error(f"Error extracting text from PPTX: {e}")
        return None

def process_file(file_url, file_type):
    file_content = download_file(file_url)
    if not file_content:
        return None
    try:
        if file_type == 'pdf':
            return extract_text_from_pdf(file_content)
        elif file_type in ['doc', 'docx']:
            return extract_text_from_docx(file_content)
        elif file_type in ['ppt', 'pptx']:
            return extract_text_from_pptx(file_content)
        else:
            logging.error(f"Unsupported file type: {file_type}")
            return None
    except Exception as e:
        logging.error(f"Error processing file: {e}")
        return "Error in processing the file."
    
# Function to extract content from a given URL
def extract_webpage_content(url):
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raises an HTTPError if the status is 4xx, 5xx
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract text from the webpage
        # This example extracts all paragraph texts; you might want to refine this
        text = ' '.join(p.get_text() for p in soup.find_all('p'))
        return text
    except Exception as e:
        print(f"Error fetching webpage content: {e}")
        return None