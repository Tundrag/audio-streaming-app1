if (typeof AlbumDetails === 'undefined') {
class AlbumDetails {
    constructor() {
        this.albumId = window.albumId;
        this.tracks = window.albumTracks || [];
        this.userPermissions = window.userPermissions || {};
        this.albumCoverPath = document.querySelector('.album-cover-large')?.src || '/static/images/default-album.jpg';
        this.albumTitle = document.querySelector('.album-title')?.textContent || '';
        
        this.isInitialized = false;
        this.trackStatusCache = new Map();
        this.ttsChannel = window.TTSStatusChannel || null;
        this.realtimeSubscriptions = new Map();
        this.activeVoiceJobs = new Map();
        this.realtimeListenersAttached = false;
        this.handleRealtimeEvent = this.handleRealtimeEvent.bind(this);
        this.handleRealtimeSocketConnected = this.handleRealtimeSocketConnected.bind(this);
        this.handleRealtimeSocketDisconnected = this.handleRealtimeSocketDisconnected.bind(this);
        
        if (!this.albumId) return;
        this.initializeWithDelay();
    }

    async initializeWithDelay() {
        await this.waitForBaseInitialization();
        
        try {
            this.initializeDOMElements();
            this.initializeEventListeners();
            this.displayTrackDurations();
            this.addBadgesToExistingTracks();
            this.initializeDragAndDrop();
            this.setupGlobalFunctions();
            this.addStyles();
            this.initializeRealtimeUpdates();
            this.isInitialized = true;

            // Check for scheduled visibility changes on tracks and show countdowns
            setTimeout(() => this.checkAllTrackSchedules(), 500);
        } catch (error) {
            this.showToast('Initialization failed', 'error');
            return;
        }

        const autoCloseTTSModal = () => {
            try {
                const tm = this.ttsManager || window.ttsManager;
                if (!tm) return;

                const hasActive =
                    (tm.activeTTSJobs && tm.activeTTSJobs.size > 0) ||
                    (tm.bulkJobs && tm.bulkJobs.size > 0) ||
                    (typeof tm.hasActiveJobs === 'function' && tm.hasActiveJobs());

                if (hasActive) return;

                const isOpen =
                    (typeof tm.isTTSModalOpen === 'boolean' && tm.isTTSModalOpen) ||
                    document.querySelector('.tts-modal.is-open, .modal-tts.is-open, #ttsModal.show');

                if (!isOpen) return;

                if (typeof tm.closeTTSModal === 'function') tm.closeTTSModal();
                else if (typeof window.closeTTSModal === 'function') window.closeTTSModal();
            } catch { }
        };

        setTimeout(autoCloseTTSModal, 1000);
        setTimeout(autoCloseTTSModal, 2500);
        setTimeout(autoCloseTTSModal, 5000);
    }

    async waitForBaseInitialization() {
        return new Promise((resolve) => {
            let attempts = 0;
            const check = () => {
                attempts++;
                const ready = document.querySelector('.main-header') && 
                             (typeof window.BadgeManager === 'undefined' || window.BadgeManager.isInitialized !== false);
                
                if (ready || attempts >= 50) {
                    resolve();
                } else {
                    setTimeout(check, 100);
                }
            };
            check();
        });
    }
    
    initializeDOMElements() {
        this.tracksList = document.getElementById('tracksList');
        this.selectAllCheckbox = document.getElementById('selectAllTracks');
        this.bulkDeleteBtn = document.getElementById('bulkDeleteBtn');
    }

    initializeEventListeners() {
        this.selectAllCheckbox?.addEventListener('change', (e) => {
            document.querySelectorAll('.track-checkbox').forEach(cb => cb.checked = e.target.checked);
            this.updateBulkDeleteButton();
        });

        document.querySelectorAll('.track-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', () => this.updateBulkDeleteButton());
            checkbox.addEventListener('click', (e) => e.stopPropagation());
        });

        document.querySelectorAll('.track-item').forEach(track => {
            const trackId = track.dataset.trackId;
            track.addEventListener('click', (e) => {
                if (!e.target.closest('.track-actions') && !e.target.closest('.track-checkbox') && !e.target.closest('.track-symbols')) {
                    e.preventDefault();
                    e.stopPropagation(); // ✅ Prevent event from bubbling to parent handlers
                    this.playTrack(trackId);
                }
            });
        });

        document.querySelectorAll('.rename-track').forEach(btn => 
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.handleRename(e.currentTarget.dataset.trackId);
            })
        );

        document.querySelectorAll('.download-track').forEach(btn => {
            if (!btn.disabled) {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.downloadTrack(e.currentTarget.dataset.trackId, e.currentTarget.dataset.trackTitle, e);
                });
            }
        });

        document.querySelectorAll('.delete-track').forEach(btn => 
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.handleDelete(e.currentTarget.dataset.trackId);
            })
        );

        this.bulkDeleteBtn?.addEventListener('click', () => this.handleBulkDelete());
    }

    setupGlobalFunctions() {
        window.albumDetails = this;
    }

    initBeforeUnloadCleanup() {
        window.addEventListener('beforeunload', () => {
            if (audioUpload?.closeAddTrackModal) {
                audioUpload.closeAddTrackModal();
            }
            if (ttsManager?.clearTTSProgress) {
                ttsManager.clearTTSProgress();
            }
            if (ttsManager?.clearBulkProgress) {
                ttsManager.clearBulkProgress();
            }

            try {
                ttsManager.activeTTSJobs.clear();
                ttsManager.bulkJobs.clear();
                ttsManager.progressErrorCounts.clear();
            } catch (error) {}

            if (window.albumDetails?.cleanup) {
                window.albumDetails.cleanup();
            }
        });
    }

    updateTrackIcon(trackId, status, voiceId = null) {
        const trackElement = document.querySelector(`.track-item[data-track-id="${trackId}"]`);
        if (!trackElement) return;
        
        const voiceSymbol = trackElement.querySelector('.voice-symbol');
        if (!voiceSymbol) return;
        
        const track = this.tracks.find(t => t.id === trackId);
        if (!track) return;

        this.applyVoiceIconState(track, voiceSymbol, status, voiceId);
    }

    getTrackDefaultVoice(track) {
        if (!track) return null;
        if (track.default_voice) return track.default_voice;
        if (track.voice) return track.voice;
        if (track.tts_voice) return track.tts_voice;
        if (track.voice_directory) {
            const match = `${track.voice_directory}`.match(/voice-([^/]+)/i);
            if (match && match[1]) return match[1];
        }
        return null;
    }

    isDefaultVoice(track, voiceId) {
        if (!track) return true;
        if (!voiceId || voiceId === 'default') return true;
        const defaultVoice = this.getTrackDefaultVoice(track);
        if (!defaultVoice) return true;
        return defaultVoice === voiceId;
    }

    lookupVoiceMeta(track, voiceId) {
        if (!track || !voiceId) return null;
        const segments = track.voice_segments;
        if (!segments) return null;

        if (Array.isArray(segments)) {
            return segments.find(entry => {
                const entryVoice = entry?.voice_id || entry?.voice || entry?.id;
                return entryVoice === voiceId;
            }) || null;
        }

        if (segments[voiceId]) return segments[voiceId];
        const normalizedKey = `voice-${voiceId}`;
        if (segments[normalizedKey]) return segments[normalizedKey];
        return null;
    }

    extractVoiceStatus(meta) {
        if (!meta) return '';
        if (typeof meta === 'string') return meta.toLowerCase();
        if (Array.isArray(meta)) {
            for (const item of meta) {
                const status = this.extractVoiceStatus(item);
                if (status) return status;
            }
            return '';
        }
        const status = meta.status || meta.state || meta.processing_status || 
                      meta.voice_status || meta.overall_status || '';
        return status ? String(status).toLowerCase() : '';
    }

    hasReadyDefaultVoice(track) {
        if (!track) return false;

        // ✅ FAST PATH: Check cached status first
        const cached = this.trackStatusCache.get(track.id);
        if (cached === 'complete' || cached === 'ready') return true;
        if (cached === 'generating' || cached === 'processing') return false;

        // ✅ LEGACY FALLBACK: Check voice_directory or file_path for old tracks
        if (track.voice_directory) return true;
        if (track.file_path && track.file_path.trim().length > 0) {
            return true;
        }

        // For TTS tracks without voice_directory/file_path, we need to check voice_generation_status
        // This will be done asynchronously via fetchDefaultVoiceStatus()
        const defaultVoice = this.getTrackDefaultVoice(track);
        if (!defaultVoice) return false;

        // Check old voice_segments metadata as fallback
        const meta = this.lookupVoiceMeta(track, defaultVoice);
        if (meta) {
            const status = this.extractVoiceStatus(meta);
            if (['ready', 'complete', 'success', 'available', 'done'].includes(status)) {
                return true;
            }
            if (meta.ready === true) return true;
        }

        if (Array.isArray(track.generated_voices) && track.generated_voices.includes(defaultVoice)) {
            return true;
        }

        return false;
    }

    async fetchDefaultVoiceStatus(track) {
        /**
         * Query voice_generation_status table via API to get real-time default voice status.
         * Updates badge icon based on actual DB state.
         */
        if (!track || !track.id) return;

        try {
            const response = await fetch(`/api/tracks/${encodeURIComponent(track.id)}/default-voice-status`, {
                credentials: 'include'
            });

            if (!response.ok) {
                console.error(`Failed to fetch voice status for track ${track.id}`);
                return;
            }

            const data = await response.json();

            // Update cache based on status
            if (data.status === 'complete') {
                this.trackStatusCache.set(track.id, 'complete');
            } else if (data.status === 'generating') {
                this.trackStatusCache.set(track.id, 'generating');
            } else if (data.status === 'failed') {
                this.trackStatusCache.set(track.id, 'error');
            }

            // Update badge icon immediately
            this.updateTrackIcon(track.id, data.status, data.default_voice);

            return data;
        } catch (error) {
            console.error(`Error fetching default voice status for track ${track.id}:`, error);
        }
    }

    updateTrackIcon(trackId, status, voiceId) {
        /**
         * Update the badge icon for a track based on voice_generation_status.
         * Only updates default voice badges.
         */
        const trackElement = document.querySelector(`.track-item[data-track-id="${trackId}"]`);
        if (!trackElement) return;

        const voiceSymbol = trackElement.querySelector('.voice-symbol');
        if (!voiceSymbol) return;

        // Map DB status to badge state
        const badgeState = {
            'complete': 'ready',
            'generating': 'processing',
            'segmenting': 'processing',
            'processing': 'processing',
            'failed': 'error',
            'error': 'error'
        }[status] || 'ready';

        // Update icon
        switch (badgeState) {
            case 'processing':
                voiceSymbol.innerHTML = '⏳';
                voiceSymbol.style.cursor = 'default';
                voiceSymbol.title = 'Generating voice...';
                voiceSymbol.onclick = null;
                break;
            case 'error':
                voiceSymbol.innerHTML = '❌';
                voiceSymbol.style.cursor = 'default';
                voiceSymbol.title = 'Default voice generation failed';
                voiceSymbol.onclick = null;
                break;
            default:  // 'ready'
                voiceSymbol.innerHTML = '🔄';
                voiceSymbol.style.cursor = 'pointer';
                voiceSymbol.title = 'Switch voice';
                voiceSymbol.onclick = (e) => {
                    e.stopPropagation();
                    this.playTrack(trackId);
                };
                break;
        }
    }

    hasDefaultVoiceFailure(track) {
        // ✅ PRIORITY: If default voice is ready, badge should NEVER show error
        // Default voice state takes precedence
        if (!track || this.hasReadyDefaultVoice(track)) {
            return false;
        }

        // Only check failure status if default voice is NOT ready
        const statusValues = [
            (track.tts_status || '').toLowerCase(),
            (track.upload_status || '').toLowerCase(),
            (track.status || '').toLowerCase()
        ];

        if (statusValues.some(value => ['failed', 'error'].includes(value))) {
            return true;
        }

        const defaultVoice = this.getTrackDefaultVoice(track);
        const meta = this.lookupVoiceMeta(track, defaultVoice);
        const voiceStatus = this.extractVoiceStatus(meta);
        return ['failed', 'error'].includes(voiceStatus);
    }

    isDefaultVoiceProcessing(track) {
        if (!track) return false;

        // ✅ PRIORITY: If default voice is ready, badge should NEVER show processing
        // Default voice state takes precedence over any other status
        if (this.hasReadyDefaultVoice(track)) {
            return false;
        }

        // Only check processing status if default voice is NOT ready
        const statusValues = [
            (track.tts_status || '').toLowerCase(),
            (track.upload_status || '').toLowerCase(),
            (track.status || '').toLowerCase()
        ];

        const indicatesProcessing = statusValues.some(value =>
            ['processing', 'segmenting', 'generating'].includes(value)
        );

        if (!indicatesProcessing) return false;

        // Check if there's an active job for the default voice
        const activeVoice = this.activeVoiceJobs.get(track.id);
        if (!activeVoice) return true;  // Processing but no specific voice = default voice processing
        return this.isDefaultVoice(track, activeVoice);
    }

    determineVoiceDisplayState(track, overrideStatus = null, voiceId = null) {
        const normalizedOverride = (overrideStatus || '').toLowerCase();
        const defaultReady = this.hasReadyDefaultVoice(track);
        const defaultFailed = this.hasDefaultVoiceFailure(track);
        const affectsDefault = this.isDefaultVoice(track, voiceId);

        if (normalizedOverride) {
            if (['error', 'failed'].includes(normalizedOverride)) {
                if (affectsDefault || !defaultReady) return 'error';
                return defaultFailed ? 'error' : 'ready';
            }

            if (['ready', 'complete'].includes(normalizedOverride)) {
                return 'ready';
            }

            if (['processing', 'segmenting', 'generating'].includes(normalizedOverride)) {
                if (!defaultReady || affectsDefault) return 'processing';
                return defaultFailed ? 'error' : 'ready';
            }
        }

        if (defaultFailed) return 'error';
        if (this.isDefaultVoiceProcessing(track)) return 'processing';
        return 'ready';
    }

    applyVoiceIconState(track, voiceSymbol, overrideStatus = null, voiceId = null) {
        const state = this.determineVoiceDisplayState(track, overrideStatus, voiceId);
        const trackTitle = track?.title || '';

        voiceSymbol.onclick = null;

        switch (state) {
            case 'processing':
                voiceSymbol.innerHTML = '⏳';
                voiceSymbol.style.cursor = 'default';
                voiceSymbol.title = 'Generating voice...';
                break;
            case 'error':
                voiceSymbol.innerHTML = '❌';
                voiceSymbol.style.cursor = 'default';
                voiceSymbol.title = 'Default voice generation failed';
                break;
            default:
                // Don't overwrite - the correct badge is already there
                break;
        }
    }

    getActiveVoiceForTrack(trackId) {
        const activeJob = this.activeVoiceJobs?.get(trackId);
        if (activeJob?.voiceId) {
            return activeJob.voiceId;
        }
        const track = this.tracks.find(t => t.id === trackId);
        return track?.processing_voice || null;
    }

    shouldTrackDefaultVoice(track) {
        if (!track) return false;
        return !this.hasReadyDefaultVoice(track) || this.isDefaultVoiceProcessing(track);
    }


    cleanup() {
        if (this.realtimeListenersAttached) {
            window.removeEventListener('ttsStatusUpdate', this.handleRealtimeEvent);
            window.removeEventListener('ttsWebSocketConnected', this.handleRealtimeSocketConnected);
            window.removeEventListener('ttsWebSocketDisconnected', this.handleRealtimeSocketDisconnected);
            this.realtimeListenersAttached = false;
        }

        if (this.ttsChannel) {
            this.realtimeSubscriptions.forEach(({ trackId, voiceId }) => {
                try {
                    this.ttsChannel.unsubscribe(trackId, voiceId);
                } catch (_) {}
            });
        }

        this.realtimeSubscriptions.clear();
    }

    async playTrack(trackId) {
        try {
            const accessResponse = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}/check-access`);

            if (accessResponse.ok) {
                const { has_access } = await accessResponse.json();
                if (has_access) {
                    const playerUrl = `/player/${encodeURIComponent(trackId)}`;
                    if (typeof window.navigateTo === 'function') {
                        window.navigateTo(playerUrl);
                    } else {
                        window.location.href = playerUrl;
                    }
                } else {
                    this.showToast('You do not have access to this track.');
                }
            } else if (accessResponse.status === 403) {
                const errorData = await accessResponse.json();
                if (typeof showUpgradeModal === 'function') {
                    showUpgradeModal(errorData.error?.message || 'Access denied.');
                } else {
                    this.showToast(errorData.error?.message || 'Access denied.');
                }
            } else {
                this.showToast('Unable to verify access. Please try again.');
            }
        } catch (error) {
            console.error('❌ AlbumDetails.playTrack error:', error);
            this.showToast('An error occurred. Please try again.');
        }
    }

    async handleRename(trackId) {
        const track = this.tracks.find(t => t.id == trackId);
        if (!track) return;

        const isTTSTrack = this.detectTTSTrack(track);

        if (isTTSTrack && (this.userPermissions.can_create || this.userPermissions.is_team)) {
            await this.openTTSEditModal(trackId, track);
        } else {
            await this.openSimpleRenameModal(trackId, track);
        }
    }

    async openSimpleRenameModal(trackId, track) {
        const modal = document.createElement('div');
        modal.className = 'modal-overlay rename-modal-overlay active';
        modal.style.display = 'flex';
        modal.innerHTML = `
            <div class="modal-content rename-modal-content">
                <div class="modal-header">
                    <h2>Rename Track</h2>
                    <button class="modal-close" aria-label="Close">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label for="renameTrackTitle">Track Title:</label>
                        <input type="text" id="renameTrackTitle"
                               value="${this.escapeHtml(track.title)}"
                               class="form-control" maxlength="80" autofocus>
                        <div class="char-counter-wrapper">
                            <span class="char-counter" id="renameTitleCounter">
                                <span class="current-count">${track.title.length}</span> / 80 characters
                            </span>
                        </div>
                    </div>
                    <div class="form-group">
                        <label for="renameTrackVisibility">Visibility</label>
                        <select id="renameTrackVisibility" class="form-control visibility-select">
                            <option value="visible" ${(!track.visibility_status || track.visibility_status === 'visible') ? 'selected' : ''}>Visible to all authorized users</option>
                            <option value="hidden_from_users" ${track.visibility_status === 'hidden_from_users' ? 'selected' : ''}>Hidden from users (Team can see)</option>
                            ${this.userPermissions.is_creator ? `<option value="hidden_from_all" ${track.visibility_status === 'hidden_from_all' ? 'selected' : ''}>Hidden from everyone including Team</option>` : ''}
                        </select>
                        <small class="help-text">Control who can see this track${this.userPermissions.is_team && !this.userPermissions.is_creator ? ' (Team members cannot hide from team)' : ''}</small>
                    </div>

                    <!-- Scheduled Visibility Display -->
                    <div id="renameTrackScheduleDisplay" class="schedule-display" style="display: none;">
                        <div class="schedule-info">
                            <i class="fas fa-clock"></i>
                            <span class="schedule-text">Scheduled to become <strong class="schedule-target-status"></strong> in <strong class="schedule-countdown"></strong></span>
                            <button type="button" class="btn-icon cancel-schedule" title="Cancel schedule">
                                <i class="fas fa-times"></i>
                            </button>
                        </div>
                    </div>

                    ${(this.userPermissions.is_creator || this.userPermissions.is_team) ? `
                        <button type="button" class="btn-schedule" id="scheduleTrackVisibilityBtn" onclick="window.albumDetails.openTrackScheduleModal('${trackId}')">
                            <i class="fas fa-clock"></i> Schedule Visibility Change
                        </button>
                    ` : ''}
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary modal-cancel">Cancel</button>
                    <button class="btn btn-primary save-rename">
                        <i class="fas fa-save"></i> Save
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        const titleInput = modal.querySelector('#renameTrackTitle');
        const titleCounter = modal.querySelector('#renameTitleCounter');
        const titleCurrentCount = titleCounter?.querySelector('.current-count');

        if (titleInput && titleCurrentCount) {
            titleInput.addEventListener('input', () => {
                const length = titleInput.value.length;
                titleCurrentCount.textContent = length;

                if (length >= 80) {
                    titleCounter.style.color = '#ef4444';
                } else if (length >= 75) {
                    titleCounter.style.color = '#f59e0b';
                } else {
                    titleCounter.style.color = '';
                }
            });
        }

        const closeModal = () => {
            modal.remove();
        };

        modal.querySelector('.modal-close').addEventListener('click', closeModal);
        modal.querySelector('.modal-cancel').addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal();
        });

        const escapeHandler = (e) => {
            if (e.key === 'Escape') {
                closeModal();
                document.removeEventListener('keydown', escapeHandler);
            }
        };
        document.addEventListener('keydown', escapeHandler);

        titleInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                modal.querySelector('.save-rename').click();
            }
        });

        // Check for existing schedule and setup schedule display
        this.checkExistingTrackSchedule(trackId);

        modal.querySelector('.save-rename').addEventListener('click', async () => {
            const newTitle = titleInput.value.trim();
            const newVisibility = modal.querySelector('#renameTrackVisibility').value;

            if (!newTitle) {
                this.showToast('Title is required', 'error');
                return;
            }

            if (newTitle.length > 80) {
                this.showToast('Title must be 80 characters or less', 'error');
                return;
            }

            // Build update data object
            const updateData = {};
            if (newTitle !== track.title) {
                updateData.title = newTitle;
            }
            if (newVisibility !== (track.visibility_status || 'visible')) {
                updateData.visibility_status = newVisibility;
            }

            // If nothing changed, close modal
            if (Object.keys(updateData).length === 0) {
                closeModal();
                return;
            }

            const saveBtn = modal.querySelector('.save-rename');
            const cancelBtn = modal.querySelector('.modal-cancel');
            saveBtn.disabled = true;
            cancelBtn.disabled = true;
            saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';

            try {
                const response = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}/tracks/${encodeURIComponent(trackId)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(updateData)
                });

                if (!response.ok) throw new Error(await response.text());

                const result = await response.json();
                const trackIndex = this.tracks.findIndex(t => t.id == trackId);
                if (trackIndex !== -1) {
                    this.tracks[trackIndex].title = result.track.title;
                    this.tracks[trackIndex].visibility_status = result.track.visibility_status;
                    const titleElement = document.querySelector(`.track-item[data-track-id="${trackId}"] .track-title`);
                    if (titleElement) {
                        titleElement.textContent = result.track.title;
                        titleElement.setAttribute('title', result.track.title);
                    }

                    // Update visibility badge
                    this.updateTrackVisibilityBadge(trackId, result.track.visibility_status);
                }

                this.showToast('Track updated successfully', 'success');
                closeModal();
            } catch (error) {
                this.showToast('Failed to rename track.', 'error');
                saveBtn.disabled = false;
                cancelBtn.disabled = false;
                saveBtn.innerHTML = '<i class="fas fa-save"></i> Save';
            }
        });

        setTimeout(() => {
            titleInput.focus();
            titleInput.select();
        }, 100);
    }

    updateTrackVisibilityBadge(trackId, visibilityStatus) {
        const trackItem = document.querySelector(`.track-item[data-track-id="${trackId}"]`);
        if (!trackItem) return;

        const trackInfo = trackItem.querySelector('.track-info');
        if (!trackInfo) return;

        // Remove existing visibility badge
        const existingBadge = trackInfo.querySelector('.track-visibility-badge');
        if (existingBadge) {
            existingBadge.remove();
        }

        // Add new badge if needed - insert after title, before duration
        if (visibilityStatus && visibilityStatus !== 'visible') {
            const badgeText = visibilityStatus === 'hidden_from_all' ? 'Hidden from All' : 'Hidden from Users';
            const badge = document.createElement('span');
            badge.className = `track-visibility-badge ${visibilityStatus}`;
            badge.title = badgeText;
            badge.innerHTML = `<i class="fas fa-eye-slash"></i><span class="badge-text">${badgeText}</span>`;

            // Prevent badge from triggering play/navigation
            badge.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
            });

            // Insert after title, before duration
            const duration = trackInfo.querySelector('.track-duration');
            if (duration) {
                trackInfo.insertBefore(badge, duration);
            } else {
                trackInfo.appendChild(badge);
            }
        }
    }

    async openTTSEditModal(trackId, track) {
        try {
            const response = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/source-text`);
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to load source text');
            }

            const data = await response.json();

            const modal = document.createElement('div');
            modal.className = 'modal-overlay tts-edit-overlay active';
            modal.style.display = 'flex';
            modal.innerHTML = `
                <div class="modal-content tts-edit-modal">
                    <div class="modal-header">
                        <h2>Edit TTS Track</h2>
                        <button class="modal-close" aria-label="Close">&times;</button>
                    </div>
                    <div class="modal-body">
                        <div class="form-group">
                            <label for="editTTSTitle">Title:</label>
                            <input type="text" id="editTTSTitle" value="${this.escapeHtml(data.title)}" 
                                   class="form-control" maxlength="80">
                            <div class="char-counter-wrapper">
                                <span class="char-counter" id="editTitleCounter">
                                    <span class="current-count">${data.title.length}</span> / 80 characters
                                </span>
                            </div>
                        </div>
                        <div class="form-group">
                            <label for="editTTSText">Content:</label>
                            <textarea id="editTTSText" rows="15" class="form-control" placeholder="Enter your text here...">${this.escapeHtml(data.text)}</textarea>
                            <div class="text-info">
                                <span>Voice: ${this.getVoiceDisplayName(data.voice)}</span>
                                <span>•</span>
                                <span>Characters: <span id="charCount">${data.character_count.toLocaleString()}</span></span>
                                <span>•</span>
                                <span>Words: <span id="wordCount">${data.word_count.toLocaleString()}</span></span>
                            </div>
                        </div>
                        <div class="form-group">
                            <label for="editTTSVisibility">Visibility</label>
                            <select id="editTTSVisibility" class="form-control visibility-select">
                                <option value="visible" ${(!track.visibility_status || track.visibility_status === 'visible') ? 'selected' : ''}>Visible to all authorized users</option>
                                <option value="hidden_from_users" ${track.visibility_status === 'hidden_from_users' ? 'selected' : ''}>Hidden from users (Team can see)</option>
                                ${this.userPermissions.is_creator ? `<option value="hidden_from_all" ${track.visibility_status === 'hidden_from_all' ? 'selected' : ''}>Hidden from everyone including Team</option>` : ''}
                            </select>
                            <small class="help-text">Control who can see this track${this.userPermissions.is_team && !this.userPermissions.is_creator ? ' (Team members cannot hide from team)' : ''}</small>
                        </div>

                        <!-- Scheduled Visibility Display -->
                        <div id="ttsTrackScheduleDisplay" class="schedule-display" style="display: none;">
                            <div class="schedule-info">
                                <i class="fas fa-clock"></i>
                                <span class="schedule-text">Scheduled to become <strong class="schedule-target-status"></strong> in <strong class="schedule-countdown"></strong></span>
                                <button type="button" class="btn-icon cancel-schedule" title="Cancel schedule">
                                    <i class="fas fa-times"></i>
                                </button>
                            </div>
                        </div>

                        ${(this.userPermissions.is_creator || this.userPermissions.is_team) ? `
                            <button type="button" class="btn-schedule" id="scheduleTTSTrackVisibilityBtn" onclick="window.albumDetails.openTrackScheduleModal('${trackId}')">
                                <i class="fas fa-clock"></i> Schedule Visibility Change
                            </button>
                        ` : ''}
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-secondary modal-cancel">Cancel</button>
                        <button class="btn btn-primary save-tts-edit">
                            <i class="fas fa-save"></i> Save & Regenerate
                        </button>
                    </div>
                </div>
            `;

            document.body.appendChild(modal);

            const titleInput = modal.querySelector('#editTTSTitle');
            const titleCounter = modal.querySelector('#editTitleCounter');
            const titleCurrentCount = titleCounter?.querySelector('.current-count');

            if (titleInput && titleCurrentCount) {
                titleInput.addEventListener('input', () => {
                    const length = titleInput.value.length;
                    titleCurrentCount.textContent = length;

                    if (length >= 80) {
                        titleCounter.style.color = '#ef4444';
                    } else if (length >= 75) {
                        titleCounter.style.color = '#f59e0b';
                    } else {
                        titleCounter.style.color = '';
                    }
                });
            }

            const textarea = modal.querySelector('#editTTSText');
            const charCount = modal.querySelector('#charCount');
            const wordCount = modal.querySelector('#wordCount');

            textarea.addEventListener('input', () => {
                const text = textarea.value;
                charCount.textContent = text.length.toLocaleString();
                wordCount.textContent = text.split(/\s+/).filter(w => w).length.toLocaleString();
            });

            const closeModal = () => {
                modal.remove();
            };

            modal.querySelector('.modal-close').addEventListener('click', closeModal);
            modal.querySelector('.modal-cancel').addEventListener('click', closeModal);
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closeModal();
            });

            const escapeHandler = (e) => {
                if (e.key === 'Escape') {
                    closeModal();
                    document.removeEventListener('keydown', escapeHandler);
                }
            };
            document.addEventListener('keydown', escapeHandler);

            // Check for existing schedule and setup schedule display for TTS track
            this.checkExistingTTSTrackSchedule(trackId);

            modal.querySelector('.save-tts-edit').addEventListener('click', async () => {
                const newTitle = titleInput.value.trim();
                const newText = textarea.value.trim();
                const newVisibility = modal.querySelector('#editTTSVisibility').value;

                if (!newTitle) {
                    this.showToast('Title is required', 'error');
                    return;
                }

                if (newTitle.length > 80) {
                    this.showToast('Title must be 80 characters or less', 'error');
                    return;
                }

                if (!newText || newText.length < 10) {
                    this.showToast('Text must be at least 10 characters', 'error');
                    return;
                }

                if (newText.length > 5000000) {
                    this.showToast('Text exceeds maximum length (5M characters)', 'error');
                    return;
                }

                const saveBtn = modal.querySelector('.save-tts-edit');
                const cancelBtn = modal.querySelector('.modal-cancel');
                saveBtn.disabled = true;
                cancelBtn.disabled = true;
                saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Regenerating...';

                try {
                    const response = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/update-tts-content`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            title: newTitle,
                            text: newText,
                            voice: data.voice,
                            visibility_status: newVisibility
                        })
                    });

                    if (!response.ok) {
                        const error = await response.json();
                        throw new Error(error.detail || 'Update failed');
                    }

                    const result = await response.json();

                    // Update track data in memory
                    const trackIndex = this.tracks.findIndex(t => t.id == trackId);

                    if (trackIndex !== -1) {
                        this.tracks[trackIndex].title = newTitle;
                        this.tracks[trackIndex].visibility_status = newVisibility;

                        // Update title in DOM
                        const titleElement = document.querySelector(`.track-item[data-track-id="${trackId}"] .track-title`);
                        if (titleElement) {
                            titleElement.textContent = newTitle;
                            titleElement.setAttribute('title', newTitle);
                        }

                        // Update visibility badge
                        this.updateTrackVisibilityBadge(trackId, newVisibility);
                    }

                    this.showToast(result.message || 'TTS track updated successfully!', 'success');
                    modal.remove();

                    // Only reload if text was changed (requires regeneration)
                    if (newText !== data.text) {
                        // Use SPA navigation to refresh the page
                        if (window.spaRouter) {
                            window.spaRouter.navigate(window.location.pathname);
                        } else if (window.navigateTo) {
                            window.navigateTo(window.location.pathname);
                        } else {
                            window.location.reload();
                        }
                    }
                    
                } catch (error) {
                    this.showToast(error.message || 'Failed to update TTS content', 'error');
                    saveBtn.disabled = false;
                    cancelBtn.disabled = false;
                    saveBtn.innerHTML = '<i class="fas fa-save"></i> Save & Regenerate';
                }
            });

            setTimeout(() => titleInput.focus(), 100);

        } catch (error) {
            this.showToast(error.message || 'Failed to load TTS content', 'error');
        }
    }

    getVoiceDisplayName(voiceId) {
        if (!voiceId) return 'Unknown Voice';
        return voiceId.replace('en-US-', '').replace('en-GB-', '').replace('Neural', '').trim() || voiceId;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async handleDelete(trackId) {
        const track = this.tracks.find(t => t.id == trackId);
        if (!track || !confirm(`Delete "${track.title}"?`)) return;

        try {
            const response = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}/tracks/${encodeURIComponent(trackId)}`, {
                method: 'DELETE'
            });

            if (!response.ok) throw new Error('Failed to delete track');

            this.tracks = this.tracks.filter(t => t.id != trackId);
            document.querySelector(`.track-item[data-track-id="${trackId}"]`)?.remove();
            this.showToast('Track deleted successfully', 'success');

            // 🔄 Refresh album listen count after deletion
            if (window.AlbumListensManager) {
                await window.AlbumListensManager.refreshCount();
            }
        } catch (error) {
            this.showToast('Failed to delete track.', 'error');
        }
    }

    async handleBulkDelete() {
        const selectedCheckboxes = document.querySelectorAll('.track-checkbox:checked');
        if (!selectedCheckboxes.length || !confirm(`Delete ${selectedCheckboxes.length} track(s)?`)) return;

        try {
            const tracksToDelete = Array.from(selectedCheckboxes).map(cb => ({
                album_id: this.albumId,
                track_id: cb.dataset.trackId
            }));

            const response = await fetch('/api/albums/bulk-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(tracksToDelete)
            });

            if (!response.ok) throw new Error('Bulk delete failed');

            tracksToDelete.forEach(({ track_id }) => {
                this.tracks = this.tracks.filter(t => t.id != track_id);
                document.querySelector(`.track-item[data-track-id="${track_id}"]`)?.remove();
            });

            if (this.selectAllCheckbox) this.selectAllCheckbox.checked = false;
            this.updateBulkDeleteButton();

            // 🔄 Refresh album listen count after bulk deletion
            if (window.AlbumListensManager) {
                await window.AlbumListensManager.refreshCount();
            }

        } catch (error) {
            this.showToast('Failed to delete tracks.', 'error');
        }
    }

    async downloadTrack(trackId, trackTitle, event) {
        const button = event.currentTarget;
        if (button.disabled || button.classList.contains('loading')) return;
        
        try {
            button.disabled = true;
            button.classList.add('loading');
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Starting...';

            const accessResponse = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}/check-access`);
            if (accessResponse.status === 403) {
                const { error } = await accessResponse.json();
                if (typeof showUpgradeModal === 'function') {
                    showUpgradeModal(error?.message || 'Subscription required');
                } else {
                    this.showToast(error?.message || 'Subscription required');
                }
                return;
            }

            if (!accessResponse.ok) throw new Error('Access verification failed');

            const response = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/download`);
            
            if (response.status === 403) {
                const errorData = await response.json();
                const message = errorData.detail?.downloads_used !== undefined 
                    ? 'Download limit reached. Please upgrade.'
                    : errorData.detail?.message || 'Access denied';
                
                if (typeof showUpgradeModal === 'function') {
                    showUpgradeModal(message);
                } else {
                    this.showToast(message);
                }
                return;
            }
            
            if (!response.ok) throw new Error('Download failed to start');

            while (true) {
                const statusResponse = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/status`);
                if (!statusResponse.ok) throw new Error('Status check failed');
                
                const status = await statusResponse.json();
                
                if (status.status === 'error') throw new Error(status.error || 'Download failed');

                if (status.status === 'queued') {
                    button.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Queued (${status.queue_position || ''})`;
                } else if (status.progress) {
                    button.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${Math.round(status.progress)}%`;
                }

                if (status.status === 'completed') {
                    const link = document.createElement('a');
                    link.href = `/api/tracks/${encodeURIComponent(trackId)}/file`;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    
                    this.showToast('Download started', 'success');
                    break;
                }

                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        } catch (error) {
            this.showToast(error.message || 'Download failed', 'error');
        } finally {
            setTimeout(() => {
                button.disabled = false;
                button.classList.remove('loading');
                button.innerHTML = '<i class="fas fa-download"></i>';
            }, 1000);
        }
    }

    displayTrackDurations() {
        this.tracks.forEach(track => {
            const durationElement = document.querySelector(`.track-item[data-track-id="${track.id}"] .track-duration`);
            if (durationElement) {
                durationElement.textContent = track.duration ? this.formatDuration(track.duration) : '0:00';
            }
        });
    }

    formatDuration(seconds) {
        if (!seconds || isNaN(seconds) || seconds <= 0) return '0:00';
        
        const totalSeconds = Math.floor(Number(seconds));
        
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const secs = totalSeconds % 60;
        
        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        } else {
            return `${minutes}:${String(secs).padStart(2, '0')}`;
        }
    }

    updateBulkDeleteButton() {
        const selectedTracks = document.querySelectorAll('.track-checkbox:checked');
        if (this.bulkDeleteBtn) {
            this.bulkDeleteBtn.style.display = selectedTracks.length > 0 ? 'block' : 'none';
        }
    }

    initializeDragAndDrop() {
        if (!this.userPermissions.can_create || !this.tracksList) return;
        
        let draggedItem = null;
        let autoScrollInterval = null;
        let scrollContainer = this.tracksList;
        
        const SCROLL_ZONE_SIZE = 50;
        const MIN_SCROLL_SPEED = 1;
        const MAX_SCROLL_SPEED = 8;
        const SCROLL_INTERVAL = 16;
        
        const startAutoScroll = (direction, speed) => {
            if (autoScrollInterval) {
                clearInterval(autoScrollInterval);
            }
            
            autoScrollInterval = setInterval(() => {
                const currentScrollTop = scrollContainer.scrollTop;
                const maxScrollTop = scrollContainer.scrollHeight - scrollContainer.clientHeight;
                
                if (direction === 'up' && currentScrollTop > 0) {
                    scrollContainer.scrollTop = Math.max(0, currentScrollTop - speed);
                } else if (direction === 'down' && currentScrollTop < maxScrollTop) {
                    scrollContainer.scrollTop = Math.min(maxScrollTop, currentScrollTop + speed);
                }
            }, SCROLL_INTERVAL);
        };
        
        const stopAutoScroll = () => {
            if (autoScrollInterval) {
                clearInterval(autoScrollInterval);
                autoScrollInterval = null;
            }
        };
        
        const handleAutoScroll = (e) => {
            if (!draggedItem) return;
            
            const containerRect = scrollContainer.getBoundingClientRect();
            const mouseY = e.clientY;
            const relativeY = mouseY - containerRect.top;
            
            if (relativeY < SCROLL_ZONE_SIZE) {
                const distanceFromEdge = relativeY;
                const speedRatio = 1 - (distanceFromEdge / SCROLL_ZONE_SIZE);
                const scrollSpeed = MIN_SCROLL_SPEED + (speedRatio * (MAX_SCROLL_SPEED - MIN_SCROLL_SPEED));
                startAutoScroll('up', scrollSpeed);
            } else if (relativeY > containerRect.height - SCROLL_ZONE_SIZE) {
                const distanceFromEdge = containerRect.height - relativeY;
                const speedRatio = 1 - (distanceFromEdge / SCROLL_ZONE_SIZE);
                const scrollSpeed = MIN_SCROLL_SPEED + (speedRatio * (MAX_SCROLL_SPEED - MIN_SCROLL_SPEED));
                startAutoScroll('down', scrollSpeed);
            } else {
                stopAutoScroll();
            }
        };
        
        document.querySelectorAll('.track-item').forEach(item => {
            const dragHandle = item.querySelector('.drag-handle');
            if (!dragHandle) return;
            
            item.setAttribute('draggable', 'true');
            
            item.addEventListener('dragstart', (e) => {
                draggedItem = item;
                item.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
                document.addEventListener('dragover', handleAutoScroll);
            });
            
            item.addEventListener('dragend', () => {
                draggedItem = null;
                item.classList.remove('dragging');
                document.querySelectorAll('.track-item').forEach(i => i.classList.remove('drag-over'));
                stopAutoScroll();
                document.removeEventListener('dragover', handleAutoScroll);
            });
            
            item.addEventListener('dragover', (e) => {
                e.preventDefault();
                if (item !== draggedItem) {
                    item.classList.add('drag-over');
                }
            });
            
            item.addEventListener('dragleave', () => {
                item.classList.remove('drag-over');
            });
            
            item.addEventListener('drop', async (e) => {
                e.preventDefault();
                item.classList.remove('drag-over');
                
                if (draggedItem && item !== draggedItem) {
                    const tracks = Array.from(this.tracksList.querySelectorAll('.track-item'));
                    const fromIndex = tracks.indexOf(draggedItem);
                    const toIndex = tracks.indexOf(item);
                    
                    if (fromIndex < toIndex) {
                        item.parentNode.insertBefore(draggedItem, item.nextSibling);
                    } else {
                        item.parentNode.insertBefore(draggedItem, item);
                    }
                    
                    await this.updateTrackOrders();
                }
            });
        });
    }

    async updateTrackOrders() {
        if (!this.userPermissions.can_create) return;

        try {
            const tracks = Array.from(document.querySelectorAll('.track-item'));
            const trackOrders = tracks.map((track, index) => ({
                id: track.dataset.trackId,
                order: index + 1
            }));

            const response = await fetch(`/api/albums/${this.albumId}/tracks/reorder`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tracks: trackOrders })
            });

            if (!response.ok) throw new Error('Failed to update track orders');
            this.showToast('Track order updated', 'success');
        } catch (error) {
            this.showToast('Failed to update track order', 'error');
        }
    }

    addBadgesToExistingTracks() {
        document.querySelectorAll('.track-symbols').forEach(symbols => symbols.remove());
        
        const allTracks = [
            ...(this.tracks || []),
            ...(window.albumTracks || [])
        ];
        
        const uniqueTracks = allTracks.reduce((acc, track) => {
            if (!acc.find(t => t.id === track.id)) {
                acc.push(track);
            }
            return acc;
        }, []);
        
        uniqueTracks.forEach((track) => {
            try {
                const trackElement = document.querySelector(`.track-item[data-track-id="${track.id}"]`);
                if (!trackElement) return;
                
                const isTTSTrack = this.detectTTSTrack(track, trackElement);
                
                if (isTTSTrack) {
                    let symbolsContainer = trackElement.querySelector('.track-symbols');
                    if (!symbolsContainer) {
                        symbolsContainer = document.createElement('div');
                        symbolsContainer.className = 'track-symbols';
                        
                        const trackTitle = trackElement.querySelector('.track-title');
                        if (trackTitle) {
                            trackTitle.appendChild(symbolsContainer);
                        } else {
                            return;
                        }
                    }
                    
                    const voiceSymbol = document.createElement('span');
                    voiceSymbol.className = 'voice-symbol';
                    this.applyVoiceIconState(track, voiceSymbol);
                    symbolsContainer.appendChild(voiceSymbol);

                    // ✅ Fetch real-time default voice status from voice_generation_status table
                    // This will update the badge based on actual DB state, not stale track fields
                    this.fetchDefaultVoiceStatus(track).catch(err => {
                        // Silently fail - badge will use fallback logic
                    });

                    if (track.has_read_along) {
                        const readAlongSymbol = document.createElement('span');
                        readAlongSymbol.className = 'read-along-symbol';
                        readAlongSymbol.innerHTML = '📖';
                        readAlongSymbol.title = 'Read-along available';
                        symbolsContainer.appendChild(readAlongSymbol);
                    }

                    const needsRealtime = this.shouldTrackDefaultVoice(track);
                    if (needsRealtime) {
                        this.subscribeToTrackUpdates(track);
                    }
                }
                
            } catch (error) {}
        });
    }

    detectTTSTrack(track, trackElement = null) {
        const detectionResults = {
            hasTrackType: track.track_type === 'tts',
            hasIsTTSFlag: track.is_tts_track === true,
            hasSourceText: !!(track.source_text && track.source_text.trim().length > 0),
            hasTTSStatus: !!(track.tts_status && ['processing', 'ready', 'complete'].includes(track.tts_status)),
            hasStatusComplete: track.status === 'complete',
            hasStatusGenerating: track.status === 'generating',
            hasNeuralVoice: !!(track.default_voice && track.default_voice.includes('Neural')),
            elementHasTTSType: trackElement && trackElement.dataset.trackType === 'tts',
            hasVoiceProperty: !!(track.voice || track.tts_voice),
            hasReadAlong: track.has_read_along === true,
            hasTTSPath: track.file_path && track.file_path.includes('/tts/'),
            hasCreationMethod: track.creation_method === 'tts' || track.upload_method === 'tts',
            hasSegmentationStatus: !!(track.segmentation_status && ['processing', 'complete'].includes(track.segmentation_status))
        };
        
        const isTTS = detectionResults.hasTrackType || 
                      detectionResults.hasIsTTSFlag || 
                      detectionResults.hasSourceText || 
                      detectionResults.hasTTSStatus || 
                      detectionResults.hasStatusComplete ||
                      detectionResults.hasStatusGenerating ||
                      detectionResults.hasNeuralVoice ||
                      detectionResults.elementHasTTSType ||
                      detectionResults.hasTTSPath;
        
        return isTTS;
    }

    async createTrackElement(track) {
        const div = document.createElement('div');
        div.className = 'track-item';
        div.dataset.trackId = track.id;
        
        const trackType = track.track_type || (track.source_text ? 'tts' : 'audio');
        div.dataset.trackType = trackType;

        const formattedDuration = this.formatDuration(track.duration || 0);
        const isTTSTrack = this.detectTTSTrack(track, div);

        let symbolsHtml = '';
        if (isTTSTrack) {
            if (track.tts_status === 'processing') {
                symbolsHtml += '<span class="voice-symbol">⏳</span>';
            } else {
                symbolsHtml += '<span class="voice-symbol" style="cursor: pointer;" onclick="window.albumDetails.playTrack(\'' + track.id + '\')"><i class="fas fa-infinity" style="color: #3b82f6;"></i></span>';
            }

            if (track.has_read_along) {
                symbolsHtml += '<span class="read-along-symbol">📖</span>';
            }
        }

        // Visibility badge HTML - just icon for mobile-friendly display
        let visibilityBadgeHtml = '';
        if (track.visibility_status && track.visibility_status !== 'visible') {
            const badgeText = track.visibility_status === 'hidden_from_all' ? 'Hidden from All' : 'Hidden from Users';
            visibilityBadgeHtml = `
                <span class="track-visibility-badge ${track.visibility_status}" title="${badgeText}">
                    <i class="fas fa-eye-slash"></i>
                    <span class="badge-text">${badgeText}</span>
                </span>
            `;
        }

        div.innerHTML = `
            ${this.userPermissions.can_create ? '<div class="drag-handle"><i class="fas fa-grip-vertical"></i></div>' : ''}
            ${this.userPermissions.can_delete ? `<input type="checkbox" class="track-checkbox" data-album-id="${this.albumId}" data-track-id="${track.id}">` : ''}
            <div class="track-content">
                <div class="track-info">
                    <h3 class="track-title">${track.title}${symbolsHtml ? `<span class="track-symbols">${symbolsHtml}</span>` : ''}</h3>
                    ${visibilityBadgeHtml}

                    <!-- Schedule countdown indicator (will be populated if schedule exists) -->
                    <span class="track-schedule-indicator" id="track-schedule-${track.id}" style="display: none;" data-tooltip="">
                        <span class="schedule-countdown-compact"></span>
                        <span class="schedule-full-text">
                            <i class="fas fa-clock"></i>
                            Scheduled: <strong class="schedule-target"></strong> in <strong class="schedule-countdown"></strong>
                        </span>
                    </span>

                    <span class="track-duration">${formattedDuration}</span>
                </div>
            </div>
            <div class="track-actions">
                ${this.userPermissions.can_rename ? `<button class="btn-icon rename-track" data-track-id="${track.id}"><i class="fas fa-edit"></i></button>` : ''}
                ${this.userPermissions.can_delete ? `<button class="btn-icon delete-track" data-track-id="${track.id}"><i class="fas fa-trash"></i></button>` : ''}
                ${this.userPermissions.can_download ? `<button class="btn-icon download-track" data-track-id="${track.id}" data-track-title="${track.title}"><i class="fas fa-download"></i></button>` : ''}
            </div>
        `;

        div.querySelector('.track-content')?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation(); // ✅ Prevent event from bubbling to parent handlers
            this.playTrack(track.id);
        });
        div.querySelector('.track-checkbox')?.addEventListener('change', () => this.updateBulkDeleteButton());

        // Prevent visibility badge from triggering play/navigation
        div.querySelector('.track-visibility-badge')?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            // Just show tooltip, don't navigate
        });

        div.querySelector('.rename-track')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.handleRename(track.id);
        });
        div.querySelector('.delete-track')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.handleDelete(track.id);
        });
        div.querySelector('.download-track')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.downloadTrack(track.id, track.title, e);
        });

        return div;
    }

    addNewTrackToUI(track) {
        // Add to tracks array
        this.tracks.push(track);
        
        // Create and append track element
        this.createTrackElement(track).then(trackElement => {
            if (this.tracksList) {
                this.tracksList.appendChild(trackElement);
                
                // Add subtle highlight
                trackElement.style.animation = 'highlightNew 2s ease';
                
                // Scroll to new track
                setTimeout(() => {
                    trackElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }, 100);
                
                // If it's a TTS track, attach progress monitoring
                if (this.detectTTSTrack(track, trackElement)) {
                    this.subscribeToTrackUpdates(track);
                }
            }
        }).catch(error => {
            console.error('Error creating track element:', error);
            this.showToast('Track created but failed to display. Refresh to see it.', 'warning');
        });
    }


    addStyles() {
        const style = document.createElement('style');
        style.textContent = `
            .tts-edit-modal {
                max-width: 800px;
                width: 95%;
                max-height: 90vh;
                display: flex;
                flex-direction: column;
            }

            .tts-edit-modal .modal-body {
                flex: 1;
                overflow-y: auto;
            }

            .tts-edit-modal .form-group {
                margin-bottom: 1.5rem;
            }

            .tts-edit-modal label {
                display: block;
                margin-bottom: 0.5rem;
                font-weight: 600;
                color: var(--text-color);
            }

            .tts-edit-modal .form-control {
                width: 100%;
                padding: 0.75rem;
                border: 1px solid var(--border-color, rgba(255,255,255,0.2));
                border-radius: 6px;
                background: var(--input-bg, rgba(255,255,255,0.05));
                color: var(--text-color);
                font-family: inherit;
                font-size: 0.95rem;
                transition: border-color 0.2s;
            }

            .tts-edit-modal .form-control:focus {
                outline: none;
                border-color: var(--primary, #3b82f6);
                box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
            }

            .tts-edit-modal textarea {
                resize: vertical;
                min-height: 300px;
                font-family: 'Courier New', monospace;
                line-height: 1.6;
            }

            .tts-edit-modal .text-info {
                margin-top: 0.5rem;
                font-size: 0.85rem;
                color: var(--text-2, rgba(255,255,255,0.6));
                display: flex;
                gap: 0.75rem;
                flex-wrap: wrap;
            }

            .track-symbols {
                display: inline-flex;
                gap: 4px;
                margin-left: 8px;
                align-items: center;
            }
            
            .voice-symbol, .read-along-symbol {
                font-size: 1rem;
                transition: transform 0.2s ease;
            }
            
            .voice-symbol:hover {
                transform: scale(1.1);
            }
            
            .player-toast {
                position: fixed;
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                background: var(--toast-bg, rgba(0, 0, 0, 0.8));
                color: var(--toast-text, white);
                padding: 10px 20px;
                border-radius: 4px;
                z-index: 9999;
                animation: fadeInOut 3s ease-in-out;
            }
            
            .player-toast.toast-success {
                background: rgba(34, 197, 94, 0.9);
            }
            
            .player-toast.toast-error {
                background: rgba(239, 68, 68, 0.9);
            }
            
            .player-toast.toast-warning {
                background: rgba(245, 158, 11, 0.9);
            }
            
            @keyframes fadeInOut {
                0%, 100% { opacity: 0; }
                10%, 90% { opacity: 1; }
            }
            
            .track-badges {
                display: none !important;
            }
            
            .track-badge {
                display: none !important;
            }

            .track-item {
                background: var(--card-bg, transparent);
                color: var(--text-color);
                border: 1px solid var(--border-color, rgba(255, 255, 255, 0.1));
                border-radius: 8px;
                margin-bottom: 0.5rem;
            }

            .track-item:hover {
                background: var(--hover-bg, rgba(255, 255, 255, 0.05));
            }

            .track-title {
                color: var(--text-color) !important;
            }

            .track-duration {
                color: var(--text-2, rgba(255, 255, 255, 0.7));
            }

            .track-visibility-badge {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                padding: 4px 10px;
                border-radius: 12px;
                margin-left: 8px;
                cursor: help;
                transition: all 0.2s ease;
                position: relative;
                font-size: 0.75rem;
                font-weight: 500;
            }

            .track-visibility-badge i {
                font-size: 0.75rem;
            }

            .track-visibility-badge .badge-text {
                display: inline;
            }

            /* Mobile: Icon-only circular badge */
            @media (max-width: 768px) {
                .track-visibility-badge {
                    width: 24px;
                    height: 24px;
                    padding: 0;
                    border-radius: 50%;
                    gap: 0;
                    justify-content: center;
                }

                .track-visibility-badge .badge-text {
                    display: none;
                }
            }

            .track-visibility-badge.visibility-hidden_from_users {
                background: rgba(245, 158, 11, 0.2);
                color: #f59e0b;
            }

            .track-visibility-badge.visibility-hidden_from_all {
                background: rgba(239, 68, 68, 0.2);
                color: #ef4444;
            }

            .track-visibility-badge:hover {
                transform: scale(1.05);
            }

            /* Custom tooltip */
            .track-visibility-badge::after {
                content: attr(title);
                position: absolute;
                bottom: calc(100% + 8px);
                left: 50%;
                transform: translateX(-50%) scale(0.95);
                background: rgba(0, 0, 0, 0.9);
                color: white;
                padding: 6px 10px;
                border-radius: 6px;
                font-size: 0.75rem;
                white-space: nowrap;
                opacity: 0;
                pointer-events: none;
                transition: all 0.2s ease;
                z-index: 1000;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
            }

            .track-visibility-badge:hover::after {
                opacity: 1;
                transform: translateX(-50%) scale(1);
            }

            /* Tooltip arrow */
            .track-visibility-badge::before {
                content: '';
                position: absolute;
                bottom: calc(100% + 2px);
                left: 50%;
                transform: translateX(-50%) scale(0.95);
                border: 4px solid transparent;
                border-top-color: rgba(0, 0, 0, 0.9);
                opacity: 0;
                pointer-events: none;
                transition: all 0.2s ease;
                z-index: 1000;
            }

            .track-visibility-badge:hover::before {
                opacity: 1;
                transform: translateX(-50%) scale(1);
            }

            [data-theme="light"] .track-visibility-badge::after {
                background: rgba(0, 0, 0, 0.85);
                color: white;
            }

            [data-theme="light"] .track-visibility-badge::before {
                border-top-color: rgba(0, 0, 0, 0.85);
            }

            [data-theme="light"] .track-visibility-badge.visibility-hidden_from_users {
                background: rgba(245, 158, 11, 0.25);
                color: #d97706;
            }

            [data-theme="light"] .track-visibility-badge.visibility-hidden_from_all {
                background: rgba(239, 68, 68, 0.25);
                color: #dc2626;
            }

            [data-theme="light"] .player-toast {
                background: rgba(255, 255, 255, 0.95);
                color: #1a202c;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            }

            [data-theme="light"] .track-item {
                background: rgba(0, 0, 0, 0.02);
                border-color: rgba(0, 0, 0, 0.1);
            }

            [data-theme="light"] .track-item:hover {
                background: rgba(0, 0, 0, 0.05);
            }

            [data-theme="light"] .track-title {
                color: #1a202c !important;
            }

            [data-theme="light"] .track-duration {
                color: rgba(0, 0, 0, 0.7);
            }

            [data-theme="light"] .tts-edit-modal .form-control {
                border-color: rgba(0,0,0,0.2);
                background: rgba(0,0,0,0.03);
                color: #1a202c;
            }

            [data-theme="light"] .tts-edit-modal .form-control:focus {
                box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
            }

            [data-theme="light"] .tts-edit-modal .text-info {
                color: rgba(0,0,0,0.6);
            }

            @media (max-width: 768px) {
                .tts-edit-modal {
                    width: 98%;
                    max-width: none;
                }

                .tts-edit-modal textarea {
                    min-height: 200px;
                    font-size: 0.9rem;
                }
            }
        `;
        document.head.appendChild(style);
    }

    initializeRealtimeUpdates() {
        if (!this.ttsChannel) {
            console.warn('TTSStatusChannel not available; album progress will not update in real time.');
            return;
        }

        if (!this.realtimeListenersAttached) {
            window.addEventListener('ttsStatusUpdate', this.handleRealtimeEvent);
            window.addEventListener('ttsWebSocketConnected', this.handleRealtimeSocketConnected);
            window.addEventListener('ttsWebSocketDisconnected', this.handleRealtimeSocketDisconnected);
            this.realtimeListenersAttached = true;
        }

        this.ttsChannel.connect();
        this.syncRealtimeSubscriptions();
    }

    handleRealtimeSocketConnected() {
        this.syncRealtimeSubscriptions();
    }

    handleRealtimeSocketDisconnected() {
        // Keep subscriptions cached so they are re-sent on next connect.
    }

    syncRealtimeSubscriptions() {
        if (!this.ttsChannel) return;

        this.tracks.forEach(track => {
            if (this.detectTTSTrack(track) && !this.isTrackReady(track)) {
                this.subscribeToTrackUpdates(track);
            }
        });
    }

    getTrackVoiceId(track) {
        return track?.processing_voice ||
               track?.default_voice ||
               track?.voice ||
               track?.tts_voice ||
               null;
    }

    isTrackReady(track) {
        const status = (track.tts_status || '').toLowerCase();
        const uploadStatus = (track.upload_status || '').toLowerCase();
        const generalStatus = (track.status || '').toLowerCase();
        const hasVoice = !!(track.voice_directory || track.voice || track.default_voice);
        return ['ready', 'complete'].includes(status) ||
               ['ready', 'complete'].includes(uploadStatus) ||
               ['ready', 'complete'].includes(generalStatus) ||
               hasVoice;
    }

    subscribeToTrackUpdates(track) {
        const voiceId = this.getTrackVoiceId(track);
        if (!voiceId) return;

        const key = `${track.id}:${voiceId}`;
        if (!this.realtimeSubscriptions.has(key)) {
            this.realtimeSubscriptions.set(key, { trackId: track.id, voiceId });
        }

        if (!this.ttsChannel) return;

        try {
            this.ttsChannel.subscribe(track.id, voiceId);
        } catch (error) {
            console.error('Failed to subscribe to TTS updates:', error);
        }
    }

    unsubscribeFromTrackUpdates(trackId, voiceId) {
        if (!voiceId) return;
        const key = `${trackId}:${voiceId}`;
        if (this.realtimeSubscriptions.has(key)) {
            this.realtimeSubscriptions.delete(key);
        }

        if (!this.ttsChannel) return;

        try {
            this.ttsChannel.unsubscribe(trackId, voiceId);
        } catch (error) {
            console.error('Failed to unsubscribe from TTS updates:', error);
        }
    }

    handleRealtimeEvent(event) {
        if (!event?.detail) return;
        const data = event.detail;
        const trackId = data.track_id;
        if (!trackId) return;

        const track = this.tracks.find(t => t.id === trackId);
        if (!track || !this.detectTTSTrack(track)) return;

        switch (data.type) {
            case 'tts_progress':
                this.handleRealtimeTTSProgress(track, data);
                break;
            case 'segmentation_progress':
                this.handleRealtimeSegmentationProgress(track, data);
                break;
            case 'generation_complete':
                this.handleRealtimeGenerationComplete(track, data);
                break;
            default:
                break;
        }
    }

    handleRealtimeTTSProgress(track, data) {
        this.trackStatusCache.set(track.id, 'processing');

        const voiceId = data.voice_id || this.getTrackDefaultVoice(track);
        if (voiceId) {
            this.activeVoiceJobs.set(track.id, { trackId: track.id, voiceId });
            track.processing_voice = voiceId;
        }

        const affectsDefault = this.isDefaultVoice(track, voiceId);
        if (affectsDefault || !this.hasReadyDefaultVoice(track)) {
            track.status = 'generating';
            track.upload_status = 'processing';
            track.tts_status = 'processing';
        }

        this.updateTrackIcon(track.id, 'processing', voiceId);
    }

    handleRealtimeSegmentationProgress(track, data) {
        const status = (data.status || 'segmenting').toLowerCase();
        const voiceId = data.voice_id || this.getTrackDefaultVoice(track) || this.getTrackVoiceId(track);
        const isDefaultVoice = this.isDefaultVoice(track, voiceId);

        if (voiceId) {
            this.activeVoiceJobs.set(track.id, { trackId: track.id, voiceId });
            track.processing_voice = voiceId;
        }

        if (status === 'error' && !isDefaultVoice) {
            this.updateTrackIcon(track.id, 'complete', voiceId);
            this.unsubscribeFromTrackUpdates(track.id, voiceId);
            return;
        }

        const effectiveStatus = status === 'error' ? 'error' : 'segmenting';
        this.trackStatusCache.set(track.id, effectiveStatus);
        if (isDefaultVoice) {
            track.status = status === 'error' ? 'failed' : 'segmenting';
            track.upload_status = status === 'error' ? 'failed' : 'processing';
            track.tts_status = status === 'error' ? 'error' : 'segmenting';
        }
        this.updateTrackIcon(track.id, effectiveStatus, voiceId);

        const completionLikely =
            status === 'segmentation_complete' ||
            status === 'complete' ||
            (typeof data.progress === 'number' && data.progress >= 99.9) ||
            (data.total_segments > 0 && data.segments_completed >= data.total_segments);

        if (completionLikely) {
            this.finalizeTrackReady(track, voiceId);
        } else if (status === 'error') {
            this.unsubscribeFromTrackUpdates(track.id, voiceId);
        }
    }

    handleRealtimeGenerationComplete(track, data) {
        const voiceId = data.voice_id || this.getTrackVoiceId(track);
        const isDefaultVoice = this.isDefaultVoice(track, voiceId);

        this.activeVoiceJobs.delete(track.id);
        if (track.processing_voice === voiceId) {
            track.processing_voice = null;
        }

        if (data.success) {
            this.finalizeTrackReady(track, voiceId);
        } else {
            if (isDefaultVoice) {
                this.trackStatusCache.set(track.id, 'error');
                track.status = 'failed';
                track.upload_status = 'failed';
                track.tts_status = 'error';
                this.updateTrackIcon(track.id, 'error', voiceId);
            } else {
                this.updateTrackIcon(track.id, 'complete', voiceId);
            }
            this.unsubscribeFromTrackUpdates(track.id, voiceId);
        }
    }

    finalizeTrackReady(track, voiceId) {
        this.trackStatusCache.set(track.id, 'complete');
        track.status = 'complete';
        track.upload_status = 'complete';
        track.tts_status = 'ready';
        if (voiceId) {
            if (!track.default_voice) {
                track.default_voice = voiceId;
            }
        }
        track.processing_voice = null;
        this.activeVoiceJobs.delete(track.id);
        this.updateTrackIcon(track.id, 'complete', voiceId);
        this.unsubscribeFromTrackUpdates(track.id, voiceId || this.getTrackVoiceId(track));
    }

    showToast(message, type = 'info', duration = 3000) {
        if (typeof showToast === 'function') {
            showToast(message, type, duration);
            return;
        }

        const existingToast = document.querySelector('.player-toast');
        if (existingToast) existingToast.remove();

        const toast = document.createElement('div');
        toast.className = `player-toast toast-${type}`;
        toast.textContent = message;

        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), duration);
    }

    // ========== TRACK SCHEDULE VISIBILITY FUNCTIONS ==========

    async checkExistingTrackSchedule(trackId) {
        try {
            const response = await fetch(`/api/tracks/${trackId}/schedule-visibility`);

            if (response.status === 403 || response.status === 404) {
                this.updateTrackScheduleDisplay(null);
                return;
            }

            const data = await response.json();

            if (response.ok && data.has_schedule) {
                this.updateTrackScheduleDisplay(data.schedule, trackId);
            } else {
                this.updateTrackScheduleDisplay(null);
            }
        } catch (error) {
            console.error('Error checking track schedule:', error);
            this.updateTrackScheduleDisplay(null);
        }
    }

    async checkExistingTTSTrackSchedule(trackId) {
        try {
            const response = await fetch(`/api/tracks/${trackId}/schedule-visibility`);

            if (response.status === 403 || response.status === 404) {
                this.updateTTSTrackScheduleDisplay(null);
                return;
            }

            const data = await response.json();

            if (response.ok && data.has_schedule) {
                this.updateTTSTrackScheduleDisplay(data.schedule, trackId);
            } else {
                this.updateTTSTrackScheduleDisplay(null);
            }
        } catch (error) {
            console.error('Error checking TTS track schedule:', error);
            this.updateTTSTrackScheduleDisplay(null);
        }
    }

    updateTTSTrackScheduleDisplay(schedule, trackId = null) {
        const display = document.getElementById('ttsTrackScheduleDisplay');
        const scheduleBtn = document.getElementById('scheduleTTSTrackVisibilityBtn');
        const visibilitySelect = document.getElementById('editTTSVisibility');
        const visibilityHelpText = visibilitySelect?.parentElement?.querySelector('.help-text');

        if (!display) return;

        if (schedule) {
            // Show schedule display
            display.style.display = 'block';
            if (scheduleBtn) scheduleBtn.style.display = 'none';

            // DISABLE visibility dropdown - user must cancel schedule first
            if (visibilitySelect) {
                visibilitySelect.disabled = true;
                visibilitySelect.style.opacity = '0.6';
                visibilitySelect.style.cursor = 'not-allowed';
            }
            if (visibilityHelpText) {
                visibilityHelpText.innerHTML = '<strong style="color: #F59E0B;">⚠️ Cancel the scheduled change below to modify visibility manually</strong>';
            }

            // Update content
            const targetStatusText = display.querySelector('.schedule-target-status');
            const countdownText = display.querySelector('.schedule-countdown');

            targetStatusText.textContent = this.getVisibilityLabel(schedule.visibility_status);
            countdownText.textContent = schedule.countdown;

            // Setup cancel button
            const cancelBtn = display.querySelector('.cancel-schedule');
            cancelBtn.onclick = () => this.cancelTrackSchedule(trackId);

            // Start countdown update
            this.startTrackCountdownUpdate(schedule.countdown_seconds);
        } else {
            // Hide schedule display
            display.style.display = 'none';
            if (scheduleBtn) scheduleBtn.style.display = 'block';
            this.stopTrackCountdownUpdate();

            // RE-ENABLE visibility dropdown
            if (visibilitySelect) {
                visibilitySelect.disabled = false;
                visibilitySelect.style.opacity = '1';
                visibilitySelect.style.cursor = 'pointer';
            }
            if (visibilityHelpText) {
                const defaultHelpText = this.userPermissions.is_team && !this.userPermissions.is_creator
                    ? 'Control who can see this track (Team members cannot hide from team)'
                    : 'Control who can see this track';
                visibilityHelpText.innerHTML = defaultHelpText;
            }
        }
    }

    updateTrackScheduleDisplay(schedule, trackId = null) {
        const display = document.getElementById('renameTrackScheduleDisplay');
        const scheduleBtn = document.getElementById('scheduleTrackVisibilityBtn');
        const visibilitySelect = document.getElementById('renameTrackVisibility');
        const visibilityHelpText = visibilitySelect?.parentElement?.querySelector('.help-text');

        if (!display) return;

        if (schedule) {
            // Show schedule display
            display.style.display = 'block';
            if (scheduleBtn) scheduleBtn.style.display = 'none';

            // DISABLE visibility dropdown - user must cancel schedule first
            if (visibilitySelect) {
                visibilitySelect.disabled = true;
                visibilitySelect.style.opacity = '0.6';
                visibilitySelect.style.cursor = 'not-allowed';
            }
            if (visibilityHelpText) {
                visibilityHelpText.innerHTML = '<strong style="color: #F59E0B;">⚠️ Cancel the scheduled change below to modify visibility manually</strong>';
            }

            // Update content
            const targetStatusText = display.querySelector('.schedule-target-status');
            const countdownText = display.querySelector('.schedule-countdown');

            targetStatusText.textContent = this.getVisibilityLabel(schedule.visibility_status);
            countdownText.textContent = schedule.countdown;

            // Setup cancel button
            const cancelBtn = display.querySelector('.cancel-schedule');
            cancelBtn.onclick = () => this.cancelTrackSchedule(trackId);

            // Start countdown update
            this.startTrackCountdownUpdate(schedule.countdown_seconds);
        } else {
            // Hide schedule display
            display.style.display = 'none';
            if (scheduleBtn) scheduleBtn.style.display = 'block';
            this.stopTrackCountdownUpdate();

            // RE-ENABLE visibility dropdown
            if (visibilitySelect) {
                visibilitySelect.disabled = false;
                visibilitySelect.style.opacity = '1';
                visibilitySelect.style.cursor = 'pointer';
            }
            if (visibilityHelpText) {
                const defaultHelpText = this.userPermissions.is_team && !this.userPermissions.is_creator
                    ? 'Control who can see this track (Team members cannot hide from team)'
                    : 'Control who can see this track';
                visibilityHelpText.innerHTML = defaultHelpText;
            }
        }
    }

    startTrackCountdownUpdate(seconds) {
        this.stopTrackCountdownUpdate();

        let remainingSeconds = seconds;

        this.trackCountdownInterval = setInterval(() => {
            remainingSeconds--;

            if (remainingSeconds <= 0) {
                this.stopTrackCountdownUpdate();
                // Use SPA navigation to refresh the page
                if (window.spaRouter) {
                    window.spaRouter.navigate(window.location.pathname);
                } else if (window.navigateTo) {
                    window.navigateTo(window.location.pathname);
                } else {
                    location.reload();
                }
                return;
            }

            const display = document.getElementById('renameTrackScheduleDisplay');
            if (display) {
                const countdownText = display.querySelector('.schedule-countdown');
                if (countdownText) {
                    countdownText.textContent = this.formatCountdown(remainingSeconds);
                }
            }
        }, 1000);
    }

    stopTrackCountdownUpdate() {
        if (this.trackCountdownInterval) {
            clearInterval(this.trackCountdownInterval);
            this.trackCountdownInterval = null;
        }
    }

    async cancelTrackSchedule(trackId) {
        if (!trackId) {
            this.showToast('Error: Invalid track ID', 'error');
            return;
        }

        if (!confirm('Are you sure you want to cancel the scheduled visibility change?')) {
            return;
        }

        try {
            const response = await fetch(`/api/tracks/${trackId}/schedule-visibility`, {
                method: 'DELETE'
            });

            const data = await response.json();

            if (response.ok) {
                this.showToast('Scheduled visibility change cancelled', 'success');
                this.updateTrackScheduleDisplay(null);
                this.updateTTSTrackScheduleDisplay(null);
                this.hideTrackCardScheduleDisplay(trackId);
            } else {
                this.showToast(data.detail || 'Failed to cancel schedule', 'error');
            }
        } catch (error) {
            console.error('Error cancelling track schedule:', error);
            this.showToast('Failed to cancel schedule', 'error');
        }
    }

    getVisibilityLabel(status) {
        const labels = {
            'visible': 'Visible to all',
            'hidden_from_users': 'Hidden from users',
            'hidden_from_all': 'Hidden from all'
        };
        return labels[status] || status;
    }

    formatCountdown(seconds) {
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = seconds % 60;

        if (days > 0) return `${days}d ${hours}h`;
        if (hours > 0) return `${hours}h ${minutes}m`;
        if (minutes > 0) return `${minutes}m ${secs}s`;
        return `${secs}s`;
    }

    openTrackScheduleModal(trackId) {
        const track = this.tracks.find(t => t.id == trackId);
        if (!track) {
            this.showToast('Track not found', 'error');
            return;
        }

        const modal = document.createElement('div');
        modal.id = 'trackScheduleVisibilityModal';
        modal.className = 'modal-overlay active';
        modal.style.display = 'flex';

        const currentStatus = track.visibility_status || 'visible';
        const statusLabel = this.getVisibilityLabel(currentStatus);

        modal.innerHTML = `
            <div class="modal-content schedule-modal-content">
                <div class="modal-header">
                    <h2><i class="fas fa-clock"></i> Schedule Visibility Change</h2>
                    <button class="modal-close" aria-label="Close">&times;</button>
                </div>

                <div class="schedule-current-status">
                    <span class="label">Current visibility:</span>
                    <span class="status-badge ${currentStatus}">${statusLabel}</span>
                </div>

                <form id="trackScheduleVisibilityForm">
                    <div class="form-group">
                        <label for="trackScheduleDateTime">
                            <i class="fas fa-calendar-alt"></i> Schedule Date & Time
                        </label>
                        <input type="datetime-local" id="trackScheduleDateTime" class="form-control" required>
                        <div class="help-text">
                            Select when the visibility should automatically change (your local time)
                        </div>
                    </div>

                    <div class="form-group">
                        <label for="trackScheduleTargetStatus">
                            <i class="fas fa-eye"></i> Change To
                        </label>
                        <select id="trackScheduleTargetStatus" class="form-control visibility-select" required>
                            <option value="">-- Select visibility status --</option>
                            <option value="visible">Visible to all authorized users</option>
                            <option value="hidden_from_users">Hidden from users (Team can see)</option>
                            ${this.userPermissions.is_creator ? '<option value="hidden_from_all">Hidden from everyone including Team</option>' : ''}
                        </select>
                        <div class="help-text">
                            The track will automatically change to this visibility at the scheduled time
                        </div>
                    </div>

                    <div class="schedule-preview" id="trackSchedulePreview" style="display: none;">
                        <i class="fas fa-info-circle"></i>
                        <span id="trackSchedulePreviewText"></span>
                    </div>

                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary track-schedule-cancel">Cancel</button>
                        <button type="submit" class="btn btn-primary">
                            <i class="fas fa-clock"></i> Schedule Change
                        </button>
                    </div>
                </form>
            </div>
        `;

        document.body.appendChild(modal);

        // Set minimum datetime (6 minutes from now)
        const now = new Date();
        now.setMinutes(now.getMinutes() + 6);
        const minDateTime = now.toISOString().slice(0, 16);
        const datetimeInput = modal.querySelector('#trackScheduleDateTime');
        datetimeInput.min = minDateTime;

        // Setup close handlers
        const closeModal = () => {
            modal.remove();
            document.body.style.overflow = '';
        };

        modal.querySelector('.modal-close').addEventListener('click', closeModal);
        modal.querySelector('.track-schedule-cancel').addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal();
        });

        // Setup preview update
        const updatePreview = () => {
            const datetime = datetimeInput.value;
            const targetStatus = modal.querySelector('#trackScheduleTargetStatus').value;
            const preview = modal.querySelector('#trackSchedulePreview');
            const previewText = modal.querySelector('#trackSchedulePreviewText');

            if (datetime && targetStatus) {
                const scheduledDate = new Date(datetime);
                const now = new Date();
                const diff = scheduledDate - now;

                const hours = Math.floor(diff / (1000 * 60 * 60));
                const days = Math.floor(hours / 24);

                let timeText = '';
                if (days > 0) {
                    timeText = `${days} day${days > 1 ? 's' : ''}`;
                } else if (hours > 0) {
                    timeText = `${hours} hour${hours > 1 ? 's' : ''}`;
                } else {
                    const minutes = Math.floor(diff / (1000 * 60));
                    timeText = `${minutes} minute${minutes > 1 ? 's' : ''}`;
                }

                const statusLabel = this.getVisibilityLabel(targetStatus);
                previewText.textContent = `The track will automatically change to "${statusLabel}" in approximately ${timeText} (${scheduledDate.toLocaleString()})`;
                preview.style.display = 'flex';
            } else {
                preview.style.display = 'none';
            }
        };

        datetimeInput.addEventListener('input', updatePreview);
        modal.querySelector('#trackScheduleTargetStatus').addEventListener('change', updatePreview);

        // Setup form submit
        modal.querySelector('#trackScheduleVisibilityForm').addEventListener('submit', async (e) => {
            e.preventDefault();

            const datetime = datetimeInput.value;
            const targetStatus = modal.querySelector('#trackScheduleTargetStatus').value;

            if (!datetime || !targetStatus) {
                this.showToast('Please fill in all fields', 'error');
                return;
            }

            // Validate that the scheduled time is at least 5 minutes in the future
            const scheduledTime = new Date(datetime);
            const now = new Date();
            const minutesInFuture = (scheduledTime - now) / (1000 * 60);

            if (minutesInFuture < 5) {
                this.showToast('Scheduled time must be at least 5 minutes in the future', 'error');
                return;
            }

            // Convert to UTC ISO string
            const scheduledAt = scheduledTime.toISOString();

            const submitBtn = modal.querySelector('button[type="submit"]');
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Scheduling...';

            try {
                const response = await fetch(`/api/tracks/${trackId}/schedule-visibility`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        scheduled_at: scheduledAt,
                        visibility_status: targetStatus
                    })
                });

                const data = await response.json();

                if (response.ok) {
                    this.showToast('Visibility change scheduled successfully', 'success');
                    closeModal();

                    // Refresh the track card schedule display
                    setTimeout(() => this.checkTrackCardSchedule(trackId), 500);

                    // If any edit modal is open, refresh its schedule display
                    if (document.getElementById('renameTrackScheduleDisplay')) {
                        setTimeout(() => this.checkExistingTrackSchedule(trackId), 500);
                    }
                    if (document.getElementById('ttsTrackScheduleDisplay')) {
                        setTimeout(() => this.checkExistingTTSTrackSchedule(trackId), 500);
                    }
                } else {
                    this.showToast(data.detail || 'Failed to schedule visibility change', 'error');
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<i class="fas fa-clock"></i> Schedule Change';
                }
            } catch (error) {
                console.error('Error scheduling visibility change:', error);
                this.showToast('Failed to schedule visibility change', 'error');
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="fas fa-clock"></i> Schedule Change';
            }
        });

        document.body.style.overflow = 'hidden';
    }

    /**
     * Check schedules for all tracks and display countdowns on cards.
     * Only visible to creators and team members.
     */
    async checkAllTrackSchedules() {
        if (!this.userPermissions.is_creator && !this.userPermissions.is_team) return;

        const trackItems = document.querySelectorAll('.track-item[data-track-id]');
        for (const trackItem of trackItems) {
            const trackId = trackItem.dataset.trackId;
            if (trackId) await this.checkTrackCardSchedule(trackId);
        }
    }

    /**
     * Check and display schedule countdown for a single track card.
     */
    async checkTrackCardSchedule(trackId) {
        try {
            const response = await fetch(`/api/tracks/${trackId}/schedule-visibility`);
            if (response.status === 403) {
                this.hideTrackCardScheduleDisplay(trackId);
                return;
            }

            const data = await response.json();
            if (response.ok && data.has_schedule) {
                this.updateTrackCardScheduleDisplay(trackId, data.schedule);
            } else {
                this.hideTrackCardScheduleDisplay(trackId);
            }
        } catch (error) {
            this.hideTrackCardScheduleDisplay(trackId);
        }
    }

    /**
     * Update track card schedule countdown display.
     */
    updateTrackCardScheduleDisplay(trackId, schedule) {
        const indicator = document.getElementById(`track-schedule-${trackId}`);
        if (!indicator) return;

        const compactText = indicator.querySelector('.schedule-countdown-compact');
        const targetText = indicator.querySelector('.schedule-target');
        const countdownText = indicator.querySelector('.schedule-countdown');

        if (targetText && countdownText && compactText) {
            const targetLabel = this.getVisibilityLabel(schedule.visibility_status);
            targetText.textContent = targetLabel;
            countdownText.textContent = schedule.countdown;
            compactText.textContent = schedule.countdown;

            indicator.className = 'track-schedule-indicator';
            indicator.classList.add(`schedule-${schedule.visibility_status}`);
            indicator.dataset.tooltip = `Scheduled: ${targetLabel} in ${schedule.countdown}`;
            indicator.style.display = 'inline-flex';

            this.startTrackCardCountdown(trackId, schedule.countdown_seconds);
        }
    }

    /**
     * Hide track card schedule countdown display.
     */
    hideTrackCardScheduleDisplay(trackId) {
        const indicator = document.getElementById(`track-schedule-${trackId}`);
        if (indicator) indicator.style.display = 'none';
        this.stopTrackCardCountdown(trackId);
    }

    startTrackCardCountdown(trackId, seconds) {
        // Stop existing countdown if any
        this.stopTrackCardCountdown(trackId);

        if (!this.trackCardCountdownIntervals) {
            this.trackCardCountdownIntervals = {};
        }

        let remainingSeconds = seconds;

        this.trackCardCountdownIntervals[trackId] = setInterval(() => {
            remainingSeconds--;

            if (remainingSeconds <= 0) {
                this.stopTrackCardCountdown(trackId);
                // Refresh the page to show updated visibility using SPA navigation
                if (window.spaRouter) {
                    window.spaRouter.navigate(window.location.pathname);
                } else if (window.navigateTo) {
                    window.navigateTo(window.location.pathname);
                } else {
                    location.reload();
                }
                return;
            }

            const indicator = document.getElementById(`track-schedule-${trackId}`);
            if (indicator) {
                const formattedCountdown = this.formatCountdown(remainingSeconds);

                // Update full text countdown (desktop)
                const countdownText = indicator.querySelector('.schedule-countdown');
                if (countdownText) {
                    countdownText.textContent = formattedCountdown;
                }

                // Update compact countdown (mobile)
                const compactText = indicator.querySelector('.schedule-countdown-compact');
                if (compactText) {
                    compactText.textContent = formattedCountdown;
                }

                // Update tooltip
                const targetText = indicator.querySelector('.schedule-target');
                if (targetText) {
                    indicator.dataset.tooltip = `Scheduled: ${targetText.textContent} in ${formattedCountdown}`;
                }
            }
        }, 1000);
    }

    stopTrackCardCountdown(trackId) {
        if (this.trackCardCountdownIntervals && this.trackCardCountdownIntervals[trackId]) {
            clearInterval(this.trackCardCountdownIntervals[trackId]);
            delete this.trackCardCountdownIntervals[trackId];
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    try {
        if (window.albumDetail) {
            window.albumDetail = null;
        }
        
        const albumDetails = new AlbumDetails();
        const audioUpload = new AudioUpload(albumDetails);
        const ttsManager = new TTSManager(albumDetails);
        const documentManager = new DocumentExtractionManager(ttsManager);
        
        albumDetails.audioUpload = audioUpload;
        albumDetails.ttsManager = ttsManager;
        albumDetails.documentManager = documentManager; 
        
        window.albumDetail = albumDetails;
        window.audioUpload = audioUpload;
        window.ttsManager = ttsManager;
        window.documentManager = documentManager;
        
        window.openAddTrackModal = () => {
            try {
                return albumDetails.isInitialized ? audioUpload.openAddTrackModal() : null;
            } catch (error) {
                albumDetails.showToast('Error opening modal', 'error');
            }
        };

        window.closeAddTrackModal = () => {
            try {
                return albumDetails.isInitialized ? audioUpload.closeAddTrackModal() : null;
            } catch (error) {
                albumDetails.showToast('Error closing modal', 'error');
            }
        };

        window.openTTSModal = () => {
            try {
                return albumDetails.isInitialized ? ttsManager.openTTSModal() : null;
            } catch (error) {
                albumDetails.showToast('Error opening TTS modal', 'error');
            }
        };

        window.closeTTSModal = () => {
            try {
                return albumDetails.isInitialized ? ttsManager.closeTTSModal() : null;
            } catch (error) {
                albumDetails.showToast('Error closing TTS modal', 'error');
            }
        };
        
        window.addEventListener('beforeunload', () => {
            if (audioUpload?.closeAddTrackModal) {
                audioUpload.closeAddTrackModal();
            }
            if (ttsManager?.clearTTSProgress) {
                ttsManager.clearTTSProgress();
            }
            if (ttsManager?.clearBulkProgress) {
                ttsManager.clearBulkProgress();
            }
            
            try {
                ttsManager.activeTTSJobs.clear();
                ttsManager.bulkJobs.clear();
                ttsManager.progressErrorCounts.clear();
            } catch (error) {}
            
            if (albumDetails?.cleanup) {
                albumDetails.cleanup();
            }
        });
        
    } catch (error) {
        const errorToast = document.createElement('div');
        errorToast.className = 'player-toast toast-error';
        errorToast.textContent = 'Failed to initialize page. Please refresh and try again.';
        errorToast.style.backgroundColor = 'rgba(220, 38, 38, 0.9)';
        errorToast.style.color = 'white';
        errorToast.style.position = 'fixed';
        errorToast.style.bottom = '20px';
        errorToast.style.left = '50%';
        errorToast.style.transform = 'translateX(-50%)';
        errorToast.style.padding = '10px 20px';
        errorToast.style.borderRadius = '4px';
        errorToast.style.zIndex = '9999';
        document.body.appendChild(errorToast);
        setTimeout(() => errorToast.remove(), 5000);
    }
});

window.AlbumDetails = AlbumDetails;
}
