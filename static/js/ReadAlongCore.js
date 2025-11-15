// ReadAlongCore.js - Main class orchestration and state management - FIXED WITH LOGGING

class ReadAlongCore {
    constructor() {
        // Core state
        this.isOpen = false;
        this.overlay = null;
        this.player = null;
        this.trackId = null;
        this.voiceId = null;
        this.currentVoiceId = null;

        // Settings from localStorage
        this.theme = localStorage.getItem('readAlongTheme') || 'dark';
        this.pageSize = parseInt(localStorage.getItem('readAlongPageSize')) || 500;
        this.currentPage = 0;
        this.totalPages = 0;
        this.totalWords = 0;
        this.pagingMode = localStorage.getItem('readAlongPagingMode') || 'paged';
        this.highlightingActive = localStorage.getItem('readAlongHighlighting') !== 'false';

        // A/V sync settings
        this.playbackOffsetMs = parseInt(localStorage.getItem('readAlongSyncOffsetMs') || '0', 10);
        this.sentenceGapThreshold = parseFloat(localStorage.getItem('readAlongSentenceGap') || '0.35');

        // Component instances
        this.ui = null;
        this.content = null;

        // RAF loop
        this.rafId = null;

        // Bound event listeners
        this.boundTimeUpdate = null;
        this.boundPlayUpdate = null;
        this.boundPauseUpdate = null;
        this.boundMetadataUpdate = null;

        this.init();
    }

    init() {
        // Initialize UI component
        this.ui = new ReadAlongUI(this);
        
        // Initialize content component  
        this.content = new ReadAlongContent(this);

        // Create overlay and setup
        this.overlay = this.ui.createOverlay();
        this.setupEvents();
        this.waitForPlayer();
    }

    waitForPlayer() {
        const checkPlayer = () => {
            if (window.persistentPlayer) {
                this.player = window.persistentPlayer;
                this.setupPlayerEvents();
            } else {
                setTimeout(checkPlayer, 100);
            }
        };
        checkPlayer();
    }

    setupEvents() {
        this.overlay.addEventListener('click', this.handleClick.bind(this));
        this.overlay.addEventListener('change', this.handleChange.bind(this));
        this.overlay.addEventListener('input', this.handleInput.bind(this));
        this.overlay.addEventListener('keydown', this.handleKeydown.bind(this));
        document.addEventListener('keydown', this.handleGlobalKeydown.bind(this));
    }

    setupPlayerEvents() {
        if (!this.player?.audio) {
            return;
        }
        
        const audio = this.player.audio;

        // Remove stale listeners
        if (this.boundTimeUpdate) audio.removeEventListener('timeupdate', this.boundTimeUpdate);
        if (this.boundPlayUpdate) audio.removeEventListener('play', this.boundPlayUpdate);
        if (this.boundPauseUpdate) audio.removeEventListener('pause', this.boundPauseUpdate);
        if (this.boundMetadataUpdate) audio.removeEventListener('loadedmetadata', this.boundMetadataUpdate);

        // FIXED: Remove highlighting from timeupdate - RAF loop handles it now
        this.boundTimeUpdate = () => {
            if (this.isOpen) {
                this.ui.updateProgress();
            }
        };
        
        this.boundPlayUpdate = () => { 
            if (this.isOpen) {
                this.ui.updatePlayButton(); 
            }
        };
        this.boundPauseUpdate = () => { 
            if (this.isOpen) {
                this.ui.updatePlayButton(); 
            }
        };
        this.boundMetadataUpdate = () => { 
            if (this.isOpen) {
                this.ui.updateDuration(); 
                setTimeout(() => this.ui.updateProgress(), 10);
            }
        };

        audio.addEventListener('timeupdate', this.boundTimeUpdate);
        audio.addEventListener('play', this.boundPlayUpdate);
        audio.addEventListener('pause', this.boundPauseUpdate);
        audio.addEventListener('loadedmetadata', this.boundMetadataUpdate);

        // Listen for voice changes
        document.addEventListener('voiceChanged', (e) => {
            if (this.isOpen && e.detail?.newVoice && e.detail.trackId === this.trackId) {
                this.handleVoiceChange(e.detail.newVoice, e.detail.oldVoice);
            }
        });

        // Keep currentWordChanged for word position updates only
        document.addEventListener('currentWordChanged', (e) => {
            if (this.isOpen && e.detail?.voice && this.highlightingActive) {
                this.content.updateSentenceHighlighting();
            }
        });
    }

    // Voice change handler
    async handleVoiceChange(newVoiceId, oldVoiceId) {
        try {
            // Update core state
            this.currentVoiceId = newVoiceId;
            this.voiceId = newVoiceId;
            
            // Show loading state
            this.ui.showState('loading');
            
            // Clear all cached content and state
            this.content.clearState();
            
            // CRITICAL FIX: Reset pagination to ensure consistent content boundaries
            if (this.pagingMode === 'paged') {
                this.currentPage = 0;  // Always start from page 0 on voice change
                this.totalPages = 0;   // Will be recalculated
                this.totalWords = 0;   // Will be recalculated
            }
            
            // Force reload content with new voice (now from page 0)
            await this.content.loadContent();
            
            // Rebuild timing index for new voice
            this.content.buildTimingIndex();
            
            // Sync with current audio position (this might auto-navigate to correct page)
            this.syncWithCurrentAudio();
            
            // Show success
            this.ui.showState('content');
            
            // Auto-scroll to current position after a brief delay
            setTimeout(() => {
                this.content.autoScrollToCurrentPosition();
            }, 200);
            
        } catch (error) {
            this.ui.showError(`Failed to switch to new voice: ${error.message}`);
        }
    }

    // Event handlers delegate to appropriate components
    handleClick(e) {
    const target = e.target.closest('button, [data-sentence-index], [data-start-time], #progressBar');
    if (!target) return;

    const id = target.id;

    // UI-related clicks (➕ edgePrev/edgeNext added)
    if ([
        'closeReadAlong',
        'settingsToggle',
        'themeToggle',
        'highlightToggle',
        'searchToggle',
        'searchClose',
        'searchNext',
        'searchPrev',
        'prevPage',
        'nextPage',
        'edgePrev',
        'edgeNext',
        'resetSync',
        'applySettings',
        'progressBar'
    ].includes(id)) {
        this.ui.handleClick(e);
        return;
    }

    // Player control clicks
    if (['playBtn', 'rewind15Btn', 'forward15Btn', 'rewind30Btn', 'forward30Btn'].includes(id)) {
        this.handlePlayerControls(id);
        return;
    }

    // Content interaction clicks (sentences, words)
    if (target.dataset.startTime || target.dataset.sentenceIndex) {
        this.content.handleContentClick(e);
        return;
    }

    // Retry button
    if (id === 'retryBtn') {
        this.content.loadContent();
    }
}



    handleChange(e) {
        this.ui.handleChange(e);
    }

    handleInput(e) {
        if (e.target.id === 'syncOffsetSlider') {
            this.playbackOffsetMs = parseInt(e.target.value, 10);
            this.ui.updateSyncUI();
            localStorage.setItem('readAlongSyncOffsetMs', String(this.playbackOffsetMs));
            this.content.updateSentenceHighlighting();
        } else if (e.target.id === 'searchInput') {
            this.content.handleSearchInput(e.target.value);
        }
    }

    handleKeydown(e) {
        if (e.target.id === 'searchInput') {
            this.content.handleSearchKeydown(e);
        }
    }

    handleGlobalKeydown(e) {
        if (!this.isOpen || e.target.matches('input, textarea, select')) return;
        
        // Quick sync adjustment
        if (e.altKey && (e.code === 'Period' || e.code === 'Comma')) {
            e.preventDefault();
            const step = (e.code === 'Period') ? +50 : -50;
            this.playbackOffsetMs = Math.max(-2000, Math.min(2000, this.playbackOffsetMs + step));
            localStorage.setItem('readAlongSyncOffsetMs', String(this.playbackOffsetMs));
            this.ui.updateSyncUI();
            this.content.updateSentenceHighlighting();
            return;
        }
        
        const keyMap = {
            'Space': () => this.player?.togglePlay(),
            'ArrowLeft': () => this.player?.seek(e.shiftKey ? -30 : -15),
            'ArrowRight': () => this.player?.seek(e.shiftKey ? 30 : 15),
            'KeyT': () => this.ui.toggleTheme(),
            'KeyH': () => this.ui.toggleHighlighting(),
            'KeyF': () => (e.ctrlKey || e.metaKey) && this.ui.toggleSearch(),
            'KeyS': () => (e.ctrlKey || e.metaKey) && this.ui.toggleSettings(),
            'PageUp': () => this.content.goToPreviousPage(),
            'PageDown': () => this.content.goToNextPage(),
            'Escape': () => {
                if (this.content.searchActive) this.ui.closeSearch();
                else if (this.overlay.querySelector('#settingsPanel').style.display !== 'none') this.ui.closeSettings();
                else this.close();
            }
        };

        if (keyMap[e.code]) {
            e.preventDefault();
            keyMap[e.code]();
        }
    }

    handlePlayerControls(id) {
        switch(id) {
            case 'playBtn': this.player?.togglePlay(); break;
            case 'rewind15Btn': this.player?.seek(-15); break;
            case 'forward15Btn': this.player?.seek(15); break;
            case 'rewind30Btn': this.player?.seek(-30); break;
            case 'forward30Btn': this.player?.seek(30); break;
        }
    }

    // Main lifecycle methods
    async open(trackId, voiceId) {
        console.log('[ReadAlong][Core] open() called with trackId:', trackId, 'voiceId:', voiceId);

        if (this.isOpen) {
            console.log('[ReadAlong][Core] Already open, returning early');
            return;
        }
        
        const playerDefaultVoice = typeof this.player?.getTrackDefaultVoice === 'function'
            ? this.player.getTrackDefaultVoice()
            : null;
        const overlayVoice = this.content.getCurrentVoice();
        const playerVoice = this.player?.currentVoice || null;
        const metadataVoice = this.player?.trackMetadata?.defaultVoice || null;
        const resolvedVoice = overlayVoice
            || voiceId
            || playerVoice
            || metadataVoice
            || playerDefaultVoice
            || this.currentVoiceId
            || this.voiceId
            || null;

        console.groupCollapsed('[ReadAlong][Core] open()');
        console.log('trackId:', trackId);
        console.log('incomingVoice:', voiceId);
        console.log('overlayVoice:', overlayVoice);
        console.log('playerVoice:', playerVoice);
        console.log('metadataVoice:', metadataVoice);
        console.log('playerDefaultVoice:', playerDefaultVoice);
        console.log('resolvedVoice:', resolvedVoice);
        console.log('persistentPlayer?', !!this.player, 'has audio?', !!this.player?.audio);
        console.log('trackData track_type:', window.trackData?.track_type, 'has_read_along:', window.trackData?.has_read_along);
        console.groupEnd();

        if (!resolvedVoice) {
            throw new Error('No voice available for read-along');
        }
        
        // CRITICAL: Check access BEFORE opening overlay
        this.trackId = trackId;
        this.voiceId = resolvedVoice;
        this.currentVoiceId = resolvedVoice;
        
        
        try {
            // Try access check without page parameters first (to avoid 422)
            const voiceToCheck = this.currentVoiceId;
            const checkUrl = `/api/tracks/${encodeURIComponent(trackId)}/read-along/${encodeURIComponent(voiceToCheck)}`;
            console.log('[ReadAlong][Core] Access check', {
                url: checkUrl,
                trackId,
                voiceId: voiceToCheck
            });

            console.log('[ReadAlong][Core] NETWORK REQUEST (access check) STARTING:', checkUrl);
            const accessFetchStart = performance.now();
            const accessResponse = await fetch(checkUrl, { credentials: 'include' });
            const accessFetchEnd = performance.now();
            console.log('[ReadAlong][Core] NETWORK REQUEST (access check) COMPLETED in', (accessFetchEnd - accessFetchStart).toFixed(0) + 'ms');
            console.log('[ReadAlong][Core] Access check response', {
                status: accessResponse.status,
                ok: accessResponse.ok
            });
            
            // Handle different error types
            if (accessResponse.status === 403) {
                const errorText = await accessResponse.text();
                
                let errorMessage = 'Read-along access not available for your tier';
                
                try {
                    const errorData = JSON.parse(errorText);
                    if (errorData.detail) {
                        errorMessage = errorData.detail;
                    } else if (errorData.error && errorData.error.message) {
                        errorMessage = errorData.error.message;
                    }
                } catch (parseError) {
                    if (errorText.includes('access not available')) {
                        errorMessage = errorText;
                    }
                }
                
                // Create access error WITHOUT opening overlay
                const accessError = new Error(errorMessage);
                accessError.isAccessControl = true;
                accessError.statusCode = 403;
                throw accessError;
            }
            
            if (accessResponse.status === 422) {
                // 422 might be access-related too, let's check the response
                const errorText = await accessResponse.text();
                
                try {
                    const errorData = JSON.parse(errorText);
                    // Check if 422 is actually an access control issue
                    if (errorData.detail && (
                        errorData.detail.includes('access not available') ||
                        errorData.detail.includes('tier') ||
                        errorData.detail.includes('subscription') ||
                        errorData.detail.includes('not allowed')
                    )) {
                        // Treat 422 as access control error
                        const accessError = new Error(errorData.detail);
                        accessError.isAccessControl = true;
                        accessError.statusCode = 422;
                        throw accessError;
                    }
                } catch (parseError) {
                    // If we can't parse, check raw text
                    if (errorText.includes('access not available') || 
                        errorText.includes('tier') || 
                        errorText.includes('subscription')) {
                        const accessError = new Error(errorText);
                        accessError.isAccessControl = true;
                        accessError.statusCode = 422;
                        throw accessError;
                    }
                }
                
                // If 422 is not access-related, try with page parameters
                const checkUrlWithPage = `${checkUrl}?page=0&page_size=500`;
                const pageResponse = await fetch(checkUrlWithPage, { credentials: 'include' });
                
                if (pageResponse.status === 403) {
                    const pageErrorText = await pageResponse.text();
                    let errorMessage = 'Read-along access not available for your tier';
                    
                    try {
                        const errorData = JSON.parse(pageErrorText);
                        if (errorData.detail) {
                            errorMessage = errorData.detail;
                        }
                    } catch (e) {
                        if (pageErrorText.includes('access not available')) {
                            errorMessage = pageErrorText;
                        }
                    }
                    
                    const accessError = new Error(errorMessage);
                    accessError.isAccessControl = true;
                    accessError.statusCode = 403;
                    throw accessError;
                }
                
                if (!pageResponse.ok) {
                    // If both fail with non-access errors, proceed and let content loading handle it
                }
            } else if (!accessResponse.ok) {
                // For other errors, proceed and let content loading handle it
            }
            
            
        } catch (error) {
            // Only throw if it's an access control error
            if (error.isAccessControl) {
                throw error;
            }
            
            // For other errors, log and proceed (let content loading handle it)
        }
        
        // ONLY reach here if access check passed or was not access-control related
        
        this.clearState();
        
        this.overlay.classList.add('visible');
        this.isOpen = true;
        document.body.style.overflow = 'hidden';

        this.startRafLoop();
        
        this.ui.updateTrackInfo();

        console.log('[ReadAlong][Core] About to load initial content for page', this.currentPage);
        try {
            await this.content.loadContent();
            console.log('[ReadAlong][Core] Initial content loaded successfully');

            this.content.buildTimingIndex();
            console.log('[ReadAlong][Core] Timing index built, timings count:', this.content.preciseWordTimings?.length || 0);

            this.syncWithCurrentAudio();
            console.log('[ReadAlong][Core] Synced with current audio');
            
            setTimeout(() => {
                this.ui.updateProgress();
                setTimeout(() => this.ui.updateProgress(), 50);
            }, 50);
            
            setTimeout(() => {
                this.content.autoScrollToCurrentPosition();
            }, 200);
            
            
        } catch (error) {
            
            // Close the overlay since content failed
            this.close();
            
            // Re-throw the error
            throw error;
        }
    }
    close() {
        if (!this.isOpen) {
            return;
        }
        
        this.highlightingActive = localStorage.getItem('readAlongHighlighting') !== 'false';
        this.stopRafLoop();
        this.clearState();
        this.overlay.classList.remove('visible');
        this.isOpen = false;
        document.body.style.overflow = '';
    }

    clearState() {
        this.content.clearState();
        this.ui.clearSearchUI();
    }

    syncWithCurrentAudio() {
        if (!this.player?.audio) {
            return;
        }
        
        this.ui.updateDuration();
        this.ui.updatePlayButton();
        this.ui.updateProgress();
        
        if (!this.player.audio.paused && this.player.audio.currentTime > 0 && this.highlightingActive) {
            this.content.updateSentenceHighlighting();
        }
    }

    // FIXED: RAF loop for smooth updates - SINGLE SOURCE OF TRUTH for highlighting
    startRafLoop() {
        if (this.rafId) {
            return;
        }
        
        
        const step = () => {
            if (this.isOpen) {
                try {
                    this.ui.updateProgress();
                } catch (e) {
                }
                
                // SINGLE SOURCE OF TRUTH: Only RAF loop calls highlighting
                if (this.highlightingActive) {
                    try {
                        this.content.updateSentenceHighlighting();
                    } catch (e) {
                    }
                }
                this.rafId = requestAnimationFrame(step);
            } else {
                this.rafId = null;
            }
        };
        this.rafId = requestAnimationFrame(step);
    }

    stopRafLoop() {
        if (this.rafId) { 
            cancelAnimationFrame(this.rafId); 
            this.rafId = null; 
        }
    }

    // Utility for time with offset
    _t() {
        return Math.max(0, (this.player?.audio?.currentTime || 0) - (this.playbackOffsetMs / 1000));
    }
}

// Initialize singleton
let readAlongOverlay = null;

document.addEventListener('DOMContentLoaded', () => {
    readAlongOverlay = new ReadAlongCore();
    window.readAlongSPAOverlay = readAlongOverlay;
});

window.ReadAlongOverlay = ReadAlongCore;
