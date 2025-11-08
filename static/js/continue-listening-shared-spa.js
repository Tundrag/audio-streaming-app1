// continue-listening-shared-spa.js - Universal controller for Continue Listening page (SSR and SPA modes)

export class ContinueListeningController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.tracks = [];
        this.bootstrapData = null;
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }

        return this.generateHTML();
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`🎧 ContinueListening: Mounting in ${this.mode} mode...`);

        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM if available
            this.hydrateFromDOM();
        }

        await this.loadTracks();
        this.setupEventListeners();

        console.log('✅ ContinueListening: Mounted successfully');
    }

    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        const bootstrapScript = document.getElementById('continue-listening-bootstrap-data');
        if (bootstrapScript) {
            try {
                this.bootstrapData = JSON.parse(bootstrapScript.textContent);
                if (this.bootstrapData.tracks) {
                    this.tracks = this.bootstrapData.tracks;
                    console.log('📦 Hydrated continue-listening data from DOM');
                }
            } catch (error) {
                console.error('Error parsing bootstrap data:', error);
            }
        }
    }

    // ✅ Generate HTML for SPA mode
    generateHTML() {
        return `
            <div class="continue-listening-page">
                <div class="page-header">
                    <h1>Continue Listening</h1>
                    <div class="track-stats" id="trackStats"></div>
                </div>
                <div class="tracks-list" id="tracksList">
                    <div class="loading">
                        <i class="fas fa-spinner"></i>
                        <p>Loading your tracks...</p>
                    </div>
                </div>
            </div>
        `;
    }

    async loadTracks() {
        // Skip if we already have hydrated data
        if (this.tracks.length > 0 && this.mode === 'ssr') {
            console.log('✅ Using hydrated tracks data');
            this.renderTracks();
            this.updateStats();
            return;
        }

        try {
            const response = await fetch('/api/continue-listening');
            if (!response.ok) {
                throw new Error('Failed to load tracks');
            }

            this.tracks = await response.json();
            console.log(`🎧 Loaded ${this.tracks.length} in-progress tracks`);

            this.renderTracks();
            this.updateStats();

        } catch (error) {
            console.error('❌ Error loading tracks:', error);
            this.renderError();
        }
    }

    renderTracks() {
        const container = document.getElementById('tracksList');
        if (!container) return;

        if (this.tracks.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-headphones"></i>
                    <h2>No Tracks in Progress</h2>
                    <p>Start listening to tracks and they'll appear here so you can continue where you left off.</p>
                    <a href="/collection" class="btn-primary" data-spa-link>Browse Collection</a>
                </div>
            `;
            return;
        }

        container.innerHTML = this.tracks.map(track => this.createTrackCard(track)).join('');
    }

    createTrackCard(track) {
        const progress = Math.min(Math.max(track.progress || 0, 0), 100);

        return `
            <div class="progress-track" data-track-id="${track.id}" data-position="${track.position || 0}">
                <img src="${track.cover_path}"
                     alt="${this.escapeHtml(track.title)}"
                     class="track-thumbnail"
                     onerror="this.src='/static/images/default-album.jpg'">
                <div class="track-info">
                    <div class="track-title">${this.escapeHtml(track.title)}</div>
                    <div class="track-album">${this.escapeHtml(track.album_title)}</div>
                    <div class="track-time">Last played: ${track.last_played_formatted || 'Recently'}</div>
                    <div class="progress-container">
                        <div class="progress-bar" style="width: ${progress}%"></div>
                    </div>
                </div>
                <button class="resume-btn" data-track-id="${track.id}" data-position="${track.position || 0}">
                    <i class="fas fa-play"></i>
                    Resume
                </button>
            </div>
        `;
    }

    updateStats() {
        const statsContainer = document.getElementById('trackStats');
        if (!statsContainer) return;

        statsContainer.innerHTML = `
            ${this.tracks.length} track${this.tracks.length !== 1 ? 's' : ''} in progress
        `;
    }

    setupEventListeners() {
        // Resume buttons
        document.querySelectorAll('.resume-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const trackId = btn.dataset.trackId;
                this.resumeTrack(trackId, e);
            });
        });

        // Track card clicks (also resume)
        document.querySelectorAll('.progress-track').forEach(card => {
            card.addEventListener('click', (e) => {
                const trackId = card.dataset.trackId;
                this.resumeTrack(trackId, e);
            });
        });

        // Handle SPA links
        document.querySelectorAll('[data-spa-link]').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const href = link.getAttribute('href');
                if (window.spaRouter) {
                    window.spaRouter.navigate(href);
                }
            });
        });
    }

    async resumeTrack(trackId, event) {
        if (!trackId) return;

        console.log(`▶️ Resuming track: ${trackId}`);

        // Get track element and saved position
        const trackElement = event ? event.target.closest('.progress-track') :
                            document.querySelector(`[data-track-id="${trackId}"]`);

        if (!trackElement) {
            console.error('Track element not found');
            return;
        }

        const savedPosition = parseFloat(trackElement.dataset.position) || 0;

        try {
            if (window.persistentPlayer) {
                // ✅ Extract track data directly from DOM (same as home page)
                const titleElement = trackElement.querySelector('.track-title');
                const albumElement = trackElement.querySelector('.track-album');
                const coverElement = trackElement.querySelector('.track-thumbnail');

                const trackData = {
                    title: titleElement ? titleElement.textContent.trim() : 'Unknown Track',
                    album_title: albumElement ? albumElement.textContent.trim() : 'Unknown Album',
                    cover_path: coverElement ? coverElement.src : '/static/images/default-album.jpg'
                };

                console.log('🎵 Resuming track from DOM data:', trackData);

                // Play in mini-player
                await window.persistentPlayer.playTrack(
                    trackId,
                    trackData.title,
                    trackData.album_title,
                    trackData.cover_path,
                    true  // ✅ Auto-play when resuming
                );

                // Seek to saved position after a short delay
                setTimeout(() => {
                    if (window.persistentPlayer.audio) {
                        window.persistentPlayer.audio.currentTime = savedPosition;
                        console.log(`⏩ Seeking to ${savedPosition}s`);
                    }
                }, 500);

                if (window.showToast) {
                    window.showToast('Resuming playback...', 'success');
                }
            } else {
                console.warn('Persistent player not available');
            }
        } catch (error) {
            console.error('❌ Error resuming track:', error);
            if (window.showToast) {
                window.showToast('Failed to resume playback', 'error');
            }
        }
    }

    renderError() {
        const container = document.getElementById('tracksList');
        if (container) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-exclamation-triangle"></i>
                    <h2>Error Loading Tracks</h2>
                    <p>There was a problem loading your in-progress tracks.</p>
                    <button class="btn-primary" onclick="location.reload()">Retry</button>
                </div>
            `;
        }
    }

    escapeHtml(unsafe) {
        if (unsafe === null || unsafe === undefined) return '';
        return String(unsafe)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    async destroy() {
        console.log('🧹 ContinueListening: Destroying...');
        // No cleanup needed as event listeners are added directly to elements
        // and will be garbage collected when elements are removed
        this.tracks = [];
    }
}
