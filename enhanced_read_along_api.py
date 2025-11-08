# enhanced_read_along_api.py

from fastapi import APIRouter, HTTPException, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any, Tuple
from bisect import bisect_right
from pydantic import BaseModel
from collections import OrderedDict
from read_along_cache import get_cache_stats
import asyncio
import time
import logging
from database import get_db
from models import Track, Album, User, CampaignTier
from auth import login_required
from read_along_cache import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    get_page_cache_key,
    get_cached_page,
    set_cached_page,
    get_page_lock,
    cleanup_page_lock,
    record_cache_miss
)

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 1000
TOKENIZATION_CONCURRENCY = 32

PAGE_CACHE_MAX_ENTRIES = 5000
TIMINGS_CACHE_MAX = 256
TEXT_CACHE_MAX = 128
PAGE_PLAN_CACHE_MAX = 256
SPAN_CACHE_MAX = 256

SENTENCE_SEARCH_FWD = 50
SENTENCE_SEARCH_BACK = 50
SENTENCE_MIN_WORDS = 10

class SearchRequest(BaseModel):
    query: str
    voice_id: str
    page_size: Optional[int] = None

def check_read_along_access(user: User, creator_id: int, db: Session) -> bool:
    if user.is_creator or user.is_team:
        return True
    
    if not user.patreon_tier_data:
        return False
    
    tier_title = user.patreon_tier_data.get('title')
    if not tier_title:
        return False
    
    tier = db.query(CampaignTier).filter(
        CampaignTier.creator_id == creator_id,
        CampaignTier.title == tier_title,
        CampaignTier.is_active == True
    ).first()
    
    return tier.read_along_access if tier else False

async def check_read_along_access_async(user: User, creator_id: int, db: Session) -> bool:
    return await run_in_threadpool(check_read_along_access, user, creator_id, db)

def resolve_word_index_for_time(words: List[Dict[str, Any]], t: float, tolerance: float = 0.25) -> Tuple[int, str]:
    """Map playback time to stable word index"""
    if not words:
        return -1, "no_words"

    starts = [w["start_time"] for w in words]
    ends = [w["end_time"] for w in words]

    if t <= starts[0] - tolerance:
        return 0, "before_start"
    if t >= ends[-1] + tolerance:
        return len(words) - 1, "after_end"

    i = bisect_right(starts, t) - 1
    if i < 0:
        return 0, "before_first"

    if starts[i] <= t < (ends[i] + tolerance):
        return i, "inside_or_padding"
    if t - ends[i] <= tolerance:
        return i, "between_prev_close"
    return min(i + 1, len(words) - 1), "between_next"

class _LRU:
    """Tiny LRU using OrderedDict"""
    def __init__(self, cap: int):
        self.cap = cap
        self._d: "OrderedDict[str, Any]" = OrderedDict()

    def get(self, k: str):
        v = self._d.get(k)
        if v is None:
            return None
        self._d.move_to_end(k)
        return v

    def set(self, k: str, v: Any):
        self._d[k] = v
        self._d.move_to_end(k)
        if len(self._d) > self.cap:
            self._d.popitem(last=False)

class ReadAlongService:
    """Read-along service with file-storage integration (paged-only, sentence-safe)"""

    def __init__(self):
        self.page_cache = _LRU(PAGE_CACHE_MAX_ENTRIES)
        self.timings_cache = _LRU(TIMINGS_CACHE_MAX)
        self.text_cache = _LRU(TEXT_CACHE_MAX)
        self.plan_cache = _LRU(PAGE_PLAN_CACHE_MAX)
        self.spans_cache = _LRU(SPAN_CACHE_MAX)
        self._cpu_sem = asyncio.Semaphore(TOKENIZATION_CONCURRENCY)

        from text_storage_service import text_storage_service
        self.text_service = text_storage_service

    def clear_all_caches(self):
        """Clear all caches to force recalculation"""
        self.page_cache = _LRU(PAGE_CACHE_MAX_ENTRIES)
        self.timings_cache = _LRU(TIMINGS_CACHE_MAX)
        self.text_cache = _LRU(TEXT_CACHE_MAX)
        self.plan_cache = _LRU(PAGE_PLAN_CACHE_MAX)
        self.spans_cache = _LRU(SPAN_CACHE_MAX)

    async def get_read_along_data(self, track_id: str, voice_id: str, page: Optional[int], page_size: Optional[int], db: Session) -> Dict[str, Any]:
        """Always paginated. If page is None, default to page 0"""
        page = 0 if page is None else int(page)
        page_size = min(max(int(page_size or DEFAULT_PAGE_SIZE), 10), MAX_PAGE_SIZE)
        
        if page == 0:
            self.clear_all_caches()

        def _fetch_track_album():
            track = db.query(Track).filter(Track.id == track_id).first()
            album = db.query(Album).filter(Album.id == track.album_id).first() if track else None
            return track, album

        track, album = await run_in_threadpool(_fetch_track_album)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        if getattr(track, "track_type", "audio") != "tts":
            raise HTTPException(status_code=400, detail="Track is not a TTS track")

        # Load word timings
        timings_key = f"{track_id}:{voice_id}"
        word_timings = self.timings_cache.get(timings_key)
        
        if word_timings is None:
            from text_storage_service import text_storage_service
            try:
                word_timings = await text_storage_service.get_word_timings(track_id, voice_id, db)
            except Exception as e:
                logger.error(f"Failed to load timings for {track_id}:{voice_id}: {e}")
                word_timings = []
            
            self.timings_cache.set(timings_key, word_timings)

        if not word_timings:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "page": page,
                "page_size": page_size,
                "total_words": 0,
                "total_pages": 0,
                "sourceText": "",
                "mappedTokens": [],
                "wordTimings": [],
                "status": "no_timings",
                "data_source": "file_storage_enhanced",
            }

        total_words = len(word_timings)

        # Load source text
        text_key = f"{track_id}"
        original_text = self.text_cache.get(text_key)
        
        if original_text is None:
            try:
                original_text = await self.text_service.get_source_text(track_id, db)
            except Exception as e:
                logger.error(f"Failed to load text for {track_id}: {e}")
                original_text = ""
            
            if not original_text:
                original_text = " ".join([w.get("word", "") for w in word_timings])
            
            self.text_cache.set(text_key, original_text)

        # Build page plan
        plan_key = f"{track_id}:{voice_id}:{page_size}"
        page_plan = self.plan_cache.get(plan_key)
        
        if page_plan is None:
            async with self._cpu_sem:
                page_plan = await run_in_threadpool(self._build_sentence_page_plan, word_timings, original_text, page_size)
            self.plan_cache.set(plan_key, page_plan)

        total_pages = len(page_plan)
        
        if total_pages == 0:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "page": page,
                "page_size": page_size,
                "total_words": total_words,
                "total_pages": 0,
                "sourceText": "",
                "mappedTokens": [],
                "wordTimings": [],
                "status": "no_timings",
                "data_source": "file_storage_enhanced",
            }

        if page < 0 or page >= total_pages:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "page": page,
                "page_size": page_size,
                "total_words": total_words,
                "total_pages": total_pages,
                "sourceText": "",
                "mappedTokens": [],
                "wordTimings": [],
                "status": "page_out_of_range",
                "data_source": "file_storage_enhanced",
            }

        start_idx, end_idx_ex = page_plan[page]
        page_words = word_timings[start_idx:end_idx_ex]

        # Check cache
        cache_key = f"{track_id}:{voice_id}:{page_size}:{page}"
        cached = self.page_cache.get(cache_key)
        if cached:
            payload = self._build_page_payload(track_id, voice_id, page, page_size, total_words, total_pages, page_words, cached["sourceText"], cached["mappedTokens"], start_idx, end_idx_ex)
            payload["status"] = "success"
            payload["data_source"] = "file_storage_enhanced"
            payload["punctuation_restored"] = True
            return payload

        # Build spans
        span_key = f"{track_id}:{voice_id}"
        spans = self.spans_cache.get(span_key)
        if spans is None:
            spans = await run_in_threadpool(self._build_word_char_spans_robust, word_timings, original_text)
            self.spans_cache.set(span_key, spans)

        page_text_segment, seg_start_char, seg_end_char = await run_in_threadpool(self._extract_text_segment_precise, original_text, spans, start_idx, end_idx_ex)

        async with self._cpu_sem:
            page_tokens = await run_in_threadpool(self._tokens_from_spans, original_text, spans, page_words, start_idx, seg_start_char, seg_end_char)

        self.page_cache.set(cache_key, {"sourceText": page_text_segment, "mappedTokens": page_tokens})

        return self._build_page_payload(track_id, voice_id, page, page_size, total_words, total_pages, page_words, page_text_segment, page_tokens, start_idx, end_idx_ex)

    def _search_impl(self, word_timings: List[Dict[str, Any]], query: str, page_size: int, track_id: str, voice_id: str, page_plan: List[Tuple[int, int]]) -> List[Dict[str, Any]]:
        """Synchronous search implementation to run in threadpool"""
        word_to_page: Dict[int, int] = {}
        for page_idx, (start_idx, end_idx_ex) in enumerate(page_plan):
            for word_idx in range(start_idx, end_idx_ex):
                if word_idx < len(word_timings):
                    word_to_page[word_idx] = page_idx

        q = query.lower().strip()
        matches_scored: List[Dict[str, Any]] = []

        def _score_single_token(t: str, q_: str) -> int:
            t = (t or "").lower()
            if not q_ or not t:
                return 0
            if t == q_:
                return 3
            if t.startswith(q_):
                return 2
            if q_ in t:
                return 1
            return 0

        if " " in q:
            words_list = [w.get("word", "").lower() for w in word_timings]
            query_words = [w for w in q.split() if w]
            query_len = len(query_words)

            if query_len == 0:
                return []

            for start_idx in range(len(words_list) - query_len + 1):
                window = words_list[start_idx : start_idx + query_len]
                window_text = " ".join(window)

                if window_text == q:
                    score = 5
                elif self._fuzzy_phrase_match(q, window_text):
                    score = 4
                else:
                    continue

                primary_word_idx = start_idx
                page = word_to_page.get(primary_word_idx, 0)

                context_start = max(0, start_idx - 3)
                context_end = min(len(word_timings), start_idx + query_len + 3)
                ctx_words = [word_timings[i]["word"] for i in range(context_start, context_end)]

                phrase_words = [word_timings[i]["word"] for i in range(start_idx, start_idx + query_len)]
                context_text = " ".join(ctx_words)
                matched_phrase = " ".join(phrase_words)

                matches_scored.append({
                    "page": page,
                    "word_index": primary_word_idx,
                    "position": primary_word_idx,
                    "match": matched_phrase,
                    "context": f"...{context_text}...",
                    "start_time": word_timings[primary_word_idx].get("start_time"),
                    "end_time": word_timings[start_idx + query_len - 1].get("end_time"),
                    "phrase_length": query_len,
                    "_score": score,
                })
        else:
            for word_index, wd in enumerate(word_timings):
                token = wd.get("word", "")
                w = token.lower()
                if not w or q not in w:
                    continue

                score = _score_single_token(w, q)
                if score <= 0:
                    continue

                page = word_to_page.get(word_index, 0)
                start_ctx = max(0, word_index - 3)
                end_ctx = min(len(word_timings), word_index + 4)
                ctx_words = [word_timings[i]["word"] for i in range(start_ctx, end_ctx)]

                matches_scored.append({
                    "page": page,
                    "word_index": word_index,
                    "position": word_index,
                    "match": wd["word"],
                    "context": f"...{' '.join(ctx_words)}...",
                    "start_time": wd.get("start_time"),
                    "end_time": wd.get("end_time"),
                    "phrase_length": 1,
                    "_score": score,
                })

        matches_scored.sort(key=lambda m: (-m.get("_score", 0), m.get("word_index", 0)))
        return [{k: v for k, v in m.items() if k != "_score"} for m in matches_scored]

    async def search_in_text(self, word_timings: List[Dict[str, Any]], query: str, page_size: int, track_id: str, voice_id: str, db: Session) -> List[Dict[str, Any]]:
        """Enhanced search that supports both single words and multi-word phrases"""
        if not query or not word_timings:
            return []

        plan_key = f"{track_id}:{voice_id}:{page_size}"
        page_plan = self.plan_cache.get(plan_key)
        if page_plan is None:
            text_key = f"{track_id}"
            original_text = self.text_cache.get(text_key)
            if original_text is None:
                try:
                    original_text = await self.text_service.get_source_text(track_id, db)
                except Exception:
                    original_text = ""
                if not original_text:
                    original_text = " ".join([w.get("word", "") for w in word_timings])
                self.text_cache.set(text_key, original_text)

            async with self._cpu_sem:
                page_plan = await run_in_threadpool(self._build_sentence_page_plan, word_timings, original_text, page_size)
            self.plan_cache.set(plan_key, page_plan)

        async with self._cpu_sem:
            return await run_in_threadpool(self._search_impl, word_timings, query, page_size, track_id, voice_id, page_plan)

    def _fuzzy_phrase_match(self, query: str, text: str) -> bool:
        """Check if query matches text with some punctuation/spacing flexibility"""
        import re
        
        def normalize_for_search(s):
            s = re.sub(r'[^\w\s]', ' ', s)
            s = re.sub(r'\s+', ' ', s)
            return s.strip()
        
        norm_query = normalize_for_search(query)
        norm_text = normalize_for_search(text)
        
        return norm_query in norm_text

    @staticmethod
    def _normalize_lookup_text(s: str) -> str:
        """Normalize only width-preserving characters so indices stay aligned"""
        if not s:
            return s
        s = s.replace('\u201c', '"')
        s = s.replace('\u201d', '"')
        s = s.replace('\u201e', '"')
        s = s.replace('\u00ab', '"')
        s = s.replace('\u00bb', '"')
        s = s.replace('\u2018', "'")
        s = s.replace('\u2019', "'")
        s = s.replace('\u201a', "'")
        s = s.replace('\u2014', '-')
        s = s.replace('\u2013', '-')
        return s

    @staticmethod
    def _is_boundary_char(ch: Optional[str]) -> bool:
        return (ch is None) or (not ch.isalnum())

    def _build_word_char_spans_robust(self, word_timings: List[Dict[str, Any]], full_text: str) -> List[Tuple[int, int]]:
        """Map each timing word to a [start, end) span in full_text using robust multi-strategy search"""
        n = len(word_timings)
        spans: List[Tuple[int, int]] = [(-1, -1)] * n
        if not full_text or n == 0:
            return spans

        lookup = self._normalize_lookup_text(full_text).lower()
        pos = 0
        L = len(lookup)
        
        successful_maps = 0

        for i, wd in enumerate(word_timings):
            w = (wd.get("word") or "").strip()
            if not w:
                continue
            lw = self._normalize_lookup_text(w).lower()
            if not lw:
                continue

            # Try sequential search from last position
            found = lookup.find(lw, pos)
            if found != -1:
                start = found
                end = found + len(lw)
                prev_ch = lookup[start - 1] if start > 0 else None
                next_ch = lookup[end] if end < L else None
                if self._is_boundary_char(prev_ch) and self._is_boundary_char(next_ch):
                    spans[i] = (start, end)
                    pos = end
                    successful_maps += 1
                    continue

            # Try window search
            search_start = max(0, pos - 100)
            search_end = min(L, pos + 1000)
            found = lookup.find(lw, search_start)
            while found != -1 and found < search_end:
                start = found
                end = found + len(lw)
                prev_ch = lookup[start - 1] if start > 0 else None
                next_ch = lookup[end] if end < L else None
                if self._is_boundary_char(prev_ch) and self._is_boundary_char(next_ch):
                    spans[i] = (start, end)
                    pos = end
                    successful_maps += 1
                    break
                found = lookup.find(lw, found + 1)
                if found >= search_end:
                    break
            else:
                # Global fallback search
                found = lookup.find(lw)
                while found != -1:
                    start = found
                    end = found + len(lw)
                    prev_ch = lookup[start - 1] if start > 0 else None
                    next_ch = lookup[end] if end < L else None
                    if self._is_boundary_char(prev_ch) and self._is_boundary_char(next_ch):
                        spans[i] = (start, end)
                        successful_maps += 1
                        break
                    found = lookup.find(lw, found + 1)

        success_rate = (successful_maps / n * 100) if n > 0 else 0
        if success_rate < 95:
            logger.warning(f"Span mapping: {successful_maps}/{n} words ({success_rate:.1f}%)")

        return spans

    def _extract_text_segment_precise(self, full_text: str, spans: List[Tuple[int, int]], start_word_idx: int, end_word_idx_ex: int) -> Tuple[str, int, int]:
        """Enhanced text extraction combining span precision with natural sentence boundaries"""
        if not full_text or start_word_idx >= end_word_idx_ex:
            return "", 0, 0

        n = len(spans)
        start_word_idx = max(0, min(start_word_idx, n - 1))
        end_word_idx_ex = max(start_word_idx + 1, min(end_word_idx_ex, n))

        s_idx = start_word_idx
        while s_idx < end_word_idx_ex and spans[s_idx][0] < 0:
            s_idx += 1
        e_idx = end_word_idx_ex - 1
        while e_idx >= start_word_idx and spans[e_idx][1] < 0:
            e_idx -= 1

        if s_idx >= end_word_idx_ex or e_idx < start_word_idx:
            return "", 0, 0

        try:
            seg_start = spans[s_idx][0]
            seg_end = spans[e_idx][1]
            
            # Find natural sentence start
            natural_start = seg_start
            if seg_start > 0:
                look_back_start = max(0, seg_start - 200)
                for i in range(seg_start - 1, look_back_start - 1, -1):
                    if full_text[i] in '.!?':
                        j = i + 1
                        while j < seg_start and full_text[j] in '"\')}] \t\n':
                            j += 1
                        if j <= seg_start:
                            natural_start = j
                            break
            
            # Find natural sentence end
            natural_end = seg_end
            terminal_set = {'.', '!', '?'}
            closers = {'"', "'", '"', '"', ')', ']', '}', '»'}
            
            i = seg_end
            while i < len(full_text):
                ch = full_text[i]
                if ch.isspace():
                    i += 1
                elif ch in terminal_set:
                    i += 1
                    while i < len(full_text) and full_text[i] in closers:
                        i += 1
                    if i < len(full_text) and full_text[i] == ' ':
                        i += 1
                    natural_end = i
                    break
                elif ch in ',:;-':
                    i += 1
                else:
                    break
                    
                if i > seg_end + 100:
                    break
            
            return full_text[natural_start:natural_end], natural_start, natural_end
            
        except Exception:
            seg_start = spans[s_idx][0]
            seg_end = spans[e_idx][1]
            return full_text[seg_start:seg_end], seg_start, seg_end

    def _tokens_from_spans(
        self,
        full_text: str,
        spans: List[Tuple[int, int]],
        page_words: List[Dict[str, Any]],
        global_start_idx: int,
        seg_start_char: int,
        seg_end_char: int
    ) -> List[Dict[str, Any]]:
        """Enhanced tokenization combining span precision with sequential positioning"""
        tokens: List[Dict[str, Any]] = []
        if seg_end_char <= seg_start_char or not page_words:
            return tokens

        def _append_gap_text(gap: str) -> None:
            if not gap:
                return
            letters = sum(1 for ch in gap if ch.isalnum())
            if letters and (letters / max(1, len(gap))) > 0.33:
                tokens.append({
                    "text": " ",
                    "type": "punctuation",
                    "hasTimings": False,
                    "start_time": None,
                    "end_time": None,
                })
            else:
                self._add_spacing_and_punctuation_natural(tokens, gap)

        word_positions = []
        search_cursor = seg_start_char

        for local_i, wd in enumerate(page_words):
            gi = global_start_idx + local_i
            if gi < 0 or gi >= len(spans):
                word_positions.append((-1, -1, local_i))
                continue

            s, e = spans[gi]
            if s < 0 or e < 0:
                word_positions.append((-1, -1, local_i))
                continue

            s_clamped = max(s, seg_start_char, search_cursor)
            e_clamped = min(e, seg_end_char)

            if s_clamped < seg_end_char and e_clamped > s_clamped:
                word_positions.append((s_clamped, e_clamped, local_i))
                search_cursor = e_clamped
            else:
                word_positions.append((-1, -1, local_i))

        cursor = seg_start_char

        for pos_start, pos_end, word_idx in word_positions:
            word_data = page_words[word_idx]

            if pos_start == -1:
                if tokens and tokens[-1]["type"] == "word":
                    tokens.append({
                        "text": " ",
                        "type": "punctuation",
                        "hasTimings": False,
                        "start_time": None,
                        "end_time": None,
                    })

                tokens.append({
                    "text": word_data["word"],
                    "type": "word",
                    "hasTimings": True,
                    "start_time": word_data["start_time"],
                    "end_time": word_data["end_time"],
                    "timing_index": word_data.get("word_index", global_start_idx + word_idx),
                    "page_index": word_idx,
                    "word_index": word_data.get("word_index", global_start_idx + word_idx),
                    "segment_index": word_data.get("segment_index"),
                    "duration": word_data.get("duration", word_data["end_time"] - word_data["start_time"]),
                })
                continue

            if pos_start > cursor:
                between_text = full_text[cursor:pos_start]
                _append_gap_text(between_text)

            tokens.append({
                "text": word_data["word"],
                "type": "word",
                "hasTimings": True,
                "start_time": word_data["start_time"],
                "end_time": word_data["end_time"],
                "timing_index": word_data.get("word_index", global_start_idx + word_idx),
                "page_index": word_idx,
                "word_index": word_data.get("word_index", global_start_idx + word_idx),
                "segment_index": word_data.get("segment_index"),
                "duration": word_data.get("duration", word_data["end_time"] - word_data["start_time"]),
            })

            cursor = pos_end

        if cursor < seg_end_char:
            remaining_text = full_text[cursor:seg_end_char]
            _append_gap_text(remaining_text)

        return tokens

    def _add_spacing_and_punctuation_natural(self, tokens: List[Dict[str, Any]], text: str) -> None:
        """Emit only: a single space for any whitespace run, and exact runs of non-word, non-space punctuation"""
        if not text:
            return

        import re
        for m in re.finditer(r'(\s+)|([^\w\s]+)', text, flags=re.UNICODE):
            ws, punct = m.groups()
            if ws:
                tokens.append({
                    "text": " ",
                    "type": "punctuation",
                    "hasTimings": False,
                    "start_time": None,
                    "end_time": None,
                })
            elif punct:
                tokens.append({
                    "text": punct,
                    "type": "punctuation",
                    "hasTimings": False,
                    "start_time": None,
                    "end_time": None,
                })

    def _build_sentence_page_plan(self, word_timings: List[Dict[str, Any]], full_text: str, page_size: int) -> List[Tuple[int, int]]:
        """Returns a list of (start_idx, end_idx_exclusive) per page"""
        n = len(word_timings)
        if n == 0:
            return []

        spans = self._build_word_char_spans(word_timings, full_text)

        is_end = [False] * n
        text_len = len(full_text)

        def next_non_space_at(pos: int) -> Optional[str]:
            i = pos
            while i < text_len and full_text[i].isspace():
                i += 1
            return full_text[i] if i < text_len else None

        terminal_set = {'.', '!', '?'}
        closers = {'"', "'", '"', '"', ')', ']', '}', '»'}

        for i, (s, e) in enumerate(spans):
            if s < 0 or e < 0:
                continue
            c1 = next_non_space_at(e)
            if c1 in terminal_set:
                is_end[i] = True
                continue
            j = e
            saw_closers = False
            while j < text_len and full_text[j] in closers.union({' ', '\t', '\r', '\n'}):
                if full_text[j] in closers:
                    saw_closers = True
                j += 1
            if j < text_len and full_text[j] in terminal_set:
                is_end[i] = True

        pages: List[Tuple[int, int]] = []
        idx = 0
        while idx < n:
            if len(pages) == 0:
                start_idx = 0
            else:
                start_idx = pages[-1][1]

            if start_idx >= n:
                break

            nominal_end_inclusive = min(start_idx + page_size - 1, n - 1)
            min_end = min(nominal_end_inclusive, start_idx + max(SENTENCE_MIN_WORDS - 1, 0))

            chosen = None
            for j in range(nominal_end_inclusive, min(n-1, nominal_end_inclusive + SENTENCE_SEARCH_FWD) + 1):
                if j >= min_end and is_end[j]:
                    chosen = j
                    break

            if chosen is None:
                back_limit = max(start_idx + SENTENCE_MIN_WORDS - 1, nominal_end_inclusive - SENTENCE_SEARCH_BACK)
                for j in range(nominal_end_inclusive, back_limit - 1, -1):
                    if j >= min_end and is_end[j]:
                        chosen = j
                        break

            end_inclusive = chosen if chosen is not None else nominal_end_inclusive

            pages.append((start_idx, end_inclusive + 1))
            idx = end_inclusive + 1

        return pages

    def _build_word_char_spans(self, word_timings: List[Dict[str, Any]], full_text: str) -> List[Tuple[int, int]]:
        """Sequential, case-insensitive scan to map each word to (start_char, end_char)"""
        spans: List[Tuple[int, int]] = [(-1, -1)] * len(word_timings)
        if not full_text:
            return spans

        lt = full_text.lower()
        pos = 0
        for i, wd in enumerate(word_timings):
            w = (wd.get("word") or "").strip()
            if not w:
                continue
            lw = w.lower()
            found = lt.find(lw, pos)
            if found == -1:
                found = lt.find(lw, max(0, pos - 5))
                if found == -1:
                    spans[i] = (-1, -1)
                    continue
            start = found
            end = found + len(lw)
            spans[i] = (start, end)
            pos = end
        return spans

    def _build_page_payload(self, track_id: str, voice_id: str, page: int, page_size: int, total_words: int, total_pages: int, page_words: List[Dict[str, Any]], page_text_segment: str, page_tokens: List[Dict[str, Any]], start_idx: int, end_idx_ex: int) -> Dict[str, Any]:
        page_time_range = {
            "start_time": page_words[0]["start_time"] if page_words else 0.0,
            "end_time": page_words[-1]["end_time"] if page_words else 0.0,
        }
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "page": page,
            "page_size": page_size,
            "total_words": total_words,
            "total_pages": total_pages,
            "sourceText": page_text_segment,
            "mappedTokens": page_tokens,
            "wordTimings": page_words,
            "word_range": {
                "start_index": start_idx,
                "end_index": end_idx_ex - 1,
                "count": len(page_words),
            },
            "page_time_range": page_time_range,
            "has_next": page < total_pages - 1,
            "has_prev": page > 0,
            "status": "success",
            "data_source": "file_storage_enhanced",
            "punctuation_restored": True,
        }

read_along_service = ReadAlongService()

@router.get("/api/tracks/{track_id}/read-along/{voice_id}")
async def get_read_along_data(
    track_id: str,
    voice_id: str,
    response: Response,
    page: Optional[int] = Query(None, ge=0),
    page_size: Optional[int] = Query(None, ge=10, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required),
):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    def _fetch_track_album():
        track = db.query(Track).filter(Track.id == track_id).first()
        album = db.query(Album).filter(Album.id == track.album_id).first() if track else None
        return track, album

    track, album = await run_in_threadpool(_fetch_track_album)
    
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    has_access = False
    if current_user.is_creator and album.created_by_id == current_user.id:
        has_access = True
    elif current_user.is_team and album.created_by_id == current_user.created_by:
        has_access = True
    elif current_user.created_by and album.created_by_id == current_user.created_by:
        has_access = True

    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")

    if not await check_read_along_access_async(current_user, album.created_by_id, db):
        raise HTTPException(status_code=403, detail="Read-along access not available for your tier")
    
    page = 0 if page is None else int(page)
    page_size = min(max(int(page_size or DEFAULT_PAGE_SIZE), 10), MAX_PAGE_SIZE)
    
    cache_key = await get_page_cache_key(track_id, voice_id, page, page_size, db)
    
    # Fast path: check cache without lock
    cached = await get_cached_page(cache_key)
    if cached:
        return cached
    
    # Slow path: acquire lock to prevent duplicate work
    page_lock = await get_page_lock(cache_key)
    
    async with page_lock:
        # Double-check cache
        cached = await get_cached_page(cache_key)
        if cached:
            return cached
        
        # Generate page
        start_time = time.time()
        page_data = await read_along_service.get_read_along_data(
            track_id, voice_id, page, page_size, db
        )
        recompute_ms = (time.time() - start_time) * 1000
        
        # Record miss with timing
        await record_cache_miss(recompute_ms)
        
        # Cache the result
        try:
            await set_cached_page(cache_key, page_data)
        except Exception as e:
            logger.warning(f"Failed to cache page: {e}")
    
    await cleanup_page_lock(cache_key)
    
    return page_data

@router.get("/api/tracks/{track_id}/word-at-time")
async def find_word_at_time(
    track_id: str,
    time: float,
    voice_id: str,
    tolerance: float = Query(0.25, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required),
):
    """Find word index at specific time"""
    def _fetch_track_album():
        track = db.query(Track).filter(Track.id == track_id).first()
        album = db.query(Album).filter(Album.id == track.album_id).first() if track else None
        return track, album

    track, album = await run_in_threadpool(_fetch_track_album)
    if not track or not album:
        raise HTTPException(status_code=404, detail="Track or album not found")

    has_access = False
    if current_user.is_creator and album.created_by_id == current_user.id:
        has_access = True
    elif current_user.is_team and album.created_by_id == current_user.created_by:
        has_access = True
    elif current_user.created_by and album.created_by_id == current_user.created_by:
        has_access = True

    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")

    if not await check_read_along_access_async(current_user, album.created_by_id, db):
        raise HTTPException(status_code=403, detail="Read-along access not available for your tier")

    try:
        from text_storage_service import text_storage_service
        words = await text_storage_service.get_word_timings(track_id, voice_id, db)
        if not words:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "time": time,
                "word_index": -1,
                "status": "no_timings",
            }

        idx, reason = resolve_word_index_for_time(words, time, tolerance)
        if idx < 0:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "time": time,
                "word_index": -1,
                "status": "not_found",
            }

        status = "found" if reason in ("inside_or_padding",) else "closest"
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "time": time,
            "word_index": idx,
            "word": words[idx]["word"],
            "word_timing": words[idx],
            "reason": reason,
            "status": status,
        }

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to find word")

@router.get("/api/tracks/{track_id}/time-for-word")
async def get_time_for_word(
    track_id: str,
    word_index: int,
    voice_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required),
):
    """Get time for a specific word index"""
    def _fetch_track_album():
        track = db.query(Track).filter(Track.id == track_id).first()
        album = db.query(Album).filter(Album.id == track.album_id).first() if track else None
        return track, album

    track, album = await run_in_threadpool(_fetch_track_album)
    if not track or not album:
        raise HTTPException(status_code=404, detail="Track or album not found")

    has_access = False
    if current_user.is_creator and album.created_by_id == current_user.id:
        has_access = True
    elif current_user.is_team and album.created_by_id == current_user.created_by:
        has_access = True
    elif current_user.created_by and album.created_by_id == current_user.created_by:
        has_access = True

    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")

    if not await check_read_along_access_async(current_user, album.created_by_id, db):
        raise HTTPException(status_code=403, detail="Read-along access not available for your tier")

    try:
        from text_storage_service import text_storage_service
        words = await text_storage_service.get_word_timings(track_id, voice_id, db)

        if not words:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "word_index": word_index,
                "time": None,
                "status": "no_timings",
            }

        if 0 <= word_index < len(words):
            word = words[word_index]
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "word_index": word_index,
                "time": word["start_time"],
                "word": word["word"],
                "word_timing": word,
                "status": "found",
            }

        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "word_index": word_index,
            "time": None,
            "status": "invalid_index",
        }

    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get time")

@router.get("/api/tracks/{track_id}/page-info")
async def get_page_info(
    track_id: str,
    voice_id: str,
    word_index: Optional[int] = None,
    time: Optional[float] = None,
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=10, le=1000),
    tolerance: float = Query(0.25, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required),
):
    """Get page information for a given word index or playback time using sentence-aware pagination"""
    def _fetch_track_album():
        track = db.query(Track).filter(Track.id == track_id).first()
        album = db.query(Album).filter(Album.id == track.album_id).first() if track else None
        return track, album

    track, album = await run_in_threadpool(_fetch_track_album)
    if not track or not album:
        raise HTTPException(status_code=404, detail="Track or album not found")

    has_access = False
    if current_user.is_creator and album.created_by_id == current_user.id:
        has_access = True
    elif current_user.is_team and album.created_by_id == current_user.created_by:
        has_access = True
    elif current_user.created_by and album.created_by_id == current_user.created_by:
        has_access = True

    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")

    if not await check_read_along_access_async(current_user, album.created_by_id, db):
        raise HTTPException(status_code=403, detail="Read-along access not available for your tier")

    try:
        from text_storage_service import text_storage_service
        words = await text_storage_service.get_word_timings(track_id, voice_id, db)

        if not words:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "total_pages": 0,
                "current_page": 0,
                "status": "no_timings",
            }

        if word_index is not None:
            target_index = max(0, min(word_index, len(words) - 1))
            reason = "explicit_index"
        elif time is not None:
            target_index, reason = resolve_word_index_for_time(words, time, tolerance)
            if target_index < 0:
                target_index, reason = 0, "fallback_zero"
        else:
            target_index, reason = 0, "default_zero"

        plan_key = f"{track_id}:{voice_id}:{page_size}"
        page_plan = read_along_service.plan_cache.get(plan_key)
        if page_plan is None:
            text_key = f"{track_id}"
            original_text = read_along_service.text_cache.get(text_key)
            if original_text is None:
                try:
                    original_text = await read_along_service.text_service.get_source_text(track_id, db)
                except Exception:
                    original_text = ""
                if not original_text:
                    original_text = " ".join([w.get("word", "") for w in words])
                read_along_service.text_cache.set(text_key, original_text)
            
            async with read_along_service._cpu_sem:
                page_plan = await run_in_threadpool(read_along_service._build_sentence_page_plan, words, original_text, page_size)
            read_along_service.plan_cache.set(plan_key, page_plan)

        total_pages = len(page_plan)
        
        current_page = 0
        for page_idx, (start_idx, end_idx_ex) in enumerate(page_plan):
            if start_idx <= target_index < end_idx_ex:
                current_page = page_idx
                break

        if current_page < len(page_plan):
            page_start_idx, page_end_idx_ex = page_plan[current_page]
            page_end_idx = page_end_idx_ex - 1
            words_on_page = page_end_idx_ex - page_start_idx
        else:
            page_start_idx = page_end_idx = 0
            words_on_page = 0

        page_start_time = words[page_start_idx]["start_time"] if words and page_start_idx < len(words) else 0.0
        page_end_time = words[page_end_idx]["end_time"] if words and page_end_idx < len(words) else 0.0

        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "word_index": target_index,
            "reason": reason,
            "current_page": current_page,
            "total_pages": total_pages,
            "page_size": page_size,
            "words_on_page": words_on_page,
            "page_bounds": {
                "start_index": page_start_idx,
                "end_index": page_end_idx,
                "start_time": page_start_time,
                "end_time": page_end_time,
            },
            "status": "success",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get page info: {str(e)}")

@router.post("/api/tracks/{track_id}/search")
async def search_read_along_text(
    track_id: str,
    search_request: SearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required),
):
    """Search for text within the read-along content (maps to sentence-aware pages)"""
    def _fetch_track_album():
        track = db.query(Track).filter(Track.id == track_id).first()
        album = db.query(Album).filter(Album.id == track.album_id).first() if track else None
        return track, album

    track, album = await run_in_threadpool(_fetch_track_album)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    has_access = False
    if current_user.is_creator and album.created_by_id == current_user.id:
        has_access = True
    elif current_user.is_team and album.created_by_id == current_user.created_by:
        has_access = True
    elif current_user.created_by and album.created_by_id == current_user.created_by:
        has_access = True
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")

    if not await check_read_along_access_async(current_user, album.created_by_id, db):
        raise HTTPException(status_code=403, detail="Read-along access not available for your tier")

    if getattr(track, "track_type", "audio") != "tts":
        raise HTTPException(status_code=400, detail="Track is not a TTS track")

    from text_storage_service import text_storage_service
    word_timings = await text_storage_service.get_word_timings(track_id, search_request.voice_id, db) or []

    page_size = min(search_request.page_size or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
    matches = await read_along_service.search_in_text(word_timings, search_request.query, page_size, track_id, search_request.voice_id, db)

    return {
        "track_id": track_id,
        "voice_id": search_request.voice_id,
        "query": search_request.query,
        "matches": matches,
        "total_matches": len(matches),
        "page_size": page_size,
        "status": "success",
        "data_source": "file_storage_enhanced",
        "punctuation_restored": True,
    }

@router.get("/api/admin/read-along/cache-stats")
async def get_cache_statistics(current_user: User = Depends(login_required)):
    """Get read-along cache statistics for monitoring"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Admin only")
    
    return await get_cache_stats()

__all__ = ["router", "read_along_service"]