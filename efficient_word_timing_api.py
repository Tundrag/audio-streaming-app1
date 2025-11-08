# efficient_word_timing_api.py - FIXED VERSION with punctuation and paragraphs

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
import asyncio
import logging
import math
import re
from datetime import datetime, timezone

from database import get_db
from models import Track, Album, User
from auth import login_required
from hls_streaming import stream_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# Configuration
DEFAULT_PAGE_SIZE = 200
SEGMENT_DURATION = 30
WORD_CACHE_TTL = 300

# In-memory cache
word_segment_cache = {}

class WordTimingOptimizer:
    """Optimized word timing with proper text structure preservation"""
    
    def __init__(self):
        self.segment_cache = {}
        self.index_cache = {}
        self.text_structure_cache = {}  # Cache for text structure
    
    async def get_word_index_at_time(self, track_id: str, voice_id: str, time: float, db: Session) -> Dict:
        """Get word index at specific time for player sync"""
        try:
            segment_index = int(time // SEGMENT_DURATION)
            cache_key = f"{track_id}:{voice_id}:seg_{segment_index}"
            
            segment_words = self.segment_cache.get(cache_key)
            
            if not segment_words:
                segment_words = await self._get_words_for_segment(
                    track_id, voice_id, segment_index, db
                )
                
                if segment_words:
                    self.segment_cache[cache_key] = {
                        'words': segment_words,
                        'cached_at': datetime.now(),
                        'segment_index': segment_index
                    }
            else:
                segment_words = segment_words['words']
            
            if not segment_words:
                return {
                    "track_id": track_id,
                    "voice_id": voice_id,
                    "time": time,
                    "word_index": -1,
                    "segment_index": segment_index,
                    "status": "no_words_in_segment"
                }
            
            word_index = self._binary_search_word_in_segment(segment_words, time)
            
            if word_index >= 0:
                word_data = segment_words[word_index]
                return {
                    "track_id": track_id,
                    "voice_id": voice_id,
                    "time": time,
                    "word_index": word_data.get('global_index', word_index),
                    "segment_index": segment_index,
                    "segment_word_index": word_index,
                    "word": word_data.get('word', ''),
                    "start_time": word_data.get('start_time'),
                    "end_time": word_data.get('end_time'),
                    "status": "found"
                }
            
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "time": time,
                "word_index": -1,
                "segment_index": segment_index,
                "status": "not_found_in_segment"
            }
            
        except Exception as e:
            logger.error(f"Error getting word index at time: {str(e)}")
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "time": time,
                "word_index": -1,
                "status": "error",
                "error": str(e)
            }
    
    async def get_paginated_words_with_structure(
        self, 
        track_id: str, 
        voice_id: str, 
        page: int = 0,
        page_size: int = DEFAULT_PAGE_SIZE,
        db: Session = None
    ) -> Dict:
        """Get paginated words WITH text structure (punctuation, paragraphs)"""
        try:
            offset = page * page_size
            
            # Get track to access source text
            track = db.query(Track).filter(Track.id == track_id).first()
            if not track:
                raise ValueError("Track not found")
            
            source_text = getattr(track, 'source_text', '')
            
            # Get total word count
            total_words = await self._get_total_word_count(track_id, voice_id, db)
            
            if offset >= total_words:
                return {
                    "track_id": track_id,
                    "voice_id": voice_id,
                    "page": page,
                    "page_size": page_size,
                    "total_words": total_words,
                    "total_pages": math.ceil(total_words / page_size),
                    "words": [],
                    "tokens": [],
                    "has_next": False,
                    "has_prev": page > 0,
                    "status": "page_out_of_range"
                }
            
            # Get words for this page with timing
            page_words = await self._get_words_range_with_structure(
                track_id, voice_id, offset, page_size, db, source_text
            )
            
            # Build structured tokens (words + punctuation + paragraphs)
            tokens = await self._build_page_tokens(
                page_words, source_text, offset
            )
            
            total_pages = math.ceil(total_words / page_size)
            
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "page": page,
                "page_size": page_size,
                "total_words": total_words,
                "total_pages": total_pages,
                "words": page_words,  # Raw word timings
                "tokens": tokens,      # Structured tokens with punctuation
                "word_range": {
                    "start_index": offset,
                    "end_index": min(offset + page_size, total_words) - 1,
                    "count": len(page_words)
                },
                "has_next": page < total_pages - 1,
                "has_prev": page > 0,
                "status": "success"
            }
            
        except Exception as e:
            logger.error(f"Error getting paginated words: {str(e)}")
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "page": page,
                "page_size": page_size,
                "total_words": 0,
                "words": [],
                "tokens": [],
                "status": "error",
                "error": str(e)
            }
    
    async def _build_page_tokens(self, page_words: List[Dict], source_text: str, offset: int) -> List[Dict]:
        """Build structured tokens with punctuation and paragraph breaks"""
        if not page_words or not source_text:
            return []
        
        tokens = []
        
        # Split source text into paragraphs
        paragraphs = source_text.split('\n\n')
        
        # Track position in source text
        word_index = 0
        global_word_index = offset
        
        for para_idx, paragraph in enumerate(paragraphs):
            if not paragraph.strip():
                continue
            
            # Add paragraph break if not first paragraph
            if para_idx > 0 and tokens:
                tokens.append({
                    'type': 'paragraph_break',
                    'text': '\n\n',
                    'hasTimings': False
                })
            
            # Tokenize paragraph preserving punctuation
            para_tokens = self._tokenize_paragraph(paragraph)
            
            for token_text in para_tokens:
                if self._is_word(token_text):
                    # This is a word - match with timing
                    if word_index < len(page_words):
                        word_data = page_words[word_index]
                        tokens.append({
                            'type': 'word',
                            'text': token_text,
                            'hasTimings': True,
                            'start_time': word_data.get('start_time'),
                            'end_time': word_data.get('end_time'),
                            'global_index': global_word_index,
                            'page_index': word_index
                        })
                        word_index += 1
                        global_word_index += 1
                    else:
                        # Word without timing (shouldn't happen)
                        tokens.append({
                            'type': 'word',
                            'text': token_text,
                            'hasTimings': False
                        })
                else:
                    # This is punctuation or whitespace
                    tokens.append({
                        'type': 'punctuation',
                        'text': token_text,
                        'hasTimings': False
                    })
        
        return tokens
    
    def _tokenize_paragraph(self, text: str) -> List[str]:
        """Tokenize paragraph preserving all punctuation and spacing"""
        # Pattern to split on word boundaries while keeping everything
        pattern = r'(\b\w+(?:\'\w+)?(?:-\w+)*\b|[^\w\s]|\s+)'
        tokens = re.findall(pattern, text)
        return [t for t in tokens if t]  # Remove empty tokens
    
    def _is_word(self, token: str) -> bool:
        """Check if token is a word (contains alphanumeric characters)"""
        return bool(re.search(r'\w', token))
    
    async def _get_words_range_with_structure(
        self, 
        track_id: str, 
        voice_id: str, 
        offset: int, 
        limit: int, 
        db: Session,
        source_text: str
    ) -> List[Dict]:
        """Get word range with structure information"""
        try:
            # Get all words from HLS
            all_words = await stream_manager.get_words_for_segment_precise(
                track_id, voice_id, None, db
            )
            
            if not all_words:
                return []
            
            # Slice to get requested range
            end_offset = min(offset + limit, len(all_words))
            page_words = all_words[offset:end_offset]
            
            # Enhance words with metadata
            for i, word in enumerate(page_words):
                word['page_index'] = i
                word['global_index'] = offset + i
                
                # Preserve original word text (don't strip punctuation)
                if 'original_text' not in word and 'word' in word:
                    word['original_text'] = word['word']
            
            return page_words
            
        except Exception as e:
            logger.error(f"Error getting words range: {str(e)}")
            return []
    
    async def _get_words_for_segment(
        self, 
        track_id: str, 
        voice_id: str, 
        segment_index: int, 
        db: Session
    ) -> List[Dict]:
        """Get words for specific HLS segment"""
        try:
            start_time = segment_index * SEGMENT_DURATION
            end_time = (segment_index + 1) * SEGMENT_DURATION
            
            all_words = await stream_manager.get_words_for_segment_precise(
                track_id, voice_id, segment_index, db
            )
            
            if not all_words:
                return []
            
            segment_words = []
            for i, word in enumerate(all_words):
                word_start = word.get('start_time', 0)
                word_end = word.get('end_time', word_start)
                
                if (word_start < end_time and word_end > start_time):
                    word_copy = word.copy()
                    word_copy['global_index'] = i
                    word_copy['segment_index'] = segment_index
                    segment_words.append(word_copy)
            
            return segment_words
            
        except Exception as e:
            logger.error(f"Error getting words for segment {segment_index}: {str(e)}")
            return []
    
    def _binary_search_word_in_segment(self, segment_words: List[Dict], time: float) -> int:
        """Binary search for word at time"""
        if not segment_words:
            return -1
        
        left, right = 0, len(segment_words) - 1
        result = -1
        
        while left <= right:
            mid = (left + right) // 2
            word = segment_words[mid]
            start_time = word.get('start_time', 0)
            end_time = word.get('end_time', start_time)
            
            if start_time <= time < end_time:
                return mid
            elif start_time <= time:
                result = mid
                left = mid + 1
            else:
                right = mid - 1
        
        return result
    
    async def _get_total_word_count(self, track_id: str, voice_id: str, db: Session) -> int:
        """Get total word count (cached)"""
        cache_key = f"{track_id}:{voice_id}:count"
        
        if cache_key in self.index_cache:
            cached = self.index_cache[cache_key]
            if (datetime.now() - cached['cached_at']).seconds < WORD_CACHE_TTL:
                return cached['count']
        
        try:
            all_words = await stream_manager.get_words_for_segment_precise(
                track_id, voice_id, None, db
            )
            count = len(all_words) if all_words else 0
            
            self.index_cache[cache_key] = {
                'count': count,
                'cached_at': datetime.now()
            }
            
            return count
            
        except Exception as e:
            logger.error(f"Error getting total word count: {str(e)}")
            return 0

# Global optimizer instance
word_optimizer = WordTimingOptimizer()

# ========================================
# API ENDPOINTS
# ========================================

@router.get("/api/tracks/{track_id}/word-at-time-fast")
async def find_word_at_time_optimized(
    track_id: str,
    time: float,
    voice_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Fast word lookup for player sync - segment-based"""
    return await word_optimizer.get_word_index_at_time(track_id, voice_id, time, db)

@router.get("/api/tracks/{track_id}/words-paginated")
async def get_words_paginated(
    track_id: str,
    voice_id: str,
    page: int = Query(0, ge=0, description="Page number (0-based)"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=10, le=1000, description="Words per page"),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get paginated words WITH text structure (punctuation, paragraphs)"""
    return await word_optimizer.get_paginated_words_with_structure(
        track_id, voice_id, page, page_size, db
    )

@router.get("/api/tracks/{track_id}/segment-words/{segment_index}")
async def get_segment_words(
    track_id: str,
    segment_index: int,
    voice_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get words for a specific HLS segment"""
    try:
        words = await word_optimizer._get_words_for_segment(
            track_id, voice_id, segment_index, db
        )
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "segment_index": segment_index,
            "words": words,
            "word_count": len(words),
            "segment_start": segment_index * SEGMENT_DURATION,
            "segment_end": (segment_index + 1) * SEGMENT_DURATION,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error getting segment words: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get segment words")

@router.post("/api/tracks/{track_id}/clear-word-cache")
async def clear_word_cache(
    track_id: str,
    voice_id: Optional[str] = None,
    current_user: User = Depends(login_required)
):
    """Clear word timing cache for a track"""
    try:
        cleared_keys = []
        
        if voice_id:
            pattern = f"{track_id}:{voice_id}:"
            keys_to_clear = [k for k in word_optimizer.segment_cache.keys() if k.startswith(pattern)]
            keys_to_clear.extend([k for k in word_optimizer.index_cache.keys() if k.startswith(pattern)])
        else:
            pattern = f"{track_id}:"
            keys_to_clear = [k for k in word_optimizer.segment_cache.keys() if k.startswith(pattern)]
            keys_to_clear.extend([k for k in word_optimizer.index_cache.keys() if k.startswith(pattern)])
        
        for key in keys_to_clear:
            word_optimizer.segment_cache.pop(key, None)
            word_optimizer.index_cache.pop(key, None)
            word_optimizer.text_structure_cache.pop(key, None)
            cleared_keys.append(key)
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "cleared_keys": len(cleared_keys),
            "status": "success"
        }
        
    except Exception as e:
        logger.error(f"Error clearing word cache: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to clear cache")

# Export router
__all__ = ['router', 'word_optimizer']