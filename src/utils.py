import logging
from io import BytesIO
from pdfminer.high_level import extract_text
from docx import Document
from pptx import Presentation
import requests
from bs4 import BeautifulSoup
import re
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
    
# Function to extract and parse content from a given URL
def extract_webpage_content(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.google.com/'
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Initialize an empty list to hold tuples of (tag name, text)
        content_list = []
        for tag in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            tag_text = tag.get_text(separator=" ", strip=True)
            clean_text = re.sub(r'\s+', ' ', tag_text).strip()
            if clean_text:  # Ensure the text is not empty
                content_list.append((tag.name, clean_text))
        
        # Join the texts and calculate the length
        full_text = ' '.join([text for _, text in content_list])
        content_length = len(full_text)
        
        return full_text, content_length
    except requests.HTTPError as http_err:
        print(f"HTTP error occurred while fetching content from {url}: {http_err}")
        return "", 0
    except Exception as e:
        print(f"An error occurred while fetching content from {url}: {e}")
        return "", 0