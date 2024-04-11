import os
import re
import logging
from io import BytesIO
from pdfminer.high_level import extract_text
from docx import Document
from pptx import Presentation
import httpx
from bs4 import BeautifulSoup
import asyncio

async def download_file(file_url):
    headers = {'Authorization': f'Bearer {os.getenv("SLACK_BOT_TOKEN")}'}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(file_url, headers=headers)
            response.raise_for_status()
            logging.info(f"File downloaded successfully: {file_url}")
            logging.info(f"Response headers: {response.headers}")
            logging.info(f"First 100 bytes of file content: {response.content[:100]}")
            return response.content
        except httpx.HTTPStatusError as exc:
            logging.error(f"Error downloading file: {exc.response.status_code}, {exc.response.text}")
            return None

async def extract_text_from_pdf(file_content):
    try:
        file_stream = BytesIO(file_content)
        # Running blocking I/O in the thread pool
        text = await asyncio.to_thread(extract_text, file_stream)
        return text
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return None

async def extract_text_from_docx(file_content):
    try:
        file_stream = BytesIO(file_content)
        # Running blocking I/O in the thread pool
        doc = await asyncio.to_thread(Document, file_stream)
        text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
        return text
    except Exception as e:
        logging.error(f"Error extracting text from DOCX: {e}")
        return None

async def extract_text_from_pptx(file_content):
    try:
        file_stream = BytesIO(file_content)
        # Running blocking I/O in the thread pool
        ppt = await asyncio.to_thread(Presentation, file_stream)
        text = []
        for slide in ppt.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    text.append(shape.text)
        return "\n".join(text)
    except Exception as e:
        logging.error(f"Error extracting text from PPTX: {e}")
        return None

async def process_file(file_url, file_type):
    file_content = await download_file(file_url)
    if not file_content:
        return None
    if file_type == 'pdf':
        return await extract_text_from_pdf(file_content)
    elif file_type in ['doc', 'docx']:
        return await extract_text_from_docx(file_content)
    elif file_type in ['ppt', 'pptx']:
        return await extract_text_from_pptx(file_content)
    else:
        logging.error(f"Unsupported file type: {file_type}")
        return None

async def extract_webpage_content(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.google.com/'
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
            soup = BeautifulSoup(response.content, 'html.parser')
            content_list = [(tag.name, tag.get_text(separator=" ", strip=True)) for tag in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']) if tag.get_text(strip=True)]
            full_text = ' '.join(text for _, text in content_list)
            return full_text
        except httpx.HTTPStatusError as http_err:
            logging.error(f"HTTP error occurred while fetching content from {url}: {http_err}")
            return ""
        except Exception as e:
            logging.error(f"An error occurred while fetching content from {url}: {e}")
            return ""
