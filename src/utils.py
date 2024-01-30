import requests
import io
import logging
from PyPDF2 import PdfReader
from docx import Document
from pptx import Presentation

# Setup basic logging
logging.basicConfig(level=logging.INFO)

def download_file(file_url, headers):
    try:
        response = requests.get(file_url, headers=headers)
        response.raise_for_status()  # will raise an HTTPError if the HTTP request returned an unsuccessful status code
        return io.BytesIO(response.content)
    except requests.RequestException as e:
        logging.error(f"Failed to download file: {e}")
        raise

def convert_pdf_to_text(file_content):
    try:
        reader = PdfReader(file_content)
        text = ""
        for page in reader.pages:
            if page.extract_text():
                text += page.extract_text() + "\n"
        return text
    except Exception as e:
        logging.error(f"Error processing PDF: {e}")
        return "Error processing PDF file. It may be corrupted or incomplete."

def convert_docx_to_text(file_content):
    try:
        doc = Document(file_content)
        text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
        return text
    except Exception as e:
        logging.error(f"Error processing DOCX: {e}")
        return "Error processing DOCX file."

def convert_pptx_to_text(file_content):
    try:
        prs = Presentation(file_content)
        text = ""
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    text += shape.text + "\n"
        return text
    except Exception as e:
        logging.error(f"Error processing PPTX: {e}")
        return "Error processing PPTX file."

def process_file(file, headers):
    try:
        file_content = download_file(file['url_private'], headers)
        file_type = file['filetype']
        if file_type == 'pdf':
            return convert_pdf_to_text(file_content)
        elif file_type == 'docx':
            return convert_docx_to_text(file_content)
        elif file_type == 'pptx':
            return convert_pptx_to_text(file_content)
    except Exception as e:
        logging.error(f"Error processing file type '{file_type}': {e}")
        return f"Error processing {file_type.upper()} file."
    raise ValueError(f"Unsupported file type: {file_type}")
