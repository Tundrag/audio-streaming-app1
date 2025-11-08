// ReadAlongUI.js - UI rendering, styling, and interface management (PAGINATION ONLY)

class ReadAlongUI {
    constructor(core) {
        this.core = core;

        // --- Scrubbing state ---
        this.isDragging = false;
        this.wasPlayingBeforeDrag = false;
        this.dragRect = null;
        this.dragRAF = null;
        this.lastPct = 0; // 0..1 visual during drag

        // bound handlers so we can add/remove listeners cleanly
        this._onDragStart = this.onDragStart.bind(this);
        this._onDragMove = this.onDragMove.bind(this);
        this._onDragEnd = this.onDragEnd.bind(this);
    }

    createOverlay() {
        const overlay = document.createElement('div');
        overlay.id = 'readAlongOverlay';
        
        overlay.innerHTML = this.getOverlayHTML();
        this.addStyles();
        document.body.appendChild(overlay);

        // ensure core.overlay is set
        this.core.overlay = overlay;
        
        // Set initial theme icon
        const themeIcon = overlay.querySelector('#themeToggle i');
        if (themeIcon) {
            themeIcon.className = this.core.theme === 'light' ? 'fas fa-moon' : 'fas fa-sun';
        }

        this.updateHighlightToggle(overlay);
        this.initializeSettings(overlay);

        this.updateEdgeNavVisibility();
        // Enable scrubbing/dragging
        this.setupProgressBarEvents();
        
        return overlay;
    }


    getOverlayHTML() {
        return `
            <div class="ra-container ${this.core.theme}">
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
                    
                    <div class="ra-pagination-section" id="paginationSection">
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

<div class="ra-edge-nav" id="edgeNav">
  <button class="ra-edge-btn ra-edge-left" id="edgePrev" title="Previous page" aria-label="Previous page">
    <i class="fas fa-chevron-left"></i>
  </button>
  <button class="ra-edge-btn ra-edge-right" id="edgeNext" title="Next page" aria-label="Next page">
    <i class="fas fa-chevron-right"></i>
  </button>
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
                            <span id="currentTime">00:00:00</span>
                            <span id="duration">00:00:00</span>
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
    }

    addStyles() {
        if (document.getElementById('readAlongStyles')) return;
        const style = document.createElement('style');
        style.id = 'readAlongStyles';
        style.textContent = `
            /* Overlay base */
            #readAlongOverlay {
                position: fixed;
                inset: 0;
                background: rgba(0,0,0,0.95);
                backdrop-filter: blur(10px);
                z-index: 100000;
                opacity: 0;
                visibility: hidden;
                transition: all .3s ease;
                font-family: system-ui,-apple-system,sans-serif;
            }
            #readAlongOverlay.visible { opacity: 1; visibility: visible; }
            #readAlongOverlay.dragging, #readAlongOverlay.dragging * { cursor: grabbing !important; user-select: none !important; }

            /* Container: dynamic viewport + safe areas (fix mobile cut-offs) */
            #readAlongOverlay .ra-container {
                height: 100dvh;                 /* modern mobile viewport */
                min-height: 100svh;             /* small viewport fallback */
                display: flex;
                flex-direction: column;
                color: white;
                box-sizing: border-box;
                padding-bottom: env(safe-area-inset-bottom, 0px);
            }
            @supports not (height: 100dvh) {
                #readAlongOverlay .ra-container { height: 100vh; }
            }

/* Edge (middle) page nav */
#readAlongOverlay .ra-container { position: relative; }

#readAlongOverlay .ra-edge-nav {
    position: absolute;
    inset: 0;
    pointer-events: none; /* only buttons receive events */
    z-index: 9;
}

#readAlongOverlay .ra-edge-btn {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    width: 48px;
    height: 76px;
    border-radius: 9999px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid rgba(255,255,255,0.35);
    background: rgba(255,255,255,0.12);
    color: #fff;
    box-shadow: 0 6px 18px rgba(0,0,0,0.28);
    backdrop-filter: blur(6px);
    pointer-events: auto;
    cursor: pointer;
    transition: transform .15s ease, background .15s ease, opacity .2s ease, box-shadow .2s ease;
    opacity: .6; /* less faint by default */
}

#readAlongOverlay .ra-edge-btn:hover {
    background: rgba(255,255,255,0.22);
    opacity: .95;
    transform: translateY(-50%) scale(1.04);
}

#readAlongOverlay .ra-edge-btn:active {
    transform: translateY(-50%) scale(0.98);
}

#readAlongOverlay .ra-edge-btn:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.6);
}

#readAlongOverlay .ra-edge-left  { left: 10px; }
#readAlongOverlay .ra-edge-right { right: 10px; }

#readAlongOverlay .ra-edge-btn[disabled] {
    opacity: .28;
    cursor: default;
    pointer-events: none;
}

#readAlongOverlay .ra-edge-btn i {
    font-size: 1.2rem;
}

/* Light theme variant */
#readAlongOverlay .ra-container.light .ra-edge-btn {
    border-color: rgba(0,0,0,0.28);
    background: rgba(0,0,0,0.10);
    color: #111;
    box-shadow: 0 6px 18px rgba(0,0,0,0.16);
}
#readAlongOverlay .ra-container.light .ra-edge-btn:hover {
    background: rgba(0,0,0,0.18);
}

/* Small heights */
@media (max-height: 540px) {
    #readAlongOverlay .ra-edge-btn { height: 60px; }
}

/* Touch */
@media (hover:none) and (pointer:coarse) {
    #readAlongOverlay .ra-edge-btn { opacity: .75; }
}


/* Ensure words are painted even if ancestors/global CSS make text transparent */
#readAlongOverlay .ra-word {
  color: var(--ra-word-fg, #eaeaea) !important;
  -webkit-text-fill-color: currentColor !important;
  opacity: 1 !important;
}
#readAlongOverlay .ra-container.light .ra-word { --ra-word-fg: #1a1a1a; }

/* Optional: make punctuation inherit word color so they never diverge */
#readAlongOverlay .ra-punctuation { color: currentColor !important; }



            /* Header */
            #readAlongOverlay .ra-header {
                display:flex; align-items:center; justify-content:space-between;
                padding: calc(.75rem + env(safe-area-inset-top, 0px)) 1.5rem .75rem;
                background: rgba(0,0,0,0.3);
                border-bottom:1px solid rgba(255,255,255,0.1);
                backdrop-filter: blur(20px);
                min-height:60px;
            }
            #readAlongOverlay .ra-close-btn, #readAlongOverlay .ra-header-btn {
                background: rgba(255,255,255,0.1);
                border:1px solid rgba(255,255,255,0.2);
                color:white; padding:.5rem; border-radius:8px; cursor:pointer;
                transition:all .2s ease; width:36px; height:36px; display:flex; align-items:center; justify-content:center; position:relative;
            }
            #readAlongOverlay .ra-close-btn:hover, #readAlongOverlay .ra-header-btn:hover { background: rgba(255,255,255,0.2); transform: translateY(-1px); }

            #readAlongOverlay .ra-album-section { display:flex; align-items:center; gap:.75rem; flex:1; justify-content:center; max-width:400px; margin:0 1rem; }
            #readAlongOverlay .ra-album-art { width:40px; height:40px; border-radius:6px; object-fit:cover; box-shadow:0 3px 10px rgba(0,0,0,0.3); }
            #readAlongOverlay .ra-track-info h1 { font-size:1rem; font-weight:500; margin:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color: rgba(255,255,255,0.95); }
            #readAlongOverlay .ra-header-controls { display:flex; gap:.3rem; }
            #readAlongOverlay .ra-toggle-text { position:absolute; bottom:-18px; left:50%; transform:translateX(-50%); font-size:.65rem; font-weight:500; opacity:.8; }
            #readAlongOverlay .ra-header-btn.highlight-off { opacity:.5; }
            #readAlongOverlay .ra-header-btn.highlight-off .ra-toggle-text { opacity:.6; }

            /* Search */
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

            /* Pagination (always present) */
            #readAlongOverlay .ra-pagination-section { display:flex; flex:1; max-width:300px; margin:0 1rem; align-items:center; gap:.75rem; }
            #readAlongOverlay .ra-page-info { display:flex; align-items:center; gap:.5rem; font-size:.85rem; color: rgba(255,255,255,0.8); }
            #readAlongOverlay .ra-separator { opacity:.5; }
            #readAlongOverlay .ra-page-controls { display:flex; gap:.25rem; }
            #readAlongOverlay .ra-page-btn { background: rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.2); color:white; padding:.4rem; border-radius:6px; cursor:pointer; transition:all .2s ease; width:32px; height:32px; display:flex; align-items:center; justify-content:center; }
            #readAlongOverlay .ra-page-btn:hover:not(:disabled) { background: rgba(255,255,255,0.2); }
            #readAlongOverlay .ra-page-btn:disabled { opacity:.4; cursor:not-allowed; }

            /* Settings */
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

            /* Content area */
            #readAlongOverlay .ra-content {
                flex:1; padding:2rem; overflow-y:auto; display:flex; align-items:flex-start; justify-content:center;
                overscroll-behavior-y: contain;
            }
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
            #readAlongOverlay .ra-time-display { font-variant-numeric: tabular-nums; }


            /* Sentences/words */
            #readAlongOverlay .ra-sentence { display:inline; cursor:pointer; transition: all .2s ease; border-radius:4px; padding:4px 6px; margin:-4px -6px; border:1px solid transparent; box-shadow:0 2px 12px transparent; }
            #readAlongOverlay .ra-sentence:hover { background: rgba(255,255,255,0.08); border-radius:6px; border:1px solid rgba(255,255,255,0.1); }
            #readAlongOverlay .ra-sentence.ra-active { background: linear-gradient(135deg, rgba(59,130,246,0.25), rgba(99,102,241,0.2)); border-radius:6px; box-shadow:0 2px 12px rgba(59,130,246,0.3); border:1px solid rgba(59,130,246,0.4); }
            #readAlongOverlay .ra-word { display:inline; cursor:pointer; transition: all .1s ease; }
            #readAlongOverlay .ra-word[data-start-time]:hover { background: rgba(255,255,255,0.1); border-radius:2px; padding:1px 2px; margin:-1px -2px; }
            #readAlongOverlay .ra-punctuation { display:inline; color: rgba(255,255,255,0.9); opacity:.95; }

            /* Footer controls: safe-area padding */
            #readAlongOverlay .ra-controls {
                background: rgba(0,0,0,0.5);
                backdrop-filter: blur(20px);
                border-top:1px solid rgba(255,255,255,0.1);
                padding: 1rem 2rem calc(1.5rem + env(safe-area-inset-bottom, 0px));
            }
            #readAlongOverlay .ra-progress-section { margin-bottom:1rem; }
            #readAlongOverlay .ra-progress-bar { width:100%; height:6px; background: rgba(255,255,255,0.2); border-radius:3px; cursor:pointer; margin-bottom:.5rem; position:relative; }
            #readAlongOverlay .ra-progress-fill { height:100%; background: linear-gradient(90deg,#3b82f6,#6366f1); border-radius:3px; width:0%; transition: width .1s ease; }
            #readAlongOverlay .ra-progress-knob { position:absolute; top:50%; transform: translate(-50%, -50%); width:16px; height:16px; background:#fff; border-radius:50%; box-shadow:0 2px 6px rgba(0,0,0,0.3); left:0%; transition: left .1s ease; z-index:1; cursor: pointer; }
            #readAlongOverlay .ra-progress-knob:hover { transform: translate(-50%, -50%) scale(1.12); }
            #readAlongOverlay .ra-progress-knob:active { transform: translate(-50%, -50%) scale(1.2); }
            #readAlongOverlay.dragging .ra-progress-fill, #readAlongOverlay.dragging .ra-progress-knob { transition: none !important; }
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
            #readAlongOverlay .ra-container.light .ra-sentence:hover { background: rgba(0,0,0,0.08); border:1px solid rgba(0,0,0,0.1); }
            #readAlongOverlay .ra-container.light .ra-sentence.ra-active { background: linear-gradient(135deg, rgba(59,130,246,0.2), rgba(99,102,241,0.15)); border:1px solid rgba(59,130,246,0.5); box-shadow:0 2px 12px rgba(59,130,246,0.25); }
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

            /* Small phones */
            @media (max-width: 420px) {
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

                #readAlongOverlay .ra-content { padding:1.25rem; }
                #readAlongOverlay .ra-text-content { font-size:1.1rem; line-height:1.7; }
                #readAlongOverlay .ra-paragraph { text-indent:1rem; margin:0 0 1.5rem 0; line-height:1.7; }

                #readAlongOverlay .ra-controls { padding: .5rem .75rem calc(.9rem + env(safe-area-inset-bottom, 0px)); }
                #readAlongOverlay .ra-skip-btn { width:40px; height:40px; }
                #readAlongOverlay .ra-play-btn { width:56px; height:56px; }
                #readAlongOverlay .ra-control-btn i { font-size:1rem; }
                #readAlongOverlay .ra-skip-time { bottom:-18px; font-size:.65rem; }
            }

            /* Short viewports (landscape phones) */
            @media (max-height: 640px) {
                #readAlongOverlay .ra-content { padding:.75rem; }
                #readAlongOverlay .ra-paragraph { margin:0 0 .9rem 0; }
                #readAlongOverlay .ra-progress-section { margin-bottom:.6rem; }
            }
        `;
        document.head.appendChild(style);
    }

    initializeSettings(overlay) {
        const pageSizeSelect = overlay.querySelector('#pageSizeSelect');
        const syncOffsetSlider = overlay.querySelector('#syncOffsetSlider');
        const syncOffsetValue = overlay.querySelector('#syncOffsetValue');

        if (pageSizeSelect) {
            pageSizeSelect.value = this.core.pageSize.toString();
        }

        if (syncOffsetSlider && syncOffsetValue) {
            syncOffsetSlider.value = this.core.playbackOffsetMs;
            syncOffsetValue.textContent = `${this.core.playbackOffsetMs}ms`;

            syncOffsetSlider.addEventListener('input', () => {
                this.core.playbackOffsetMs = parseInt(syncOffsetSlider.value, 10) || 0;
                localStorage.setItem('readAlongSyncOffsetMs', String(this.core.playbackOffsetMs));
                syncOffsetValue.textContent = `${this.core.playbackOffsetMs}ms`;
                this.core.content.updateSentenceHighlighting();
            });
        }
    }

    handleClick(e) {
        const target = e.target.closest('button');
        if (!target) return;

        const id = target.id;

        switch(id) {
            case 'closeReadAlong': this.core.close(); break;
            case 'settingsToggle': this.toggleSettings(); break;
            case 'resetSync': this.resetSync(); break;
            case 'applySettings': this.applySettings(); break;
            case 'prevPage': this.core.content.goToPreviousPage(); break;
            case 'nextPage': this.core.content.goToNextPage(); break;
            case 'edgePrev': this.core.content.goToPreviousPage(); break;   // NEW
            case 'edgeNext': this.core.content.goToNextPage(); break;       // NEW
            case 'themeToggle': this.toggleTheme(); break;
            case 'highlightToggle': this.toggleHighlighting(); break;
            case 'searchToggle': this.toggleSearch(); break;
            case 'searchClose': this.closeSearch(); break;
            case 'searchNext': this.core.content.searchNext(); break;
            case 'searchPrev': this.core.content.searchPrev(); break;
            case 'progressBar': this.handleProgressClick(e); break;
        }
    }
    updateEdgeNavVisibility() {
        const edgeNav = this.core.overlay.querySelector('#edgeNav');
        if (!edgeNav) return;

        // Show if: multiple pages AND not in search UI (so it doesn’t block)
        const show = (this.core.totalPages > 1) && !this.core.content.searchActive;

        edgeNav.style.display = show ? 'block' : 'none';

        // Disable buttons at ends
        const prev = this.core.overlay.querySelector('#edgePrev');
        const next = this.core.overlay.querySelector('#edgeNext');
        if (prev) prev.disabled = this.core.currentPage <= 0;
        if (next) next.disabled = this.core.currentPage >= (this.core.totalPages - 1);

        // Lower opacity when disabled
        if (prev) prev.style.opacity = prev.disabled ? '0.2' : '';
        if (next) next.style.opacity = next.disabled ? '0.2' : '';
    }

    handleChange(e) {
        // No paging mode selection anymore - nothing to handle
    }

    // Theme management
    toggleTheme() {
        this.core.theme = this.core.theme === 'dark' ? 'light' : 'dark';
        const container = this.core.overlay.querySelector('.ra-container');
        container.className = `ra-container ${this.core.theme}`;
        const icon = this.core.overlay.querySelector('#themeToggle i');
        icon.className = this.core.theme === 'light' ? 'fas fa-moon' : 'fas fa-sun';
        localStorage.setItem('readAlongTheme', this.core.theme);
    }

    // Highlighting toggle
    toggleHighlighting() {
        this.core.highlightingActive = !this.core.highlightingActive;
        localStorage.setItem('readAlongHighlighting', this.core.highlightingActive.toString());
        
        if (!this.core.highlightingActive) {
            this.core.overlay.querySelectorAll('.ra-sentence.ra-active').forEach(el => {
                el.classList.remove('ra-active');
            });
            this.core.content.currentSentenceIndex = -1;
        }
        
        this.updateHighlightToggle();
    }

    updateHighlightToggle(overlay = this.core.overlay) {
        const toggleBtn = overlay.querySelector('#highlightToggle');
        const toggleText = overlay.querySelector('#highlightToggle .ra-toggle-text');
        
        if (toggleBtn && toggleText) {
            if (this.core.highlightingActive) {
                toggleBtn.classList.remove('highlight-off');
                toggleText.textContent = 'On';
            } else {
                toggleBtn.classList.add('highlight-off');
                toggleText.textContent = 'Off';
            }
        }
    }

    // Settings panel
    toggleSettings() {
        const settingsPanel = this.core.overlay.querySelector('#settingsPanel');
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
        const settingsPanel = this.core.overlay.querySelector('#settingsPanel');
        settingsPanel.style.display = 'none';
    }

    resetSync() {
        this.core.playbackOffsetMs = 0;
        localStorage.setItem('readAlongSyncOffsetMs', '0');
        this.updateSyncUI();
        this.core.content.updateSentenceHighlighting();
    }

    updateSyncUI() {
        const syncOffsetSlider = this.core.overlay.querySelector('#syncOffsetSlider');
        const syncOffsetValue = this.core.overlay.querySelector('#syncOffsetValue');
        if (syncOffsetSlider) syncOffsetSlider.value = this.core.playbackOffsetMs;
        if (syncOffsetValue) syncOffsetValue.textContent = `${this.core.playbackOffsetMs}ms`;
    }

    applySettings() {
        const pageSizeSelect = this.core.overlay.querySelector('#pageSizeSelect');
        
        const newPageSize = parseInt(pageSizeSelect.value);
        const settingsChanged = newPageSize !== this.core.pageSize;
        
        if (settingsChanged) {
            this.core.pageSize = newPageSize;
            this.core.currentPage = 0;
            
            localStorage.setItem('readAlongPageSize', this.core.pageSize.toString());
            
            this.updatePaginationUI();
            this.core.content.loadContent();
        }
        
        this.closeSettings();
    }

    // Search UI
    toggleSearch() {
        const searchSection = this.core.overlay.querySelector('#searchSection');
        const albumSection = this.core.overlay.querySelector('.ra-album-section');
        const paginationSection = this.core.overlay.querySelector('#paginationSection');
        this.updateEdgeNavVisibility();
        
        if (this.core.content.searchActive) {
            this.closeSearch();
        } else {
            this.core.content.searchActive = true;
            albumSection.style.display = 'none';
            paginationSection.style.display = 'none';
            searchSection.style.display = 'flex';
            searchSection.classList.add('active');
            
            const searchInput = this.core.overlay.querySelector('#searchInput');
            if (searchInput) {
                searchInput.focus();
                if (this.core.content.searchTerm) {
                    searchInput.value = this.core.content.searchTerm;
                    searchInput.select();
                }
            }
        }
    }

    closeSearch() {
        const searchSection = this.core.overlay.querySelector('#searchSection');
        const albumSection = this.core.overlay.querySelector('.ra-album-section');
        
        this.core.content.closeSearch();
        this.updateEdgeNavVisibility();
        
        searchSection.style.display = 'none';
        searchSection.classList.remove('active');
        albumSection.style.display = 'flex';
        this.updatePaginationUI();
        
        const searchInput = this.core.overlay.querySelector('#searchInput');
        if (searchInput) searchInput.value = '';
    }

    clearSearchUI() {
        const searchSection = this.core.overlay.querySelector('#searchSection');
        const albumSection = this.core.overlay.querySelector('.ra-album-section');
        
        if (searchSection && albumSection) {
            searchSection.style.display = 'none';
            searchSection.classList.remove('active');
            albumSection.style.display = 'flex';
        }
    }

    // Pagination UI - ALWAYS ACTIVE NOW
    updatePaginationUI() {
        const paginationSection = this.core.overlay.querySelector('#paginationSection');
        const albumSection = this.core.overlay.querySelector('.ra-album-section');
        const pageInfo = this.core.overlay.querySelector('#pageInfo');
        const wordInfo = this.core.overlay.querySelector('#wordInfo');
        const prevPageBtn = this.core.overlay.querySelector('#prevPage');
        const nextPageBtn = this.core.overlay.querySelector('#nextPage');

        // Show pagination if we have multiple pages OR if we're in search mode
        if (this.core.totalPages > 1 || this.core.content.searchActive) {
            if (!this.core.content.searchActive) {
                albumSection.style.display = 'none';
                paginationSection.style.display = 'flex';
            }
            
            if (pageInfo) pageInfo.textContent = `Page ${this.core.currentPage + 1} of ${this.core.totalPages}`;
            if (wordInfo) {
                const wordsOnPage = Math.min(this.core.pageSize, this.core.totalWords - (this.core.currentPage * this.core.pageSize));
                wordInfo.textContent = `${wordsOnPage} words`;
            }
            
            if (prevPageBtn) prevPageBtn.disabled = this.core.currentPage === 0;
            if (nextPageBtn) nextPageBtn.disabled = this.core.currentPage >= this.core.totalPages - 1;
            this.updateEdgeNavVisibility();
        } else if (!this.core.content.searchActive) {
            // Single page and not searching - show album info instead
            paginationSection.style.display = 'none';
            albumSection.style.display = 'flex';
        }
    }

    // Track info
    updateTrackInfo() {
        const trackTitle = this.core.overlay.querySelector('#trackTitle');
        const albumArt = this.core.overlay.querySelector('#albumArt');
        if (trackTitle) trackTitle.textContent = window.trackData?.title || window.currentTrackTitle || 'Unknown Track';
        if (albumArt) {
            albumArt.src = window.albumData?.cover_path || window.currentAlbumCoverPath || '';
            albumArt.alt = (window.albumData?.title || window.currentAlbumTitle || 'Album') + ' Cover';
        }
    }

    // Progress and playback UI (non-drag updates from timeupdate)
    updateProgress() {
        if (this.isDragging) return; // during drag, visual is driven by pointer
        const progressFill = this.core.overlay.querySelector('#progressFill');
        const progressKnob = this.core.overlay.querySelector('#progressKnob');
        const currentTimeEl = this.core.overlay.querySelector('#currentTime');
        
        if (!this.core.player?.audio?.duration || !this.core.player?.audio) {
            if (progressFill) progressFill.style.width = '0%';
            if (progressKnob) progressKnob.style.left = '0%';
            if (currentTimeEl) currentTimeEl.textContent = '0:00';
            return;
        }
        
        const currentTime = this.core.player.audio.currentTime || 0;
        const duration = this.core.player.audio.duration || 1;
        const pct = Math.max(0, Math.min(100, (currentTime / duration) * 100));
        
        if (progressFill) progressFill.style.width = `${pct}%`;
        if (progressKnob) progressKnob.style.left = `${pct}%`;
        if (currentTimeEl) currentTimeEl.textContent = this.formatTime(currentTime);
    }

    updateDuration() {
        const durationEl = this.core.overlay.querySelector('#duration');
        if (durationEl && this.core.player?.audio?.duration) durationEl.textContent = this.formatTime(this.core.player.audio.duration);
    }

    updatePlayButton() {
        const playIcon = this.core.overlay.querySelector('#playIcon');
        if (playIcon && this.core.player?.audio) playIcon.className = this.core.player.audio.paused ? 'fas fa-play' : 'fas fa-pause';
    }

    handleProgressClick(e) {
        if (!this.core.player?.audio?.duration) return;
        const progressBar = this.core.overlay.querySelector('#progressBar');
        const rect = progressBar.getBoundingClientRect();
        const x = e.clientX - rect.left;
        this.handleProgressScrub(x, rect);
    }

    // Scrubbing implementation
    setupProgressBarEvents() {
        const overlay = this.core.overlay;
        const bar = overlay.querySelector('#progressBar');
        const knob = overlay.querySelector('#progressKnob');
        if (!bar || !knob) return;

        // mouse
        bar.addEventListener('mousedown', this._onDragStart);
        knob.addEventListener('mousedown', this._onDragStart);
        window.addEventListener('mousemove', this._onDragMove);
        window.addEventListener('mouseup', this._onDragEnd);

        // touch (passive:false so we can prevent scroll while dragging)
        bar.addEventListener('touchstart', this._onDragStart, { passive: false });
        knob.addEventListener('touchstart', this._onDragStart, { passive: false });
        window.addEventListener('touchmove', this._onDragMove, { passive: false });
        window.addEventListener('touchend', this._onDragEnd);
        window.addEventListener('touchcancel', this._onDragEnd);
    }

    onDragStart(e) {
        if (!this.core.player?.audio?.duration) return;
        const overlay = this.core.overlay;
        const bar = overlay.querySelector('#progressBar');
        this.dragRect = bar.getBoundingClientRect();

        const x = this.getPointerX(e) - this.dragRect.left;
        e.preventDefault();

        this.isDragging = true;
        overlay.classList.add('dragging');

        const audio = this.core.player.audio;
        this.wasPlayingBeforeDrag = audio && !audio.paused;
        if (audio) audio.pause();

        this.handleProgressScrub(x, this.dragRect, { visualOnly: true });
    }

    onDragMove(e) {
        if (!this.isDragging || !this.dragRect) return;
        if (e.cancelable) e.preventDefault();
        const x = this.getPointerX(e) - this.dragRect.left;

        if (this.dragRAF) cancelAnimationFrame(this.dragRAF);
        this.dragRAF = requestAnimationFrame(() => {
            this.handleProgressScrub(x, this.dragRect, { visualOnly: true });
        });
    }

    onDragEnd(e) {
        if (!this.isDragging) return;
        const overlay = this.core.overlay;
        overlay.classList.remove('dragging');
        this.isDragging = false;

        const rect = this.dragRect;
        this.dragRect = null;

        const x = this.getPointerX(e) - rect.left;
        this.handleProgressScrub(x, rect, { commit: true });
    }

    getPointerX(e) {
        if (e.touches && e.touches.length) return e.touches[0].clientX;
        if (e.changedTouches && e.changedTouches.length) return e.changedTouches[0].clientX;
        return e.clientX;
    }

    async handleProgressScrub(x, rect, opts = {}) {
        if (!rect || !this.core.player?.audio?.duration) return;
        const duration = this.core.player.audio.duration;
        const clamped = Math.max(0, Math.min(rect.width, x));
        const pct = rect.width ? clamped / rect.width : 0;
        this.lastPct = pct;

        const progressFill = this.core.overlay.querySelector('#progressFill');
        const progressKnob = this.core.overlay.querySelector('#progressKnob');
        const currentTimeEl = this.core.overlay.querySelector('#currentTime');

        const pct100 = (pct * 100).toFixed(4);
        if (progressFill) progressFill.style.width = `${pct100}%`;
        if (progressKnob) progressKnob.style.left = `${pct100}%`;
        if (currentTimeEl) currentTimeEl.textContent = this.formatTime(duration * pct);

        if (opts.visualOnly) return;

        const newTime = duration * pct;

        let success = false;
        try {
            success = await this.core.content.seekToTimeWithPrecision(newTime);
        } catch (_) { /* ignore */ }

        if (!success && this.core.player?.audio) {
            this.core.player.audio.currentTime = newTime;
        }

        try { this.core.content.updateSentenceHighlighting(true); } catch (_) {}

        if (this.wasPlayingBeforeDrag && this.core.player?.audio?.paused) {
            try { await this.core.player.audio.play(); } catch (_) { /* autoplay block ignored */ }
        }

        this.updatePlayButton();
    }

    // State management
    showState(state) {
        const loading = this.core.overlay.querySelector('.ra-loading-state');
        const error = this.core.overlay.querySelector('.ra-error-state');
        const content = this.core.overlay.querySelector('.ra-text-content');
        loading.style.display = state === 'loading' ? 'flex' : 'none';
        error.style.display = state === 'error' ? 'flex' : 'none';
        content.style.display = state === 'content' ? 'block' : 'none';
    }

    showError(message) {
        const errorMessage = this.core.overlay.querySelector('#errorMessage');
        if (errorMessage) errorMessage.textContent = message;
        this.showState('error');
    }

    // Utility
    formatTime(totalSeconds) {
        if (!Number.isFinite(totalSeconds)) return '00:00:00';
        const s = Math.max(0, Math.floor(totalSeconds));
        const hh = Math.floor(s / 3600);
        const mm = Math.floor((s % 3600) / 60);
        const ss = s % 60;
        const pad = (n) => String(n).padStart(2, '0');
        return `${pad(hh)}:${pad(mm)}:${pad(ss)}`;
    }
}
window.ReadAlongUI = ReadAlongUI;