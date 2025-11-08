if (typeof TTSManager === 'undefined') {
class TTSManager {
    constructor(albumDetails) {
        this.albumDetails = albumDetails;
        this.albumId = albumDetails.albumId;
        this.ttsConfig = null;
        this.configLoaded = false;
        this.ttsChannel = window.TTSStatusChannel || null;
        this.activeJobs = new Map();
        this.realtimeListenersAttached = false;
        this.handleStatusUpdate = this.handleStatusUpdate.bind(this);
        this.handleChannelConnected = this.handleChannelConnected.bind(this);
        this.handleChannelDisconnected = this.handleChannelDisconnected.bind(this);
        
        this.initializeDOMElements();
        this.initializeEventListeners();
        this.fetchTTSConfig();
        this.initializeBulkTTSControls();
        this.addStyles();
        this.initializeRealtimeUpdates();
    }

    initializeDOMElements() {
        this.ttsModal = document.getElementById('ttsModal');
        this.ttsForm = document.getElementById('ttsForm');
        this.ttsBtn = document.querySelector('.create-tts-btn');
        this.ttsTitle = document.getElementById('ttsTitle');
        this.ttsText = document.getElementById('ttsText');
        this.ttsVoice = document.getElementById('ttsVoice');
        this.initializeTitleCounter();
        this.ensureBulkTTSElements();
        this.ttsProgress = document.getElementById('ttsProgress');
        this.ttsProgressBar = this.ttsProgress?.querySelector('.progress-fill');
        this.ttsProgressText = this.ttsProgress?.querySelector('.progress-text');
    }

    initializeTitleCounter() {
        const titleInput = this.ttsTitle;
        const counterElement = document.getElementById('ttsTitleCounter');
        const currentCountSpan = counterElement?.querySelector('.current-count');
        
        if (!titleInput || !counterElement || !currentCountSpan) return;
        
        const updateCounter = () => {
            const currentLength = titleInput.value.length;
            currentCountSpan.textContent = currentLength;
            
            if (currentLength >= 75) {
                counterElement.style.color = '#f59e0b';
            } else if (currentLength >= 80) {
                counterElement.style.color = '#ef4444';
            } else {
                counterElement.style.color = '';
            }
        };
        
        titleInput.addEventListener('input', updateCounter);
        updateCounter();
    }

    async fetchTTSConfig() {
        try {
            const response = await fetch('/api/tts/config');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            this.ttsConfig = await response.json();
            this.configLoaded = true;
            this.updateCharacterLimitDisplay();
            this.initializeCharacterCounter();
        } catch (error) {
            console.error('Failed to fetch TTS config:', error);
            this.albumDetails.showToast('Failed to load TTS configuration. Please refresh the page.', 'error');
            
            const submitBtn = this.ttsForm?.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.title = 'Configuration not loaded';
            }
        }
    }

    updateCharacterLimitDisplay() {
        const limitCountSpan = document.querySelector('#ttsCharCounter .limit-count');
        if (limitCountSpan && this.ttsConfig) {
            limitCountSpan.textContent = this.ttsConfig.max_characters.toLocaleString();
            limitCountSpan.classList.remove('loading');
        }
    }

    initializeCharacterCounter() {
        const textarea = this.ttsText;
        const counterElement = document.getElementById('ttsCharCounter');
        const currentCountSpan = counterElement?.querySelector('.current-count');
        
        if (!textarea || !counterElement || !currentCountSpan || !this.ttsConfig) return;
        
        const updateCounter = () => {
            const currentLength = textarea.value.length;
            currentCountSpan.textContent = currentLength.toLocaleString();
            
            if (currentLength > this.ttsConfig.max_characters) {
                counterElement.classList.add('over-limit');
            } else {
                counterElement.classList.remove('over-limit');
            }
        };
        
        textarea.addEventListener('input', updateCounter);
        updateCounter();
    }

    ensureBulkTTSElements() {
        if (document.getElementById('enableBulkGeneration')) return;
        
        const ttsForm = this.ttsForm;
        if (!ttsForm) return;
        
        const voiceSelect = ttsForm.querySelector('#ttsVoice');
        if (!voiceSelect) return;
        
        const bulkControlsHTML = `
            <div class="bulk-options" style="margin-top: 1rem; padding: 1rem; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; background: rgba(255,255,255,0.02);">
                <label class="bulk-enable-label" style="display: flex; align-items: center; gap: 8px; margin-bottom: 1rem; cursor: pointer;">
                    <input type="checkbox" id="enableBulkGeneration" style="margin: 0;">
                    <span style="font-weight: 500;">Enable Bulk Generation</span>
                    <span style="font-size: 0.8rem; opacity: 0.7;">(for large content like audiobooks)</span>
                </label>
                
                <div id="bulkControls" style="display: none;">
                    <div style="margin-bottom: 1rem;">
                        <label for="bulkSplitCount" style="display: block; margin-bottom: 4px; font-size: 0.9rem;">Split into how many tracks:</label>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <input type="number" id="bulkSplitCount" min="2" max="20" value="3" 
                                   style="width: 80px; padding: 6px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.3); background: rgba(255,255,255,0.1); color: inherit;">
                            <span style="font-size: 0.8rem; opacity: 0.7;">tracks (2-20)</span>
                        </div>
                    </div>
                    
                    <div style="margin-bottom: 1rem;">
                        <label for="bulkSeriesTitle" style="display: block; margin-bottom: 4px; font-size: 0.9rem;">Series title:</label>
                        <input type="text" id="bulkSeriesTitle" placeholder="e.g., 'The Great Gatsby' or 'Chapter Series'" 
                               style="width: 100%; padding: 8px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.3); background: rgba(255,255,255,0.1); color: inherit;">
                        <div style="font-size: 0.75rem; opacity: 0.6; margin-top: 2px;">Parts will be named: "{Series Title} - Part 1 of X", "Part 2 of X", etc.</div>
                    </div>
                    
                    <div class="bulk-info" style="font-size: 0.85rem; color: var(--text-2); margin-top: 8px; padding: 8px; background: rgba(0,0,0,0.2); border-radius: 4px;">
                        <div id="textAnalysis"></div>
                    </div>
                </div>
            </div>
        `;
        
        voiceSelect.parentNode.insertAdjacentHTML('afterend', bulkControlsHTML);
    }

    initializeBulkTTSControls() {
        const enableBulkCheckbox = document.getElementById('enableBulkGeneration');
        const bulkControls = document.getElementById('bulkControls');
        const textArea = this.ttsText;
        
        if (!enableBulkCheckbox || !bulkControls) return;
        
        enableBulkCheckbox.addEventListener('change', (e) => {
            bulkControls.style.display = e.target.checked ? 'block' : 'none';
            this.updateTextAnalysis();
            
            if (e.target.checked) {
                const seriesTitleInput = document.getElementById('bulkSeriesTitle');
                const mainTitle = this.ttsTitle?.value?.trim();
                if (seriesTitleInput && !seriesTitleInput.value && mainTitle) {
                    seriesTitleInput.value = mainTitle;
                }
            }
        });
        
        textArea?.addEventListener('input', () => {
            this.updateTextAnalysis();
        });
        
        document.getElementById('bulkSplitCount')?.addEventListener('input', () => {
            this.updateTextAnalysis();
        });
        
        this.ttsTitle?.addEventListener('input', () => {
            const enableBulk = document.getElementById('enableBulkGeneration')?.checked;
            const seriesTitle = document.getElementById('bulkSeriesTitle');
            if (enableBulk && seriesTitle && !seriesTitle.value) {
                seriesTitle.value = this.ttsTitle.value;
            }
        });
    }

    updateTextAnalysis() {
        const text = this.ttsText?.value || '';
        const enableBulk = document.getElementById('enableBulkGeneration')?.checked;
        const splitCount = parseInt(document.getElementById('bulkSplitCount')?.value, 10) || 3;
        const analysisDiv = document.getElementById('textAnalysis');
        if (!analysisDiv || !enableBulk) return;

        const charCount = text.length;
        const wordCount = text.split(/\s+/).filter(word => word.length > 0).length;

        // Use config WPM if available, else fallback to 180 WPM
        const WPM = this.ttsConfig?.wpm_estimate || 180;

        // Seconds estimates
        const totalSeconds = Math.round((wordCount / WPM) * 60);
        const perTrackSeconds = Math.max(1, Math.round(totalSeconds / Math.max(1, splitCount)));

        const charsPerTrack = Math.round(charCount / splitCount);
        const wordsPerTrack = Math.round(wordCount / splitCount);

        let analysisHTML = `
            <div style="margin-bottom: 8px;"><strong>Text Analysis:</strong></div>
            <div>• Total: ${charCount.toLocaleString()} characters, ${wordCount.toLocaleString()} words</div>
            <div>• Estimated duration: ~${this.formatHMS(totalSeconds)}</div>
            <div>• Per track: ~${charsPerTrack.toLocaleString()} chars, ${wordsPerTrack.toLocaleString()} words (~${this.formatHMS(perTrackSeconds)} each)</div>
        `;

        // Guidance (thresholds converted to seconds)
        if (charCount > 2_000_000) {
            analysisHTML += '<div style="color: #ef4444; margin-top: 4px;">⚠️ Exceeds 2M character limit - please reduce text</div>';
        } else if (charCount < 10_000) {
            analysisHTML += '<div style="color: #f59e0b; margin-top: 4px;">⚠️ Minimum 10k characters recommended for bulk generation</div>';
        } else if (perTrackSeconds > 6 * 3600) { // > 6 hours
            analysisHTML += '<div style="color: #f59e0b; margin-top: 4px;">⚠️ Each track will be over 6 hours - consider more splits</div>';
        } else if (perTrackSeconds < 30 * 60) { // < 30 minutes
            analysisHTML += '<div style="color: #f59e0b; margin-top: 4px;">⚠️ Each track will be under 30 minutes - consider fewer splits</div>';
        } else {
            analysisHTML += '<div style="color: #22c55e; margin-top: 4px;">✓ Good split size for bulk generation</div>';
        }

        analysisDiv.innerHTML = analysisHTML;
    }

    initializeEventListeners() {
        this.ttsForm?.addEventListener('submit', async (e) => {
            e.preventDefault();
            await this.createTTSTrack();
        });

        this.ttsBtn?.addEventListener('click', (e) => {
            e.preventDefault();
            this.openTTSModal();
        });

        document.addEventListener('click', (e) => {
            if (e.target.closest('[data-action="open-tts-modal"]')) {
                e.preventDefault();
                this.openTTSModal();
            }
        }, true);

        this.ttsModal?.addEventListener('click', (e) => {
            if (e.target === this.ttsModal) this.closeTTSModal();
        });
    }

    openTTSModal() {
        if (this.ttsModal) {
            this.ttsModal.classList.add('active');
            this.ttsModal.style.display = 'flex';
            document.body.style.overflow = 'hidden';
            this.ttsForm?.reset();

            const enableBulk = document.getElementById('enableBulkGeneration');
            const bulkControls = document.getElementById('bulkControls');
            if (enableBulk) enableBulk.checked = false;
            if (bulkControls) bulkControls.style.display = 'none';

            setTimeout(() => this.ttsTitle?.focus(), 100);

            // Debug: Log modal and dropdown dimensions
            setTimeout(() => {
                const modalContent = this.ttsModal.querySelector('.modal-content');
                const dropdown = this.ttsModal.querySelector('.visibility-select');
                const formGroup = this.ttsModal.querySelector('.form-group');
                const isMobile = window.innerWidth <= 768;

                console.log('🔍 [TTS Modal] Dimensions:');
                console.log('  Window width:', window.innerWidth, 'px');
                console.log('  Is mobile:', isMobile);
                console.log('  Modal content width:', modalContent?.offsetWidth, 'px');
                console.log('  Form group width:', formGroup?.offsetWidth, 'px');
                console.log('  Dropdown width:', dropdown?.offsetWidth, 'px');

                if (dropdown) {
                    const styles = window.getComputedStyle(dropdown);
                    console.log('  Dropdown styles:');
                    console.log('    - width:', styles.width);
                    console.log('    - max-width:', styles.maxWidth);
                    console.log('    - box-sizing:', styles.boxSizing);
                    console.log('    - font-size:', styles.fontSize);
                    console.log('    - appearance:', styles.appearance || styles.webkitAppearance);
                    console.log('    - padding:', styles.padding);
                }
            }, 100);
        }
    }

    closeTTSModal() {
        if (this.ttsModal) {
            this.ttsModal.classList.remove('active');
            this.ttsModal.style.display = 'none';
            document.body.style.overflow = '';
            this.ttsForm?.reset();
        }
    }


    formatHMS(seconds) {
        seconds = Math.max(0, Math.floor(Number(seconds) || 0));
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = seconds % 60;
        const pad = (n) => String(n).padStart(2, '0');
        return `${pad(h)}:${pad(m)}:${pad(s)}`;
    }
    async createTTSTrack() {
        if (!this.configLoaded || !this.ttsConfig) {
            this.albumDetails.showToast('TTS configuration not loaded. Please refresh the page.', 'error');
            return;
        }

        const detectAlbumId = () => {
            if (this.albumDetails?.albumId) return this.albumDetails.albumId;
            if (window.albumId) return window.albumId;

            const elWithData = document.querySelector('[data-album-id]');
            if (elWithData?.dataset?.albumId) return elWithData.dataset.albumId;

            const hiddenInput = document.getElementById('albumId');
            if (hiddenInput?.value) return hiddenInput.value;

            const qs = new URLSearchParams(location.search);
            const qId = qs.get('album_id') || qs.get('albumId');
            if (qId) return qId;

            const m = (location.pathname || '').match(/\/(?:albums?|a)\/([^\/?#]+)/i);
            if (m && m[1]) return decodeURIComponent(m[1]);

            return null;
        };

        const albumId = this.albumId || detectAlbumId();
        if (!albumId) {
            this.albumDetails?.showToast?.('Album not found. Please refresh the page.', 'error');
            return;
        }
        this.albumId = albumId;
        if (!window.albumId) window.albumId = albumId;

        const title = this.ttsTitle?.value?.trim();
        const text = this.ttsText?.value?.trim();
        const voice = this.ttsVoice?.value || 'en-US-AvaNeural';

        const bulkCheckbox = document.getElementById('enableBulkGeneration');
        const bulkSplitInput = document.getElementById('bulkSplitCount');
        const bulkTitleInput = document.getElementById('bulkSeriesTitle');

        const enableBulk = !!bulkCheckbox?.checked;
        const bulkSplitCount = parseInt(bulkSplitInput?.value, 10) || 1;
        const bulkSeriesTitle = bulkTitleInput?.value?.trim();

        // Validation
        if (!title) {
            this.albumDetails.showToast('Please enter a track title');
            this.ttsTitle?.focus();
            return;
        }

        if (!text || text.length < this.ttsConfig.min_characters) {
            this.albumDetails.showToast(
                `Text must be at least ${this.ttsConfig.min_characters} characters long`
            );
            this.ttsText?.focus();
            return;
        }

        if (text.length > this.ttsConfig.max_characters) {
            const excess = text.length - this.ttsConfig.max_characters;
            this.albumDetails.showToast(
                `Text exceeds limit by ${excess.toLocaleString()} characters. Maximum is ${this.ttsConfig.max_characters.toLocaleString()}.`,
                'error',
                6000
            );
            this.ttsText?.focus();
            return;
        }

        if (enableBulk) {
            if (bulkSplitCount < this.ttsConfig.min_bulk_split || bulkSplitCount > this.ttsConfig.max_bulk_split) {
                this.albumDetails.showToast(
                    `Split count must be between ${this.ttsConfig.min_bulk_split} and ${this.ttsConfig.max_bulk_split}`
                );
                bulkSplitInput?.focus();
                return;
            }

            if (!bulkSeriesTitle) {
                this.albumDetails.showToast('Please enter a series title for bulk generation');
                bulkTitleInput?.focus();
                return;
            }

            if (text.length < this.ttsConfig.min_bulk_characters) {
                this.albumDetails.showToast(
                    `Text must be at least ${this.ttsConfig.min_bulk_characters.toLocaleString()} characters for bulk generation`
                );
                this.ttsText?.focus();
                return;
            }
        }

        const submitButton = this.ttsForm.querySelector('button[type="submit"]');
        const originalText = submitButton.textContent;

        try {
            submitButton.disabled = true;
            submitButton.innerHTML = enableBulk
                ? '<i class="fas fa-spinner fa-spin"></i> Creating Bulk Tracks...'
                : '<i class="fas fa-spinner fa-spin"></i> Creating Track...';

            const requestBody = { title, text, voice };
            if (enableBulk && bulkSplitCount > 1) {
                requestBody.bulk_split_count = bulkSplitCount;
                requestBody.bulk_series_title = bulkSeriesTitle;
            }

            const response = await fetch(`/api/albums/${encodeURIComponent(albumId)}/tracks/create-tts`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                let data = null, bodyText = '';
                try { data = await response.clone().json(); } catch {}
                if (!data) { try { bodyText = await response.text(); } catch {} }

                const pickMsg = () => {
                    if (typeof data?.detail === 'string') return data.detail;
                    if (Array.isArray(data?.detail) && data.detail[0]?.msg) return data.detail[0].msg;
                    if (data?.message) return data.message;
                    if (response.status === 404) return 'Album not found or you do not have access.';
                    return bodyText || response.statusText || 'Request failed';
                };

                const findLimit = (s) => {
                    if (!s || typeof s !== 'string') return null;
                    const m = s.match(/(?:at\s*most|maximum|limit(?:\s*of)?)\s*([\d,._]+)\s*(?:characters|chars)?/i);
                    return m ? Number(m[1].replace(/[^\d]/g, '')) : null;
                };

                const msgRaw = pickMsg();
                const limit = findLimit(msgRaw) ?? findLimit(bodyText);
                const current = (this.ttsText?.value || '').length;

                let msg = msgRaw;
                if (limit && current > limit) {
                    const diff = current - limit;
                    msg = `Your text is ${current.toLocaleString()} characters, which exceeds the server limit of ${limit.toLocaleString()} by ${diff.toLocaleString()}.`;
                }

                this.albumDetails.showToast(msg, limit ? 'warning' : 'error', 6000);
                throw new Error(msg);
            }

            const result = await response.json();
            console.log('TTS Creation Result:', result);


            // Close modal immediately
            this.closeTTSModal();

            // Add track(s) to UI
            if (result.status === 'bulk_queued') {
                const totalTracks = result.total_tracks;

                console.log('Bulk result.tracks:', result.tracks); // 🔍 DEBUG
                console.log('Is array?', Array.isArray(result.tracks)); // 🔍 DEBUG
                console.log('Length:', result.tracks?.length); // 🔍 DEBUG
                this.albumDetails.showToast(
                    `${totalTracks} tracks created! Processing in background.`,
                    'success',
                    4000
                );

                // Add all tracks to UI if data is provided
                if (result.tracks && Array.isArray(result.tracks)) {
                    result.tracks.forEach(track => {
                        this.albumDetails.addNewTrackToUI(track);
                        this.trackTTSJob(track.id, track.default_voice || voice);
                    });
                } else if (Array.isArray(result.track_ids)) {
                    result.track_ids.forEach(id => this.trackTTSJob(id, voice));
                }
            } else {
                this.albumDetails.showToast(
                    `Track "${title}" created! Processing in background.`,
                    'success',
                    3000
                );

                // Add single track to UI if data is provided
                if (result.track) {
                    this.albumDetails.addNewTrackToUI(result.track);
                    this.trackTTSJob(result.track.id, result.track.default_voice || voice);
                } else if (result.track_id) {
                    this.trackTTSJob(result.track_id, voice);
                }
            }

        } catch (error) {
            const msg = typeof error === 'string' ? error : (error?.message || 'Request failed');
            this.albumDetails.showToast(msg, 'error');
        } finally {
            submitButton.disabled = false;
            submitButton.innerHTML = originalText;
        }
    }

    initializeRealtimeUpdates() {
        if (!this.ttsChannel) {
            console.warn('TTSStatusChannel not available; TTS modal will not show live progress.');
            return;
        }
        if (!this.realtimeListenersAttached) {
            window.addEventListener('ttsStatusUpdate', this.handleStatusUpdate);
            window.addEventListener('ttsWebSocketConnected', this.handleChannelConnected);
            window.addEventListener('ttsWebSocketDisconnected', this.handleChannelDisconnected);
            this.realtimeListenersAttached = true;
        }
        this.ttsChannel.connect();
    }

    handleChannelConnected() {
        if (!this.ttsChannel) return;
        this.activeJobs.forEach(({ trackId, voiceId }) => {
            try {
                this.ttsChannel.subscribe(trackId, voiceId);
            } catch (error) {
                console.error('TTSManager resubscribe failed:', error);
            }
        });
    }

    handleChannelDisconnected() {
        // Keep activeJobs so we resubscribe on reconnect.
    }

    trackTTSJob(trackId, voiceId) {
        if (!trackId || !voiceId || !this.ttsChannel) return;
        if (!this.realtimeListenersAttached) {
            this.initializeRealtimeUpdates();
        }
        const key = `${trackId}:${voiceId}`;
        if (!this.activeJobs.has(key)) {
            this.activeJobs.set(key, {
                trackId,
                voiceId,
                status: 'queued',
                phase: 'tts',
                progress: 0,
                message: 'Queued for processing...'
            });
            try {
                this.ttsChannel.subscribe(trackId, voiceId);
            } catch (error) {
                console.error('TTSManager subscribe failed:', error);
            }
        }
        this.updateProgressOverlay();
    }

    clearTTSJob(trackId, voiceId) {
        const key = `${trackId}:${voiceId}`;
        if (this.activeJobs.has(key)) {
            this.activeJobs.delete(key);
            if (this.ttsChannel) {
                try {
                    this.ttsChannel.unsubscribe(trackId, voiceId);
                } catch (error) {
                    console.error('TTSManager unsubscribe failed:', error);
                }
            }
        }
        this.updateProgressOverlay();
    }

    handleStatusUpdate(event) {
        const data = event.detail;
        if (!data) return;

        const trackId = data.track_id;
        const voiceId = data.voice_id;
        if (!trackId || !voiceId) return;

        const key = `${trackId}:${voiceId}`;
        if (!this.activeJobs.has(key)) return;

        const job = this.activeJobs.get(key);

        if (data.type === 'tts_progress') {
            job.status = data.status || 'processing';
            job.phase = data.phase || 'tts';
            job.progress = Number(data.progress || 0);
            job.message = data.message || `Generating voice... ${Math.round(job.progress)}%`;
        } else if (data.type === 'segmentation_progress') {
            job.status = data.status || 'segmenting';
            job.phase = 'segmentation';
            job.progress = Number(data.progress || 0);
            job.message = data.message || `Preparing segments... ${Math.round(job.progress)}%`;
        } else if (data.type === 'generation_complete') {
            if (data.success) {
                job.status = 'complete';
                job.phase = 'complete';
                job.progress = 100;
                job.message = 'Voice ready!';
                setTimeout(() => this.clearTTSJob(trackId, voiceId), 1500);
            } else {
                job.status = 'error';
                job.message = 'Generation failed.';
                setTimeout(() => this.clearTTSJob(trackId, voiceId), 3000);
            }
        }

        this.updateProgressOverlay();
    }

    updateProgressOverlay() {
        if (!this.ttsProgress) return;

        if (this.activeJobs.size === 0) {
            this.clearProgressOverlay();
            return;
        }

        const [job] = this.activeJobs.values();
        this.ttsProgress.style.display = 'block';
        if (this.ttsProgressBar) {
            const pct = Math.max(0, Math.min(100, Math.round(job.progress || 0)));
            this.ttsProgressBar.style.width = `${pct}%`;
        }
        if (this.ttsProgressText) {
            this.ttsProgressText.textContent = job.message || 'Processing...';
        }
    }

    clearProgressOverlay() {
        if (this.ttsProgress) {
            this.ttsProgress.style.display = 'none';
        }
        if (this.ttsProgressBar) {
            this.ttsProgressBar.style.width = '0%';
        }
        if (this.ttsProgressText) {
            this.ttsProgressText.textContent = 'Preparing...';
        }
    }

    clearProgressOverlay() {
        if (this.ttsProgress) {
            this.ttsProgress.style.display = 'none';
        }
        if (this.ttsProgressBar) {
            this.ttsProgressBar.style.width = '0%';
        }
        if (this.ttsProgressText) {
            this.ttsProgressText.textContent = 'Preparing...';
        }
    }

    clearTTSProgress() {
        this.activeJobs.forEach(({ trackId, voiceId }) => {
            if (this.ttsChannel) {
                try {
                    this.ttsChannel.unsubscribe(trackId, voiceId);
                } catch (_) {}
            }
        });
        this.activeJobs.clear();
        this.clearProgressOverlay();

        if (this.realtimeListenersAttached) {
            window.removeEventListener('ttsStatusUpdate', this.handleStatusUpdate);
            window.removeEventListener('ttsWebSocketConnected', this.handleChannelConnected);
            window.removeEventListener('ttsWebSocketDisconnected', this.handleChannelDisconnected);
            this.realtimeListenersAttached = false;
        }
    }

    addStyles() {
        const style = document.createElement('style');
        style.textContent = `
            .bulk-options {
                animation: fadeIn 0.3s ease;
                margin-top: 1rem;
                padding: 1rem;
                border: 1px solid var(--border-color, rgba(255, 255, 255, 0.1));
                border-radius: 8px;
                background: var(--card-bg, rgba(255, 255, 255, 0.02));
            }
            @keyframes highlightNew {
    0% { 
        background: rgba(59, 130, 246, 0.3);
        transform: scale(1.01);
    }
    50% {
        background: rgba(59, 130, 246, 0.2);
    }
    100% { 
        background: var(--card-bg, transparent);
        transform: scale(1);
    }
}
            .bulk-enable-label {
                font-weight: 500;
                user-select: none;
                display: flex;
                align-items: center;
                gap: 8px;
                margin-bottom: 1rem;
                cursor: pointer;
                color: var(--text-color);
            }
            
            .bulk-enable-label:hover {
                opacity: 0.8;
            }
            
            #bulkControls {
                animation: slideDown 0.3s ease;
                transform-origin: top;
            }
            
            #bulkControls input[type="number"],
            #bulkControls input[type="text"] {
                transition: border-color 0.2s ease, box-shadow 0.2s ease;
                width: 100%;
                padding: 8px;
                border-radius: 4px;
                border: 1px solid var(--border-color, rgba(255, 255, 255, 0.3));
                background: var(--input-bg, rgba(255, 255, 255, 0.1));
                color: var(--text-color);
            }
            
            #bulkControls input[type="number"]:focus,
            #bulkControls input[type="text"]:focus {
                border-color: var(--primary);
                box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1);
                outline: none;
            }
            
            .bulk-info {
                line-height: 1.4;
                font-family: monospace;
                font-size: 0.85rem;
                color: var(--text-2);
                margin-top: 8px;
                padding: 8px;
                background: var(--card-bg, rgba(0, 0, 0, 0.2));
                border-radius: 4px;
            }
    
            [data-theme="light"] .bulk-options {
                border-color: rgba(0, 0, 0, 0.1);
                background: rgba(0, 0, 0, 0.02);
            }
    
            [data-theme="light"] .bulk-enable-label {
                color: #1a202c;
            }
    
            [data-theme="light"] #bulkControls input[type="number"],
            [data-theme="light"] #bulkControls input[type="text"] {
                border-color: rgba(0, 0, 0, 0.3);
                background: rgba(0, 0, 0, 0.05);
                color: #1a202c;
            }
    
            [data-theme="light"] .bulk-info {
                color: rgba(0, 0, 0, 0.7);
                background: rgba(0, 0, 0, 0.05);
            }
            
            @keyframes fadeIn {
                from { opacity: 0; }
                to { opacity: 1; }
            }
            
            @keyframes slideDown {
                from { 
                    opacity: 0;
                    transform: scaleY(0);
                }
                to { 
                    opacity: 1;
                    transform: scaleY(1);
                }
            }
            
            @media (max-width: 768px) {
                .create-tts-btn {
                    max-width: 200px;
                    width: auto;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    padding: 8px 16px;
                    margin: 4px auto;
                    font-size: 0.9rem;
                }
                
                .bulk-options {
                    padding: 0.75rem;
                }
                
                #bulkSplitCount {
                    width: 60px !important;
                }
            }
        `;
        document.head.appendChild(style);
    }
}
    window.TTSManager = TTSManager;
}
