import io
import logging
from typing import Optional
import pdfplumber
import docx

logger = logging.getLogger(__name__)

def extract_text_from_file(file_content: bytes, filename: str) -> Optional[str]:
    """
    Extracts raw text from PDF, DOCX, or TXT file bytes.
    """
    ext = filename.split('.')[-1].lower() if '.' in filename else ''
    
    try:
        if ext == 'pdf':
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                text = "\n".join([page.extract_text() for page in pdf.pages if page.extract_text()])
            return text
            
        elif ext in ['docx', 'doc']:
            doc = docx.Document(io.BytesIO(file_content))
            text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
            return text
            
        elif ext in ['txt', 'md', 'csv']:
            return file_content.decode('utf-8', errors='ignore')
            
        else:
            logger.warning(f"Unsupported file extension: {ext}")
            return None
            
    except Exception as e:
        logger.error(f"Failed to extract text from {filename}: {e}")
        return None
