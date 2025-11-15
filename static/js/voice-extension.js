class VoiceExtension {
    constructor(player) {
        // Core
        this.player = player;
        this.isEnabled = false;
        this.trackType = 'audio';
        this.currentVoice = null;
        this.trackDefaultVoice = null;

        // Flags / state
        this.isVoiceChanging = false;
        this.isCreator = false;
        this.isTeam = false;
        this.currentTab = 'available';
        this._isSaving = false;

        // In-memory cache for voice preference (cleared on page reload)
        this._cachedVoicePreference = null;
        this._voicePreferenceFetched = false;

        // Modal / UI
        this.modal = null;
        this.currentSampleAudio = null;
        this.confirmationState = null;

        // Word timings
        this.wordTimingsCache = {};
        this.pendingWordSwitch = null;

        // Progress (ONE voice at a time)
        this.currentlyProcessingVoice = null;
        this.processingPhase = null;
        this.progressPolling = null;
        this.progressRefreshInterval = null;
        this.currentProgressPhase = null;
        this.activeProgressVoice = null;
        this.progressCache = {};

        this.awaitingSegmentationVoice = null;

        // Real-time status channel
        this.ttsChannel = window.TTSStatusChannel || null;
        this.useWebSocketUpdates = !!this.ttsChannel;
        this.channelSubscriptions = new Map();

        this.handleTTSStatusEvent = this.handleTTSStatusEvent.bind(this);
        this.handleChannelConnected = this.handleChannelConnected.bind(this);
        this.handleChannelDisconnected = this.handleChannelDisconnected.bind(this);

        if (this.ttsChannel) {
            window.addEventListener('ttsStatusUpdate', this.handleTTSStatusEvent);
            window.addEventListener('ttsWebSocketConnected', this.handleChannelConnected);
            window.addEventListener('ttsWebSocketDisconnected', this.handleChannelDisconnected);
        } else {
        }

        // Data
        this.voicesWithTiers = [];
        this.generatedVoices = [];
        this.processingVoices = new Set();
        this.requireConfirmation = false;
        this.autoSwitchOnComplete = true;
        this.autoCloseModalOnAutoSwitch = false;

        // ✅ Store init promise so player can wait for global favorite to load
        this.initPromise = this.init();
    }

    async init() {
        this.getUserInfo();
        this.createVoiceUI();
        this.hookIntoPlayer();

        // Load global favorite on initialization (early, so it's available for first track)
        await this.fetchUserVoicePreference().catch(() => {});

        if (this.ttsChannel) {
            this.ttsChannel.connect();
        }

        // Mark as fully initialized
        this.isInitialized = true;
    }

    getUserInfo() {
        const userData = window.userData || window.currentUser || {};
        this.isCreator = !!userData.is_creator;
        this.isTeam = !!userData.is_team;

        const userDataElement = document.querySelector('#user-data');
        if (userDataElement) {
            try {
                const data = JSON.parse(userDataElement.textContent);
                this.isCreator = data.is_creator ?? this.isCreator;
                this.isTeam = data.is_team ?? this.isTeam;
            } catch (_) {}
        }
    }

    hookIntoPlayer() {
        const originalSetTrackMetadata = this.player.setTrackMetadata.bind(this.player);
        this.player.setTrackMetadata = (trackId, title, album, coverPath, voice = null, trackType = 'audio', albumId = null) => {
            originalSetTrackMetadata(trackId, title, album, coverPath, voice, trackType, albumId);
            this.onTrackChanged(trackId, voice, trackType);
        };
    }

    notifyVoiceChanged(newVoiceId) {
        document.dispatchEvent(new CustomEvent('voicePreferenceChanged', {
            detail: {
                trackId: this.player.currentTrackId,
                voiceId: newVoiceId,
                oldVoice: this.currentVoice,
                newVoice: newVoiceId,
                timestamp: Date.now()
            }
        }));
    }

    async loadWordTimings(trackId, voiceId, forceReload = false) {
        const cacheKey = `${trackId}:${voiceId}`;
        const cached = this.wordTimingsCache[cacheKey];
        if (!forceReload && cached && Date.now() - cached.timestamp < 300000) {
            return cached.timings;
        }
        try {
            const res = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/word-timings/${encodeURIComponent(voiceId)}`);
            if (!res.ok) return null;
            const data = await res.json();
            const wordTimings = data.word_timings || [];
            this.wordTimingsCache[cacheKey] = {
                timings: wordTimings,
                timestamp: Date.now(),
                totalWords: wordTimings.length,
                duration: data.duration || 0
            };
            return wordTimings;
        } catch {
            return null;
        }
    }

    async getCurrentWordForVoiceSwitch() {
        if (!this.player.currentTrackId || !this.currentVoice) return -1;
        const cacheKey = `${this.player.currentTrackId}:${this.currentVoice}`;
        const cached = this.wordTimingsCache[cacheKey];
        const now = this.player.audio.currentTime;

        if (cached?.timings) return this.findWordIndexFromTimings(cached.timings, now);

        const timings = await this.loadWordTimings(this.player.currentTrackId, this.currentVoice);
        if (timings) return this.findWordIndexFromTimings(timings, now);
        return -1;
    }

    findWordIndexFromTimings(timings, time) {
        for (let i = 0; i < timings.length; i++) {
            const w = timings[i];
            if (w.start_time <= time && time < w.end_time) return i;
        }
        return -1;
    }

    getPollDelay(phase, pct = 0) {
        const hidden = document.hidden;
        const modalOpen = this.modal?.classList.contains('active');
        const burst = modalOpen && !hidden;

        if (phase === 'tts') {
            if (hidden) return 10000 + Math.random() * 2000;
            if (burst) return pct < 60 ? 1200 + Math.random() * 400 : 2000 + Math.random() * 600;
            if (pct < 20) return 1500 + Math.random() * 500;
            if (pct < 60) return 2500 + Math.random() * 800;
            if (pct < 90) return 4000 + Math.random() * 1000;
            return 6000 + Math.random() * 1500;
        }
        if (phase === 'segmentation') {
            if (hidden) return 12000 + Math.random() * 2000;
            if (burst) return pct < 50 ? 2500 + Math.random() * 700 : 3500 + Math.random() * 900;
            if (pct < 50) return 4000 + Math.random() * 1200;
            if (pct >= 95 && !document.hidden) return 1500 + Math.random() * 400;
            return 7000 + Math.random() * 1500;
        }
        return hidden ? 10000 : 4000;
    }

    startProgressTracking(trackId, voiceId, phase = 'tts', options = {}) {
        const { forcePolling = false, skipStop = false } = options;

        if (!skipStop) {
            this.stopProgressTracking();
        } else if (this.progressPolling) {
            clearTimeout(this.progressPolling);
            this.progressPolling = null;
        }

        this.currentProgressPhase = phase;
        this.processingPhase = phase;
        this.activeProgressVoice = voiceId;

        const shouldUseRealtime = this.useWebSocketUpdates && !forcePolling && this.ttsChannel;

        if (shouldUseRealtime) {
            this.subscribeToVoiceUpdates(trackId, voiceId);
            // Still do one immediate check to get current status
            if (phase === 'tts') this.checkTTSProgress(trackId, voiceId);
            else this.checkSegmentProgress(trackId, voiceId);
            // With WebSocket, we don't need polling
            return;
        }

        // Fallback to polling if WebSocket not available
        // Immediate check
        if (phase === 'tts') this.checkTTSProgress(trackId, voiceId);
        else this.checkSegmentProgress(trackId, voiceId);

        // Adaptive polling
        const poll = () => {
            if (!this.currentlyProcessingVoice || this.currentlyProcessingVoice !== voiceId) {
                this.stopProgressTracking();
                return;
            }

            const progressKey = `${trackId}:${voiceId}`;
            const cached = this.progressCache[progressKey];
            const pct = cached?.percentage || 0;

            if (phase === 'tts') {
                this.checkTTSProgress(trackId, voiceId);
            } else {
                this.checkSegmentProgress(trackId, voiceId);
            }

            this.progressPolling = setTimeout(poll, this.getPollDelay(phase, pct));
        };

        this.progressPolling = setTimeout(poll, this.getPollDelay(phase, 0));
    }

    stopProgressTracking() {
        if (this.progressPolling) {
            clearTimeout(this.progressPolling);
            this.progressPolling = null;
        }
        // Unsubscribe from WebSocket updates
        if (this.currentlyProcessingVoice && this.player.currentTrackId) {
            this.unsubscribeFromVoiceUpdates(this.player.currentTrackId, this.currentlyProcessingVoice);
        }
        this.currentProgressPhase = null;
        this.activeProgressVoice = null;
    }

    async checkTTSProgress(trackId, voiceId) {
        try {
            const response = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/tts-progress/${encodeURIComponent(voiceId)}`);
            if (!response.ok) return;
            const data = await response.json();

            const percentage = Number(data.progress || 0) || 0;
            const progressKey = `${trackId}:${voiceId}`;

            this.progressCache[progressKey] = {
                phase: 'tts',
                percentage,
                message: `Generating... ${percentage.toFixed(0)}%`,
                status: data.status,
                lastUpdated: Date.now()
            };

            this.updateProgressOverlay({
                title: `Generating Voice: ${this.getVoiceDisplayName(voiceId)}`,
                message: `${this.getTTSMessage(data)} (${percentage.toFixed(0)}%)`,
                current: data.chunks_completed || 0,
                total: data.total_chunks || 0,
                percentage,
                phase: 'tts'
            });

            if (data.status === 'complete' || data.status === 'segmentation_ready') {
                this.processingPhase = 'segmentation';
                this.awaitingSegmentationVoice = voiceId;
                this.startProgressTracking(trackId, voiceId, 'segmentation');
            } else if (data.status === 'error') {
                this.failVoiceProcessing(voiceId, data.message || 'Unknown error');
            }

            this.refreshModalIfOpen();
        } catch (error) {}
    }

    getTTSMessage(data) {
        switch (data.phase) {
            case 'initializing': return 'Starting voice generation';
            case 'generating': return `Creating audio chunks (${data.chunks_completed || 0}/${data.total_chunks || 0})`;
            case 'concatenating': return 'Combining audio files';
            case 'finalizing': return 'Finalizing voice';
            default: return data.message || 'Processing';
        }
    }

    async checkSegmentProgress(trackId, voiceId) {
        try {
            const response = await fetch(`/api/segment-progress/${encodeURIComponent(trackId)}?voice_id=${encodeURIComponent(voiceId)}`);
            if (!response.ok) return;
            const data = await response.json();

            const percentage = Number(data.percentage || data.percent || 0) || 0;
            const progressKey = `${trackId}:${voiceId}`;

            this.progressCache[progressKey] = {
                phase: 'segmentation',
                percentage,
                message: `Preparing... ${percentage.toFixed(0)}%`,
                submessage: data.formatted ? `${data.formatted.current}/${data.formatted.total}` : '',
                status: data.status,
                lastUpdated: Date.now()
            };

            if (data.status === 'creating_segments' || data.status === 'processing') {
                this.updateProgressOverlay({
                    title: `Preparing Voice: ${this.getVoiceDisplayName(voiceId)}`,
                    message: `Creating playback segments... (${percentage.toFixed(0)}%)`,
                    current: data.current || 0,
                    total: data.total || 0,
                    percentage,
                    phase: 'segmentation'
                });
            }

            const doneByStatus = data.status === 'complete';
            const doneByCount = (data.total > 0 && data.current >= data.total);
            const doneByFormat = (data.formatted && data.formatted.current && data.formatted.total &&
                data.formatted.current === data.formatted.total);
            const doneByPct = percentage >= 99.9;

            if (doneByStatus || doneByCount || doneByFormat || doneByPct) {
                this.onVoiceReady(trackId, voiceId);
                this.updateVoiceCardToReady(voiceId);
            } else if (data.status === 'error') {
                this.failVoiceProcessing(voiceId, data.message || 'Voice preparation failed');
            }

            this.refreshModalIfOpen();
        } catch (error) {}
    }

    updateProgressOverlay(progressData) {
        const el = {
            wrap: document.getElementById('segmentProgress'),
            bar: document.getElementById('segmentProgressBar'),
            txt: document.getElementById('segmentCount'),
            title: document.querySelector('.segment-title')
        };
        if (!el.wrap || !el.bar || !el.txt) return;

        el.wrap.style.display = 'block';
        if (el.title) el.title.textContent = progressData.title || 'Processing...';

        const pct = Math.min(100, Math.max(0, progressData.percentage || 0));
        el.bar.style.width = `${pct}%`;
        el.txt.textContent = this.getProgressMessage(progressData);
    }

    getProgressMessage(data) {
        if (data.phase === 'tts') {
            return data.total > 0 ? `${data.current} / ${data.total} chunks` : `${Math.round(data.percentage)}% - Generating audio`;
        }
        if (data.phase === 'segmentation') {
            return `${data.current || 0} / ${data.total || 0} segments`;
        }
        if (data.phase === 'complete') {
            return 'Complete - Voice ready!';
        }
        return data.message || 'Processing...';
    }

    hideProgressOverlay() {
        const wrap = document.getElementById('segmentProgress');
        if (wrap) setTimeout(() => (wrap.style.display = 'none'), 1200);
    }

    onVoiceReady(trackId, voiceId) {
        const shouldAutoSwitch =
            this.autoSwitchOnComplete &&
            this.pendingWordSwitch?.targetVoice === voiceId &&
            this.player?.currentTrackId === trackId;

        if (this.awaitingSegmentationVoice === voiceId) {
            this.awaitingSegmentationVoice = null;
        }

        this.stopProgressTracking();
        this.hideProgressOverlay();
        this.completeVoiceProcessing(voiceId);

        if (!this.generatedVoices.includes(voiceId)) {
            this.generatedVoices.push(voiceId);
        }

        this.player.showToast(`Voice ${this.getVoiceDisplayName(voiceId)} ready!`, 'success');
        this.loadWordTimings(trackId, voiceId).catch(() => {});
        this.refreshModalIfOpen();
        this.updateVoiceCardToReady(voiceId);

        if (shouldAutoSwitch) {
            this.completeVoiceSwitch(voiceId)
                .then(() => {
                    if (this.modal?.classList.contains('active')) {
                        this.closeVoiceModal();
                    }
                })
                .catch(() => {});
        }
    }

    failVoiceProcessing(voiceId, msg) {
        this.stopProgressTracking();
        this.hideProgressOverlay();
        this.completeVoiceProcessing(voiceId);
        this.refreshModalIfOpen();
        if (this.awaitingSegmentationVoice === voiceId) {
            this.awaitingSegmentationVoice = null;
        }
        this.player.showToast(`Voice preparation failed: ${msg}`, 'error');
    }

    completeVoiceProcessing(voiceId) {
        if (this.currentlyProcessingVoice === voiceId) {
            this.currentlyProcessingVoice = null;
            this.processingPhase = null;
        }
        this.processingVoices.delete(voiceId);

        const trackId = this.player.currentTrackId;
        if (trackId) delete this.progressCache[`${trackId}:${voiceId}`];
    }

    updateVoiceCardToReady(voiceId) {
        const card = document.querySelector(`.voice-option[data-voice-id="${voiceId}"]`);
        if (!card) return;

        card.classList.remove('processing', 'segmenting');
        card.dataset.isProcessing = 'false';

        let sampleBtn = card.querySelector('.sample-play-btn');
        if (sampleBtn) {
            const newBtn = document.createElement('button');
            newBtn.className = 'sample-play-btn';
            newBtn.setAttribute('data-voice-id', voiceId);
            newBtn.innerHTML = `<i class="fas fa-play"></i><span>Sample</span>`;
            sampleBtn.replaceWith(newBtn);
            newBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.playSample(voiceId, newBtn);
            });
        }

        let status = card.querySelector('.voice-status');
        if (!status) {
            status = document.createElement('div');
            status.className = 'voice-status';
            card.appendChild(status);
        }
        status.classList.remove('processing', 'segmenting');
        status.textContent = '✓ ✅ Generated - Ready to Switch';

        const delBtn = card.querySelector('.voice-delete-btn');
        if (delBtn) {
            delBtn.disabled = false;
            const icon = delBtn.querySelector('i');
            if (icon) icon.className = 'fas fa-trash';
        }
    }

    async onTrackChanged(trackId, voice, trackType) {
        this.trackType = trackType;
        this.isEnabled = trackType === 'tts';

        if (this.isEnabled) {
            this.trackDefaultVoice = this.getTrackDefaultVoice();

            // Load available voices for this track
            await this.loadGeneratedVoices(trackId);

            // Voice parameter has been pre-validated in player-shared-spa.js with correct priority.
            // Priority: explicit voice parameter > cached favorite > track default
            let initialVoice;
            if (voice) {
                initialVoice = voice;
            } else if (this._cachedVoicePreference && this.generatedVoices.includes(this._cachedVoicePreference)) {
                initialVoice = this._cachedVoicePreference;
            } else if (this._cachedVoicePreference) {
                initialVoice = this._cachedVoicePreference;
            } else {
                initialVoice = this.trackDefaultVoice;
            }

            this.currentVoice = initialVoice;

            if (this.player) {
                this.player.currentVoice = this.currentVoice;
            }

            // ⚠️ REMOVED: Don't preload word-timings on every track load
            // Word-timings are ONLY needed for:
            // 1. Read-along overlay (loaded on-demand when opened)
            // 2. Voice switching (loaded when user switches voice)
            // Preloading wastes 400ms+ of network/backend time unnecessarily
        } else {
            this.currentVoice = null;
            this.wordTimingsCache = {};
        }

        this.currentlyProcessingVoice = null;
        this.processingPhase = null;
        this.processingVoices.clear();
        this.progressCache = {};
        this.stopProgressRefresh();
        this.updateVoiceButton();
    }

    getTrackDefaultVoice() {
        const sources = [
            this.player.trackMetadata?.default_voice,
            this.player.currentTrack?.default_voice,
            this.player.streamConfig?.voice_id,
            window.trackData?.default_voice
        ];
        for (const s of sources) if (s) return s;

        const playerData = document.querySelector('#player-data');
        if (playerData) {
            try {
                const data = JSON.parse(playerData.textContent);
                return data.track?.default_voice || null;
            } catch (_) {}
        }
        return null;
    }

    async saveVoiceState() {
        if (this._isSaving || !this.isEnabled || !this.currentVoice) return;

        const trackId = this.player.currentTrackId;
        if (!trackId) return;

        try {
            this._isSaving = true;

            // Update player state
            this.player.currentVoice = this.currentVoice;
            if (this.player.trackMetadata) this.player.trackMetadata.voice = this.currentVoice;

            // Save to database (single source of truth)
            await this.saveUserVoicePreference(this.currentVoice);
        } catch (err) {
        } finally {
            this._isSaving = false;
        }
    }

    async loadVoiceState() {
        if (!this.isEnabled) return;
        const trackId = this.player.currentTrackId;
        if (!trackId) return;

        try {
            const savedVoice = await this.loadVoicePreference(trackId);

            if (savedVoice && savedVoice !== this.currentVoice) {
                this.currentVoice = savedVoice;
                this.player.currentVoice = savedVoice;
                if (this.player.trackMetadata) this.player.trackMetadata.voice = savedVoice;
                this.updateVoiceButton();
                await this.saveVoiceState();
            }
        } catch (err) {
        }
    }

    getVoiceFromPlayerState(trackId) {
        try {
            const s = sessionStorage.getItem(`playerState_${trackId}`);
            if (s) return JSON.parse(s).voice;
        } catch (_) {}
        return null;
    }

    async fetchUserVoicePreference() {
        // Return cached value if already fetched
        if (this._voicePreferenceFetched) return this._cachedVoicePreference;

        try {
            const response = await fetch('/api/user/preferences');
            if (!response.ok) {
                this._voicePreferenceFetched = true;
                this._cachedVoicePreference = null;
                return null;
            }
            const data = await response.json();
            this._cachedVoicePreference = data.preferred_voice;
            this._voicePreferenceFetched = true;
            return data.preferred_voice;
        } catch (error) {
            this._voicePreferenceFetched = true;
            this._cachedVoicePreference = null;
            return null;
        }
    }

    async saveUserVoicePreference(voiceId, isFavorite = false) {
        try {
            const trackId = this.player.currentTrackId;
            if (!trackId) return false;

            const response = await fetch(`/api/user/voice-preference/${encodeURIComponent(trackId)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    voice_id: voiceId,
                    is_favorite: isFavorite
                })
            });

            if (response.ok) {
                const data = await response.json();
                if (data.is_favorite) {
                    this._cachedVoicePreference = voiceId;
                    this._voicePreferenceFetched = true;
                }
                return data;
            }
            return null;
        } catch (error) {
            return null;
        }
    }

    async loadVoicePreference(trackId) {
        try {
            const response = await fetch(`/api/user/voice-preference/${encodeURIComponent(trackId)}`);
            if (!response.ok) {
                return this._cachedVoicePreference || this.getTrackDefaultVoice();
            }

            const data = await response.json();
            // Priority: track-specific > global favorite > track default
            return data.voice_id || this._cachedVoicePreference || this.getTrackDefaultVoice();
        } catch (error) {
            return this._cachedVoicePreference || this.getTrackDefaultVoice();
        }
    }

    async toggleFavorite(voiceId, setAsFavorite) {
        const trackId = this.player.currentTrackId;
        if (!trackId) return;

        try {
            if (setAsFavorite) {
                // Heart the voice (set as global favorite)
                const result = await this.saveUserVoicePreference(voiceId, true);
                if (result) {
                    const previousFavorite = result.previous_favorite;
                    this._cachedVoicePreference = voiceId;
                    this._voicePreferenceFetched = true;

                    // Show toast notification
                    if (previousFavorite && previousFavorite !== voiceId) {
                        this.player.showToast(
                            `❤️ ${this.getVoiceDisplayName(voiceId)} is now your favorite voice. Previous favorite (${this.getVoiceDisplayName(previousFavorite)}) unselected.`,
                            'success',
                            5000
                        );
                    } else {
                        this.player.showToast(`❤️ ${this.getVoiceDisplayName(voiceId)} set as favorite for all tracks`, 'success', 3000);
                    }

                    // Refresh modal to update heart icons
                    this.refreshModalIfOpen();
                }
            } else {
                // Unheart (remove favorite)
                const response = await fetch('/api/user/voice-preference/favorite', {
                    method: 'DELETE'
                });

                if (response.ok) {
                    const data = await response.json();
                    this._cachedVoicePreference = null;
                    this._voicePreferenceFetched = true;

                    this.player.showToast(`🤍 Favorite voice removed`, 'info', 2000);

                    // Refresh modal to update heart icons
                    this.refreshModalIfOpen();
                }
            }
        } catch (error) {
            this.player.showToast('Failed to update favorite voice', 'error');
        }
    }

    createVoiceUI() {
        this.createVoiceModal();
        this.createVoiceButton();
    }

    createVoiceButton() {
        const voiceBtn = document.getElementById('voiceChangeBtn');
        if (voiceBtn) voiceBtn.addEventListener('click', () => this.openVoiceModal());
    }

    createVoiceModal() {
        ['voiceSelectionModal', 'voiceConfirmationModal', 'voiceModalStyles'].forEach(id => {
            document.getElementById(id)?.remove();
        });

        const styles = document.createElement('style');
        styles.id = 'voiceModalStyles';
        styles.textContent = this.getModalCSS();
        document.head.appendChild(styles);

        document.body.insertAdjacentHTML('beforeend', this.getModalHTML());

        this.modal = document.getElementById('voiceSelectionModal');
        this.setupModalEventListeners();
        this.setupConfirmationModalEventListeners();
    }

    getModalCSS() {
        return `
            .voice-modal { position: fixed !important; top: 0 !important; left: 0 !important; width: 100% !important; height: 100% !important; background-color: rgba(0, 0, 0, 0.6) !important; z-index: 2001 !important; justify-content: center !important; align-items: center !important; backdrop-filter: blur(3px) !important; }
            .voice-modal.active { display: flex !important; }
            .voice-modal-content { background-color: #1f2937 !important; color: #f3f4f6 !important; border-radius: 12px !important; padding: 30px !important; width: 90% !important; max-width: 600px !important; max-height: 80vh !important; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4) !important; animation: modalSlideIn 0.3s ease !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; overflow-y: auto !important; }
            .voice-modal-header { display: flex !important; align-items: center !important; justify-content: space-between !important; margin-bottom: 1rem !important; padding-bottom: 1rem !important; border-bottom: 1px solid rgba(255, 255, 255, 0.1) !important; }
            .voice-modal-header-left { display: flex !important; align-items: center !important; gap: 12px !important; }
            .voice-modal-header i { font-size: 1.8rem !important; color: #a0522d !important; }
            .voice-modal-header h2 { margin: 0 !important; font-size: 1.5rem !important; color: white !important; font-weight: 600 !important; }
            .close-modal-btn { background: none !important; border: none !important; color: white !important; font-size: 1.2rem !important; cursor: pointer !important; padding: 4px !important; border-radius: 4px !important; transition: background-color 0.2s ease !important; }
            .close-modal-btn:hover { background: rgba(255, 255, 255, 0.1) !important; }

            .voice-tabs { display: flex !important; margin-bottom: 1.5rem !important; border-radius: 8px !important; overflow: hidden !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; }
            .voice-tab { flex: 1 !important; padding: 12px 16px !important; background: rgba(255, 255, 255, 0.05) !important; color: #d1d5db !important; border: none !important; cursor: pointer !important; transition: all 0.2s ease !important; font-size: 0.95rem !important; font-weight: 500 !important; position: relative !important; }
            .voice-tab:not(:last-child) { border-right: 1px solid rgba(255, 255, 255, 0.1) !important; }
            .voice-tab:hover { background: rgba(255, 255, 255, 0.1) !important; }
            .voice-tab.active { background: rgba(160, 82, 45, 0.2) !important; color: #a0522d !important; border-bottom: 2px solid #a0522d !important; }
            .voice-tab-count { display: inline-block !important; margin-left: 6px !important; padding: 2px 6px !important; border-radius: 10px !important; font-size: 0.8rem !important; background: rgba(255, 255, 255, 0.1) !important; }
            .voice-tab.active .voice-tab-count { background: rgba(160, 82, 45, 0.3) !important; color: #d97706 !important; }

            .voice-grid { display: grid !important; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)) !important; gap: 12px !important; margin-bottom: 1.5rem !important; }
            .voice-option { background: rgba(255, 255, 255, 0.05) !important; border: 2px solid rgba(255, 255, 255, 0.1) !important; border-radius: 8px !important; padding: 16px !important; cursor: pointer !important; transition: all 0.2s ease !important; text-align: center !important; position: relative !important; }
            .voice-option:hover { background: rgba(255, 255, 255, 0.1) !important; border-color: rgba(160, 82, 45, 0.5) !important; transform: translateY(-2px) !important; }
            .voice-option.current { background: rgba(160, 82, 45, 0.2) !important; border-color: #a0522d !important; box-shadow: 0 0 0 1px rgba(160, 82, 45, 0.3) !important; }
            .voice-option.current::before { content: "✓ " !important; color: #a0522d !important; font-weight: bold !important; }
            .voice-option.no-access { opacity: 0.6 !important; cursor: not-allowed !important; }

            .voice-option.processing { opacity: 0.7 !important; pointer-events: none !important; border-color: rgba(251, 191, 36, 0.5) !important; background: rgba(251, 191, 36, 0.05) !important; animation: processingPulse 2s ease-in-out infinite !important; }
            .voice-option.segmenting { opacity: 0.7 !important; pointer-events: none !important; border-color: rgba(34, 197, 94, 0.5) !important; background: rgba(34, 197, 94, 0.05) !important; animation: segmentationPulse 2s ease-in-out infinite !important; }

            .voice-option.processing .voice-name::after { content: " (Processing...)" !important; color: #f59e0b !important; font-size: 0.8rem !important; font-weight: normal !important; }
            .voice-option.segmenting .voice-name::after { content: " (Preparing...)" !important; color: #22c55e !important; font-size: 0.8rem !important; font-weight: normal !important; }

            .voice-option-header { display: flex !important; align-items: center !important; justify-content: space-between !important; margin-bottom: 4px !important; min-height: 24px !important; align-items: flex-start !important; }
            .voice-name { font-weight: 600 !important; font-size: 1rem !important; color: white !important; flex: 1 !important; text-align: left !important; line-height: 1.2 !important; word-break: break-word !important; }

            .voice-actions { display: flex !important; gap: 4px !important; align-items: center !important; }

            .voice-heart-btn { background: rgba(239, 68, 68, 0.1) !important; border: 1px solid rgba(239, 68, 68, 0.3) !important; color: #ef4444 !important; padding: 4px 6px !important; border-radius: 4px !important; font-size: 0.8rem !important; cursor: pointer !important; transition: all 0.2s ease !important; display: flex !important; align-items: center !important; justify-content: center !important; min-width: 24px !important; height: 24px !important; }
            .voice-heart-btn:hover { background: rgba(239, 68, 68, 0.2) !important; border-color: rgba(239, 68, 68, 0.5) !important; transform: scale(1.1) !important; }
            .voice-heart-btn.favorited { background: rgba(239, 68, 68, 0.3) !important; border-color: rgba(239, 68, 68, 0.6) !important; animation: heartBeat 0.3s ease !important; }
            .voice-heart-btn.favorited i { color: #ef4444 !important; }

            .voice-delete-btn { background: rgba(239, 68, 68, 0.1) !important; border: 1px solid rgba(239, 68, 68, 0.3) !important; color: #ef4444 !important; padding: 4px 6px !important; border-radius: 4px !important; font-size: 0.7rem !important; cursor: pointer !important; transition: all 0.2s ease !important; display: flex !important; align-items: center !important; justify-content: center !important; min-width: 24px !important; height: 24px !important; }
            .voice-delete-btn:hover:not(:disabled) { background: rgba(239, 68, 68, 0.2) !important; border-color: rgba(239, 68, 68, 0.5) !important; transform: scale(1.05) !important; }
            .voice-delete-btn:disabled { opacity: 0.5 !important; cursor: not-allowed !important; }
            .voice-delete-btn.deleting { background: rgba(251, 191, 36, 0.2) !important; border-color: rgba(251, 191, 36, 0.5) !important; color: #f59e0b !important; }

            .voice-info-btn { background: rgba(59, 130, 246, 0.1) !important; border: 1px solid rgba(59, 130, 246, 0.3) !important; color: #60a5fa !important; padding: 4px 8px !important; border-radius: 4px !important; font-size: 0.9rem !important; cursor: pointer !important; transition: all 0.2s ease !important; margin-left: 8px !important; }
            .voice-info-btn:hover { background: rgba(59, 130, 246, 0.2) !important; border-color: rgba(59, 130, 246, 0.5) !important; transform: scale(1.05) !important; }

            .voice-description { font-size: 0.85rem !important; opacity: 0.8 !important; color: #d1d5db !important; margin-bottom: 8px !important; }
            .voice-tier { font-size: 0.75rem !important; padding: 3px 10px !important; border-radius: 12px !important; font-weight: 500 !important; display: inline-block !important; margin-top: 4px !important; text-transform: uppercase !important; letter-spacing: 0.5px !important; }
            .voice-tier.free, .voice-tier.tier-0 { background: rgba(34, 197, 94, 0.2) !important; color: #4ade80 !important; border: 1px solid rgba(34, 197, 94, 0.3) !important; }
            .voice-tier.tier-1 { background: rgba(59, 130, 246, 0.2) !important; color: #60a5fa !important; border: 1px solid rgba(59, 130, 246, 0.3) !important; }
            .voice-tier.tier-2 { background: rgba(168, 85, 247, 0.2) !important; color: #c084fc !important; border: 1px solid rgba(168, 85, 247, 0.3) !important; }
            .voice-tier.tier-3 { background: rgba(245, 158, 11, 0.2) !important; color: #fbbf24 !important; border: 1px solid rgba(245, 158, 11, 0.3) !important; }
            .voice-tier.tier-4 { background: rgba(251, 146, 60, 0.2) !important; color: #fb923c !important; border: 1px solid rgba(251, 146, 60, 0.3) !important; }
            .voice-tier.tier-5 { background: rgba(239, 68, 68, 0.2) !important; color: #f87171 !important; border: 1px solid rgba(239, 68, 68, 0.3) !important; }

            .voice-tier.tier-high { background: linear-gradient(135deg, rgba(236, 72, 153, 0.2), rgba(239, 68, 68, 0.2)) !important; color: #ec4899 !important; border: 1px solid rgba(236, 72, 153, 0.3) !important; }
            .voice-tier.assigned { background: rgba(59, 130, 246, 0.2) !important; color: #60a5fa !important; border: 1px solid rgba(59, 130, 246, 0.3) !important; }
            .voice-tier.not-assigned { background: rgba(107, 114, 128, 0.2) !important; color: #9ca3af !important; border: 1px solid rgba(107, 114, 128, 0.3) !important; }

            .voice-status { font-size: 0.75rem !important; color: #60a5fa !important; margin-top: 6px !important; line-height: 1.3 !important; }
            .voice-status.processing { color: #f59e0b !important; font-weight: 600 !important; }
            .voice-status.segmenting { color: #22c55e !important; font-weight: 600 !important; }

            .voice-status .progress-bar-container { width: 100% !important; height: 4px !important; background: rgba(255, 255, 255, 0.1) !important; border-radius: 2px !important; margin-top: 4px !important; overflow: hidden !important; }
            .voice-status .progress-bar { height: 100% !important; background: linear-gradient(90deg, #22c55e, #16a34a) !important; border-radius: 2px !important; transition: width 0.3s ease !important; min-width: 2px !important; }
            .voice-status.processing .progress-bar { background: linear-gradient(90deg, #f59e0b, #d97706) !important; }
            .voice-status.segmenting .progress-bar { background: linear-gradient(90deg, #22c55e, #059669) !important; box-shadow: 0 0 4px rgba(34, 197, 94, 0.3) !important; }

            .voice-loading, .voice-error { text-align: center !important; padding: 2rem !important; color: #d1d5db !important; }
            .voice-loading i, .voice-error i { font-size: 2rem !important; margin-bottom: 1rem !important; color: #a0522d !important; }

            .voice-info-footer { padding-top: 1rem !important; border-top: 1px solid rgba(255, 255, 255, 0.1) !important; font-size: 0.85rem !important; opacity: 0.8 !important; }

            .sample-play-btn { background: rgba(160, 82, 45, 0.1) !important; border: 1px solid rgba(160, 82, 45, 0.3) !important; color: #a0522d !important; padding: 6px 12px !important; border-radius: 6px !important; font-size: 0.8rem !important; cursor: pointer !important; transition: all 0.2s ease !important; display: flex !important; align-items: center !important; justify-content: center !important; gap: 4px !important; width: 100% !important; margin: 8px 0 4px 0 !important; min-height: 36px !important; }
            .sample-play-btn:hover:not(:disabled) { background: rgba(160, 82, 45, 0.2) !important; border-color: rgba(160, 82, 45, 0.5) !important; transform: translateY(-1px) !important; }
            .sample-play-btn:disabled { opacity: 0.7 !important; cursor: not-allowed !important; }
            .sample-play-btn.processing { background: rgba(251, 191, 36, 0.1) !important; border-color: rgba(251, 191, 36, 0.3) !important; color: #f59e0b !important; cursor: not-allowed !important; }
            .sample-play-btn.segmenting { background: rgba(34, 197, 94, 0.1) !important; border-color: rgba(34, 197, 94, 0.3) !important; color: #22c55e !important; cursor: not-allowed !important; flex-direction: column !important; padding: 8px 12px !important; gap: 2px !important; }

            .sample-play-btn .progress-details { font-size: 0.7rem !important; opacity: 0.8 !important; color: inherit !important; margin-top: 2px !important; }

            .voice-info-modal { position: fixed !important; top: 0 !important; left: 0 !important; width: 100% !important; height: 100% !important; background-color: rgba(0, 0, 0, 0.7) !important; z-index: 2002 !important; justify-content: center !important; align-items: center !important; backdrop-filter: blur(4px) !important; display: none !important; }
            .voice-info-modal.active { display: flex !important; }
            .voice-info-modal-content { background-color: #1f2937 !important; color: #f3f4f6 !important; border-radius: 12px !important; padding: 24px !important; width: 90% !important; max-width: 500px !important; max-height: 80vh !important; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5) !important; animation: modalSlideIn 0.3s ease !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; overflow-y: auto !important; }
            .voice-info-header { display: flex !important; align-items: center !important; justify-content: space-between !important; margin-bottom: 1rem !important; padding-bottom: 0.75rem !important; border-bottom: 1px solid rgba(255, 255, 255, 0.1) !important; }
            .voice-info-header h3 { margin: 0 !important; font-size: 1.3rem !important; color: white !important; font-weight: 600 !important; display: flex !important; align-items: center !important; gap: 8px !important; }
            .close-info-modal-btn { background: none !important; border: none !important; color: white !important; font-size: 1.2rem !important; cursor: pointer !important; padding: 4px !important; border-radius: 4px !important; transition: background-color 0.2s ease !important; }
            .close-info-modal-btn:hover { background: rgba(255, 255, 255, 0.1) !important; }
            .voice-info-body { font-size: 0.95rem !important; line-height: 1.6 !important; }
            .voice-info-section { margin-bottom: 1.5rem !important; }
            .voice-info-section h4 { margin: 0 0 0.5rem 0 !important; font-size: 1.1rem !important; color: #60a5fa !important; font-weight: 600 !important; display: flex !important; align-items: center !important; gap: 8px !important; }
            .voice-info-section p { margin: 0 0 0.5rem 0 !important; color: #d1d5db !important; }
            .voice-info-note { font-size: 0.85rem !important; color: #9ca3af !important; margin-left: 1rem !important; line-height: 1.7 !important; }

            @keyframes heartBeat { 0% { transform: scale(1); } 25% { transform: scale(1.2); } 50% { transform: scale(1); } 75% { transform: scale(1.1); } 100% { transform: scale(1); } }
            @keyframes segmentationPulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }
            @keyframes processingPulse { 0% { opacity: 1; } 50% { opacity: 0.8; } 100% { opacity: 1; } }

            @keyframes modalSlideIn { from { opacity: 0; transform: translateY(-30px) scale(0.95); } to { opacity: 1; transform: translateY(0) scale(1); } }
            @keyframes confirmationSlideIn { from { opacity: 0; transform: translateY(-40px) scale(0.9); } to { opacity: 1; transform: translateY(0) scale(1); } }

            @media (max-width: 768px) {
                .voice-grid { grid-template-columns: 1fr !important; }
                .voice-modal-content { max-width: 500px !important; }
                .sample-play-btn.segmenting { padding: 10px 8px !important; font-size: 0.8rem !important; }
                .sample-play-btn .progress-details { font-size: 0.65rem !important; }
                .voice-status .progress-bar-container { height: 3px !important; }
            }
        `;
    }

    getModalHTML() {
        return `
            <div id="voiceSelectionModal" class="voice-modal" style="display: none;">
                <div class="voice-modal-content">
                    <div class="voice-modal-header">
                        <div class="voice-modal-header-left">
                            <i class="fas fa-comments"></i>
                            <h2>Change Voice</h2>
                            <button type="button" class="voice-info-btn" id="voiceInfoBtn" title="How voice preferences work">
                                <i class="fas fa-info-circle"></i>
                            </button>
                        </div>
                        <button type="button" class="close-modal-btn" id="closeVoiceModal">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="voice-tabs">
                        <button class="voice-tab active" data-tab="available" id="availableTab">
                            Available to You
                            <span class="voice-tab-count" id="availableCount">0</span>
                        </button>
                        <button class="voice-tab" data-tab="all" id="allVoicesTab">
                            All Voices
                            <span class="voice-tab-count" id="allCount">0</span>
                        </button>
                    </div>
                    <div class="voice-modal-body">
                        <div id="voiceGridContainer">
                            <div class="voice-loading">
                                <i class="fas fa-spinner fa-spin"></i>
                                <p>Loading available voices...</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div id="voiceConfirmationModal" class="voice-confirmation-modal" style="display: none;">
                <div class="voice-confirmation-content">
                    <div class="confirmation-header">
                        <h3 id="confirmationTrackTitle">This will create a new voice for track</h3>
                        <p>Please listen to voice sample first</p>
                    </div>
                    <div class="voice-preview-section">
                        <div class="voice-preview-header">
                            <div class="voice-preview-info">
                                <h4 id="confirmationVoiceName">Voice Name</h4>
                                <p id="confirmationVoiceDesc">Voice Description</p>
                            </div>
                        </div>
                        <button class="confirmation-sample-btn" id="confirmationSampleBtn">
                            <i class="fas fa-play"></i>
                            <span>Play Voice Sample</span>
                        </button>
                    </div>
                    <div class="confirmation-actions">
                        <button class="confirmation-btn cancel" id="confirmationCancelBtn">Cancel</button>
                        <button class="confirmation-btn generate" id="confirmationGenerateBtn">Generate</button>
                    </div>
                </div>
            </div>

            <div id="voiceInfoModal" class="voice-info-modal" style="display: none;">
                <div class="voice-info-modal-content">
                    <div class="voice-info-header">
                        <h3><i class="fas fa-heart"></i> Voice Preferences</h3>
                        <button type="button" class="close-info-modal-btn" id="closeVoiceInfoModal">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="voice-info-body">
                        <div class="voice-info-section">
                            <h4><i class="fas fa-heart" style="color: #ef4444;"></i> Favorite Voice (Heart Icon)</h4>
                            <p>Click the heart icon to set a voice as your <strong>favorite</strong>. Your favorite voice will be used for <strong>all tracks</strong> where it's already cached.</p>
                            <p class="voice-info-note">• Only 1 favorite allowed at a time<br>• Automatically switches if the voice is already generated<br>• Falls back to track default if not cached</p>
                        </div>
                        <div class="voice-info-section">
                            <h4><i class="far fa-hand-pointer"></i> Track-Specific Selection</h4>
                            <p>Click a voice card (without clicking the heart) to use that voice for <strong>this track only</strong>.</p>
                            <p class="voice-info-note">• Overrides your favorite for this specific track<br>• Different tracks can have different voices</p>
                        </div>
                        <div class="voice-info-section">
                            <h4><i class="fas fa-info-circle"></i> Priority</h4>
                            <p>1. <strong>Favorite voice</strong> (if cached for this track)<br>2. <strong>Track-specific selection</strong><br>3. <strong>Track default voice</strong></p>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    setupModalEventListeners() {
        if (!this.modal) return;

        this.modal.addEventListener('click', (e) => { if (e.target === this.modal) this.closeVoiceModal(); });
        document.getElementById('closeVoiceModal')?.addEventListener('click', () => this.closeVoiceModal());
        document.getElementById('availableTab')?.addEventListener('click', () => this.switchTab('available'));
        document.getElementById('allVoicesTab')?.addEventListener('click', () => this.switchTab('all'));

        // Info button
        document.getElementById('voiceInfoBtn')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.showVoiceInfoModal();
        });

        // Info modal close
        document.getElementById('closeVoiceInfoModal')?.addEventListener('click', () => {
            this.hideVoiceInfoModal();
        });

        const infoModal = document.getElementById('voiceInfoModal');
        if (infoModal) {
            infoModal.addEventListener('click', (e) => {
                if (e.target === infoModal) this.hideVoiceInfoModal();
            });
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (this.modal?.classList.contains('active')) this.closeVoiceModal();
                if (infoModal?.classList.contains('active')) this.hideVoiceInfoModal();
            }
        });
    }

    setupConfirmationModalEventListeners() {
        document.getElementById('confirmationCancelBtn')?.addEventListener('click', () => {
            this.hideVoiceConfirmationModal();
        });

        document.getElementById('confirmationGenerateBtn')?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!this.confirmationState) {
                return;
            }
            const { voiceId } = this.confirmationState;

            this.hideVoiceConfirmationModal();

            if (this.modal && !this.modal.classList.contains('active')) {
                this.modal.classList.add('active');
                document.body.style.overflow = 'hidden';
            }
            this.startProgressRefresh();

            this.changeVoiceWithWordPrecision(voiceId);
        });

        document.getElementById('confirmationSampleBtn')?.addEventListener('click', (e) => {
            e.stopPropagation();
            if (this.confirmationState) {
                this.playConfirmationSample(this.confirmationState.voiceId);
            }
        });

        document.getElementById('voiceConfirmationModal')?.addEventListener('click', (e) => {
            if (e.target.id === 'voiceConfirmationModal') {
                this.hideVoiceConfirmationModal();
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const modal = document.getElementById('voiceConfirmationModal');
                if (modal?.classList.contains('active')) this.hideVoiceConfirmationModal();
            }
        });
    }

    openVoiceModal = async () => {
        if (!this.isEnabled) {
            this.player.showToast('Voice changing only available for TTS tracks', 'info');
            return;
        }

        const container = document.getElementById('voiceGridContainer');
        if (!this.modal || !container) return;

        this.modal.classList.add('active');
        document.body.style.overflow = 'hidden';
        container.innerHTML = '<div class="voice-loading"><i class="fas fa-spinner fa-spin"></i><p>Loading voices...</p></div>';

        try {
            // Refresh global favorite to ensure hearts are correct
            await this.fetchUserVoicePreference();
            await this.loadVoiceData();
            this.startProgressRefresh();
        } catch (error) {
            container.innerHTML = `<div class="voice-error"><i class="fas fa-exclamation-triangle"></i><p>Failed to load voices: ${error.message}</p></div>`;
        }
    };

    closeVoiceModal() {
        this.stopCurrentSample();
        this.stopProgressRefresh();

        if (this.modal) {
            this.modal.classList.remove('active');
            document.body.style.overflow = '';
        }
        this.hideVoiceConfirmationModal();
        this.isVoiceChanging = false;
    }

    switchTab(tab) {
        if (this.currentTab === tab) return;
        this.currentTab = tab;

        document.querySelectorAll('.voice-tab').forEach(el => el.classList.remove('active'));
        document.querySelector(`.voice-tab[data-tab="${tab}"]`)?.classList.add('active');

        this.renderVoiceGrid(this.lastTrackData, tab);
    }

    async loadVoiceData() {
        const trackId = this.player.currentTrackId;
        const [voicesRes, trackRes] = await Promise.all([
            fetch(`/api/voices/available?track_id=${encodeURIComponent(trackId)}`),
            fetch(`/api/tracks/${encodeURIComponent(trackId)}/voices`)
        ]);
        if (!voicesRes.ok || !trackRes.ok) throw new Error('Failed to fetch voice data');

        const voicesData = await voicesRes.json();
        const trackData = await trackRes.json();

        this.voicesWithTiers = voicesData.voices || [];
        this.generatedVoices = trackData.generated_voices || [];
        this.lastTrackData = trackData;

        this.renderVoiceGrid(trackData, this.currentTab);
    }

    renderVoiceGrid(trackData, activeTab = 'available') {
        const container = document.getElementById('voiceGridContainer');
        const currentVoice = this.currentVoice || trackData.default_voice || this.trackDefaultVoice;

        if (!this.voicesWithTiers.length) {
            container.innerHTML = '<div class="voice-error"><i class="fas fa-microphone-slash"></i><p>No voices available.</p></div>';
            this.updateTabCounts(0, 0);
            return;
        }

        const trackDefaultVoice = this.getTrackDefaultVoice();
        const availableVoices = this.voicesWithTiers.filter(v => v.has_access || v.voice_id === trackDefaultVoice);
        const filtered = activeTab === 'available' ? availableVoices : this.voicesWithTiers;

        if (!filtered.length) {
            const msg = activeTab === 'available'
                ? 'No voices available with your current subscription.'
                : 'No voices found in system.';
            container.innerHTML = `<div class="voice-error"><i class="fas fa-microphone-slash"></i><p>${msg}</p></div>`;
            this.updateTabCounts(availableVoices.length, this.voicesWithTiers.length);
            return;
        }

        const voiceGrid = filtered.map(v => this.createVoiceOption(v, currentVoice)).join('');
        container.innerHTML = `
            <div class="voice-grid">${voiceGrid}</div>
            <div class="voice-info-footer">
                <p><strong>Generated:</strong> ${this.generatedVoices.length} voices ready</p>
            </div>
        `;
        this.attachVoiceEventListeners(currentVoice);
        this.updateTabCounts(availableVoices.length, this.voicesWithTiers.length);
    }

    updateTabCounts(availableCount, totalCount) {
        const a = document.getElementById('availableCount');
        const b = document.getElementById('allCount');
        if (a) a.textContent = availableCount;
        if (b) b.textContent = totalCount;
    }

    createVoiceOption(voice, currentVoice) {
        const voiceName = voice.display_name || this.getVoiceDisplayName(voice.voice_id);
        const isCurrent = voice.voice_id === currentVoice;
        const isGenerated = this.generatedVoices.includes(voice.voice_id);
        const defaultVoice = this.getTrackDefaultVoice();
        const hasAccess = voice.has_access || (voice.voice_id === defaultVoice);
        const tierInfo = this.getTierInfo(voice);
        const isFavorite = this._cachedVoicePreference === voice.voice_id;

        const isCurrentlyProcessing = this.currentlyProcessingVoice === voice.voice_id;
        const trackId = this.player.currentTrackId;
        const progressKey = `${trackId}:${voice.voice_id}`;
        const cachedProgress = this.progressCache[progressKey];

        let statusText = this.getStatusText(hasAccess, isGenerated, isCurrentlyProcessing);
        let accessClass = hasAccess ? '' : 'no-access';
        let processingClass = '';
        let sampleButtonHtml = '';

        if (isCurrentlyProcessing && cachedProgress) {
            const pct = cachedProgress.percentage || 0;

            if (cachedProgress.phase === 'tts') {
                processingClass = 'processing';
                sampleButtonHtml = `<button class="sample-play-btn processing" disabled>
                    <i class="fas fa-spinner fa-spin"></i><span>Generating... ${pct.toFixed(0)}%</span>
                </button>`;
                statusText = `<div class="voice-status processing">
                    ${cachedProgress.message}
                    <div class="progress-bar-container"><div class="progress-bar" style="width:${pct}%"></div></div>
                </div>`;
            } else if (cachedProgress.phase === 'segmentation') {
                processingClass = 'segmenting';
                sampleButtonHtml = `<button class="sample-play-btn segmenting" disabled>
                    <i class="fas fa-spinner fa-spin"></i><span>Preparing... ${pct.toFixed(0)}%</span>
                    ${cachedProgress.submessage ? `<div class="progress-details">${cachedProgress.submessage}</div>` : ''}
                </button>`;
                statusText = `<div class="voice-status segmenting">
                    ${cachedProgress.message}
                    <div class="progress-bar-container"><div class="progress-bar" style="width:${pct}%"></div></div>
                </div>`;
            }
        } else if (isCurrentlyProcessing) {
            processingClass = 'processing';
            sampleButtonHtml = `<button class="sample-play-btn processing" disabled>
                <i class="fas fa-spinner fa-spin"></i><span>Starting...</span>
            </button>`;
        } else {
            sampleButtonHtml = `<button class="sample-play-btn" data-voice-id="${voice.voice_id}">
                <i class="fas fa-play"></i><span>Sample</span>
            </button>`;
        }

        const showDelete = this.isCreator && isGenerated && !isCurrentlyProcessing;
        const deleteButtonHtml = showDelete
            ? `<button class="voice-delete-btn" data-voice-id="${voice.voice_id}" title="Delete cached voice"><i class="fas fa-trash"></i></button>`
            : '';

        // Heart button for favoriting
        const heartIcon = isFavorite ? 'fas fa-heart' : 'far fa-heart';
        const heartTitle = isFavorite ? 'Unfavorite (remove global preference)' : 'Favorite (use for all tracks)';
        const heartButtonHtml = `<button class="voice-heart-btn ${isFavorite ? 'favorited' : ''}" data-voice-id="${voice.voice_id}" title="${heartTitle}"><i class="${heartIcon}"></i></button>`;

        return `
            <div class="voice-option ${isCurrent ? 'current' : ''} ${accessClass} ${processingClass}"
                 data-voice-id="${voice.voice_id}"
                 data-has-access="${hasAccess}"
                 data-is-processing="${isCurrentlyProcessing}">
                <div class="voice-option-header">
                    <div class="voice-name">${voiceName}</div>
                    <div class="voice-actions">
                        ${heartButtonHtml}
                        ${deleteButtonHtml}
                    </div>
                </div>
                <div class="voice-description">${voice.description || voice.gender || 'Voice'}, ${voice.language_code}</div>
                <div class="voice-tier ${tierInfo.class}">${tierInfo.label}</div>
                ${sampleButtonHtml}
                ${statusText}
            </div>
        `;
    }

    getTierInfo(voice) {
        const defaultVoice = this.getTrackDefaultVoice();
        if (voice.voice_id === defaultVoice) return { label: 'Default/Free', class: 'free' };

        const amt = voice.tier_amount || voice.amount_cents || 0;
        const label = voice.tier_label || 'Unknown';
        let klass = 'not-assigned';
        if (amt === 0) klass = 'tier-0';
        else if (amt <= 1900) klass = 'tier-1';
        else if (amt <= 2900) klass = 'tier-3';
        else if (amt <= 3900) klass = 'tier-4';
        else klass = 'tier-5';

        return { label, class: klass };
    }

    getStatusText(hasAccess, isGenerated, isProcessing = false) {
        if (!hasAccess) return '<div class="voice-status">Tier Required</div>';
        if (isProcessing) return '<div class="voice-status processing">Processing...</div>';
        if (!isGenerated) return '<div class="voice-status not-generated">🎤 Generate Audio</div>';
        return '<div class="voice-status generated-ready">✅ Generated - Ready to Switch</div>';
    }

    startProgressRefresh() {
        this.stopProgressRefresh();
        
        const refresh = async () => {
            if (!this.modal || !this.modal.classList.contains('active')) {
                this.stopProgressRefresh();
                return;
            }
            
            const trackId = this.player.currentTrackId;
            const voiceId = this.currentlyProcessingVoice;
            if (!trackId || !voiceId) return;

            const key = `${trackId}:${voiceId}`;
            const last = this.progressCache[key];
            if (last && Date.now() - last.lastUpdated < 2000) {
                this.progressRefreshInterval = setTimeout(refresh, this.getPollDelay(this.processingPhase, last.percentage || 0));
                return;
            }

            if (this.processingPhase === 'tts') await this.checkTTSProgress(trackId, voiceId);
            else if (this.processingPhase === 'segmentation') await this.checkSegmentProgress(trackId, voiceId);
            
            const updated = this.progressCache[key];
            const pct = updated?.percentage || 0;
            this.progressRefreshInterval = setTimeout(refresh, this.getPollDelay(this.processingPhase, pct));
        };

        this.progressRefreshInterval = setTimeout(refresh, 100);
    }

    stopProgressRefresh() {
        if (this.progressRefreshInterval) {
            clearTimeout(this.progressRefreshInterval);
            this.progressRefreshInterval = null;
        }
    }

    refreshModalIfOpen() {
        if (this.modal?.classList.contains('active')) this.loadVoiceData().catch(() => {});
    }

    attachVoiceEventListeners(currentVoice) {
        document.querySelectorAll('.voice-option').forEach(option => {
            const voiceId = option.dataset.voiceId;
            const hasAccess = option.dataset.hasAccess === 'true';
            const isProcessing = option.dataset.isProcessing === 'true';

            const canGenerate = hasAccess || this.isCreator || this.isTeam;

            option.addEventListener('click', (e) => {
                if (
                    e.target.closest('.voice-delete-btn') ||
                    e.target.closest('.voice-heart-btn') ||
                    e.target.closest('.sample-play-btn') ||
                    e.target.closest('.generate-voice-btn')
                ) return;

                if (isProcessing) {
                    this.player.showToast('This voice is currently being processed', 'info');
                    return;
                }
                if (voiceId === currentVoice) {
                    this.player.showToast('This voice is already selected', 'info');
                    return;
                }

                const isGenerated = this.generatedVoices.includes(voiceId);

                if (isGenerated) {
                    // Save as track-specific preference (not favorite) before switching
                    this.saveUserVoicePreference(voiceId, false).then(() => {
                        this.changeVoiceWithWordPrecision(voiceId);
                    });
                } else if (canGenerate) {
                    if (this.requireConfirmation) {
                        const info = this.getVoiceInfoFromOption(option);
                        this.showVoiceConfirmationModal(voiceId, info);
                    } else {
                        // Save as track-specific preference before generating
                        this.saveUserVoicePreference(voiceId, false).then(() => {
                            this.changeVoiceWithWordPrecision(voiceId);
                        });
                    }
                } else {
                    this.player.showToast('This voice requires a higher tier subscription', 'warning');
                }
            });

            const sampleBtn = option.querySelector('.sample-play-btn');
            if (sampleBtn && !sampleBtn.disabled) {
                sampleBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.playSample(voiceId, sampleBtn);
                });
            }

            const genBtn = option.querySelector('.generate-voice-btn');
            if (genBtn) {
                genBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (!canGenerate) {
                        this.player.showToast('This voice requires a higher tier subscription', 'warning');
                        return;
                    }
                    if (isProcessing) {
                        this.player.showToast('This voice is currently being processed', 'info');
                        return;
                    }
                    if (this.requireConfirmation) {
                        const info = this.getVoiceInfoFromOption(option);
                        this.showVoiceConfirmationModal(voiceId, info);
                    } else {
                        this.changeVoiceWithWordPrecision(voiceId);
                    }
                });
            }

            const deleteBtn = option.querySelector('.voice-delete-btn');
            if (deleteBtn) {
                deleteBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.deleteVoiceCache(voiceId, deleteBtn);
                });
            }

            const heartBtn = option.querySelector('.voice-heart-btn');
            if (heartBtn) {
                heartBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const isFavorited = heartBtn.classList.contains('favorited');
                    this.toggleFavorite(voiceId, !isFavorited);
                });
            }
        });
    }

    getVoiceInfoFromOption(option) {
        return {
            display_name: option.querySelector('.voice-name')?.textContent || 'Unknown Voice',
            description: option.querySelector('.voice-description')?.textContent || 'Voice',
            gender: 'Voice',
            language_code: 'en-US'
        };
    }

    async playSample(voiceId, buttonElement) {
        this.stopCurrentSample();

        const icon = buttonElement.querySelector('i');
        const span = buttonElement.querySelector('span');
        icon.className = 'fas fa-spinner fa-spin';
        span.textContent = 'Loading...';
        buttonElement.disabled = true;

        try {
            const audio = new Audio();
            this.currentSampleAudio = audio;

            const reset = () => {
                icon.className = 'fas fa-play';
                span.textContent = 'Sample';
                buttonElement.disabled = false;
                if (this.currentSampleAudio === audio) this.currentSampleAudio = null;
            };

            audio.addEventListener('canplay', () => {
                icon.className = 'fas fa-pause';
                span.textContent = 'Playing...';
            });
            audio.addEventListener('ended', reset);
            audio.addEventListener('error', () => {
                this.player.showToast('Failed to play voice sample', 'error');
                reset();
            });

            audio.src = `/api/voices/${encodeURIComponent(voiceId)}/sample?t=${Date.now()}`;
            await audio.play();
        } catch {
            this.player.showToast('Failed to play voice sample', 'error');
            icon.className = 'fas fa-play';
            span.textContent = 'Sample';
            buttonElement.disabled = false;
        }
    }

    stopCurrentSample() {
        if (this.currentSampleAudio) {
            this.currentSampleAudio.pause();
            this.currentSampleAudio.currentTime = 0;
            this.currentSampleAudio = null;
        }
        document.querySelectorAll('.sample-play-btn').forEach(btn => {
            const icon = btn.querySelector('i');
            const span = btn.querySelector('span');
            if (icon) icon.className = 'fas fa-play';
            if (span) span.textContent = 'Sample';
            btn.disabled = false;
        });
    }

    async deleteVoiceCache(voiceId, btn) {
        if (!this.isCreator) {
            this.player.showToast('Only creators can delete voice cache', 'warning');
            return;
        }
        const name = this.getVoiceDisplayName(voiceId);
        const confirmed = confirm(`Delete cached voice "${name}"?\n\nThis will remove the cached audio files. The voice can be regenerated if needed.`);
        if (!confirmed) return;

        const icon = btn.querySelector('i');
        const origClass = icon.className;
        icon.className = 'fas fa-spinner fa-spin';
        btn.disabled = true;
        btn.classList.add('deleting');

        try {
            const res = await fetch(`/api/tracks/${encodeURIComponent(this.player.currentTrackId)}/voice/${encodeURIComponent(voiceId)}/delete-cache`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}: Delete failed`);
            }
            this.generatedVoices = this.generatedVoices.filter(v => v !== voiceId);
            this.player.showToast(`Voice "${name}" cache deleted`, 'success');
            this.loadVoiceData().catch(() => {});
        } catch (e) {
            this.player.showToast(`Failed to delete voice cache: ${e.message}`, 'error');
        } finally {
            icon.className = origClass;
            btn.disabled = false;
            btn.classList.remove('deleting');
        }
    }

    showVoiceConfirmationModal(voiceId, voiceInfo) {
        const modal = document.getElementById('voiceConfirmationModal');
        if (!modal) return;

        this.confirmationState = { voiceId, voiceInfo, hasListenedToSample: false, sampleAudio: null };

        const trackTitle = this.player.trackMetadata?.title || 'this track';
        const titleEl = document.getElementById('confirmationTrackTitle');
        if (titleEl) titleEl.textContent = `This will create a new voice for track "${trackTitle}"`;

        const nameEl = document.getElementById('confirmationVoiceName');
        const descEl = document.getElementById('confirmationVoiceDesc');
        if (nameEl) nameEl.textContent = voiceInfo.display_name || this.getVoiceDisplayName(voiceId);
        if (descEl) descEl.textContent = voiceInfo.description || `${voiceInfo.gender || 'Voice'}, ${voiceInfo.language_code}`;

        const genBtn = document.getElementById('confirmationGenerateBtn');
        if (genBtn) {
            genBtn.disabled = false;
            genBtn.classList.add('force-enabled');
        }

        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    hideVoiceConfirmationModal() {
        const modal = document.getElementById('voiceConfirmationModal');
        if (!modal) return;
        this.stopConfirmationSample();
        modal.classList.remove('active');
        document.body.style.overflow = '';
        this.confirmationState = null;
    }

    showVoiceInfoModal() {
        const modal = document.getElementById('voiceInfoModal');
        if (!modal) return;

        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    hideVoiceInfoModal() {
        const modal = document.getElementById('voiceInfoModal');
        if (!modal) return;

        modal.classList.remove('active');
        document.body.style.overflow = '';
    }

    async playConfirmationSample(voiceId) {
        if (!this.confirmationState) return;

        const btn = document.getElementById('confirmationSampleBtn');
        if (!btn) return;

        this.stopConfirmationSample();

        const icon = btn.querySelector('i');
        const span = btn.querySelector('span');
        icon.className = 'fas fa-spinner fa-spin';
        span.textContent = 'Loading Sample...';
        btn.disabled = true;

        try {
            const audio = new Audio();
            this.confirmationState.sampleAudio = audio;

            const reset = () => {
                icon.className = 'fas fa-play';
                span.textContent = 'Play Voice Sample';
                btn.disabled = false;
                if (this.confirmationState?.sampleAudio === audio) this.confirmationState.sampleAudio = null;
            };

            audio.addEventListener('canplay', () => {
                icon.className = 'fas fa-pause';
                span.textContent = 'Playing Sample...';
            });
            audio.addEventListener('ended', () => {
                reset();
                this.player.showToast('Sample completed - you can now generate this voice', 'success', 2000);
            });
            audio.addEventListener('error', () => {
                this.player.showToast('Failed to play voice sample', 'error');
                reset();
            });

            const stop = () => {
                if (!audio.paused) {
                    audio.pause();
                    audio.currentTime = 0;
                    reset();
                }
            };
            btn.addEventListener('click', stop, { once: true });

            audio.src = `/api/voices/${encodeURIComponent(voiceId)}/sample?t=${Date.now()}`;
            await audio.play();
        } catch {
            this.player.showToast('Failed to play voice sample', 'error');
            icon.className = 'fas fa-play';
            span.textContent = 'Play Voice Sample';
            btn.disabled = false;
        }
    }

    stopConfirmationSample() {
        if (this.confirmationState?.sampleAudio) {
            this.confirmationState.sampleAudio.pause();
            this.confirmationState.sampleAudio.currentTime = 0;
            this.confirmationState.sampleAudio = null;
        }
        const btn = document.getElementById('confirmationSampleBtn');
        if (btn) {
            const icon = btn.querySelector('i');
            const span = btn.querySelector('span');
            if (icon) icon.className = 'fas fa-play';
            if (span) span.textContent = 'Play Voice Sample';
            btn.disabled = false;
        }
    }

    async changeVoiceWithWordPrecision(newVoiceId) {
        if (this.isVoiceChanging && this.activeProgressVoice !== newVoiceId) {
            this.stopProgressTracking();
        } else if (this.isVoiceChanging) {
            this.player.showToast('Voice change already in progress', 'warning');
            return;
        }
        this.isVoiceChanging = true;

        try {
            const voiceName = this.getVoiceDisplayName(newVoiceId);
            const currentWordIndex = await this.getCurrentWordForVoiceSwitch();
            const wasPlaying = !this.player.audio.paused;

            const response = await fetch(`/api/tracks/${encodeURIComponent(this.player.currentTrackId)}/voice/switch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_voice: newVoiceId })
            });

            if (response.status === 403) {
                const err = await response.json().catch(() => ({}));
                if (typeof showUpgradeModal === 'function') {
                    showUpgradeModal(err.detail || err.error?.message || 'This content requires a higher tier subscription');
                } else {
                    this.player.showToast(err.detail || 'Upgrade required', 'warning');
                }
                return;
            }
            if (response.status === 429) {
                const err = await response.json().catch(() => ({}));
                const msg =
                    (typeof err.detail === 'string' && err.detail) ||
                    err?.detail?.message || err?.message ||
                    'Maximum voices cached for this track. All voices are currently in use. Please wait.';
                this.player.showToast(msg, 'warning', 6000);
                return;
            }
            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${response.status}: Voice change failed`);
            }

            const result = await response.json();
            const backendSaysCached = !!result.cached;
            const locallyKnownCached = this.generatedVoices.includes(newVoiceId);

            if (backendSaysCached || locallyKnownCached) {
                this.closeVoiceModal();
                this.player.showToast(`Switching to ${voiceName}...`, 'info', 2000);
                await this.completeWordPreciseVoiceSwitch(newVoiceId, currentWordIndex, wasPlaying);
                this.player.showToast(`Switched to ${voiceName}`, 'success');
            } else {
                this.player.showToast(`Starting ${voiceName} generation...`, 'info', 3000);

                this.currentlyProcessingVoice = newVoiceId;
                this.processingPhase = 'tts';
                this.processingVoices.add(newVoiceId);

                this.pendingWordSwitch = { targetVoice: newVoiceId, wordIndex: currentWordIndex, wasPlaying };

                if (this.modal?.classList.contains('active')) {
                    this.startProgressRefresh();
                    this.refreshModalIfOpen();
                }

                this.startProgressTracking(this.player.currentTrackId, newVoiceId, 'tts');
            }
        } catch (error) {
            this.stopProgressTracking();
            this.hideProgressOverlay();
            this.completeVoiceProcessing(newVoiceId);
            this.refreshModalIfOpen();
            this.player.showToast(`Voice change failed: ${error.message}`, 'error');
            this.pendingWordSwitch = null;
        } finally {
            this.isVoiceChanging = false;
        }
    }

    async completeWordPreciseVoiceSwitch(newVoiceId, targetWordIndex = null, shouldPlay = false) {
        this.currentVoice = newVoiceId;
        this.player.currentVoice = newVoiceId;
        if (this.player.trackMetadata) this.player.trackMetadata.voice = newVoiceId;

        this.updateVoiceButton();
        // Don't save here - preference is already saved in click handler

        if (window.trackData) window.trackData.current_voice = newVoiceId;
        window.currentVoice = newVoiceId;
        this.notifyVoiceChanged(newVoiceId);

        const timings = await this.loadWordTimings(this.player.currentTrackId, newVoiceId, true);
        let targetTime = null;

        if (targetWordIndex !== null && targetWordIndex >= 0 && timings?.length > 0) {
            targetTime = targetWordIndex < timings.length ? timings[targetWordIndex].start_time : this.player.audio.currentTime;
        }

        await this.delegateStreamReinitializationToPlayer(newVoiceId, targetTime, shouldPlay);
    }

    async delegateStreamReinitializationToPlayer(newVoice, targetTime = null, shouldPlay = false) {
        if (!this.isEnabled || !this.player.currentTrackId) return;

        try {
            if (this.player.progress) await this.player.progress.saveBeforeReinit();

            const currentPosition = (targetTime !== null) ? targetTime : this.player.audio.currentTime;
            const speed = this.player.audio.playbackRate;

            this.currentVoice = newVoice;
            // Don't save here - preference is already saved in click handler

            await this.player.initializeTrackForPlayback();

            const finish = async () => {
                this.player.audio.playbackRate = speed;
                if (currentPosition > 0) this.player.audio.currentTime = currentPosition;
                if (shouldPlay) await this.player.audio.play().catch(() => {});
            };

            if (this.player.audio.readyState >= 1) await finish();
            else {
                await new Promise((resolve) => {
                    const onLoaded = async () => {
                        this.player.audio.removeEventListener('loadedmetadata', onLoaded);
                        await finish();
                        resolve();
                    };
                    this.player.audio.addEventListener('loadedmetadata', onLoaded);
                });
            }

            this.player.recoveryAttempts = 0;
            if (this.player.progress) this.player.progress.resumeSync();
            this.updateVoiceButton();
        } catch (err) {
            if (this.player.reinitializeStream) {
                try { await this.player.reinitializeStream(); } catch (_) {}
            }
            throw err;
        }
    }

    async completeVoiceSwitch(newVoiceId) {
        if (this.pendingWordSwitch?.targetVoice === newVoiceId) {
            const { wordIndex, wasPlaying } = this.pendingWordSwitch;
            this.pendingWordSwitch = null;
            await this.completeWordPreciseVoiceSwitch(newVoiceId, wordIndex, wasPlaying);
        } else {
            const idx = await this.getCurrentWordForVoiceSwitch();
            const wasPlaying = !this.player.audio.paused;
            await this.completeWordPreciseVoiceSwitch(newVoiceId, idx, wasPlaying);
        }
    }

    updateVoiceButton() {
        const voiceBtn = document.getElementById('voiceChangeBtn');
        const voiceDisplay = document.getElementById('currentVoiceDisplay');
        if (!voiceBtn) return;

        if (this.isEnabled) {
            voiceBtn.style.display = 'flex';
            if (voiceDisplay && this.currentVoice) voiceDisplay.textContent = `Voice: ${this.getVoiceDisplayName(this.currentVoice)}`;
            else if (voiceDisplay) voiceDisplay.textContent = 'Voice: Default';
        } else {
            voiceBtn.style.display = 'none';
        }
    }

    getVoiceDisplayName(voiceId) {
        if (!voiceId) return 'Default';
        return voiceId.replace(/^en-(US|GB)-/, '').replace('Neural', '');
    }

    async loadGeneratedVoices(trackId) {
        try {
            const res = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/voices`);
            if (res.ok) {
                const data = await res.json();
                this.generatedVoices = data.generated_voices || [];
                // ✅ OPTIMIZATION: Don't preload word timings for all voices
                // They will be loaded on-demand when user switches voices
                // This eliminates unnecessary API calls on page load
            }
        } catch (_) {}
    }

    isVoiceChangingSupported() { return this.isEnabled; }
    getCurrentVoice() { return this.isEnabled ? (this.currentVoice || this.trackDefaultVoice) : null; }
    getAvailableVoices() { return this.voicesWithTiers.map(v => v.voice_id); }
    getGeneratedVoices() { return this.generatedVoices; }

    // WebSocket Methods for Real-time TTS Updates

    subscribeToVoiceUpdates(trackId, voiceId) {
        if (!this.ttsChannel) return;

        const key = `${trackId}:${voiceId}`;
        if (!this.channelSubscriptions.has(key)) {
            this.channelSubscriptions.set(key, { trackId, voiceId });
        }

        try {
            this.ttsChannel.subscribe(trackId, voiceId);
        } catch (error) {
        }
    }

    unsubscribeFromVoiceUpdates(trackId, voiceId) {
        if (!this.ttsChannel) return;

        const key = `${trackId}:${voiceId}`;
        if (this.channelSubscriptions.has(key)) {
            this.channelSubscriptions.delete(key);
        }

        try {
            this.ttsChannel.unsubscribe(trackId, voiceId);
        } catch (error) {
        }
    }

    handleTTSStatusEvent(event) {
        const data = event.detail;
        if (!data) return;

        const { type, track_id: trackId, voice_id: voiceId } = data;

        if (trackId !== this.player.currentTrackId) return;
        if (this.currentlyProcessingVoice && voiceId !== this.currentlyProcessingVoice) return;

        switch (type) {
            case 'tts_progress':
                this.handleTTSProgressUpdate(data);
                break;
            case 'segmentation_progress':
                this.handleSegmentationProgressUpdate(data);
                break;
            case 'generation_complete':
                this.handleGenerationComplete(data);
                break;
            case 'error':
                break;
            default:
                break;
        }
    }

    handleChannelConnected() {
        if (!this.ttsChannel) return;
        this.useWebSocketUpdates = true;
        if (this.progressPolling) {
            clearTimeout(this.progressPolling);
            this.progressPolling = null;
        }
        if (this.currentlyProcessingVoice && this.player.currentTrackId) {
            this.subscribeToVoiceUpdates(this.player.currentTrackId, this.currentlyProcessingVoice);
        }
    }

    handleChannelDisconnected() {
        if (!this.ttsChannel) return;
        if (!this.useWebSocketUpdates) return;

        this.useWebSocketUpdates = false;
        if (this.currentlyProcessingVoice && this.player.currentTrackId && !this.progressPolling) {
            this.startProgressTracking(
                this.player.currentTrackId,
                this.currentlyProcessingVoice,
                this.processingPhase || 'tts',
                { forcePolling: true, skipStop: true }
            );
        }
    }

    handleTTSProgressUpdate(data) {
        const { track_id, voice_id, progress, phase, message, chunks_completed, total_chunks, status } = data;

        const progressKey = `${track_id}:${voice_id}`;
        const percentage = Number(progress || 0) || 0;

        this.progressCache[progressKey] = {
            phase: 'tts',
            percentage,
            message: `${message} ${percentage.toFixed(0)}%`,
            status,
            lastUpdated: Date.now()
        };

        this.updateProgressOverlay({
            title: `Generating Voice: ${this.getVoiceDisplayName(voice_id)}`,
            message: `${message} (${percentage.toFixed(0)}%)`,
            current: chunks_completed || 0,
            total: total_chunks || 0,
            percentage,
            phase: 'tts'
        });

        if (status === 'complete' || status === 'segmentation_ready') {
            this.processingPhase = 'segmentation';
            this.currentProgressPhase = 'segmentation';
            this.awaitingSegmentationVoice = voice_id;
            // No need to restart tracking, WebSocket will continue sending updates
        } else if (status === 'error') {
            this.failVoiceProcessing(voice_id, message || 'Unknown error');
        }

        this.refreshModalIfOpen();
    }

    handleSegmentationProgressUpdate(data) {
        const { track_id, voice_id, progress, segments_completed, total_segments, message, status } = data;

        const progressKey = `${track_id}:${voice_id}`;
        const percentage = Number(progress || 0) || 0;
        const normalizedStatus = status || 'segmenting';

        this.progressCache[progressKey] = {
            phase: 'segmentation',
            percentage,
            message: `Segmenting... ${percentage.toFixed(0)}%`,
            status: normalizedStatus,
            lastUpdated: Date.now()
        };

        this.updateProgressOverlay({
            title: `Segmenting Voice: ${this.getVoiceDisplayName(voice_id)}`,
            message: `${message} (${percentage.toFixed(0)}%)`,
            current: segments_completed || 0,
            total: total_segments || 0,
            percentage,
            phase: 'segmentation'
        });

        const doneByStatus = normalizedStatus === 'segmentation_complete' || normalizedStatus === 'complete';
        const doneByPct = percentage >= 99.9;
        const doneByCounts = total_segments > 0 && segments_completed >= total_segments;

        if (doneByStatus || doneByPct || doneByCounts) {
            this.awaitingSegmentationVoice = null;
            this.onVoiceReady(track_id, voice_id);
            return;
        }

        if (normalizedStatus === 'error') {
            this.awaitingSegmentationVoice = null;
            this.failVoiceProcessing(voice_id, message || 'Voice preparation failed');
            return;
        }

        this.refreshModalIfOpen();
    }

    handleGenerationComplete(data) {
        const { track_id, voice_id, success } = data;

        if (success) {
            this.awaitingSegmentationVoice = voice_id;
            this.processingPhase = 'segmentation';
            this.currentProgressPhase = 'segmentation';

            if (this.isSegmentationComplete(track_id, voice_id)) {
                this.awaitingSegmentationVoice = null;
                this.onVoiceReady(track_id, voice_id);
            } else {
                this.refreshModalIfOpen();
            }
        } else {
            this.awaitingSegmentationVoice = null;
            this.failVoiceProcessing(voice_id, 'Generation failed');
            return;
        }
    }

    isSegmentationComplete(trackId, voiceId) {
        const progressKey = `${trackId}:${voiceId}`;
        const cached = this.progressCache[progressKey];
        if (!cached) return false;

        if (cached.status === 'segmentation_complete' || cached.status === 'complete') return true;
        return cached.phase === 'segmentation' && (cached.percentage ?? 0) >= 99.9;
    }

    destroy() {
        if (this.ttsChannel) {
            window.removeEventListener('ttsStatusUpdate', this.handleTTSStatusEvent);
            window.removeEventListener('ttsWebSocketConnected', this.handleChannelConnected);
            window.removeEventListener('ttsWebSocketDisconnected', this.handleChannelDisconnected);

            this.channelSubscriptions.forEach(({ trackId, voiceId }) => {
                try {
                    this.ttsChannel.unsubscribe(trackId, voiceId);
                } catch (_) {}
            });
            this.channelSubscriptions.clear();
        }

        this.stopProgressTracking();
        this.stopCurrentSample();
        this.stopConfirmationSample();
        this.stopProgressRefresh();

        this.progressCache = {};
        this.currentlyProcessingVoice = null;
        this.processingPhase = null;
        this.processingVoices.clear();
        this.wordTimingsCache = {};
        this.pendingWordSwitch = null;
        this.confirmationState = null;

        if (this.modal) { this.modal.remove(); this.modal = null; }
        document.getElementById('voiceConfirmationModal')?.remove();
        document.getElementById('voiceModalStyles')?.remove();

        Object.assign(this, {
            isEnabled: false,
            currentVoice: null,
            trackType: 'audio',
            isVoiceChanging: false,
            trackDefaultVoice: null,
            voicesWithTiers: [],
            generatedVoices: [],
            currentProgressPhase: null,
            activeProgressVoice: null,
            currentSampleAudio: null,
            confirmationState: null,
            currentTab: 'available',
            lastTrackData: null
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const boot = () => {
        if (window.persistentPlayer) {
            if (!window.persistentPlayer.voiceExtension) {
                window.persistentPlayer.voiceExtension = new VoiceExtension(window.persistentPlayer);
                window.voiceExtension = window.persistentPlayer.voiceExtension;
            }
        } else {
            setTimeout(boot, 100);
        }
    };
    boot();
});
