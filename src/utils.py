import logging
from io import BytesIO
from PyPDF2 import PdfReader
from docx import Document
from pptx import Presentation
import requests
import os

def download_file(file_url):
    headers = {'Authorization': f'Bearer {os.getenv("SLACK_BOT_TOKEN")}'}
    response = requests.get(file_url, headers=headers)
    if response.status_code == 200:
        logging.info(f"File downloaded successfully: {file_url}")
        return response.content
    else:
        logging.error(f"Error downloading file: {response.status_code}, {response.text}")
        return None

def extract_text_from_pdf(file_content):
    try:
        file_stream = BytesIO(file_content)
        reader = PdfReader(file_stream)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text
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

    if file_type == 'pdf':
        return extract_text_from_pdf(file_content)
    elif file_type in ['doc', 'docx']:
        return extract_text_from_docx(file_content)
    elif file_type in ['ppt', 'pptx']:
        return extract_text_from_pptx(file_content)
    else:
        logging.error(f"Unsupported file type: {file_type}")
        return None
