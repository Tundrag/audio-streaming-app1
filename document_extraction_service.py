# document_extraction_service.py - Complete Memory-Efficient Version

import os
import re
import json
import uuid
import shutil
import logging
import aiofiles
import tempfile
import warnings
import difflib
import time
import asyncio
from zipfile import ZipFile
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException, Request, Depends, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

from redis_state.config import redis_client

logger = logging.getLogger(__name__)

# Configuration
warnings.filterwarnings("ignore")
DOCUMENT_TEMP_FOLDER = "/tmp/media_storage/document_extraction"

# Redis key patterns for progress tracking
PROGRESS_KEY_PREFIX = "document:extraction:progress:"
PROGRESS_TTL = 3600  # 1 hour TTL for progress data

# Create router - matching frontend expectations
router = APIRouter(prefix="/document", tags=["Document"])

# ----------------------------------------------------------------------------
# INITIALIZATION
# ----------------------------------------------------------------------------
def init_document_service():
    """Initialize document extraction service"""
    os.makedirs(DOCUMENT_TEMP_FOLDER, exist_ok=True)
    logger.info(f"Document extraction service initialized. Using temp folder: {DOCUMENT_TEMP_FOLDER}")

# ----------------------------------------------------------------------------
# MODELS
# ----------------------------------------------------------------------------
class SelectedIndices(BaseModel):
    selected_indices: List[int]
    session_id: str

# ----------------------------------------------------------------------------
# UTILITY FUNCTIONS
# ----------------------------------------------------------------------------
def similar(a: str, b: str) -> float:
    """Return a similarity ratio between two strings (case-insensitive)"""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()

def is_filtered_chapter(title: str, book_title: str = "") -> bool:
    """Decide whether a chapter should be filtered out"""
    cleaned = re.sub(r'[\W_]+', '', title.lower())
    if cleaned in {"tableofcontents", "contents", "toc"}:
        return True
    if book_title:
        if similar(title, book_title) > 0.8:
            return True
        title_lower = title.lower()
        book_title_lower = book_title.lower()
        if title_lower in book_title_lower or book_title_lower in title_lower:
            return True
    return False

def secure_filename(filename: str) -> str:
    """Create a secure filename"""
    filename = os.path.basename(filename)
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "", filename)
    return filename or f"file_{uuid.uuid4().hex}"

def update_progress(session_id: str, processed: int, total: int, completed: bool = False) -> None:
    """Update extraction progress in Redis for multi-container shared state"""
    progress_key = f"{PROGRESS_KEY_PREFIX}{session_id}"
    progress_data = {
        "processed": processed,
        "total": total,
        "completed": completed,
        "updated_at": datetime.now().isoformat()
    }

    # Store as JSON in Redis with TTL
    try:
        redis_client.set(progress_key, json.dumps(progress_data), ex=PROGRESS_TTL)
        logger.debug(f"[DOCUMENT:{session_id}] Progress updated in Redis: {processed}/{total} (completed={completed})")
    except Exception as e:
        logger.warning(f"[DOCUMENT:{session_id}] Failed to update progress in Redis: {e}")

def get_progress(session_id: str) -> Optional[Dict[str, Any]]:
    """Get extraction progress from Redis"""
    progress_key = f"{PROGRESS_KEY_PREFIX}{session_id}"
    try:
        progress_json = redis_client.get(progress_key)
        if progress_json:
            return json.loads(progress_json)
        return None
    except Exception as e:
        logger.warning(f"[DOCUMENT:{session_id}] Failed to get progress from Redis: {e}")
        return None

def update_progress_error(session_id: str, error: str) -> None:
    """Update extraction progress with error state"""
    progress_key = f"{PROGRESS_KEY_PREFIX}{session_id}"
    progress_data = {
        "error": error,
        "completed": True,
        "failed": True,
        "updated_at": datetime.now().isoformat()
    }

    try:
        redis_client.set(progress_key, json.dumps(progress_data), ex=PROGRESS_TTL)
        logger.error(f"[DOCUMENT:{session_id}] Progress error stored in Redis: {error}")
    except Exception as e:
        logger.warning(f"[DOCUMENT:{session_id}] Failed to update error in Redis: {e}")

def delete_progress(session_id: str) -> None:
    """Delete extraction progress from Redis"""
    progress_key = f"{PROGRESS_KEY_PREFIX}{session_id}"
    try:
        redis_client.delete(progress_key)
        logger.debug(f"[DOCUMENT:{session_id}] Progress deleted from Redis")
    except Exception as e:
        logger.warning(f"[DOCUMENT:{session_id}] Failed to delete progress from Redis: {e}")

# ----------------------------------------------------------------------------
# HTML CLEANING HELPERS (for EPUB)
# ----------------------------------------------------------------------------
def remove_unwanted_tags(soup: BeautifulSoup, book_title: Optional[str] = None) -> None:
    """Remove navigational and TOC sections"""
    for tag in soup.find_all(["nav", "header", "footer"]):
        tag.decompose()

    for tag in soup.find_all(
        lambda t: (
            t.name in ["div", "section"] and (
                (t.get("id") and "toc" in t.get("id").lower()) or
                (t.get("class") and any("toc" in c.lower() for c in t.get("class")))
            )
        )
    ):
        tag.decompose()

    if book_title:
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "title"]):
            tag_text = tag.get_text().strip()
            if tag_text and similar(tag_text, book_title) >= 0.8:
                tag.decompose()

def find_chapter_title(soup: BeautifulSoup) -> Optional[str]:
    """Attempt to detect a chapter title from HTML"""
    heading_classes = ['heading_s', 'chapter', 'title', 'chapter-title', 'heading']
    for class_name in heading_classes:
        element = soup.find('div', class_=class_name)
        if element and element.get_text().strip():
            return element.get_text().strip()

    for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
        element = soup.find(tag)
        if element and element.get_text().strip():
            return element.get_text().strip()

    return None

def extract_clean_text(html_bytes: bytes, book_title: Optional[str] = None) -> str:
    """Parse HTML content, removing extraneous sections"""
    try:
        soup = BeautifulSoup(html_bytes, "html.parser")
    except Exception:
        soup = BeautifulSoup(html_bytes, "lxml")

    if soup.head:
        soup.head.decompose()
    if soup.body:
        soup = BeautifulSoup(str(soup.body), "html.parser")

    remove_unwanted_tags(soup, book_title)
    chapter_title = find_chapter_title(soup)

    chapter_section = soup.find('section', attrs={'epub:type': 'bodymatter chapter'})
    if chapter_section:
        content = chapter_section
    else:
        main_div = soup.find('div', class_='main')
        content = main_div if main_div else soup

    text = content.get_text(separator="\n").strip()
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('<?xml') or line.startswith('<!DOCTYPE') or line.startswith('<html'):
            continue
        lines.append(line)
    text = "\n".join(lines).strip()

    if chapter_title and not text.startswith(chapter_title):
        text = chapter_title + "\n\n" + text

    # Clean up URLs and other unwanted content
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'///.*?///', '', text, flags=re.DOTALL)
    text = re.sub(r'\*+', '', text)

    return text

# ----------------------------------------------------------------------------
# FILE PROCESSING FUNCTIONS - MEMORY EFFICIENT
# ----------------------------------------------------------------------------
def _read_entire_file_from_zip(zip_file: ZipFile, filename: str, chunk_size: int = 65536) -> bytes:
    """Read entire file from ZIP"""
    data_chunks = []
    with zip_file.open(filename, "r") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            data_chunks.append(chunk)
    return b"".join(data_chunks)

async def save_chapter_content(content: str, chapter_path: str) -> None:
    """Save chapter content to individual file"""
    async with aiofiles.open(chapter_path, "w", encoding="utf-8") as f:
        await f.write(content)

def process_txt_sync(txt_file_path: str, session_folder: str) -> Dict[str, Any]:
    """Process TXT file - stores content in separate files"""
    with open(txt_file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Split by double newlines to create sections
    sections = [section.strip() for section in content.split('\n\n') if section.strip()]
    
    if not sections:
        sections = [content]
    
    chapters_dir = os.path.join(session_folder, "chapters")
    os.makedirs(chapters_dir, exist_ok=True)
    
    chapters = []
    for i, section in enumerate(sections):
        chapter_path = os.path.join(chapters_dir, f"chapter_{i}.txt")
        with open(chapter_path, "w", encoding="utf-8") as f:
            f.write(section)
        
        chapters.append({
            "title": f"Section {i + 1}",
            "content_path": chapter_path,
            "content_preview": section[:200].strip()
        })
    
    return {
        "chapters": chapters, 
        "book_title": os.path.splitext(os.path.basename(txt_file_path))[0],
        "doc_type": "txt"
    }

def process_pdf_sync(pdf_file_path: str, session_folder: str) -> Dict[str, Any]:
    """Process PDF file - stores each page in separate files"""
    try:
        import pdfplumber
        
        chapters_dir = os.path.join(session_folder, "chapters")
        os.makedirs(chapters_dir, exist_ok=True)
        
        chapters = []
        with pdfplumber.open(pdf_file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text and page_text.strip():
                    chapter_path = os.path.join(chapters_dir, f"page_{i}.txt")
                    with open(chapter_path, "w", encoding="utf-8") as f:
                        f.write(page_text)
                    
                    chapters.append({
                        "title": f"Page {i + 1}",
                        "content_path": chapter_path,
                        "content_preview": page_text[:200].strip()
                    })
        
        return {
            "chapters": chapters, 
            "book_title": os.path.splitext(os.path.basename(pdf_file_path))[0],
            "doc_type": "pdf"
        }
    except Exception as e:
        raise Exception(f"Failed to process PDF file: {str(e)}")

def process_docx_sync(docx_file_path: str, session_folder: str) -> Dict[str, Any]:
    """Process DOCX file - stores sections in separate files"""
    try:
        from docx import Document
        doc = Document(docx_file_path)
        full_text = "\n".join([para.text for para in doc.paragraphs])
        
        # Split into sections by paragraph breaks
        sections = [section.strip() for section in full_text.split('\n\n') if section.strip()]
        if not sections:
            sections = [full_text]
        
        chapters_dir = os.path.join(session_folder, "chapters")
        os.makedirs(chapters_dir, exist_ok=True)
        
        chapters = []
        for i, section in enumerate(sections):
            chapter_path = os.path.join(chapters_dir, f"section_{i}.txt")
            with open(chapter_path, "w", encoding="utf-8") as f:
                f.write(section)
            
            chapters.append({
                "title": f"Section {i + 1}",
                "content_path": chapter_path,
                "content_preview": section[:200].strip()
            })
        
        return {
            "chapters": chapters, 
            "book_title": os.path.splitext(os.path.basename(docx_file_path))[0],
            "doc_type": "docx"
        }
    except Exception as e:
        raise Exception(f"Failed to process DOCX file: {str(e)}")

def process_doc_sync(doc_file_path: str, session_folder: str) -> Dict[str, Any]:
    """Process DOC file - stores sections in separate files"""
    try:
        import textract
        text = textract.process(doc_file_path).decode("utf-8")
        
        # Split into sections
        sections = [section.strip() for section in text.split('\n\n') if section.strip()]
        if not sections:
            sections = [text]
        
        chapters_dir = os.path.join(session_folder, "chapters")
        os.makedirs(chapters_dir, exist_ok=True)
        
        chapters = []
        for i, section in enumerate(sections):
            chapter_path = os.path.join(chapters_dir, f"section_{i}.txt")
            with open(chapter_path, "w", encoding="utf-8") as f:
                f.write(section)
            
            chapters.append({
                "title": f"Section {i + 1}",
                "content_path": chapter_path,
                "content_preview": section[:200].strip()
            })
        
        return {
            "chapters": chapters, 
            "book_title": os.path.splitext(os.path.basename(doc_file_path))[0],
            "doc_type": "doc"
        }
    except Exception as e:
        raise Exception(f"Failed to process DOC file: {str(e)}")

def process_epub_sync(epub_file_path: str, session_id: str, session_folder: str, ignore_toc: bool = False) -> Dict[str, Any]:
    """Process EPUB file - stores each chapter in separate files"""
    try:
        parse_method = "simple_parse" if ignore_toc else "opf_spine_parse"
        logger.info(f"[DOCUMENT:{session_id}] Using {parse_method}")
        
        chapters_dir = os.path.join(session_folder, "chapters")
        os.makedirs(chapters_dir, exist_ok=True)
        
        chapters = []
        book_title = None
        
        with ZipFile(epub_file_path, "r") as zf:
            container_path = "META-INF/container.xml"
            opf_path = None
            
            if container_path in zf.namelist():
                container_data = _read_entire_file_from_zip(zf, container_path)
                try:
                    container_tree = ET.fromstring(container_data)
                    rootfile_elem = container_tree.find(".//{*}rootfile")
                    if rootfile_elem is not None:
                        opf_path = rootfile_elem.attrib.get("full-path")
                except Exception as e:
                    logger.warning(f"Error reading container.xml: {e}")
            
            manifest_map = {}
            spine_items = []
            
            if opf_path and opf_path in zf.namelist():
                try:
                    opf_data = _read_entire_file_from_zip(zf, opf_path)
                    opf_tree = ET.fromstring(opf_data)
                    
                    # Extract book title
                    metadata_elem = opf_tree.find(".//{*}metadata")
                    if metadata_elem is not None:
                        title_elem = metadata_elem.find(".//{http://purl.org/dc/elements/1.1/}title")
                        if title_elem is not None and title_elem.text:
                            book_title = title_elem.text.strip()
                    
                    # Build manifest map
                    manifest_elem = opf_tree.find(".//{*}manifest")
                    if manifest_elem is not None:
                        for item_elem in manifest_elem.findall("{*}item"):
                            item_id = item_elem.attrib.get("id")
                            href = item_elem.attrib.get("href")
                            if item_id and href:
                                base_path = os.path.dirname(opf_path)
                                full_path = os.path.normpath(os.path.join(base_path, href))
                                manifest_map[item_id] = full_path
                    
                    # Get spine items
                    spine_elem = opf_tree.find(".//{*}spine")
                    if spine_elem is not None:
                        spine_items = spine_elem.findall("{*}itemref")
                except Exception as e:
                    logger.warning(f"Error parsing OPF: {e}")
            
            # Process chapters
            current_chapter = None
            chapter_index = 0
            
            if not ignore_toc and spine_items:
                total_items = len(spine_items)
                processed = 0
                
                for itemref in spine_items:
                    processed += 1
                    update_progress(session_id, processed, total_items, False)
                    logger.info(f"[DOCUMENT:{session_id}] Processing item {processed}/{total_items}")
                    time.sleep(0.01)
                    
                    itemref_id = itemref.attrib.get("idref")
                    if not itemref_id or itemref_id not in manifest_map:
                        continue
                    
                    real_path = manifest_map[itemref_id]
                    if real_path not in zf.namelist():
                        continue
                    
                    try:
                        raw_data = _read_entire_file_from_zip(zf, real_path)
                        text_content = extract_clean_text(raw_data, book_title)
                        
                        if text_content:
                            soup = BeautifulSoup(raw_data, "html.parser")
                            remove_unwanted_tags(soup, book_title)
                            found_title = find_chapter_title(soup)
                            
                            if found_title:
                                # Save previous chapter if exists
                                if current_chapter:
                                    chapters.append(current_chapter)
                                
                                # Create new chapter file
                                chapter_path = os.path.join(chapters_dir, f"chapter_{chapter_index}.txt")
                                with open(chapter_path, "w", encoding="utf-8") as f:
                                    f.write(text_content)
                                
                                current_chapter = {
                                    "title": found_title,
                                    "content_path": chapter_path,
                                    "content_preview": text_content[:200].strip()
                                }
                                chapter_index += 1
                            else:
                                if not current_chapter:
                                    chapter_path = os.path.join(chapters_dir, f"chapter_{chapter_index}.txt")
                                    with open(chapter_path, "w", encoding="utf-8") as f:
                                        f.write(text_content)
                                    
                                    current_chapter = {
                                        "title": f"Chapter {chapter_index + 1}",
                                        "content_path": chapter_path,
                                        "content_preview": text_content[:200].strip()
                                    }
                                    chapter_index += 1
                                else:
                                    # Append to existing chapter file
                                    with open(current_chapter["content_path"], "a", encoding="utf-8") as f:
                                        f.write("\n\n" + text_content)
                    except Exception as e:
                        logger.warning(f"Could not parse {real_path}: {e}")
                        continue
            else:
                # Simple parsing
                html_candidates = [f for f in zf.namelist() if f.lower().endswith((".html", ".htm", ".xhtml"))]
                html_candidates.sort()
                total_items = len(html_candidates)
                
                for i, fname in enumerate(html_candidates):
                    processed = i + 1
                    update_progress(session_id, processed, total_items, False)
                    time.sleep(0.01)
                    
                    try:
                        data = _read_entire_file_from_zip(zf, fname)
                        text_content = extract_clean_text(data, book_title)
                        if not text_content:
                            continue
                        
                        soup = BeautifulSoup(data, "html.parser")
                        remove_unwanted_tags(soup, book_title)
                        found_title = find_chapter_title(soup)
                        
                        if found_title:
                            if current_chapter:
                                chapters.append(current_chapter)
                            
                            chapter_path = os.path.join(chapters_dir, f"chapter_{chapter_index}.txt")
                            with open(chapter_path, "w", encoding="utf-8") as f:
                                f.write(text_content)
                            
                            current_chapter = {
                                "title": found_title,
                                "content_path": chapter_path,
                                "content_preview": text_content[:200].strip()
                            }
                            chapter_index += 1
                        else:
                            if not current_chapter:
                                chapter_path = os.path.join(chapters_dir, f"chapter_{chapter_index}.txt")
                                with open(chapter_path, "w", encoding="utf-8") as f:
                                    f.write(text_content)
                                
                                current_chapter = {
                                    "title": f"Chapter {chapter_index + 1}",
                                    "content_path": chapter_path,
                                    "content_preview": text_content[:200].strip()
                                }
                                chapter_index += 1
                            else:
                                with open(current_chapter["content_path"], "a", encoding="utf-8") as f:
                                    f.write("\n\n" + text_content)
                    except Exception as e:
                        logger.warning(f"Could not parse {fname}: {e}")
                        continue
            
            if current_chapter:
                chapters.append(current_chapter)
            
            update_progress(session_id, total_items, total_items, True)
            return {
                "chapters": chapters, 
                "book_title": book_title or "Untitled Book",
                "doc_type": "epub"
            }
            
    except Exception as e:
        logger.exception(f"Failed to process EPUB: {e}")
        update_progress_error(session_id, str(e))
        raise

# ----------------------------------------------------------------------------
# BACKGROUND PROCESSING
# ----------------------------------------------------------------------------
async def run_extraction(file_path: str, session_id: str, file_type: str):
    """Run document extraction in background"""
    try:
        logger.info(f"[DOCUMENT:{session_id}] Starting extraction for {file_type} file")
        
        session_folder = os.path.join(DOCUMENT_TEMP_FOLDER, f"session_{session_id}")
        
        if file_type == "epub":
            result = await asyncio.to_thread(process_epub_sync, file_path, session_id, session_folder, False)
        elif file_type == "txt":
            result = await asyncio.to_thread(process_txt_sync, file_path, session_folder)
        elif file_type == "pdf":
            result = await asyncio.to_thread(process_pdf_sync, file_path, session_folder)
        elif file_type == "docx":
            result = await asyncio.to_thread(process_docx_sync, file_path, session_folder)
        elif file_type == "doc":
            result = await asyncio.to_thread(process_doc_sync, file_path, session_folder)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
        
        logger.info(f"[DOCUMENT:{session_id}] Extraction completed, found {len(result.get('chapters', []))} chapters")
        
        # Filter out unwanted chapters
        filtered_chapters = [
            ch for ch in result["chapters"]
            if not is_filtered_chapter(ch.get("title", ""), result.get("book_title", ""))
        ]
        
        if not filtered_chapters:
            raise Exception("No valid content found in the document.")
        
        logger.info(f"[DOCUMENT:{session_id}] After filtering: {len(filtered_chapters)} chapters remain")
        
        # Store ONLY metadata (no content in memory)
        session_data = {
            "book_title": result.get("book_title", "Untitled Document"),
            "chapters": filtered_chapters,  # Only paths and metadata, no content
            "doc_type": result.get("doc_type", file_type),
            "file_path": file_path,
            "last_activity": datetime.now().isoformat(),
        }
        
        session_file = os.path.join(session_folder, "session_data.json")
        async with aiofiles.open(session_file, "w") as sf:
            await sf.write(json.dumps(session_data))

        logger.info(f"[DOCUMENT:{session_id}] Session data saved to {session_file}")

        # ✅ ALSO store metadata in Redis for multi-container access
        session_data_key = f"document:extraction:metadata:{session_id}"
        try:
            redis_client.set(
                session_data_key,
                json.dumps(session_data),
                ex=PROGRESS_TTL  # Same 1-hour TTL as progress
            )
            logger.info(f"[DOCUMENT:{session_id}] Session metadata stored in Redis")
        except Exception as e:
            logger.warning(f"[DOCUMENT:{session_id}] Failed to store metadata in Redis: {e}")

        # Mark progress as completed
        update_progress(session_id, processed=100, total=100, completed=True)
            
    except Exception as e:
        logger.exception(f"[DOCUMENT:{session_id}] Extraction failed")
        update_progress_error(session_id, str(e))

# ----------------------------------------------------------------------------
# CLEANUP FUNCTIONS
# ----------------------------------------------------------------------------
async def cleanup_orphaned_sessions():
    """Simple cleanup of orphaned document extraction sessions"""
    if not os.path.exists(DOCUMENT_TEMP_FOLDER):
        return {"cleaned": 0, "message": "No document extraction folder found"}

    try:
        current_time = datetime.now()
        cutoff = current_time - timedelta(hours=1)
        cleaned = 0

        for item_name in os.listdir(DOCUMENT_TEMP_FOLDER):
            item_path = os.path.join(DOCUMENT_TEMP_FOLDER, item_name)

            if os.path.isdir(item_path) and item_name.startswith("session_"):
                try:
                    mod_time = datetime.fromtimestamp(os.path.getmtime(item_path))
                    if mod_time < cutoff:
                        shutil.rmtree(item_path)

                        session_id = item_name.replace("session_", "")
                        delete_progress(session_id)

                        cleaned += 1
                except Exception:
                    pass

        # Also clean up orphaned Redis keys for sessions without filesystem data
        try:
            for key in redis_client.scan_iter(match=f"{PROGRESS_KEY_PREFIX}*"):
                try:
                    progress_json = redis_client.get(key)
                    if progress_json:
                        progress = json.loads(progress_json)
                        updated_at_str = progress.get("updated_at")
                        if updated_at_str:
                            updated_at = datetime.fromisoformat(updated_at_str)
                            if updated_at < cutoff:
                                redis_client.delete(key)
                                cleaned += 1
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to cleanup orphaned Redis keys: {e}")

        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} orphaned document extraction sessions")

        return {"cleaned": cleaned, "message": f"Cleaned {cleaned} orphaned sessions"}

    except Exception as e:
        logger.error(f"Error in document extraction cleanup: {str(e)}")
        return {"cleaned": 0, "message": f"Cleanup failed: {str(e)}"}

# ----------------------------------------------------------------------------
# API ENDPOINTS
# ----------------------------------------------------------------------------
@router.post("/extract_content")
async def extract_document_content(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None
):
    """Extract content from uploaded document"""
    session_id = str(uuid.uuid4())
    
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    file_ext = file.filename.lower().split('.')[-1]
    valid_extensions = ['epub', 'pdf', 'docx', 'doc', 'txt']
    
    if file_ext not in valid_extensions:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file type. Supported types: {', '.join(valid_extensions)}"
        )
    
    # Create session folder
    session_folder = os.path.join(DOCUMENT_TEMP_FOLDER, f"session_{session_id}")
    os.makedirs(session_folder, exist_ok=True)
    
    # Save uploaded file
    safe_filename = secure_filename(file.filename)
    file_path = os.path.join(session_folder, safe_filename)
    
    try:
        total_size = 0
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await file.read(65536):
                total_size += len(chunk)
                await f.write(chunk)
        logger.info(f"[DOCUMENT:{session_id}] Saved file ({total_size/1024/1024:.2f}MB)")
    except Exception as e:
        logger.exception(f"[DOCUMENT:{session_id}] Failed to save file")
        raise HTTPException(status_code=500, detail=f"Could not save file: {str(e)}")
    
    # Initialize progress tracking in Redis
    update_progress(session_id, processed=0, total=0, completed=False)

    # Start background extraction
    background_tasks.add_task(run_extraction, file_path, session_id, file_ext)
    
    return {
        "session_id": session_id,
        "message": "Extraction started. Poll /document/extraction_progress for updates.",
        "doc_type": file_ext
    }

@router.get("/extraction_progress")
async def get_extraction_progress(session_id: str):
    """Get extraction progress for a session from Redis"""
    progress = get_progress(session_id)

    if not progress:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    logger.info(f"[Progress Check] Session {session_id}: {progress}")
    return progress

@router.get("/get_content_info")
async def get_document_content(session_id: str):
    """Get extracted document content for chapter selection"""

    # ✅ Try Redis first (shared across containers)
    session_data_key = f"document:extraction:metadata:{session_id}"
    try:
        metadata_json = redis_client.get(session_data_key)
        if metadata_json:
            logger.info(f"[DOCUMENT:{session_id}] Loaded metadata from Redis (multi-container)")
            return json.loads(metadata_json)
    except Exception as e:
        logger.warning(f"[DOCUMENT:{session_id}] Failed to read from Redis: {e}")

    # ⚠️ Fallback: try local filesystem (backwards compatibility)
    session_folder = os.path.join(DOCUMENT_TEMP_FOLDER, f"session_{session_id}")
    session_file = os.path.join(session_folder, "session_data.json")

    logger.info(f"[DOCUMENT:{session_id}] Trying local file fallback: {session_file}")

    if os.path.exists(session_file):
        try:
            async with aiofiles.open(session_file, "r") as sf:
                data = json.loads(await sf.read())
            logger.info(f"[DOCUMENT:{session_id}] Loaded from local file (fallback) with {len(data.get('chapters', []))} chapters")
            return data
        except Exception as e:
            logger.exception(f"[DOCUMENT:{session_id}] Failed to read local file")
            raise HTTPException(status_code=500, detail=f"Cannot read session data: {str(e)}")

    logger.error(f"[DOCUMENT:{session_id}] Session not found in Redis or local filesystem")
    raise HTTPException(status_code=404, detail="Session data not found")

@router.post("/get_selected_content")
async def get_selected_content_stream(selected: SelectedIndices):
    """Stream selected content with TRUE memory efficiency - reads directly from individual files"""
    session_id = selected.session_id
    session_folder = os.path.join(DOCUMENT_TEMP_FOLDER, f"session_{session_id}")

    # ✅ Try Redis first (shared across containers)
    session_data_key = f"document:extraction:metadata:{session_id}"
    session_data = None

    try:
        metadata_json = redis_client.get(session_data_key)
        if metadata_json:
            session_data = json.loads(metadata_json)
            logger.info(f"[DOCUMENT:{session_id}] Loaded metadata from Redis for content selection")
    except Exception as e:
        logger.warning(f"[DOCUMENT:{session_id}] Failed to read from Redis: {e}")

    # ⚠️ Fallback: try local filesystem
    if not session_data:
        session_file = os.path.join(session_folder, "session_data.json")

        if not os.path.exists(session_file):
            raise HTTPException(status_code=404, detail="Session data not found")

        try:
            async with aiofiles.open(session_file, "r") as sf:
                session_data = json.loads(await sf.read())
            logger.info(f"[DOCUMENT:{session_id}] Loaded from local file (fallback) for content selection")
        except Exception as e:
            raise HTTPException(status_code=500, detail="Cannot read session data")
    
    chapters_metadata = session_data.get("chapters", [])
    logger.info(f"[DOCUMENT:{session_id}] Streaming {len(selected.selected_indices)} selected chapters")
    
    async def generate_content():
        """Generator function for streaming content - ZERO content loaded into memory"""
        try:
            for i, chapter_index in enumerate(selected.selected_indices):
                if 0 <= chapter_index < len(chapters_metadata):
                    chapter_meta = chapters_metadata[chapter_index]
                    chapter_file_path = chapter_meta.get("content_path")
                    
                    if not chapter_file_path or not os.path.exists(chapter_file_path):
                        continue
                    
                    # Add separator between chapters (except for first)
                    if i > 0:
                        yield "\n\n"
                    
                    # Stream directly from individual chapter file in chunks
                    async with aiofiles.open(chapter_file_path, "r", encoding="utf-8") as f:
                        while True:
                            chunk = await f.read(8192)  # Only 8KB in memory at any time
                            if not chunk:
                                break
                            yield chunk
                            # Small delay to prevent overwhelming the connection
                            await asyncio.sleep(0.001)
                    
        finally:
            # Cleanup after streaming completes
            try:
                if os.path.exists(session_folder):
                    shutil.rmtree(session_folder)
                delete_progress(session_id)
                logger.info(f"[DOCUMENT:{session_id}] Session cleaned up after streaming")
            except Exception as cleanup_error:
                logger.warning(f"[DOCUMENT:{session_id}] Stream cleanup failed: {cleanup_error}")
    
    return StreamingResponse(
        generate_content(),
        media_type="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename=extracted_content_{session_id}.txt"
        }
    )

@router.delete("/cleanup/{session_id}")
async def cleanup_session(session_id: str):
    """Clean up a specific session folder and Redis progress"""
    session_folder = os.path.join(DOCUMENT_TEMP_FOLDER, f"session_{session_id}")

    deleted = False
    try:
        if os.path.exists(session_folder):
            shutil.rmtree(session_folder)
            deleted = True

        # Always try to clean up Redis progress
        delete_progress(session_id)

        return {"message": "Session cleaned up successfully", "deleted": deleted}
    except Exception as e:
        logger.error(f"Error cleaning up session {session_id}: {e}")
        return {"message": f"Error cleaning up session: {str(e)}", "deleted": False}

@router.get("/cleanup")
async def cleanup_all_sessions():
    """Clean up all orphaned sessions"""
    return await cleanup_orphaned_sessions()

# Initialize on import
init_document_service()