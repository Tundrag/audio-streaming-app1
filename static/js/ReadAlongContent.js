// ReadAlongContent.js - COMPLETE: Anti-flicker word highlighting + boundary de-duplication + full features

class ReadAlongContent {
    constructor(core) {
        this.core = core;

        // Content data
        this.sourceText = '';
        this.mappedTokens = [];
        this.currentSentenceIndex = -1;
        this.lastAudioTime = -1;
        this.lastLogTime = 0; // For throttling logs

        // PURE WORD TIMING SYSTEM (No fallbacks)
        this.preciseWordTimings = []; // Raw word timings from API -> DOM mapped
        this.wordToSentenceMap = new Map(); // word_index -> sentence_index
        this.currentWordIndex = -1;

        // ANTI-FLICKER: Track current highlighted elements
        this.currentHighlightedWordElement = null;
        this.previousWordIndex = -1;
        this.currentHighlightedSentenceElement = null;
        this.previousSentenceIndex = -1;

        // Stickiness windows (override via localStorage)
        this.wordStickyMs = Number(localStorage.getItem('raWordStickyMs') || 2000); // ~comma pause
        this.sentenceStickyMs = Number(localStorage.getItem('raSentenceStickyMs') || 1000);

        // Track last confirmed word + when it was last seen (in audio time)
        this._lastWord = null;                  // last non-null currentWord object
        this._lastWordSeenAtAudioT = 0;         // audio time (seconds) we last had a word

        // Auto-navigation with race protection
        this.autoNavigating = false;
        this.lastNavigationCheck = 0;
        this.navigationThrottle = 2000;
        this._navToken = 0;

        // Real user scrolling detection
        this.userScrolling = false;

        // Search
        this.searchTerm = '';
        this.searchMatches = [];
        this.currentSearchIndex = -1;
        this.searchActive = false;
        this.serverSearchResults = [];
        this.currentServerSearchIndex = -1;
        this.isSearching = false;

        // Optional tuning var (unused externally)
        this.sentenceGapThreshold = parseFloat(localStorage.getItem('readAlongSentenceGap') || '0.4');

        // Active token list used during rendering (so lookahead uses sanitized tokens)
        this._activeTokens = null;

        this.initializeScrollDetection();
    }

    // === Audio / time ===

    getCurrentAudioTime(shouldLog = true) {
        if (!this.core.player?.audio) {
            if (shouldLog) console.log('ReadAlong: No audio player available');
            return 0;
        }
        const rawTime = this.core.player.audio.currentTime || 0;
        const offsetSeconds = (this.core.playbackOffsetMs || 0) / 1000;
        const adjustedTime = Math.max(0, rawTime - offsetSeconds);
        if (shouldLog) {
        }
        return adjustedTime;
    }

    // === Scroll / UI ===

    initializeScrollDetection() {
        setTimeout(() => {
            if (this.core.overlay) {
                const scroller = this.core.overlay.querySelector('#textContent')?.parentElement || this.core.overlay;
                let scrollTimer;
                scroller.addEventListener('scroll', () => {
                    this.userScrolling = true;
                    clearTimeout(scrollTimer);
                    scrollTimer = setTimeout(() => (this.userScrolling = false), 120);
                }, { passive: true });
            }
        }, 100);
    }

    clearState() {

        // Text and mapping
        this.sourceText = '';
        this.mappedTokens = [];
        this.currentSentenceIndex = -1;
        this.lastAudioTime = -1;

        // Word timings
        this.preciseWordTimings = [];
        this.wordToSentenceMap.clear();
        this.currentWordIndex = -1;

        // Anti-flicker
        this.currentHighlightedWordElement = null;
        this.previousWordIndex = -1;
        this.currentHighlightedSentenceElement = null;
        this.previousSentenceIndex = -1;

        // Sticky trackers
        this._lastWord = null;
        this._lastWordSeenAtAudioT = 0;

        // Search
        this.searchTerm = '';
        this.searchMatches = [];
        this.currentSearchIndex = -1;
        this.searchActive = false;
        this.serverSearchResults = [];
        this.currentServerSearchIndex = -1;
        this.isSearching = false;

        // Auto-nav
        this.autoNavigating = false;
        this.lastNavigationCheck = 0;
        this._navToken = 0;
        this.userScrolling = false;

        // DOM cleanup
        if (this.core.overlay) {
            const textContent = this.core.overlay.querySelector('#textContent');
            if (textContent) textContent.innerHTML = '';

            this.core.overlay.querySelectorAll('.ra-sentence.ra-active').forEach(el => el.classList.remove('ra-active'));
            this.core.overlay.querySelectorAll('.ra-current-word').forEach(el => el.classList.remove('ra-current-word'));
            this.core.overlay.querySelectorAll('.ra-search-match').forEach(el => {
                const parent = el.parentElement;
                parent.replaceChild(document.createTextNode(el.textContent), el);
                parent.normalize();
            });
        }
    }

    // === Data loading ===

    async loadContent() {
        try {
            this.core.ui?.showState?.('loading');

            const readAlongData = await this.loadReadAlongData();
            if (!readAlongData || (!readAlongData.sourceText && !readAlongData.wordTimings?.length)) {
                throw new Error('No content available for the selected voice');
            }

            // Clear container
            const textContent = this.core.overlay.querySelector('#textContent');
            if (textContent) textContent.innerHTML = '';

            // Store raw data
            this.sourceText = readAlongData.sourceText || '';
            this.mappedTokens = readAlongData.mappedTokens || [];
            this.rawWordTimings = readAlongData.wordTimings || [];

            if (readAlongData.totalWords) this.core.totalWords = readAlongData.totalWords;
            if (readAlongData.total_pages) this.core.totalPages = readAlongData.total_pages;

            this.core.ui?.updatePaginationUI?.();
            this.renderText();
            this.buildTimingIndex();
            this.core.ui?.showState?.('content');
            this.initializeScrollDetection();

        } catch (error) {
            console.error('ReadAlong: Content loading failed:', error);
            this.core.ui?.showError?.(error.message || 'Failed to load content for selected voice');
        }
    }

    async loadReadAlongData() {
        const voiceId = this.getCurrentVoice() || this.core.voiceId;
        let url = `/api/tracks/${encodeURIComponent(this.core.trackId)}/read-along/${encodeURIComponent(voiceId)}`;

        if (this.core.pagingMode === 'paged') {
            const params = new URLSearchParams({
                page: String(this.core.currentPage),
                page_size: String(this.core.pageSize),
                _t: Date.now() // Cache-busting timestamp
            });
            url += `?${params}`;
        } else {
            url += `?_t=${Date.now()}`; // Cache-busting for non-paged mode
        }


        try {
            const response = await fetch(url, { 
                credentials: 'include',
                cache: 'no-store' // Force fresh data
            });

            if (!response.ok) {
                const errorText = await response.text();
                console.error('ReadAlong Debug - Error Response:', errorText);
                
                // Handle access control specifically
                if (response.status === 403) {
                    let errorMessage = 'Read-along access not available for your tier';
                    
                    try {
                        const errorData = JSON.parse(errorText);
                        if (errorData.detail) {
                            errorMessage = errorData.detail;
                        } else if (errorData.error && errorData.error.message) {
                            errorMessage = errorData.error.message;
                        }
                    } catch (parseError) {
                        // Use errorText directly if it's not JSON
                        if (errorText.includes('access not available')) {
                            errorMessage = errorText;
                        }
                    }
                    
                    // Throw error with access control flag so button handler can detect it
                    const accessError = new Error(errorMessage);
                    accessError.isAccessControl = true;
                    accessError.statusCode = 403;
                    throw accessError;
                }
                
                throw new Error(`API Error ${response.status}: ${errorText}`);
            }

            const responseText = await response.text();

            let data;
            try {
                data = JSON.parse(responseText);
            } catch (jsonError) {
                console.error('ReadAlong Debug - JSON Parse Error:', jsonError);
                throw new Error('Server returned invalid JSON');
            }


            if (!data || (!data.sourceText && !data.wordTimings?.length)) {
                throw new Error('No content in server response');
            }

            return {
                sourceText: data.sourceText || this._reconstructTextFromWordTimings(data.wordTimings),
                mappedTokens: data.mappedTokens || this._convertWordTimingsToTokens(data.wordTimings),
                wordTimings: data.wordTimings || [],
                totalWords: data.total_words || data.totalWords || data.wordTimings?.length || 0,
                total_pages: data.total_pages || 1,
                voiceId: voiceId,
                dataSource: data.data_source || 'unknown',
                punctuationRestored: data.punctuation_restored || false
            };
        } catch (error) {
            console.error('ReadAlong Debug - Full Error:', error);
            throw error; // Re-throw to preserve access control information
        }
    }


    _reconstructTextFromWordTimings(wordTimings) {
        if (!wordTimings || !wordTimings.length) return '';
        try {
            return wordTimings.map(w => w.word).join(' ');
        } catch (e) {
            return '';
        }
    }

    _convertWordTimingsToTokens(wordTimings) {
        if (!wordTimings || !wordTimings.length) return [];
        try {
            const tokens = [];
            wordTimings.forEach((word, index) => {
                tokens.push({
                    text: word.word,
                    type: 'word',
                    hasTimings: true,
                    start_time: word.start_time,
                    end_time: word.end_time,
                    timing_index: index,
                    word_index: word.word_index ?? index,
                    duration: word.duration || (word.end_time - word.start_time)
                });
                if (index < wordTimings.length - 1) {
                    tokens.push({ text: ' ', type: 'punctuation', hasTimings: false });
                }
            });
            return tokens;
        } catch (e) {
            return [];
        }
    }

    // === Pagination helpers ===

    async goToPreviousPage() {
        if (this.core.currentPage > 0) {
            this.core.currentPage--;
            await this.loadContent();
        }
    }

    async goToNextPage() {
        if (this.core.currentPage < this.core.totalPages - 1) {
            this.core.currentPage++;
            await this.loadContent();
        }
    }

    // === Rendering ===

    renderText() {
        const container = this.core.overlay.querySelector('#textContent');
        if (!container) return;

        container.innerHTML = '';
        if (!this.mappedTokens || this.mappedTokens.length === 0) {
            this.core.ui?.showError?.('No content mapping available for selected voice');
            return;
        }
        this.renderRawTimingText(container);
    }

    // Sanitize tokens to kill page-boundary tails & duplicates
    _sanitizeAndTrimPageTokens(tokens) {
        if (!Array.isArray(tokens) || !tokens.length) return [];

        // 1) Drop any "punctuation" token that actually carries letters/digits (these are leaked tail words)
        const isAlphaNum = (ch) => /[A-Za-z0-9]/.test(ch); // conservative (ASCII covers our leak cases)
        const filtered = tokens.filter(t => !(t?.type === 'punctuation' && t.text && isAlphaNum(t.text)));

        // 2) On non-first pages, trim ALL leading non-word tokens before the first timed word.
        //    (Prevents stray period/ellipsis/quote from previous page.)
        if (this.core.pagingMode === 'paged' && (this.core.currentPage || 0) > 0) {
            const firstWordIdx = filtered.findIndex(t => t?.type === 'word' && t.hasTimings);
            if (firstWordIdx > 0) {
                // Optionally keep one opening quote just before first word
                const keepOpeners = new Set(['"', '“', '‘', '«', '(', '[']);
                const prev = filtered[firstWordIdx - 1];
                const startIdx = (prev && prev.type === 'punctuation' && keepOpeners.has(prev.text)) ? (firstWordIdx - 1) : firstWordIdx;
                return filtered.slice(startIdx);
            }
        }

        return filtered;
    }

    // Enhanced text rendering with natural sentence highlighting
    renderRawTimingText(container) {
        // Use sanitized page tokens
        const TOKENS = this._sanitizeAndTrimPageTokens(this.mappedTokens);
        this._activeTokens = TOKENS;


        let htmlContent = '';
        let currentSentence = '';
        let sentenceIndex = 0;
        let wordCount = 0;
        let wordsInCurrentSentence = 0;

        const esc = (s) => {
            const d = document.createElement('div');
            d.textContent = s;
            return d.innerHTML;
        };

        for (let i = 0; i < TOKENS.length; i++) {
            const token = TOKENS[i];

            if (token.type === 'word' && token.hasTimings) {
                const attrs = [
                    `class="ra-word"`,
                    `data-start-time="${token.start_time}"`,
                    `data-end-time="${token.end_time}"`,
                    `data-sentence="${sentenceIndex}"`,
                    `data-timing-index="${token.timing_index ?? wordCount}"`
                ];
                if (typeof token.word_index === 'number') {
                    attrs.push(`data-word-index="${token.word_index}"`);
                }
                currentSentence += `<span ${attrs.join(' ')}>${esc(token.text)}</span>`;
                wordCount++;
                wordsInCurrentSentence++;
            } else if (token.type === 'punctuation') {
                currentSentence += `<span class="ra-punctuation">${esc(token.text)}</span>`;
            }

            const shouldEnd = this._shouldEndSentenceNatural(
                token,
                TOKENS[i + 1],
                wordsInCurrentSentence,
                i,
                sentenceIndex,
                TOKENS
            );

            if (shouldEnd) {
                if (currentSentence.trim()) {
                    htmlContent += this.wrapSentence(currentSentence.trim(), sentenceIndex);
                    currentSentence = '';
                    sentenceIndex++;
                    wordsInCurrentSentence = 0;
                }
            }
        }

        if (currentSentence.trim()) {
            htmlContent += this.wrapSentence(currentSentence.trim(), sentenceIndex);
        }

        if (htmlContent.trim()) {
            container.innerHTML = `<div class="ra-paragraph" data-page="${this.core.currentPage || 0}">${htmlContent}</div>`;
        }

        this._activeTokens = null;

    }

    // Natural sentence boundary detection for proper highlighting
    _shouldEndSentenceNatural(currentToken, nextToken, wordsInSentence, tokenIndex, sentenceIndex, TOKENS) {
        // Minimum words for natural flow
        if (wordsInSentence < 8) {
            // But allow finishing if we hit terminal punctuation near the end of the page
            // (handled below)
        }

        const isEOS = currentToken?.type === 'punctuation' && ['.', '!', '?'].includes(currentToken.text);

        if (isEOS) {
            // Look-ahead to see next word
            let nextWordText = null;
            for (let i = tokenIndex + 1; i < TOKENS.length; i++) {
                const t = TOKENS[i];
                if (t?.type === 'word') {
                    nextWordText = t.text;
                    break;
                }
            }
            const nextCap = nextWordText ? nextWordText[0] === nextWordText[0].toUpperCase() : false;
            const longEnough = wordsInSentence >= 12;

            // End if new sentence likely begins OR we're at end of stream
            if (nextCap || longEnough || !nextWordText) return true;
        }

        // Hard cap to prevent overly long sentences
        if (wordsInSentence >= 30) return true;

        return false;
    }

    wrapSentence(sentenceContent, index) {
        const clean = sentenceContent.trim().replace(/\s+/g, ' ');
        return `<span class="ra-sentence" data-sentence-index="${index}">${clean}</span> `;
    }

    // === Timing index ===

    buildTimingIndex() {
        const container = this.core.overlay.querySelector('#textContent');
        if (!container) {
            this.preciseWordTimings = [];
            this.wordToSentenceMap.clear();
            return;
        }

        this.preciseWordTimings = [];
        this.wordToSentenceMap.clear();

        const sentenceEls = Array.from(container.querySelectorAll('.ra-sentence'));

        sentenceEls.forEach(sentenceEl => {
            const sentenceIndex = parseInt(sentenceEl.dataset.sentenceIndex);
            const words = Array.from(sentenceEl.querySelectorAll('.ra-word[data-start-time]'));


            words.forEach(wordEl => {
                const startTime = parseFloat(wordEl.dataset.startTime);
                const endTime = parseFloat(wordEl.dataset.endTime);
                const timingIndex = parseInt(wordEl.dataset.timingIndex) || -1;

                if (isNaN(startTime) || isNaN(endTime)) {
                    return;
                }

                const wordTiming = {
                    globalIndex: timingIndex,
                    sentenceIndex,
                    startTime,
                    endTime,
                    element: wordEl,
                    word: (wordEl.textContent || '').trim()
                };

                this.preciseWordTimings.push(wordTiming);
                this.wordToSentenceMap.set(timingIndex, sentenceIndex);

            });
        });

        this.preciseWordTimings.sort((a, b) => a.startTime - b.startTime);

        if (this.preciseWordTimings.length > 0) {
            const first = this.preciseWordTimings[0];
            const last = this.preciseWordTimings[this.preciseWordTimings.length - 1];
        }
    }

    // === Word lookup ===

    findCurrentWordByPreciseTiming(currentTime, shouldLog = true) {
        if (!this.preciseWordTimings.length) {
            if (shouldLog) console.log('ReadAlong: No word timings available');
            return null;
        }

        // Binary search
        let left = 0, right = this.preciseWordTimings.length - 1, best = null;
        while (left <= right) {
            const mid = (left + right) >> 1;
            const w = this.preciseWordTimings[mid];
            if (currentTime >= w.startTime && currentTime <= w.endTime) {
                if (shouldLog) {
                }
                return w;
            }
            if (currentTime < w.startTime) right = mid - 1;
            else { best = w; left = mid + 1; }
        }
        // Allow 50ms grace after a word
        if (best && (currentTime - best.endTime) <= 0.05) {
            if (shouldLog) {
            }
            return best;
        }
        if (shouldLog) console.log(`NO WORD MATCH for time ${currentTime.toFixed(3)}s`);
        return null;
    }

    // === Highlighting (sentence + word) ===

    updateSentenceHighlighting() {
        if (!this.core.player?.audio || !this.core.highlightingActive) return;
        if (this.core.player.audio.paused) return;

        const currentTime = this.getCurrentAudioTime();

        const now = Date.now();
        const timeDiff = Math.abs(currentTime - this.lastAudioTime);
        const shouldLog = !this.lastLogTime || (now - this.lastLogTime) > 100 || timeDiff > 0.05;

        // Paging-aware auto-nav (throttled)
        if (this.core.pagingMode === 'paged' && this.core.totalPages > 1) {
            if (now - this.lastNavigationCheck > this.navigationThrottle) {
                this.lastNavigationCheck = now;
                this.checkAndNavigateToCorrectPage(currentTime);
            }
        }

        let currentWord = this.findCurrentWordByPreciseTiming(currentTime, shouldLog);

        if (!currentWord && this._lastWord) {
            const gapS = Math.max(0, currentTime - (this._lastWordSeenAtAudioT || 0));
            if (gapS <= (this.wordStickyMs / 1000)) currentWord = this._lastWord;
        }

        let targetSentenceIndex = -1;

        if (currentWord) {
            targetSentenceIndex = currentWord.sentenceIndex;

            if (currentWord !== this._lastWord) {
                this._lastWord = currentWord;
                this._lastWordSeenAtAudioT = currentTime;
            }
            if (shouldLog) {
            }
        } else {
            const gapS = Math.max(0, currentTime - (this._lastWordSeenAtAudioT || 0));
            if (this.currentSentenceIndex >= 0 && gapS <= (this.sentenceStickyMs / 1000)) {
                targetSentenceIndex = this.currentSentenceIndex;
                if (shouldLog) console.log(`STICKY SENTENCE: holding sentence ${targetSentenceIndex} during ${(gapS * 1000).toFixed(0)}ms gap`);
            } else {
                targetSentenceIndex = -1;
                if (shouldLog) console.log(`NO CURRENT WORD at ${currentTime.toFixed(3)}s -> CLEAR highlighting (gap: ${(gapS * 1000).toFixed(0)}ms)`);
            }
        }

        if (targetSentenceIndex !== this.previousSentenceIndex) {
            if (this.currentHighlightedSentenceElement) {
                this.currentHighlightedSentenceElement.classList.remove('ra-active');
                if (shouldLog) console.log(`CLEARED highlight from sentence ${this.previousSentenceIndex}`);
            }

            if (targetSentenceIndex >= 0) {
                const el = this.core.overlay.querySelector(`[data-sentence-index="${targetSentenceIndex}"]`);
                if (el) {
                    el.classList.add('ra-active');
                    this.currentHighlightedSentenceElement = el;

                    // <<< NEW: don't auto-scroll if we just navigated search or search UI active >>>
                    const suppress = (this.searchActive === true) ||
                                     (this.suppressAutoScrollUntil && Date.now() < this.suppressAutoScrollUntil);

                    if (!this.userScrolling && !suppress) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                    if (shouldLog) console.log(`HIGHLIGHTED sentence ${targetSentenceIndex} at time ${currentTime.toFixed(3)}s`);
                } else {
                    this.currentHighlightedSentenceElement = null;
                }
            } else {
                this.currentHighlightedSentenceElement = null;
            }

            this.currentSentenceIndex = targetSentenceIndex;
            this.previousSentenceIndex = targetSentenceIndex;
        }

        this.lastAudioTime = currentTime;
        if (shouldLog) this.lastLogTime = now;

        this.updateCurrentWordHighlight(currentWord, currentTime, shouldLog);
    }

    // Anti-flicker word highlight with sticky gap
    updateCurrentWordHighlight(currentWord, currentTime, shouldLog = true) {
        if (!currentWord) {
            const gapS = Math.max(0, currentTime - (this._lastWordSeenAtAudioT || 0));
            if (gapS <= (this.wordStickyMs / 1000)) return; // keep
            if (this.currentHighlightedWordElement) {
                this.currentHighlightedWordElement.classList.remove('ra-current-word');
                this.currentHighlightedWordElement = null;
                this.previousWordIndex = -1;
            }
            this.currentWordIndex = -1;
            return;
        }

        const newWordIndex = currentWord.globalIndex ?? -1;
        const newEl = currentWord.element || null;
        if (newWordIndex === this.previousWordIndex && newEl === this.currentHighlightedWordElement) return;

        if (this.currentHighlightedWordElement) {
            this.currentHighlightedWordElement.classList.remove('ra-current-word');
        }
        if (newEl) newEl.classList.add('ra-current-word');

        this.currentHighlightedWordElement = newEl;
        this.previousWordIndex = newWordIndex;
        this.currentWordIndex = newWordIndex;

        this._lastWord = currentWord;
        this._lastWordSeenAtAudioT = currentTime;

        if (shouldLog) console.log(`WORD HIGHLIGHT: "${currentWord.word}" (index: ${newWordIndex})`);
    }

    // === Auto-scroll / seek ===

    autoScrollToCurrentPosition() {
        if (!this.core.player?.audio || !this.preciseWordTimings.length) {
            return;
        }
        const currentTime = this.getCurrentAudioTime();
        if (currentTime <= 0) {
            return;
        }
        if (this.core.pagingMode === 'paged' && this.core.totalPages > 1) {
            this.checkAndNavigateToCorrectPage(currentTime);
            return;
        }
        const currentWord = this.findCurrentWordByPreciseTiming(currentTime, false);
        if (currentWord && currentWord.element && !this.userScrolling) {
            currentWord.element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else {
        }
    }

    getCurrentVoice() {
        if (this.core.player?.currentVoice) return this.core.player.currentVoice;
        if (this.core.player?.voiceExtension?.getCurrentVoice) {
            const v = this.core.player.voiceExtension.getCurrentVoice();
            if (v) return v;
        }
        if (this.core.currentVoiceId) return this.core.currentVoiceId;
        if (this.core.voiceId) return this.core.voiceId;
        return null;
    }

    handleContentClick(e) {
        const target = e.target.closest('[data-sentence-index], [data-start-time]');
        if (!target) return;

        if (target.dataset.startTime) {
            e.stopPropagation();
            const sentenceEl = target.closest('.ra-sentence');
            const idx = sentenceEl ? parseInt(sentenceEl.dataset.sentenceIndex) : -1;
            if (idx >= 0) {
                if (e.shiftKey) {
                    const ts = parseFloat(target.dataset.startTime);
                    this.seekToTimeWithPrecision(ts);
                    this.activateSentence(idx, { seek: false });
                } else {
                    this.activateSentence(idx, { seek: true, preroll: 0.02 });
                }
            }
        } else if (target.dataset.sentenceIndex) {
            const idx = parseInt(target.dataset.sentenceIndex);
            this.activateSentence(idx, { seek: true, preroll: 0.02 });
        }

        // ALT+click: live offset calibration
        if (e.altKey && target.dataset.startTime) {
            e.preventDefault();
            e.stopPropagation();
            const wordT = parseFloat(target.dataset.startTime);
            const curT = this.core.player.audio.currentTime;
            const deltaMs = Math.round((wordT - curT) * 1000);
            this.core.playbackOffsetMs += deltaMs;
            localStorage.setItem('readAlongSyncOffsetMs', String(this.core.playbackOffsetMs));
            this.core.ui?.updateSyncUI?.();
            this.updateSentenceHighlighting();
        }
    }

    activateSentence(index, { seek = true, preroll = 0.02 } = {}) {
        const firstWordInSentence = this.preciseWordTimings.find(w => w.sentenceIndex === index);
        if (!firstWordInSentence) {
            return;
        }

        if (this.currentHighlightedSentenceElement) {
            this.currentHighlightedSentenceElement.classList.remove('ra-active');
        }

        const el = this.core.overlay.querySelector(`[data-sentence-index="${index}"]`);
        if (el) {
            el.classList.add('ra-active');
            this.currentHighlightedSentenceElement = el;
            this.currentSentenceIndex = index;
            this.previousSentenceIndex = index;
            if (!this.userScrolling) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        if (seek && this.core.player?.audio) {
            const seekTime = Math.max(0, firstWordInSentence.startTime - preroll);
            this.seekToTimeWithPrecision(seekTime);
        }
    }

    async seekToTimeWithPrecision(targetTime, tolerance = 0.05) {
        if (!this.core.player?.audio?.duration || targetTime < 0 || targetTime > this.core.player.audio.duration) {
            return false;
        }

        const snap = this.findCurrentWordByPreciseTiming(targetTime, false);
        if (snap) {
            targetTime = snap.startTime;
        }

        const cur = this.core.player.audio.currentTime;
        if (Math.abs(cur - targetTime) < tolerance) return true;

        try {
            // ✅ Seek first
            this.core.player.audio.currentTime = targetTime;

            await new Promise(resolve => {
                const onSeeked = () => {
                    this.core.player.audio.removeEventListener('seeked', onSeeked);
                    resolve();
                };
                this.core.player.audio.addEventListener('seeked', onSeeked, { once: true });
            });

            // ✅ After seek, navigate pages for the *new* time
            let pageChanged = false;
            if (this.core.pagingMode === 'paged' && this.core.totalPages > 1) {
                pageChanged = await this.checkAndNavigateToCorrectPage(targetTime);
            }

            // ✅ If paused, re-apply highlight so it doesn't vanish after reload
            if (this.core.player.audio.paused) {
                this._forceHighlightAtTime(targetTime);
            } else if (pageChanged) {
                // If we were playing and page changed, give highlighting a nudge quickly
                setTimeout(() => this.updateSentenceHighlighting(), 50);
            }

            return true;
        } catch {
            return false;
        }
    }

    _forceHighlightAtTime(t) {
        const w = this.findCurrentWordByPreciseTiming(t, false);
        if (!w) return;

        // Sentence
        if (this.currentHighlightedSentenceElement) {
            this.currentHighlightedSentenceElement.classList.remove('ra-active');
        }
        const sentEl = this.core.overlay?.querySelector(`[data-sentence-index="${w.sentenceIndex}"]`);
        if (sentEl) {
            sentEl.classList.add('ra-active');
            this.currentHighlightedSentenceElement = sentEl;
            this.currentSentenceIndex = w.sentenceIndex;
            this.previousSentenceIndex = w.sentenceIndex;
        }

        // Word
        if (this.currentHighlightedWordElement) {
            this.currentHighlightedWordElement.classList.remove('ra-current-word');
        }
        if (w.element) {
            w.element.classList.add('ra-current-word');
            this.currentHighlightedWordElement = w.element;
            this.currentWordIndex = w.globalIndex ?? -1;
            this.previousWordIndex = w.globalIndex ?? -1;
        }

        // Keep sticky state coherent
        this._lastWord = w;
        this._lastWordSeenAtAudioT = t;
    }

    // === Auto page navigation ===

    async checkAndNavigateToCorrectPage(currentTime) {
        if (this.autoNavigating || this.core.pagingMode !== 'paged') return false;

        this.autoNavigating = true;
        // ✅ Use the provided time, not the stale audio time
        const reqAt = (typeof currentTime === 'number') ? currentTime : this.getCurrentAudioTime(false);

        try {
            const voiceId = this.getCurrentVoice() || this.core.voiceId;
            const response = await fetch(
                `/api/tracks/${encodeURIComponent(this.core.trackId)}/page-info?time=${reqAt}&voice_id=${encodeURIComponent(voiceId)}&page_size=${this.core.pageSize}`,
                { credentials: 'include' }
            );

            if (!response.ok) {
                return false;
            }

            const pageInfo = await response.json();

            // ✅ Stale check vs the same reference time
            const nowT = (typeof currentTime === 'number') ? currentTime : this.getCurrentAudioTime(false);
            if (Math.abs(nowT - reqAt) > 1.0) return false;

            const targetPage = pageInfo.current_page;

            if (typeof targetPage === 'number' &&
                targetPage !== this.core.currentPage &&
                targetPage >= 0 &&
                targetPage < pageInfo.total_pages) {
                this.core.currentPage = targetPage;
                await this.loadContent(); // will rebuild DOM
                return true; // ✅ tell caller we changed pages
            }

            return false;
        } catch (error) {
            return false;
        } finally {
            setTimeout(() => { this.autoNavigating = false; }, 300);
        }
    }

    // === Search (server + local fallback) ===

    handleSearchInput(value) {
        this.searchTerm = value ?? '';

        // keep server state; just clear local wrappers
        this._clearSearchHighlightsKeepServer();
        this.searchMatches = [];
        this.currentSearchIndex = -1;

        const term = this.searchTerm.trim();
        this._lastTypedAt = Date.now();
        if (this.AUTO_NAV_GRACE_MS == null) this.AUTO_NAV_GRACE_MS = 600;

        if (!term) {
            this.isSearching = false;
            this.updateSearchCounter();
            this.updateSearchButtons();
            return;
        }

        // Always do local substring highlighting immediately (letter-by-letter, cross-node)
        this.performSearch();
        this.updateSearchCounter();
        this.updateSearchButtons();

        // Debounced server search for 2+ chars, but don't auto-jump while typing
        clearTimeout(this._searchDebounce);
        this._searchDebounce = setTimeout(async () => {
            // Gate server search:
            // - single letter => skip (local only)
            // - multi-word with very short last token (<2) => skip for now (still typing second word)
            const hasSpace = term.includes(' ');
            const lastLen = this._lastTokenLength(term);

            const shouldServerSearch =
                (term.length >= 2) &&
                (!hasSpace || lastLen >= 2);

            if (!shouldServerSearch) {
                this.isSearching = false;
                this.updateSearchCounter();
                this.updateSearchButtons();
                return;
            }

            this.isSearching = true;
            this.updateSearchCounter();
            this.updateSearchButtons();

            try {
                // NO autoNavigate while user is actively typing
                await this.performServerSearch({ autoNavigate: false });
            } catch (err) {
            } finally {
                this.isSearching = false;
                this.updateSearchCounter();
                this.updateSearchButtons();
            }
        }, 200);
    }


    _qualityScoreForToken(tokenText, query) {
        const t = (tokenText || '').toLowerCase();
        const q = (query || '').toLowerCase();
        if (!q) return 0;
        if (t === q) return 3;
        if (t.startsWith(q)) return 2;
        if (t.includes(q)) return 1;
        return 0;
    }

    _sortServerResultsInPlace() {
        const q = (this.searchTerm || '').trim().toLowerCase();
        this.serverSearchResults.sort((a, b) => {
            // phrase_length > 1 gets highest priority (treat as exact phrase)
            const aPhrase = (a.phrase_length || 1) > 1;
            const bPhrase = (b.phrase_length || 1) > 1;
            if (aPhrase !== bPhrase) return bPhrase - aPhrase;

            const sa = this._qualityScoreForToken(a.match || '', q);
            const sb = this._qualityScoreForToken(b.match || '', q);
            if (sa !== sb) return sb - sa;

            // tie-break by earliest position
            return (a.word_index || 0) - (b.word_index || 0);
        });
    }


    handleSearchKeydown(e) {
        switch (e.code) {
            case 'Enter':
                e.preventDefault();
                if (e.shiftKey) this.searchPrev();
                else this.searchNext();
                break;
            case 'Escape':
                e.preventDefault();
                this.core.ui?.closeSearch?.();
                break;
        }
    }

    async performServerSearch(opts = { autoNavigate: true }) {
        const voiceId = this.getCurrentVoice() || this.core.voiceId;
        const term = (this.searchTerm || '').trim();

        if (!this.core.trackId || !voiceId || !term) {
            this.isSearching = false;
            return;
        }

        // Single-letter stays local
        if (term.length === 1) {
            this.isSearching = false;
            return;
        }

        const url = `/api/tracks/${encodeURIComponent(this.core.trackId)}/search`;
        const body = { query: term, voice_id: voiceId, page_size: this.core.pageSize };

        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body)
        });

        if (!res.ok) throw new Error(`Search ${res.status}`);
        const data = await res.json();

        this.serverSearchResults = Array.isArray(data.matches) ? data.matches : [];
        // Best-first ordering on the client too (exact > prefix > substring; earliest first)
        this._sortServerResultsInPlace();

        this.currentServerSearchIndex = this.serverSearchResults.length ? 0 : -1;

        // Only auto-navigate if explicitly allowed AND we've paused typing for a bit
        if (opts.autoNavigate) {
            const quiet = !this._lastTypedAt || (Date.now() - this._lastTypedAt) > (this.AUTO_NAV_GRACE_MS || 600);
            if (quiet && this.serverSearchResults.length) {
                await this._goToServerIndex(this.currentServerSearchIndex);
            }
        }
    }

    async navigateToSearchResult(idx) {
        await this._goToServerIndex(idx);
    }

    searchNext() {
        if (this.serverSearchResults.length) {
            // If unset, start from current (or 0 if none)
            if (this.currentServerSearchIndex < 0) this.currentServerSearchIndex = 0;
            this.currentServerSearchIndex =
                (this.currentServerSearchIndex + 1) % this.serverSearchResults.length;
            this._goToServerIndex(this.currentServerSearchIndex);
            this.updateSearchCounter();
            return;
        }
        // Fallback: local (same-page) matches
        if (!this.searchMatches.length) return;
        this.currentSearchIndex = (this.currentSearchIndex + 1) % this.searchMatches.length;
        this.highlightCurrentMatch();
        this.updateSearchCounter();
    }

    searchPrev() {
        if (this.serverSearchResults.length) {
            if (this.currentServerSearchIndex < 0) this.currentServerSearchIndex = 0;
            this.currentServerSearchIndex =
                this.currentServerSearchIndex <= 0
                    ? this.serverSearchResults.length - 1
                    : this.currentServerSearchIndex - 1;
            this._goToServerIndex(this.currentServerSearchIndex);
            this.updateSearchCounter();
            return;
        }
        if (!this.searchMatches.length) return;
        this.currentSearchIndex =
            this.currentSearchIndex <= 0 ? this.searchMatches.length - 1 : this.currentSearchIndex - 1;
        this.highlightCurrentMatch();
        this.updateSearchCounter();
    }

    async _goToServerIndex(idx) {
        const m = this.serverSearchResults[idx];
        if (!m) return;

        // Remember which result we intend to land on (survives content reload)
        this.currentServerSearchIndex = idx;

        // Drop stale navigations
        this._searchNavToken = (this._searchNavToken || 0) + 1;
        const myToken = this._searchNavToken;

        // Page resolution: trust server .page, else derive from word_index
        const targetPage = (typeof m.page === 'number')
            ? m.page
            : Math.floor((m.word_index || 0) / this.core.pageSize);

        // Load the correct page if needed
        if (this.core.pagingMode === 'paged') {
            if (targetPage !== this.core.currentPage && targetPage >= 0) {
                this.core.currentPage = targetPage;
                await this.loadContent();
                if (myToken !== this._searchNavToken) return; // stale
                await new Promise(r => requestAnimationFrame(r));
            }
        } else if (this.core.totalPages > 1) {
            this.core.pagingMode = 'full';
            localStorage.setItem('readAlongPagingMode', 'full');
            await this.loadContent();
            if (myToken !== this._searchNavToken) return;
            await new Promise(r => requestAnimationFrame(r));
        }

        // Resolve elements now that the page DOM is present
        const group = this._resolveMatchElements(m);

        // Time-based fallback
        if (!group.length && (m.start_time != null)) {
            const el = this._findClosestWordByTime(m.start_time);
            if (el) group.push(el);
        }

        // If still nothing, do local substring search on this page as a last resort
        if (!group.length) {
            this.performSearch();
            if (this.searchMatches.length) {
                this.currentSearchIndex = 0;
                this.highlightCurrentMatch();
            }
            return;
        }

        // IMPORTANT: do NOT reset server index here
        // Clean previous local wrappers but keep server indices intact
        this._clearSearchHighlightsKeepServer();

        // Mark current group and scroll
        group.forEach(el => el.classList.add('ra-search-match', 'current'));
        this.suppressAutoScrollUntil = Date.now() + 800;
        if (!this.userScrolling) {
            (group[0].closest('.ra-sentence') || group[0]).scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        // Keep a minimal local list for visual state
        this.searchMatches = [group];
        this.currentSearchIndex = 0;
    }

    // Robust resolver (word_index -> element)
    _resolveMatchElements(m) {
        const out = [];
        const base = m.word_index;
        const len = Math.max(1, m.phrase_length || 1);
        if (typeof base !== 'number') return out;

        for (let i = 0; i < len; i++) {
            const idx = base + i;
            let el =
                this.core.overlay.querySelector(`[data-word-index="${idx}"]`) ||
                this.core.overlay.querySelector(`[data-timing-index="${idx}"]`);
            if (!el && m.start_time != null) {
                el = this._findClosestWordByTime(m.start_time);
            }
            if (el) out.push(el);
        }

        // Keep contiguous group within the same sentence; otherwise anchor to first
        if (out.length > 1) {
            const s = out[0].closest('.ra-sentence');
            if (s && !out.every(e => e.closest('.ra-sentence') === s)) return [out[0]];
        }
        return out;
    }


    _findClosestWordByTime(targetTime) {
        const words = Array.from(this.core.overlay.querySelectorAll('.ra-word[data-start-time]'));
        let best = null, bestDiff = Infinity;
        for (const w of words) {
            const t = parseFloat(w.dataset.startTime);
            if (isNaN(t)) continue;
            const d = Math.abs(t - targetTime);
            if (d < bestDiff) { bestDiff = d; best = w; }
        }
        return best;
    }

    _lastTokenLength(term) {
        const parts = (term || '').trim().split(/\s+/);
        return parts.length ? parts[parts.length - 1].length : 0;
    }

    performSearch() {
        const container = this.core.overlay.querySelector('#textContent');
        if (!container) return;

        const term = (this.searchTerm || '').trim();
        this.clearSearchHighlights();
        this.searchMatches = [];
        this.currentSearchIndex = -1;

        if (!term) {
            this.isSearching = false;
            this.updateSearchCounter();
            this.updateSearchButtons();
            return;
        }

        // Build a flat text index of the container
        const idxMap = this._getTextNodesIndexMap(container);
        const fullText = idxMap.fullText;
        const lowerFull = fullText.toLowerCase();
        const lowerTerm = term.toLowerCase();

        // 1) Find matches in ASC order (allow overlaps)
        const ranges = [];
        let pos = 0;
        while (true) {
            const hit = lowerFull.indexOf(lowerTerm, pos);
            if (hit === -1) break;
            ranges.push([hit, hit + lowerTerm.length]);   // [start,end)
            pos = hit + 1; // allow overlapping matches
        }

        // 2) Wrap in DESC order (DOM-stable), but collect spans in ASC order
        const spansAsc = new Array(ranges.length).fill(null);
        for (let k = ranges.length - 1; k >= 0; k--) {
            const [s, e] = ranges[k];
            spansAsc[k] = this._wrapMatchRange(idxMap, s, e);
        }

        // 3) Save in ascending order so currentSearchIndex=0 is the FIRST match
        this.searchMatches = spansAsc.filter(Boolean).map(span => [span]);

        if (this.searchMatches.length > 0) {
            this.currentSearchIndex = 0;          // first match in document order
            this.suppressAutoScrollUntil = Date.now() + 800; // prevent RAF auto-scroll fight
            this.highlightCurrentMatch();         // scrolls to the first
        }

        this.isSearching = false;
        this.updateSearchCounter();
        this.updateSearchButtons();
    }

    _getTextNodesIndexMap(container) {
        const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
        const nodes = [];
        let node, offset = 0, full = '';
        while ((node = walker.nextNode())) {
            if (node.parentElement && node.parentElement.classList.contains('ra-search-match')) continue;
            const text = node.nodeValue || '';
            nodes.push({ node, start: offset, end: offset + text.length });
            offset += text.length;
            full += text;
        }
        return { nodes, fullText: full, container };
    }

    _wrapMatchRange(idxMap, start, end) {
        const { nodes } = idxMap;

        // Find start node
        let i = 0;
        while (i < nodes.length && nodes[i].end <= start) i++;
        if (i === nodes.length) return null;
        const startEntry = nodes[i];

        // Find end node
        let j = i;
        while (j < nodes.length && nodes[j].start < end) j++;
        const endEntry = nodes[Math.min(j - 1, nodes.length - 1)];
        if (!startEntry || !endEntry) return null;

        const range = document.createRange();
        const startOffsetInNode = Math.max(0, start - startEntry.start);
        const endOffsetInNode = Math.max(0, end - endEntry.start);

        range.setStart(startEntry.node, startOffsetInNode);
        range.setEnd(endEntry.node, endOffsetInNode);

        const wrapper = document.createElement('span');
        wrapper.className = 'ra-search-match';

        try {
            range.surroundContents(wrapper);
            return wrapper;
        } catch {
            const frag = range.extractContents();
            wrapper.appendChild(frag);
            range.insertNode(wrapper);
            return wrapper;
        }
    }

    highlightCurrentMatch() {
        // Clear previous 'current'
        this.core.overlay.querySelectorAll('.ra-search-match.current')
            .forEach(el => el.classList.remove('current'));

        if (this.currentSearchIndex >= 0 && this.currentSearchIndex < this.searchMatches.length) {
            const group = this.searchMatches[this.currentSearchIndex]; // ascending order
            group.forEach(el => el.classList.add('current'));
            if (!this.userScrolling) {
                (group[0].closest('.ra-sentence') || group[0]).scrollIntoView({
                    behavior: 'smooth',
                    block: 'center'
                });
            }
        }
    }

    clearSearchHighlights() {
        // Full reset (used when closing search or new term)
        this._clearSearchHighlightsKeepServer();
        this.searchMatches = [];
        this.currentSearchIndex = -1;
        // DO NOT touch: this.serverSearchResults / this.currentServerSearchIndex here
    }

    _clearSearchHighlightsKeepServer() {
        const container = this.core.overlay?.querySelector('#textContent');
        if (!container) return;
        const matches = Array.from(container.querySelectorAll('.ra-search-match'));
        for (const el of matches) {
            if (!el.classList.contains('ra-word') && !el.hasAttribute('data-start-time')) {
                const parent = el.parentNode;
                while (el.firstChild) parent.insertBefore(el.firstChild, el);
                parent.removeChild(el);
                parent.normalize();
            } else {
                el.classList.remove('ra-search-match', 'current');
            }
        }
    }


    updateSearchCounter() {
        const counter = this.core.overlay.querySelector('#searchCounter');
        if (!counter) return;

        if (this.isSearching) {
            counter.textContent = 'Searching...';
            return;
        }

        if (this.serverSearchResults.length > 0) {
            counter.textContent = `${this.currentServerSearchIndex + 1} of ${this.serverSearchResults.length}`;
            return;
        }

        if (!this.searchMatches.length) {
            counter.textContent = this.searchTerm ? 'No matches' : '0 of 0';
        } else {
            counter.textContent = `${this.currentSearchIndex + 1} of ${this.searchMatches.length}`;
        }
    }

    updateSearchButtons() {
        const prevBtn = this.core.overlay.querySelector('#searchPrev');
        const nextBtn = this.core.overlay.querySelector('#searchNext');

        const hasMatches = this.serverSearchResults.length > 0 || this.searchMatches.length > 0;
        const isDisabled = this.isSearching || !hasMatches;

        if (prevBtn) prevBtn.disabled = isDisabled;
        if (nextBtn) nextBtn.disabled = isDisabled;
    }

    closeSearch() {
        this.searchActive = false;
        this.clearSearchHighlights();
        this.searchTerm = '';
        this.searchMatches = [];
        this.currentSearchIndex = -1;
        this.serverSearchResults = [];
        this.currentServerSearchIndex = -1;
        this.isSearching = false;

        this.updateSearchCounter();
    }

    escapeRegex(string) {
        return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
}

window.ReadAlongContent = ReadAlongContent;