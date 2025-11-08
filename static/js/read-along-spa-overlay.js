// read-along-spa-overlay.js - Complete with punctuation-first fixes

class ReadAlongOverlay {
    constructor() {
        this.isOpen = false;
        this.overlay = null;
        this.player = null;

        // Core data
        this.sourceText = '';
        this.mappedTokens = [];
        this.wordTimings = [];
        this.currentSentenceIndex = -1;
        this.lastAudioTime = -1;
        this.highlightingActive = true;

        // Settings
        this.theme = localStorage.getItem('readAlongTheme') || 'dark';
        this.pageSize = parseInt(localStorage.getItem('readAlongPageSize')) || 500;
        this.currentPage = 0;
        this.totalPages = 0;
        this.totalWords = 0;
        this.pagingMode = localStorage.getItem('readAlongPagingMode') || 'paged';

        // Auto-navigation
        this.autoNavigating = false;
        this.lastNavigationCheck = 0;
        this.navigationThrottle = 2000;

        // Voice sync
        this.currentVoiceId = null;

        // Search
        this.searchTerm = '';
        this.searchMatches = [];
        this.currentSearchIndex = -1;
        this.searchActive = false;
        this.serverSearchResults = [];
        this.currentServerSearchIndex = -1;
        this.isSearching = false;

        // Timing
        this.sentenceTimings = [];
        this.boundaryPadding = 0.0;
        this.highlightHoldMs = 200;
        this._lastValidSentenceAt = 0;
        this.rafId = null;

        // A/V sync
        this.playbackOffsetMs = parseInt(localStorage.getItem('readAlongSyncOffsetMs') || '0', 10);
        this.sentenceGapThreshold = parseFloat(localStorage.getItem('readAlongSentenceGap') || '0.35');

        // Bound listeners
        this.boundTimeUpdate = null;
        this.boundPlayUpdate = null;
        this.boundPauseUpdate = null;
        this.boundMetadataUpdate = null;

        this.init();
    }

    _t() {
        return Math.max(0, (this.player?.audio?.currentTime || 0) - (this.playbackOffsetMs / 1000));
    }

    init() {
        this.createOverlay();
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

    createOverlay() {
        this.overlay = document.createElement('div');
        this.overlay.id = 'readAlongOverlay';
        
        this.overlay.innerHTML = `
            <div class="ra-container ${this.theme}">
                <header class="ra-header">
                    <button class="ra-close-btn" id="closeReadAlong">
                        <i class="fas fa-times"></i>
                    </button>
                    <div class="ra-album-section">
                        <img class="ra-album-art" id="albumArt" src="" alt="Album Cover">
                        <div class="ra-track-info">
                            <h1 id="trackTitle">Track Title</h1>
                        </div>
                    </div>
                    
                    <div class="ra-search-section" id="searchSection" style="display: none;">
                        <div class="ra-search-box">
                            <input type="text" id="searchInput" placeholder="Find in text..." autocomplete="off">
                            <div class="ra-search-controls">
                                <span class="ra-search-counter" id="searchCounter">0 of 0</span>
                                <button class="ra-search-btn" id="searchPrev" title="Previous match">
                                    <i class="fas fa-chevron-up"></i>
                                </button>
                                <button class="ra-search-btn" id="searchNext" title="Next match">
                                    <i class="fas fa-chevron-down"></i>
                                </button>
                                <button class="ra-search-btn" id="searchClose" title="Close search">
                                    <i class="fas fa-times"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                    
                    <div class="ra-pagination-section" id="paginationSection" style="display: none;">
                        <div class="ra-page-info">
                            <span id="pageInfo">Page 1 of 1</span>
                            <span class="ra-separator">|</span>
                            <span id="wordInfo">500 words</span>
                        </div>
                        <div class="ra-page-controls">
                            <button class="ra-page-btn" id="prevPage" title="Previous page">
                                <i class="fas fa-chevron-left"></i>
                            </button>
                            <button class="ra-page-btn" id="nextPage" title="Next page">
                                <i class="fas fa-chevron-right"></i>
                            </button>
                        </div>
                    </div>
                    
                    <div class="ra-header-controls">
                        <button class="ra-header-btn" id="searchToggle" title="Search in text">
                            <i class="fas fa-search"></i>
                        </button>
                        <button class="ra-header-btn" id="highlightToggle" title="Toggle highlighting">
                            <i class="fas fa-lightbulb"></i>
                            <span class="ra-toggle-text">On</span>
                        </button>
                        <button class="ra-header-btn" id="settingsToggle" title="Settings">
                            <i class="fas fa-cog"></i>
                        </button>
                        <button class="ra-header-btn" id="themeToggle" title="Toggle theme">
                            <i class="fas fa-sun"></i>
                        </button>
                    </div>
                </header>

                <div class="ra-settings-panel" id="settingsPanel" style="display: none;">
                    <div class="ra-settings-content">
                        <h3>Read-Along Settings</h3>
                        
                        <div class="ra-setting-group">
                            <label for="pagingModeSelect">Loading Mode:</label>
                            <select id="pagingModeSelect" class="ra-select">
                                <option value="paged">Load by Pages (default)</option>
                                <option value="full">Load All Text</option>
                            </select>
                        </div>
                        
                        <div class="ra-setting-group" id="pageSizeGroup">
                            <label for="pageSizeSelect">Words per Page:</label>
                            <select id="pageSizeSelect" class="ra-select">
                                <option value="100">100 words</option>
                                <option value="250">250 words</option>
                                <option value="500">500 words (default)</option>
                                <option value="750">750 words</option>
                                <option value="1000">1000 words</option>
                            </select>
                        </div>

                        <div class="ra-setting-group">
                            <label for="syncOffset">A/V Sync Offset (ms):</label>
                            <div class="ra-sync-controls">
                                <input type="range" id="syncOffsetSlider" min="-2000" max="2000" step="50" class="ra-slider">
                                <span id="syncOffsetValue">0ms</span>
                                <button class="ra-sync-reset" id="resetSync" title="Reset to 0">Reset</button>
                            </div>
                            <p class="ra-setting-note">Negative values make highlights appear earlier, positive values later. Use Alt+, and Alt+. for quick adjust.</p>
                        </div>
                        
                        <div class="ra-setting-group">
                            <button class="ra-apply-btn" id="applySettings">Apply Settings</button>
                        </div>
                    </div>
                </div>

                <main class="ra-content">
                    <div class="ra-loading-state" id="loadingState">
                        <i class="fas fa-spinner fa-spin"></i>
                        <p>Loading content...</p>
                    </div>
                    <div class="ra-error-state" id="errorState" style="display: none;">
                        <i class="fas fa-exclamation-triangle"></i>
                        <p id="errorMessage">Failed to load content</p>
                        <button id="retryBtn" class="ra-retry-btn">Retry</button>
                    </div>
                    <div class="ra-text-content" id="textContent" style="display: none;"></div>
                </main>

                <footer class="ra-controls">
                    <div class="ra-progress-section">
                        <div class="ra-progress-bar" id="progressBar">
                            <div class="ra-progress-fill" id="progressFill"></div>
                            <div class="ra-progress-knob" id="progressKnob"></div>
                        </div>
                        <div class="ra-time-display">
                            <span id="currentTime">0:00</span>
                            <span id="duration">0:00</span>
                        </div>
                    </div>
                    <div class="ra-playback-controls">
                        <button class="ra-control-btn ra-skip-btn" id="rewind30Btn" title="Rewind 30 seconds">
                            <i class="fas fa-rotate-left"></i>
                            <span class="ra-skip-time">30</span>
                        </button>
                        <button class="ra-control-btn ra-skip-btn" id="rewind15Btn" title="Rewind 15 seconds">
                            <i class="fas fa-rotate-left"></i>
                            <span class="ra-skip-time">15</span>
                        </button>
                        <button class="ra-control-btn ra-play-btn" id="playBtn">
                            <i class="fas fa-play" id="playIcon"></i>
                        </button>
                        <button class="ra-control-btn ra-skip-btn" id="forward15Btn" title="Forward 15 seconds">
                            <i class="fas fa-rotate-right"></i>
                            <span class="ra-skip-time">15</span>
                        </button>
                        <button class="ra-control-btn ra-skip-btn" id="forward30Btn" title="Forward 30 seconds">
                            <i class="fas fa-rotate-right"></i>
                            <span class="ra-skip-time">30</span>
                        </button>
                    </div>
                </footer>
            </div>
        `;

        this.addStyles();
        document.body.appendChild(this.overlay);
        
        // Set initial theme icon
        const themeIcon = this.overlay.querySelector('#themeToggle i');
        if (themeIcon) {
            themeIcon.className = this.theme === 'light' ? 'fas fa-moon' : 'fas fa-sun';
        }

        this.highlightingActive = localStorage.getItem('readAlongHighlighting') !== 'false';
        this.updateHighlightToggle();
        this.initializeSettings();
    }

    initializeSettings() {
        const pagingModeSelect = this.overlay.querySelector('#pagingModeSelect');
        const pageSizeSelect = this.overlay.querySelector('#pageSizeSelect');
        const pageSizeGroup = this.overlay.querySelector('#pageSizeGroup');
        const syncOffsetSlider = this.overlay.querySelector('#syncOffsetSlider');
        const syncOffsetValue = this.overlay.querySelector('#syncOffsetValue');

        if (pagingModeSelect) {
            pagingModeSelect.value = this.pagingMode;
            pageSizeGroup.style.display = this.pagingMode === 'paged' ? 'block' : 'none';
        }

        if (pageSizeSelect) {
            pageSizeSelect.value = this.pageSize.toString();
        }

        if (syncOffsetSlider && syncOffsetValue) {
            syncOffsetSlider.value = this.playbackOffsetMs;
            syncOffsetValue.textContent = `${this.playbackOffsetMs}ms`;
        }
    }

    addStyles() {
        if (document.getElementById('readAlongStyles')) return;
        const style = document.createElement('style');
        style.id = 'readAlongStyles';
        style.textContent = `
            #readAlongOverlay { position: fixed; inset: 0; background: rgba(0,0,0,0.95); backdrop-filter: blur(10px); z-index: 100000; opacity: 0; visibility: hidden; transition: all .3s ease; font-family: system-ui,-apple-system,sans-serif; }
            #readAlongOverlay.visible { opacity: 1; visibility: visible; }
            #readAlongOverlay .ra-container { height: 100vh; display: flex; flex-direction: column; color: white; }
            #readAlongOverlay .ra-header { display:flex; align-items:center; justify-content:space-between; padding:.75rem 1.5rem; background: rgba(0,0,0,0.3); border-bottom:1px solid rgba(255,255,255,0.1); backdrop-filter: blur(20px); min-height:60px; }
            #readAlongOverlay .ra-close-btn, #readAlongOverlay .ra-header-btn { background: rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.2); color:white; padding:.5rem; border-radius:8px; cursor:pointer; transition:all .2s ease; width:36px; height:36px; display:flex; align-items:center; justify-content:center; position:relative; }
            #readAlongOverlay .ra-close-btn:hover, #readAlongOverlay .ra-header-btn:hover { background: rgba(255,255,255,0.2); transform: translateY(-1px); }
            #readAlongOverlay .ra-album-section { display:flex; align-items:center; gap:.75rem; flex:1; justify-content:center; max-width:400px; margin:0 1rem; }
            #readAlongOverlay .ra-album-art { width:40px; height:40px; border-radius:6px; object-fit:cover; box-shadow:0 3px 10px rgba(0,0,0,0.3); }
            #readAlongOverlay .ra-track-info h1 { font-size:1rem; font-weight:500; margin:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color: rgba(255,255,255,0.95); }
            #readAlongOverlay .ra-header-controls { display:flex; gap:.3rem; }
            
            #readAlongOverlay .ra-toggle-text { position:absolute; bottom:-18px; left:50%; transform:translateX(-50%); font-size:.65rem; font-weight:500; opacity:.8; }
            #readAlongOverlay .ra-header-btn.highlight-off { opacity:.5; }
            #readAlongOverlay .ra-header-btn.highlight-off .ra-toggle-text { opacity:.6; }
            
            #readAlongOverlay .ra-search-section { display:none; flex:1; max-width:400px; margin:0 1rem; }
            #readAlongOverlay .ra-search-section.active { display:flex; }
            #readAlongOverlay .ra-search-box { display:flex; align-items:center; background: rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.2); border-radius:8px; padding:.25rem; width:100%; }
            #readAlongOverlay .ra-search-box input { flex:1; background:transparent; border:none; color:white; padding:.5rem .75rem; font-size:.9rem; outline:none; }
            #readAlongOverlay .ra-search-box input::placeholder { color: rgba(255,255,255,0.6); }
            #readAlongOverlay .ra-search-controls { display:flex; align-items:center; gap:.25rem; }
            #readAlongOverlay .ra-search-counter { font-size:.75rem; color: rgba(255,255,255,0.7); white-space:nowrap; margin-right:.25rem; }
            #readAlongOverlay .ra-search-btn { background:transparent; border:none; color: rgba(255,255,255,0.8); padding:.25rem; border-radius:4px; cursor:pointer; transition: all .2s ease; width:28px; height:28px; display:flex; align-items:center; justify-content:center; }
            #readAlongOverlay .ra-search-btn:hover { background: rgba(255,255,255,0.15); color:white; }
            #readAlongOverlay .ra-search-btn:disabled { opacity:.4; cursor:not-allowed; }
            #readAlongOverlay .ra-search-match { background: rgba(255,235,59,0.4); color:#000; border-radius:2px; padding:1px 2px; }
            #readAlongOverlay .ra-search-match.current { background: rgba(255,193,7,0.7); box-shadow:0 0 0 1px rgba(255,193,7,0.8); }
            
            #readAlongOverlay .ra-pagination-section { display:none; flex:1; max-width:300px; margin:0 1rem; align-items:center; gap:.75rem; }
            #readAlongOverlay .ra-pagination-section.active { display:flex; }
            #readAlongOverlay .ra-page-info { display:flex; align-items:center; gap:.5rem; font-size:.85rem; color: rgba(255,255,255,0.8); }
            #readAlongOverlay .ra-separator { opacity:.5; }
            #readAlongOverlay .ra-page-controls { display:flex; gap:.25rem; }
            #readAlongOverlay .ra-page-btn { background: rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.2); color:white; padding:.4rem; border-radius:6px; cursor:pointer; transition:all .2s ease; width:32px; height:32px; display:flex; align-items:center; justify-content:center; }
            #readAlongOverlay .ra-page-btn:hover:not(:disabled) { background: rgba(255,255,255,0.2); }
            #readAlongOverlay .ra-page-btn:disabled { opacity:.4; cursor:not-allowed; }

            #readAlongOverlay .ra-settings-panel { position:absolute; top:60px; right:0; width:320px; background: rgba(0,0,0,0.95); border:1px solid rgba(255,255,255,0.2); border-radius:0 0 0 12px; z-index:10; }
            #readAlongOverlay .ra-settings-content { padding:1.5rem; }
            #readAlongOverlay .ra-settings-content h3 { margin:0 0 1rem 0; font-size:1rem; color:white; }
            #readAlongOverlay .ra-setting-group { margin-bottom:1rem; }
            #readAlongOverlay .ra-setting-group label { display:block; margin-bottom:.5rem; font-size:.9rem; color: rgba(255,255,255,0.9); }
            #readAlongOverlay .ra-select { width:100%; padding:.5rem; background: rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.2); border-radius:6px; color:white; font-size:.9rem; }
            #readAlongOverlay .ra-select option { background:#333; color:white; }
            #readAlongOverlay .ra-sync-controls { display:flex; align-items:center; gap:.5rem; }
            #readAlongOverlay .ra-slider { flex:1; height:4px; background: rgba(255,255,255,0.2); border-radius:2px; outline:none; -webkit-appearance:none; }
            #readAlongOverlay .ra-slider::-webkit-slider-thumb { -webkit-appearance:none; width:16px; height:16px; background:#3b82f6; border-radius:50%; cursor:pointer; }
            #readAlongOverlay .ra-slider::-moz-range-thumb { width:16px; height:16px; background:#3b82f6; border-radius:50%; cursor:pointer; border:none; }
            #readAlongOverlay .ra-sync-reset { background: rgba(255,255,255,0.1); color:white; border:1px solid rgba(255,255,255,0.2); padding:.25rem .5rem; border-radius:4px; cursor:pointer; font-size:.8rem; }
            #readAlongOverlay .ra-sync-reset:hover { background: rgba(255,255,255,0.2); }
            #readAlongOverlay .ra-setting-note { font-size:.75rem; color: rgba(255,255,255,0.6); margin-top:.25rem; }
            #readAlongOverlay .ra-apply-btn { background:#3b82f6; color:white; border:none; padding:.75rem 1.5rem; border-radius:8px; cursor:pointer; width:100%; font-size:.9rem; transition: all .2s ease; }
            #readAlongOverlay .ra-apply-btn:hover { background:#2563eb; }

            #readAlongOverlay .ra-content { flex:1; padding:2rem; overflow-y:auto; display:flex; align-items:flex-start; justify-content:center; }
            #readAlongOverlay .ra-content::-webkit-scrollbar { width:6px; } 
            #readAlongOverlay .ra-content::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.3); border-radius:3px; }
            #readAlongOverlay .ra-loading-state, #readAlongOverlay .ra-error-state { display:flex; flex-direction:column; align-items:center; text-align:center; color: rgba(255,255,255,0.7); }
            #readAlongOverlay .ra-loading-state i { font-size:2.5rem; margin-bottom:1.5rem; color:#3b82f6; }
            #readAlongOverlay .ra-error-state i { color:#ef4444; }
            #readAlongOverlay .ra-retry-btn { background:#3b82f6; color:white; border:none; padding:.75rem 1.5rem; border-radius:8px; cursor:pointer; margin-top:1rem; transition: all .2s ease; }
            #readAlongOverlay .ra-retry-btn:hover { background:#2563eb; transform: translateY(-1px); }
            #readAlongOverlay .ra-text-content { max-width:800px; width:100%; line-height:1.8; font-size:1.2rem; }
            #readAlongOverlay .ra-paragraph { margin:0 0 1.8rem 0; line-height:1.8; text-align:justify; text-justify: inter-word; text-indent:1.5rem; font-family: 'Georgia','Times New Roman',serif; }
            #readAlongOverlay .ra-paragraph:first-child { text-indent:0; }
            #readAlongOverlay .ra-paragraph.ra-chapter-start { text-indent:0; text-align:center; font-weight:600; margin:2rem 0; text-transform:uppercase; letter-spacing:.1em; color: rgba(255,255,255,0.9); }
            #readAlongOverlay .ra-sentence { display:inline; cursor:pointer; transition: all .2s ease; border-radius:4px; padding:0; }
            #readAlongOverlay .ra-sentence:hover { background: rgba(255,255,255,0.08); padding:2px 4px; margin:-2px -4px; border-radius:6px; }
            #readAlongOverlay .ra-sentence.ra-active { background: linear-gradient(135deg, rgba(59,130,246,0.25), rgba(99,102,241,0.2)); padding:4px 6px; margin:-4px -6px; border-radius:6px; box-shadow:0 2px 12px rgba(59,130,246,0.3); border:1px solid rgba(59,130,246,0.4); }
            #readAlongOverlay .ra-word { display:inline; cursor:pointer; transition: all .1s ease; }
            #readAlongOverlay .ra-word[data-start-time]:hover { background: rgba(255,255,255,0.1); border-radius:2px; padding:1px 2px; margin:-1px -2px; }
            #readAlongOverlay .ra-punctuation { display:inline; color: rgba(255,255,255,0.9); opacity:.95; }
            #readAlongOverlay .ra-controls { background: rgba(0,0,0,0.5); backdrop-filter: blur(20px); border-top:1px solid rgba(255,255,255,0.1); padding:1rem 2rem 1.5rem; }
            #readAlongOverlay .ra-progress-section { margin-bottom:1rem; }
            #readAlongOverlay .ra-progress-bar { width:100%; height:6px; background: rgba(255,255,255,0.2); border-radius:3px; cursor:pointer; margin-bottom:.5rem; position:relative; }
            #readAlongOverlay .ra-progress-fill { height:100%; background: linear-gradient(90deg,#3b82f6,#6366f1); border-radius:3px; width:0%; transition: width .1s ease; }
            #readAlongOverlay .ra-progress-knob { position:absolute; top:50%; transform: translate(-50%, -50%); width:16px; height:16px; background:#fff; border-radius:50%; box-shadow:0 2px 6px rgba(0,0,0,0.3); left:0%; transition: left .1s ease; z-index:1; }
            #readAlongOverlay .ra-progress-knob:hover { transform: translate(-50%, -50%) scale(1.1); }
            #readAlongOverlay .ra-time-display { display:flex; justify-content:space-between; font-size:.9rem; color: rgba(255,255,255,0.8); }
            #readAlongOverlay .ra-playback-controls { display:flex; align-items:center; justify-content:center; gap:1.5rem; }
            #readAlongOverlay .ra-control-btn { background: rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.2); color:white; border-radius:50%; cursor:pointer; transition: all .2s ease; display:flex; align-items:center; justify-content:center; position:relative; }
            #readAlongOverlay .ra-control-btn:hover { background: rgba(255,255,255,0.2); transform: translateY(-2px); }
            #readAlongOverlay .ra-skip-btn { width:50px; height:50px; }
            #readAlongOverlay .ra-play-btn { width:70px; height:70px; background: linear-gradient(135deg,#3b82f6,#6366f1); border:2px solid rgba(255,255,255,0.2); box-shadow:0 4px 15px rgba(59,130,246,0.3); }
            #readAlongOverlay .ra-play-btn:hover { background: linear-gradient(135deg,#2563eb,#5b5bd6); box-shadow:0 6px 20px rgba(59,130,246,0.4); transform: translateY(-3px); }
            #readAlongOverlay .ra-skip-time { position:absolute; bottom:-22px; left:50%; transform: translateX(-50%); font-size:.7rem; color: rgba(255,255,255,0.6); font-weight:500; }
            #readAlongOverlay .ra-control-btn i { font-size:1.2rem; }
            #readAlongOverlay .ra-play-btn i { font-size:1.6rem; }
            
            /* Light theme */
            #readAlongOverlay .ra-container.light { background: rgba(255,255,255,0.95); color:#1a1a1a; }
            #readAlongOverlay .ra-container.light .ra-header { background: rgba(255,255,255,0.8); border-bottom-color: rgba(0,0,0,0.1); }
            #readAlongOverlay .ra-container.light .ra-close-btn, #readAlongOverlay .ra-container.light .ra-header-btn { background: rgba(0,0,0,0.08); border-color: rgba(0,0,0,0.15); color:#1a1a1a; }
            #readAlongOverlay .ra-container.light .ra-close-btn:hover, #readAlongOverlay .ra-container.light .ra-header-btn:hover { background: rgba(0,0,0,0.12); }
            #readAlongOverlay .ra-container.light .ra-track-info h1 { color: rgba(26,26,26,0.95); }
            #readAlongOverlay .ra-container.light .ra-paragraph { color: rgba(26,26,26,0.95); }
            #readAlongOverlay .ra-container.light .ra-paragraph.ra-chapter-start { color: rgba(26,26,26,0.9); }
            #readAlongOverlay .ra-container.light .ra-sentence:hover { background: rgba(0,0,0,0.08); }
            #readAlongOverlay .ra-container.light .ra-sentence.ra-active { background: linear-gradient(135deg, rgba(59,130,246,0.2), rgba(99,102,241,0.15)); border-color: rgba(59,130,246,0.5); box-shadow:0 2px 12px rgba(59,130,246,0.25); }
            #readAlongOverlay .ra-container.light .ra-word[data-start-time]:hover { background: rgba(0,0,0,0.1); }
            #readAlongOverlay .ra-container.light .ra-punctuation { color: rgba(26,26,26,0.9); }
            #readAlongOverlay .ra-container.light .ra-controls { background: rgba(255,255,255,0.8); border-top-color: rgba(0,0,0,0.1); }
            #readAlongOverlay .ra-container.light .ra-progress-bar { background: rgba(0,0,0,0.2); }
            #readAlongOverlay .ra-container.light .ra-progress-knob { background:#1a1a1a; box-shadow:0 2px 6px rgba(0,0,0,0.2); }
            #readAlongOverlay .ra-container.light .ra-control-btn { background: rgba(0,0,0,0.1); border-color: rgba(0,0,0,0.2); color:#1a1a1a; }
            #readAlongOverlay .ra-container.light .ra-control-btn:hover { background: rgba(0,0,0,0.15); }
            #readAlongOverlay .ra-container.light .ra-skip-time { color: rgba(0,0,0,0.6); }
            #readAlongOverlay .ra-container.light .ra-search-box { background: rgba(0,0,0,0.08); border-color: rgba(0,0,0,0.2); }
            #readAlongOverlay .ra-container.light .ra-search-box input { color:#1a1a1a; }
            #readAlongOverlay .ra-container.light .ra-search-box input::placeholder { color: rgba(0,0,0,0.6); }
            #readAlongOverlay .ra-container.light .ra-search-counter { color: rgba(0,0,0,0.7); }
            #readAlongOverlay .ra-container.light .ra-search-btn { color: rgba(0,0,0,0.8); }
            #readAlongOverlay .ra-container.light .ra-search-btn:hover { background: rgba(0,0,0,0.1); color:#1a1a1a; }
            #readAlongOverlay .ra-container.light .ra-pagination-section { color: rgba(0,0,0,0.8); }
            #readAlongOverlay .ra-container.light .ra-page-btn { background: rgba(0,0,0,0.1); border-color: rgba(0,0,0,0.2); color:#1a1a1a; }
            #readAlongOverlay .ra-container.light .ra-page-btn:hover:not(:disabled) { background: rgba(0,0,0,0.15); }
            #readAlongOverlay .ra-container.light .ra-settings-panel { background: rgba(255,255,255,0.95); border-color: rgba(0,0,0,0.2); }
            #readAlongOverlay .ra-container.light .ra-settings-content h3 { color:#1a1a1a; }
            #readAlongOverlay .ra-container.light .ra-setting-group label { color: rgba(0,0,0,0.9); }
            #readAlongOverlay .ra-container.light .ra-select { background: rgba(0,0,0,0.08); border-color: rgba(0,0,0,0.2); color:#1a1a1a; }
            #readAlongOverlay .ra-container.light .ra-sync-reset { background: rgba(0,0,0,0.1); color:#1a1a1a; border-color: rgba(0,0,0,0.2); }
            #readAlongOverlay .ra-container.light .ra-sync-reset:hover { background: rgba(0,0,0,0.15); }
            #readAlongOverlay .ra-container.light .ra-setting-note { color: rgba(0,0,0,0.6); }
            
            @media (max-width: 768px) {
                #readAlongOverlay .ra-header { padding:.75rem 1.25rem; min-height:52px; }
                #readAlongOverlay .ra-close-btn, #readAlongOverlay .ra-header-btn { width:32px; height:32px; }
                #readAlongOverlay .ra-album-art { width:32px; height:32px; }
                #readAlongOverlay .ra-track-info h1 { font-size:.9rem; }
                #readAlongOverlay .ra-search-section { margin:0 .5rem; max-width:300px; }
                #readAlongOverlay .ra-search-box input { font-size:.85rem; padding:.4rem .6rem; }
                #readAlongOverlay .ra-search-counter { font-size:.7rem; }
                #readAlongOverlay .ra-search-btn { width:24px; height:24px; }
                #readAlongOverlay .ra-pagination-section { margin:0 .5rem; max-width:250px; }
                #readAlongOverlay .ra-page-info { font-size:.8rem; }
                #readAlongOverlay .ra-settings-panel { width:100%; right:0; border-radius:0; }
                #readAlongOverlay .ra-content { padding:1.25rem; }
                #readAlongOverlay .ra-text-content { font-size:1.1rem; line-height:1.7; }
                #readAlongOverlay .ra-paragraph { text-indent:1rem; margin:0 0 1.5rem 0; line-height:1.7; }
                #readAlongOverlay .ra-controls { padding:1rem 1.5rem 1.25rem; }
                #readAlongOverlay .ra-skip-btn { width:45px; height:45px; }
                #readAlongOverlay .ra-play-btn { width:60px; height:60px; }
                #readAlongOverlay .ra-control-btn i { font-size:1.1rem; }
                #readAlongOverlay .ra-play-btn i { font-size:1.4rem; }
                #readAlongOverlay .ra-toggle-text { font-size:.6rem; bottom:-16px; }
            }
        `;
        document.head.appendChild(style);
    }

    setupEvents() {
        this.overlay.addEventListener('click', this.handleClick.bind(this));
        this.overlay.addEventListener('change', this.handleChange.bind(this));
        this.overlay.addEventListener('input', this.handleInput.bind(this));
        this.overlay.addEventListener('keydown', this.handleKeydown.bind(this));
        document.addEventListener('keydown', this.handleGlobalKeydown.bind(this));
    }

    // NEW: Helper method to centralize sentence activation and seeking
    activateSentence(index, { seek = true, preroll = 0 } = {}) {
        const target = this.sentenceTimings.find(s => s.index === index);
        if (!target) return;
        
        // Highlight the sentence
        const prev = this.overlay.querySelector(`[data-sentence-index="${this.currentSentenceIndex}"]`);
        prev?.classList.remove('ra-active');
        const el = this.overlay.querySelector(`[data-sentence-index="${index}"]`);
        el?.classList.add('ra-active');
        this.currentSentenceIndex = index;
        el?.scrollIntoView({ behavior: 'smooth', block: 'center' });

        // Seek if requested
        if (seek && this.player?.audio) {
            const t = Math.max(0, target.startTime - preroll);
            this.seekToTimeWithPrecision(t);
        }
    }

    handleClick(e) {
        const target = e.target.closest('button, [data-sentence-index], [data-start-time], #progressBar');
        if (!target) return;

        const id = target.id;
        
        switch(id) {
            case 'closeReadAlong': this.close(); break;
            case 'settingsToggle': this.toggleSettings(); break;
            case 'resetSync': this.resetSync(); break;
            case 'applySettings': this.applySettings(); break;
            case 'prevPage': this.goToPreviousPage(); break;
            case 'nextPage': this.goToNextPage(); break;
            case 'themeToggle': this.toggleTheme(); break;
            case 'highlightToggle': this.toggleHighlighting(); break;
            case 'searchToggle': this.toggleSearch(); break;
            case 'searchClose': this.closeSearch(); break;
            case 'searchNext': this.searchNext(); break;
            case 'searchPrev': this.searchPrev(); break;
            case 'retryBtn': this.loadContent(); break;
            case 'playBtn': this.player?.togglePlay(); break;
            case 'rewind15Btn': this.player?.seek(-15); break;
            case 'forward15Btn': this.player?.seek(15); break;
            case 'rewind30Btn': this.player?.seek(-30); break;
            case 'forward30Btn': this.player?.seek(30); break;
            case 'progressBar': this.handleProgressClick(e); break;
        }

        // FIXED: Enhanced word click handling with sentence-level activation
        if (target.dataset.startTime) {
            e.stopPropagation();
            const sentenceEl = target.closest('.ra-sentence');
            const idx = sentenceEl ? parseInt(sentenceEl.dataset.sentenceIndex) : -1;
            if (idx >= 0) {
                if (e.shiftKey) {
                    // Shift+click: precise word seeking (power user)
                    const ts = parseFloat(target.dataset.startTime);
                    this.seekToTimeWithPrecision(ts);
                    this.activateSentence(idx, { seek: false });
                } else {
                    // Default click: activate and seek to sentence start
                    this.activateSentence(idx, { seek: true, preroll: 0 });
                }
            }
        } else if (target.dataset.sentenceIndex) {
            // FIXED: Sentence click using new helper
            const idx = parseInt(target.dataset.sentenceIndex);
            this.activateSentence(idx, { seek: true, preroll: 0 });
        }

        // ALT+click calibration (unchanged)
        if (e.altKey && target.dataset.startTime) {
            e.preventDefault();
            e.stopPropagation();
            const wordT = parseFloat(target.dataset.startTime);
            const curT = this.player.audio.currentTime;
            const deltaMs = Math.round((wordT - curT) * 1000);
            this.playbackOffsetMs += deltaMs;
            localStorage.setItem('readAlongSyncOffsetMs', String(this.playbackOffsetMs));
            this.updateSyncUI();
            this.updateSentenceHighlighting();
        }
    }

    handleChange(e) {
        if (e.target.id === 'pagingModeSelect') {
            const pageSizeGroup = this.overlay.querySelector('#pageSizeGroup');
            pageSizeGroup.style.display = e.target.value === 'paged' ? 'block' : 'none';
        }
    }

    handleInput(e) {
        if (e.target.id === 'syncOffsetSlider') {
            this.playbackOffsetMs = parseInt(e.target.value, 10);
            this.updateSyncUI();
            localStorage.setItem('readAlongSyncOffsetMs', String(this.playbackOffsetMs));
            this.updateSentenceHighlighting();
        } else if (e.target.id === 'searchInput') {
            this.handleSearchInput(e.target.value);
        }
    }

    handleKeydown(e) {
        if (e.target.id === 'searchInput') {
            switch (e.code) {
                case 'Enter':
                    e.preventDefault();
                    if (e.shiftKey) this.searchPrev();
                    else this.searchNext();
                    break;
                case 'Escape':
                    e.preventDefault();
                    this.closeSearch();
                    break;
            }
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
            this.updateSyncUI();
            this.updateSentenceHighlighting();
            return;
        }
        
        const keyMap = {
            'Space': () => this.player?.togglePlay(),
            'ArrowLeft': () => this.player?.seek(e.shiftKey ? -30 : -15),
            'ArrowRight': () => this.player?.seek(e.shiftKey ? 30 : 15),
            'KeyT': () => this.toggleTheme(),
            'KeyH': () => this.toggleHighlighting(),
            'KeyF': () => (e.ctrlKey || e.metaKey) && this.toggleSearch(),
            'KeyS': () => (e.ctrlKey || e.metaKey) && this.toggleSettings(),
            'PageUp': () => this.goToPreviousPage(),
            'PageDown': () => this.goToNextPage(),
            'Escape': () => {
                if (this.searchActive) this.closeSearch();
                else if (this.overlay.querySelector('#settingsPanel').style.display !== 'none') this.closeSettings();
                else this.close();
            }
        };

        if (keyMap[e.code]) {
            e.preventDefault();
            keyMap[e.code]();
        }
    }

    setupPlayerEvents() {
        if (!this.player?.audio) return;
        const audio = this.player.audio;

        // Remove stale listeners
        if (this.boundTimeUpdate) audio.removeEventListener('timeupdate', this.boundTimeUpdate);
        if (this.boundPlayUpdate) audio.removeEventListener('play', this.boundPlayUpdate);
        if (this.boundPauseUpdate) audio.removeEventListener('pause', this.boundPauseUpdate);
        if (this.boundMetadataUpdate) audio.removeEventListener('loadedmetadata', this.boundMetadataUpdate);

        // Bind fresh
        this.boundTimeUpdate = () => {
            if (this.isOpen) {
                this.updateProgress();
                if (this.highlightingActive) this.updateSentenceHighlighting();
            }
        };
        this.boundPlayUpdate = () => { if (this.isOpen) this.updatePlayButton(); };
        this.boundPauseUpdate = () => { if (this.isOpen) this.updatePlayButton(); };
        this.boundMetadataUpdate = () => { 
            if (this.isOpen) {
                this.updateDuration(); 
                setTimeout(() => this.updateProgress(), 10);
            }
        };

        audio.addEventListener('timeupdate', this.boundTimeUpdate);
        audio.addEventListener('play', this.boundPlayUpdate);
        audio.addEventListener('pause', this.boundPauseUpdate);
        audio.addEventListener('loadedmetadata', this.boundMetadataUpdate);

        document.addEventListener('currentWordChanged', (e) => {
            if (this.isOpen && e.detail?.voice && e.detail.voice !== this.currentVoiceId) {
                this.handleVoiceChange(e.detail.voice);
            }
        });
    }

    async open(trackId, voiceId) {
        if (this.isOpen) return;
        this.clearState();

        this.trackId = trackId;
        this.voiceId = voiceId;
        this.currentVoiceId = this.getCurrentVoice() || voiceId || null;

        this.overlay.classList.add('visible');
        this.isOpen = true;
        document.body.style.overflow = 'hidden';

        this.startRafLoop();
        this.updateTrackInfo();
        await this.loadContent();
        this.buildTimingIndex();
        this.syncWithCurrentAudio();
        
        setTimeout(() => {
            this.updateProgress();
            setTimeout(() => this.updateProgress(), 50);
        }, 50);
        
        setTimeout(() => this.autoScrollToCurrentPosition(), 200);
    }

    clearState() {
        this.sourceText = '';
        this.mappedTokens = [];
        this.wordTimings = [];
        this.currentSentenceIndex = -1;
        this.lastAudioTime = -1;
        this.sentenceTimings = [];

        this.searchTerm = '';
        this.searchMatches = [];
        this.currentSearchIndex = -1;
        this.searchActive = false;
        this.serverSearchResults = [];
        this.currentServerSearchIndex = -1;
        this.isSearching = false;

        this.autoNavigating = false;
        this.lastNavigationCheck = 0;

        if (this.overlay) {
            this.overlay.querySelectorAll('.ra-sentence.ra-active').forEach(el => {
                el.classList.remove('ra-active');
            });
            
            const searchSection = this.overlay.querySelector('#searchSection');
            const albumSection = this.overlay.querySelector('.ra-album-section');
            if (searchSection && albumSection) {
                searchSection.style.display = 'none';
                searchSection.classList.remove('active');
                albumSection.style.display = 'flex';
            }
        }
    }

    close() {
        if (!this.isOpen) return;
        this.highlightingActive = localStorage.getItem('readAlongHighlighting') !== 'false';
        this.stopRafLoop();
        this.clearState();
        this.overlay.classList.remove('visible');
        this.isOpen = false;
        document.body.style.overflow = '';
    }

    autoScrollToCurrentPosition() {
        if (!this.player?.audio || !this.sentenceTimings.length) return;
        
        const currentTime = this._t();
        if (currentTime <= 0) return;
        
        if (this.pagingMode === 'paged' && this.totalPages > 1) {
            this.checkAndNavigateToCorrectPage(currentTime);
            return;
        }
        
        const sentenceIndex = this.findCurrentSentenceByTime(currentTime);
        
        if (sentenceIndex >= 0) {
            const sentenceEl = this.overlay.querySelector(`[data-sentence-index="${sentenceIndex}"]`);
            if (sentenceEl) {
                sentenceEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        } else {
            const progress = currentTime / (this.player.audio.duration || 1);
            const allSentences = this.overlay.querySelectorAll('.ra-sentence');
            if (allSentences.length > 0) {
                const targetIndex = Math.floor(progress * allSentences.length);
                const targetElement = allSentences[Math.min(targetIndex, allSentences.length - 1)];
                if (targetElement) {
                    targetElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }
        }
    }

    syncWithCurrentAudio() {
        if (!this.player?.audio) return;
        this.updateDuration();
        this.updatePlayButton();
        this.updateProgress();
        
        if (!this.player.audio.paused && this.player.audio.currentTime > 0 && this.highlightingActive) {
            this.updateSentenceHighlighting();
        }
    }

    updateTrackInfo() {
        const trackTitle = this.overlay.querySelector('#trackTitle');
        const albumArt = this.overlay.querySelector('#albumArt');
        if (trackTitle) trackTitle.textContent = window.trackData?.title || window.currentTrackTitle || 'Unknown Track';
        if (albumArt) {
            albumArt.src = window.albumData?.cover_path || window.currentAlbumCoverPath || '';
            albumArt.alt = (window.albumData?.title || window.currentAlbumTitle || 'Album') + ' Cover';
        }
    }

    toggleHighlighting() {
        this.highlightingActive = !this.highlightingActive;
        localStorage.setItem('readAlongHighlighting', this.highlightingActive.toString());
        
        if (!this.highlightingActive) {
            this.overlay.querySelectorAll('.ra-sentence.ra-active').forEach(el => {
                el.classList.remove('ra-active');
            });
            this.currentSentenceIndex = -1;
        }
        
        this.updateHighlightToggle();
    }

    updateHighlightToggle() {
        const toggleBtn = this.overlay.querySelector('#highlightToggle');
        const toggleText = this.overlay.querySelector('#highlightToggle .ra-toggle-text');
        
        if (toggleBtn && toggleText) {
            if (this.highlightingActive) {
                toggleBtn.classList.remove('highlight-off');
                toggleText.textContent = 'On';
            } else {
                toggleBtn.classList.add('highlight-off');
                toggleText.textContent = 'Off';
            }
        }
    }

    toggleSettings() {
        const settingsPanel = this.overlay.querySelector('#settingsPanel');
        const isVisible = settingsPanel.style.display !== 'none';
        
        if (isVisible) {
            this.closeSettings();
        } else {
            this.closeSearch();
            settingsPanel.style.display = 'block';
            this.updateSyncUI();
        }
    }

    closeSettings() {
        const settingsPanel = this.overlay.querySelector('#settingsPanel');
        settingsPanel.style.display = 'none';
    }

    resetSync() {
        this.playbackOffsetMs = 0;
        localStorage.setItem('readAlongSyncOffsetMs', '0');
        this.updateSyncUI();
        this.updateSentenceHighlighting();
    }

    updateSyncUI() {
        const syncOffsetSlider = this.overlay.querySelector('#syncOffsetSlider');
        const syncOffsetValue = this.overlay.querySelector('#syncOffsetValue');
        if (syncOffsetSlider) syncOffsetSlider.value = this.playbackOffsetMs;
        if (syncOffsetValue) syncOffsetValue.textContent = `${this.playbackOffsetMs}ms`;
    }

    applySettings() {
        const pagingModeSelect = this.overlay.querySelector('#pagingModeSelect');
        const pageSizeSelect = this.overlay.querySelector('#pageSizeSelect');
        
        const newPagingMode = pagingModeSelect.value;
        const newPageSize = parseInt(pageSizeSelect.value);
        
        const settingsChanged = newPagingMode !== this.pagingMode || newPageSize !== this.pageSize;
        
        if (settingsChanged) {
            this.pagingMode = newPagingMode;
            this.pageSize = newPageSize;
            this.currentPage = 0;
            
            localStorage.setItem('readAlongPagingMode', this.pagingMode);
            localStorage.setItem('readAlongPageSize', this.pageSize.toString());
            
            this.updatePaginationUI();
            this.loadContent();
        }
        
        this.closeSettings();
    }

    async goToPreviousPage() {
        if (this.currentPage > 0) {
            this.currentPage--;
            await this.loadContent();
        }
    }

    async goToNextPage() {
        if (this.currentPage < this.totalPages - 1) {
            this.currentPage++;
            await this.loadContent();
        }
    }

    updatePaginationUI() {
        const paginationSection = this.overlay.querySelector('#paginationSection');
        const albumSection = this.overlay.querySelector('.ra-album-section');
        const pageInfo = this.overlay.querySelector('#pageInfo');
        const wordInfo = this.overlay.querySelector('#wordInfo');
        const prevPageBtn = this.overlay.querySelector('#prevPage');
        const nextPageBtn = this.overlay.querySelector('#nextPage');

        if (this.pagingMode === 'paged' && this.totalPages > 1) {
            albumSection.style.display = 'none';
            paginationSection.style.display = 'flex';
            paginationSection.classList.add('active');
            
            if (pageInfo) pageInfo.textContent = `Page ${this.currentPage + 1} of ${this.totalPages}`;
            if (wordInfo) {
                const wordsOnPage = Math.min(this.pageSize, this.totalWords - (this.currentPage * this.pageSize));
                wordInfo.textContent = `${wordsOnPage} words`;
            }
            
            if (prevPageBtn) prevPageBtn.disabled = this.currentPage === 0;
            if (nextPageBtn) nextPageBtn.disabled = this.currentPage >= this.totalPages - 1;
        } else {
            paginationSection.style.display = 'none';
            paginationSection.classList.remove('active');
            albumSection.style.display = 'flex';
        }
    }

    async loadContent() {
        try {
            this.showState('loading');
            const readAlongData = await this.loadReadAlongData();
            if (!readAlongData || !readAlongData.sourceText) throw new Error('No source text available');
            
            this.sourceText = readAlongData.sourceText;
            this.mappedTokens = readAlongData.mappedTokens || [];
            this.wordTimings = readAlongData.wordTimings || [];
            
            if (readAlongData.totalWords) this.totalWords = readAlongData.totalWords;
            if (readAlongData.total_pages) this.totalPages = readAlongData.total_pages;
            
            this.updatePaginationUI();
            this.renderText();
            this.buildTimingIndex();
            this.showState('content');
        } catch (error) {
            this.showError(error.message || 'Failed to load content');
        }
    }

    async loadReadAlongData() {
        const voiceId = this.getCurrentVoice() || this.voiceId;
        let url = `/api/tracks/${encodeURIComponent(this.trackId)}/read-along/${encodeURIComponent(voiceId)}`;
        
        if (this.pagingMode === 'paged') {
            const params = new URLSearchParams({
                page: this.currentPage.toString(),
                page_size: this.pageSize.toString()
            });
            url += `?${params}`;
        }
        
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Failed to load read-along data: ${response.status}`);
        const data = await response.json();
        
        return {
            sourceText: data.sourceText || '',
            mappedTokens: data.mappedTokens || [],
            wordTimings: data.wordTimings || [],
            totalWords: data.total_words || data.totalWords || 0,
            total_pages: data.total_pages || 1,
            voiceId: voiceId
        };
    }

    renderText() {
        const container = this.overlay.querySelector('#textContent');
        if (!container) return;
        container.innerHTML = '';
        
        if (!this.mappedTokens || this.mappedTokens.length === 0) {
            this.showError('No text mapping available');
            return;
        }
        
        this.renderMappedText(container);
        this.addEventListeners(container);
    }

    renderMappedText(container) {
        let htmlContent = '';
        let currentParagraph = '';
        let sentenceIndex = 0;
        let currentSentence = '';
        let lastTimedEnd = null;
        let usePauseSegmentation = (this.pagingMode === 'paged');

        this.mappedTokens.forEach((token, tokenIndex) => {
            if (this.isParagraphBreak(token, tokenIndex)) {
                if (currentSentence.trim()) {
                    currentParagraph += this.wrapSentence(currentSentence.trim(), sentenceIndex);
                    currentSentence = '';
                    sentenceIndex++;
                    lastTimedEnd = null;
                }
                if (currentParagraph.trim()) {
                    const paragraphClass = this.detectParagraphType(currentParagraph);
                    htmlContent += `<div class="ra-paragraph ${paragraphClass}">${currentParagraph}</div>`;
                    currentParagraph = '';
                }
                return;
            }

            if (token.text === '\n' || token.type === 'linebreak') {
                currentSentence += ' ';
                return;
            }

            if (token.type === 'word') {
                if (token.hasTimings) {
                    const attrs = [
                        `class="ra-word"`,
                        `data-start-time="${token.start_time}"`,
                        `data-end-time="${token.end_time}"`,
                        `data-sentence="${sentenceIndex}"`,
                    ];
                    if ('timing_index' in token) attrs.push(`data-timing-index="${token.timing_index}"`);
                    if ('page_index' in token) attrs.push(`data-page-index="${token.page_index}"`);
                    currentSentence += `<span ${attrs.join(' ')}>${token.text}</span>`;
                    lastTimedEnd = token.end_time;
                } else {
                    currentSentence += `<span class="ra-word" data-sentence="${sentenceIndex}">${token.text}</span>`;
                }
            } else {
                currentSentence += `<span class="ra-punctuation">${token.text}</span>`;
            }

            if (tokenIndex < this.mappedTokens.length - 1) {
                const nextToken = this.mappedTokens[tokenIndex + 1];
                if (this.needsSpaceAfter(token, nextToken)) currentSentence += ' ';
            }

            // FIXED: Hybrid sentence splitting - punctuation AND gap-based
            let shouldSplit = false;
            if (this.isSentenceEnd(token, tokenIndex)) {
                shouldSplit = true;
            } else if (usePauseSegmentation && token.type === 'word' && token.hasTimings && lastTimedEnd != null) {
                const gap = token.start_time - lastTimedEnd;
                if (gap >= this.sentenceGapThreshold) shouldSplit = true;
            }
            if (shouldSplit) {
                if (currentSentence.trim()) {
                    currentParagraph += this.wrapSentence(currentSentence.trim(), sentenceIndex);
                    currentSentence = '';
                    sentenceIndex++;
                    lastTimedEnd = null;
                }
            }
        });

        if (currentSentence.trim()) currentParagraph += this.wrapSentence(currentSentence.trim(), sentenceIndex);
        if (currentParagraph.trim()) {
            const paragraphClass = this.detectParagraphType(currentParagraph);
            htmlContent += `<div class="ra-paragraph ${paragraphClass}">${currentParagraph}</div>`;
        }

        container.innerHTML = htmlContent;
    }

    // FIXED: Enhanced punctuation detection including quotes/ellipsis combos
    isSentenceEnd(token) {
        if (token.type !== 'punctuation') return false;
        const sentenceEnders = ['.', '!', '?', '...', '…', '."', '!"', '?"', '."', '!"', '?"'];
        return sentenceEnders.some(ender => token.text.includes(ender));
    }

    // Enhanced event listener attachment
    addEventListeners(container) {
        // Word click handling
        container.querySelectorAll('.ra-word[data-start-time]').forEach(wordEl => {
            wordEl.addEventListener('click', async (e) => {
                e.stopPropagation();
                const sentenceEl = wordEl.closest('.ra-sentence');
                const idx = sentenceEl ? parseInt(sentenceEl.dataset.sentenceIndex) : -1;
                if (idx >= 0) {
                    if (e.shiftKey) {
                        // Shift+click: precise word seeking (power user)
                        const ts = parseFloat(wordEl.dataset.startTime);
                        await this.seekToTimeWithPrecision(ts);
                        this.activateSentence(idx, { seek: false });
                    } else {
                        // Default click: activate sentence and seek to sentence start
                        this.activateSentence(idx, { seek: true, preroll: 0 });
                    }
                }
            });
        });

        // Sentence click handling
        container.querySelectorAll('.ra-sentence').forEach(sentenceEl => {
            sentenceEl.addEventListener('click', async () => {
                const idx = parseInt(sentenceEl.dataset.sentenceIndex);
                this.activateSentence(idx, { seek: true, preroll: 0 });
            });
        });
    }

    detectParagraphType(paragraphContent) {
        const text = paragraphContent.toLowerCase();
        if (/chapter|section|\d+\./.test(text.substring(0, 50))) return 'ra-chapter-start';
        return '';
    }

    wrapSentence(sentenceContent, index) {
        const cleanContent = sentenceContent.trim().replace(/\s+/g, ' ');
        return `<span class="ra-sentence" data-sentence-index="${index}">${cleanContent}</span> `;
    }

    isParagraphBreak(token, index) {
        return token.text === '\n\n' || (token.text && token.text.includes('\n\n'));
    }

    needsSpaceAfter(token, nextToken) {
        if (!nextToken) return false;
        if (token.type === 'word' && nextToken.type === 'word') return true;
        if (token.type === 'word' && nextToken.type === 'punctuation') return ['(', '[', '{', '"', "'"].includes(nextToken.text);
        if (token.type === 'punctuation' && nextToken.type === 'word') return ['.', '!', '?', ';', ':', ',', ')', ']', '}', '"', "'"].includes(token.text);
        return false;
    }

    buildTimingIndex() {
        const container = this.overlay.querySelector('#textContent');
        if (!container) { 
            this.sentenceTimings = []; 
            return; 
        }

        this.sentenceTimings = [];
        const sentenceEls = Array.from(container.querySelectorAll('.ra-sentence'));

        for (const el of sentenceEls) {
            const idx = parseInt(el.dataset.sentenceIndex);
            const words = Array.from(el.querySelectorAll('.ra-word[data-start-time]'));
            if (words.length) {
                const start = parseFloat(words[0].dataset.startTime);
                const end = parseFloat(words[words.length - 1].dataset.endTime);
                this.sentenceTimings.push({
                    index: idx,
                    startTime: Math.max(0, start - this.boundaryPadding),
                    endTime: end + this.boundaryPadding,
                    el
                });
            } else {
                const prev = this.sentenceTimings[this.sentenceTimings.length - 1];
                let inferredStart = prev ? prev.endTime : 0;
                let inferredEnd = inferredStart + 0.25;
                this.sentenceTimings.push({ index: idx, startTime: inferredStart, endTime: inferredEnd, el });
            }
        }

        this.sentenceTimings.sort((a,b) => a.index - b.index);
    }

    findCurrentSentenceByTime(t) {
        if (this.currentSentenceIndex >= 0) {
            const cur = this.sentenceTimings.find(s => s.index === this.currentSentenceIndex);
            if (cur && t >= cur.startTime && t < cur.endTime) {
                this._lastValidSentenceAt = performance.now();
                return cur.index;
            }
        }

        for (const s of this.sentenceTimings) {
            if (t >= s.startTime && t < s.endTime) {
                this._lastValidSentenceAt = performance.now();
                return s.index;
            }
        }

        if (this.currentSentenceIndex >= 0 &&
            performance.now() - this._lastValidSentenceAt < this.highlightHoldMs) {
            return this.currentSentenceIndex;
        }

        return -1;
    }

    updateSentenceHighlighting() {
        if (!this.player?.audio || !this.highlightingActive) return;

        const currentTime = this._t();

        if (this.pagingMode === 'paged' && this.totalPages > 1) {
            const now = Date.now();
            if (now - this.lastNavigationCheck > this.navigationThrottle) {
                this.lastNavigationCheck = now;
                this.checkAndNavigateToCorrectPage(currentTime);
            }
        }

        const nextIndex = this.findCurrentSentenceByTime(currentTime);

        if (nextIndex === -1 || nextIndex === this.currentSentenceIndex) return;

        const prevEl = this.overlay.querySelector(`[data-sentence-index="${this.currentSentenceIndex}"]`);
        prevEl?.classList.remove('ra-active');

        const nextEl = this.overlay.querySelector(`[data-sentence-index="${nextIndex}"]`);
        if (nextEl) {
            nextEl.classList.add('ra-active');
            nextEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        this.currentSentenceIndex = nextIndex;
    }

    estimateTimeForSentence(sentenceIndex) {
        const s = this.sentenceTimings.find(x => x.index === sentenceIndex);
        return s ? Math.max(0, s.startTime + 0.01) : null;
    }

    async checkAndNavigateToCorrectPage(currentTime) {
        if (this.autoNavigating || this.pagingMode !== 'paged') return;
        
        try {
            this.autoNavigating = true;
            
            const voiceId = this.getCurrentVoice() || this.voiceId;
            const response = await fetch(
                `/api/tracks/${encodeURIComponent(this.trackId)}/page-info?time=${currentTime}&voice_id=${encodeURIComponent(voiceId)}&page_size=${this.pageSize}`
            );
            
            if (!response.ok) {
                return;
            }
            
            const pageInfo = await response.json();
            const targetPage = pageInfo.current_page;
            
            if (targetPage !== this.currentPage && targetPage >= 0 && targetPage < this.totalPages) {
                this.currentPage = targetPage;
                await this.loadContent();
                
                setTimeout(() => {
                    this.updateSentenceHighlighting();
                }, 100);
            }
            
        } catch (error) {
        } finally {
            setTimeout(() => {
                this.autoNavigating = false;
            }, 500);
        }
    }

    async seekToTimeWithPrecision(targetTime, tolerance = 0.1) {
        if (!this.player?.audio?.duration || targetTime < 0 || targetTime > this.player.audio.duration) return false;
        const cur = this.player.audio.currentTime;
        if (Math.abs(cur - targetTime) < tolerance) return true;
        
        try {
            if (this.pagingMode === 'paged' && this.totalPages > 1) {
                await this.checkAndNavigateToCorrectPage(targetTime);
            }
            
            this.player.audio.currentTime = targetTime;
            await new Promise(resolve => {
                const onSeeked = () => { 
                    this.player.audio.removeEventListener('seeked', onSeeked); 
                    
                    if (this.highlightingActive) {
                        setTimeout(() => this.updateSentenceHighlighting(), 100);
                    }
                    
                    resolve(); 
                };
                this.player.audio.addEventListener('seeked', onSeeked, { once: true });
            });
            return true;
        } catch (_) { 
            return false; 
        }
    }

    toggleTheme() {
        this.theme = this.theme === 'dark' ? 'light' : 'dark';
        const container = this.overlay.querySelector('.ra-container');
        container.className = `ra-container ${this.theme}`;
        const icon = this.overlay.querySelector('#themeToggle i');
        icon.className = this.theme === 'light' ? 'fas fa-moon' : 'fas fa-sun';
        localStorage.setItem('readAlongTheme', this.theme);
    }

    toggleSearch() {
        const searchSection = this.overlay.querySelector('#searchSection');
        const albumSection = this.overlay.querySelector('.ra-album-section');
        const paginationSection = this.overlay.querySelector('#paginationSection');
        
        if (this.searchActive) {
            this.closeSearch();
        } else {
            this.searchActive = true;
            albumSection.style.display = 'none';
            paginationSection.style.display = 'none';
            paginationSection.classList.remove('active');
            searchSection.style.display = 'flex';
            searchSection.classList.add('active');
            
            const searchInput = this.overlay.querySelector('#searchInput');
            if (searchInput) {
                searchInput.focus();
                if (this.searchTerm) {
                    searchInput.value = this.searchTerm;
                    searchInput.select();
                }
            }
        }
    }

    closeSearch() {
        const searchSection = this.overlay.querySelector('#searchSection');
        const albumSection = this.overlay.querySelector('.ra-album-section');
        
        this.searchActive = false;
        this.clearSearchHighlights();
        this.searchTerm = '';
        this.searchMatches = [];
        this.currentSearchIndex = -1;
        this.serverSearchResults = [];
        this.currentServerSearchIndex = -1;
        this.isSearching = false;
        
        searchSection.style.display = 'none';
        searchSection.classList.remove('active');
        albumSection.style.display = 'flex';
        this.updatePaginationUI();
        
        const searchInput = this.overlay.querySelector('#searchInput');
        if (searchInput) searchInput.value = '';
        
        this.updateSearchCounter();
    }

    handleSearchInput(value) {
        this.searchTerm = value.trim();
        this.clearSearchHighlights();
        this.searchMatches = [];
        this.currentSearchIndex = -1;
        this.serverSearchResults = [];
        this.currentServerSearchIndex = -1;

        if (!this.searchTerm) {
            this.isSearching = false;
            this.updateSearchCounter();
            this.updateSearchButtons();
            return;
        }

        this.isSearching = true;
        this.updateSearchCounter();
        this.updateSearchButtons();

        clearTimeout(this._searchDebounce);
        this._searchDebounce = setTimeout(async () => {
            try {
                await this.performServerSearch();
            } catch (err) {
                this.isSearching = false;
                this.performSearch();
                this.updateSearchCounter();
                this.updateSearchButtons();
            }
        }, 250);
    }

    async performServerSearch() {
        const voiceId = this.getCurrentVoice() || this.voiceId;
        if (!this.trackId || !voiceId || !this.searchTerm) {
            this.isSearching = false;
            return;
        }

        const url = `/api/tracks/${encodeURIComponent(this.trackId)}/search`;
        const body = {
            query: this.searchTerm,
            voice_id: voiceId,
            page_size: this.pageSize
        };

        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body)
        });

        if (!res.ok) throw new Error(`Search ${res.status}`);
        const data = await res.json();

        this.serverSearchResults = Array.isArray(data.matches) ? data.matches : [];
        this.currentServerSearchIndex = this.serverSearchResults.length ? 0 : -1;
        this.isSearching = false;

        if (this.serverSearchResults.length) {
            await this.navigateToSearchResult(this.currentServerSearchIndex);
        } else {
            this.performSearch();
        }
        this.updateSearchCounter();
        this.updateSearchButtons();
    }

    async navigateToSearchResult(idx) {
        const m = this.serverSearchResults[idx];
        if (!m) return;

        const targetPage = (typeof m.page === 'number')
            ? m.page
            : Math.floor((m.word_index || 0) / this.pageSize);

        if (this.pagingMode === 'paged') {
            if (targetPage !== this.currentPage) {
                this.currentPage = targetPage;
                await this.loadContent();
            }
        } else {
            if (this.totalPages > 1) {
                this.pagingMode = 'full';
                localStorage.setItem('readAlongPagingMode', 'full');
                await this.loadContent();
            }
        }

        this.clearSearchHighlights();
        this.performSearch();

        const globalWordIndex = m.word_index ?? null;
        if (globalWordIndex !== null) {
            const el = this.overlay.querySelector(`[data-timing-index="${globalWordIndex}"]`)
                  || this.overlay.querySelector(`[data-page-index="${globalWordIndex - (this.currentPage * this.pageSize)}"]`);
            if (el) {
                (el.closest('.ra-sentence') || el).scrollIntoView({ behavior: 'smooth', block: 'center' });
                return;
            }
        }

        const onPageMatches = this.overlay.querySelectorAll('.ra-search-match');
        if (onPageMatches.length) {
            onPageMatches[0].classList.add('current');
            onPageMatches[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    searchNext() {
        if (this.serverSearchResults.length) {
            this.currentServerSearchIndex = (this.currentServerSearchIndex + 1) % this.serverSearchResults.length;
            this.navigateToSearchResult(this.currentServerSearchIndex);
            this.updateSearchCounter();
            return;
        }
        if (this.searchMatches.length === 0) return;
        this.currentSearchIndex = (this.currentSearchIndex + 1) % this.searchMatches.length;
        this.highlightCurrentMatch();
        this.updateSearchCounter();
    }

    searchPrev() {
        if (this.serverSearchResults.length) {
            this.currentServerSearchIndex =
                this.currentServerSearchIndex <= 0
                    ? this.serverSearchResults.length - 1
                    : this.currentServerSearchIndex - 1;
            this.navigateToSearchResult(this.currentServerSearchIndex);
            this.updateSearchCounter();
            return;
        }
        if (this.searchMatches.length === 0) return;
        this.currentSearchIndex =
            this.currentSearchIndex <= 0 ? this.searchMatches.length - 1 : this.currentSearchIndex - 1;
        this.highlightCurrentMatch();
        this.updateSearchCounter();
    }

    performSearch() {
        const container = this.overlay.querySelector('#textContent');
        if (!container || !this.searchTerm) return;
        
        const regex = new RegExp(this.escapeRegex(this.searchTerm), 'gi');
        const walker = document.createTreeWalker(
            container,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: (node) => {
                    if (node.parentElement?.classList.contains('ra-search-match')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return regex.test(node.textContent) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
                }
            }
        );
        
        const textNodes = [];
        let node;
        while (node = walker.nextNode()) {
            textNodes.push(node);
        }
        
        textNodes.forEach(textNode => {
            const parent = textNode.parentElement;
            const text = textNode.textContent;
            let lastIndex = 0;
            let match;
            const fragment = document.createDocumentFragment();
            
            regex.lastIndex = 0;
            
            while ((match = regex.exec(text)) !== null) {
                if (match.index > lastIndex) {
                    fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
                }
                
                const matchEl = document.createElement('span');
                matchEl.className = 'ra-search-match';
                matchEl.textContent = match[0];
                fragment.appendChild(matchEl);
                
                this.searchMatches.push(matchEl);
                lastIndex = match.index + match[0].length;
            }
            
            if (lastIndex < text.length) {
                fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
            }
            
            if (fragment.childNodes.length > 1 || 
                (fragment.childNodes.length === 1 && fragment.firstChild.nodeType === Node.ELEMENT_NODE)) {
                parent.replaceChild(fragment, textNode);
            }
        });
        
        if (this.searchMatches.length > 0) {
            this.currentSearchIndex = 0;
            this.highlightCurrentMatch();
        }
    }

    highlightCurrentMatch() {
        this.searchMatches.forEach(match => match.classList.remove('current'));
        
        if (this.currentSearchIndex >= 0 && this.currentSearchIndex < this.searchMatches.length) {
            const currentMatch = this.searchMatches[this.currentSearchIndex];
            currentMatch.classList.add('current');
            if (!this.userScrolling) {
                currentMatch.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
    }

    clearSearchHighlights() {
        const container = this.overlay.querySelector('#textContent');
        if (!container) return;
        
        const matches = container.querySelectorAll('.ra-search-match');
        matches.forEach(match => {
            const parent = match.parentElement;
            parent.replaceChild(document.createTextNode(match.textContent), match);
            parent.normalize();
        });
    }

    updateSearchCounter() {
        const counter = this.overlay.querySelector('#searchCounter');
        if (!counter) return;
        
        if (this.isSearching) {
            counter.textContent = 'Searching...';
            return;
        }

        if (this.serverSearchResults.length > 0) {
            counter.textContent = `${this.currentServerSearchIndex + 1} of ${this.serverSearchResults.length}`;
            return;
        }

        if (this.searchMatches.length === 0) {
            counter.textContent = this.searchTerm ? 'No matches' : '0 of 0';
        } else {
            counter.textContent = `${this.currentSearchIndex + 1} of ${this.searchMatches.length}`;
        }
    }

    updateSearchButtons() {
        const prevBtn = this.overlay.querySelector('#searchPrev');
        const nextBtn = this.overlay.querySelector('#searchNext');
        
        const hasMatches = this.serverSearchResults.length > 0 || this.searchMatches.length > 0;
        const isDisabled = this.isSearching || !hasMatches;
        
        if (prevBtn) prevBtn.disabled = isDisabled;
        if (nextBtn) nextBtn.disabled = isDisabled;
    }

    escapeRegex(string) {
        return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    updateProgress() {
        const progressFill = this.overlay.querySelector('#progressFill');
        const progressKnob = this.overlay.querySelector('#progressKnob');
        const currentTimeEl = this.overlay.querySelector('#currentTime');
        
        if (!this.player?.audio?.duration || !this.player?.audio) {
            if (progressFill) progressFill.style.width = '0%';
            if (progressKnob) progressKnob.style.left = '0%';
            if (currentTimeEl) currentTimeEl.textContent = '0:00';
            return;
        }
        
        const currentTime = this.player.audio.currentTime || 0;
        const duration = this.player.audio.duration || 1;
        const pct = Math.max(0, Math.min(100, (currentTime / duration) * 100));
        
        if (progressFill) progressFill.style.width = `${pct}%`;
        if (progressKnob) progressKnob.style.left = `${pct}%`;
        if (currentTimeEl) currentTimeEl.textContent = this.formatTime(currentTime);
    }

    updateDuration() {
        const durationEl = this.overlay.querySelector('#duration');
        if (durationEl && this.player?.audio?.duration) durationEl.textContent = this.formatTime(this.player.audio.duration);
    }

    updatePlayButton() {
        const playIcon = this.overlay.querySelector('#playIcon');
        if (playIcon && this.player?.audio) playIcon.className = this.player.audio.paused ? 'fas fa-play' : 'fas fa-pause';
    }

    handleProgressClick(e) {
        if (!this.player?.audio?.duration) return;
        const progressBar = this.overlay.querySelector('#progressBar');
        const rect = progressBar.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const percentage = Math.max(0, Math.min(1, x / rect.width));
        const newTime = percentage * this.player.audio.duration;
        
        this.seekToTimeWithPrecision(newTime).then(success => {
            if (!success) {
                this.player.audio.currentTime = newTime;
            }
        });
        
        const progressKnob = this.overlay.querySelector('#progressKnob');
        if (progressKnob) {
            progressKnob.style.left = `${percentage * 100}%`;
        }
    }

    showState(state) {
        const loading = this.overlay.querySelector('.ra-loading-state');
        const error = this.overlay.querySelector('.ra-error-state');
        const content = this.overlay.querySelector('.ra-text-content');
        loading.style.display = state === 'loading' ? 'flex' : 'none';
        error.style.display = state === 'error' ? 'flex' : 'none';
        content.style.display = state === 'content' ? 'block' : 'none';
    }

    showError(message) {
        const errorMessage = this.overlay.querySelector('#errorMessage');
        if (errorMessage) errorMessage.textContent = message;
        this.showState('error');
    }

    formatTime(seconds) {
        if (isNaN(seconds)) return '0:00';
        const s = Math.floor(seconds);
        const mins = Math.floor(s / 60);
        const secs = s % 60;
        return `${mins}:${String(secs).padStart(2, '0')}`;
    }

    getCurrentVoice() {
        if (this.player?.voiceExtension?.getCurrentVoice) {
            const extVoice = this.player.voiceExtension.getCurrentVoice();
            if (extVoice) return extVoice;
        }
        const sources = [
            this.player?.currentVoice,
            this.player?.trackMetadata?.voice,
            window.trackData?.current_voice,
            window.trackData?.default_voice,
            this.voiceId
        ];
        for (const src of sources) if (src) return src;
        return null;
    }

    async handleVoiceChange(newVoiceId) {
        if (!newVoiceId || newVoiceId === this.currentVoiceId) return;
        const oldVoice = this.currentVoiceId;
        this.currentVoiceId = newVoiceId;
        
        try {
            this.showState('loading');
            await this.loadContent();
            this.buildTimingIndex();
            this.syncWithCurrentAudio();
        } catch (_e) {
            this.currentVoiceId = oldVoice;
            try {
                await this.loadContent();
                this.buildTimingIndex();
                this.syncWithCurrentAudio();
            } catch (_e2) {
                this.showError('Failed to sync with voice change');
            }
        }
    }

    startRafLoop() {
        if (this.rafId) return;
        
        const step = () => {
            if (this.isOpen) {
                try {
                    this.updateProgress();
                } catch (e) {
                }
                
                if (this.highlightingActive) {
                    try {
                        this.updateSentenceHighlighting();
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
}

// Initialize singleton
let readAlongOverlay = null;

document.addEventListener('DOMContentLoaded', () => {
    readAlongOverlay = new ReadAlongOverlay();
    window.readAlongSPAOverlay = readAlongOverlay;
});

window.ReadAlongOverlay = ReadAlongOverlay;