import requests
import io
from PyPDF2 import PdfReader
from docx import Document
from pptx import Presentation

def download_file(file_url, headers):
    response = requests.get(file_url, headers=headers)
    if response.status_code == 200:
        return io.BytesIO(response.content)
    else:
        raise Exception(f"Failed to download file: {response.status_code}")

def convert_pdf_to_text(file_content):
    reader = PdfReader(file_content)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def convert_docx_to_text(file_content):
    doc = Document(file_content)
    text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
    return text

def convert_pptx_to_text(file_content):
    prs = Presentation(file_content)
    text = ""
    for slide in prs.slides:
        for shape in slide.shapes:
            if hasattr(shape, "text"):
                text += shape.text + "\n"
    return text

def process_file(file, headers):
    file_content = download_file(file['url_private'], headers)
    file_type = file['filetype']
    if file_type == 'pdf':
        return convert_pdf_to_text(file_content)
    elif file_type == 'docx':
        return convert_docx_to_text(file_content)
    elif file_type == 'pptx':
        return convert_pptx_to_text(file_content)
    else:
        raise ValueError(f"Unsupported file type: {file_type}")
