class TTSManager {
    constructor(albumDetails) {
        this.albumDetails = albumDetails;
        this.albumId = albumDetails.albumId;
        
        this.activeTTSJobs = new Map();
        this.ttsProgress = { isProcessing: false, currentJob: null, progress: 0, status: '' };
        this.ttsProgressIntervals = new Map();
        this.progressErrorCounts = new Map();
        this.maxProgressErrors = 10;
        
        this.bulkJobs = new Map();
        this.bulkProgressIntervals = new Map();
        
        this.initializeDOMElements();
        this.initializeEventListeners();
        this.checkExistingTTSProgress();
        this.initializeBulkTTSControls();
        this.addStyles();
    }

    initializeDOMElements() {
        this.ttsModal = document.getElementById('ttsModal');
        this.ttsForm = document.getElementById('ttsForm');
        this.ttsBtn = document.querySelector('.create-tts-btn');
        this.ttsProgressEl = document.getElementById('ttsProgress');
        this.ttsTitle = document.getElementById('ttsTitle');
        this.ttsText = document.getElementById('ttsText');
        this.ttsVoice = document.getElementById('ttsVoice');
        
        this.ensureBulkTTSElements();
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
        const splitCount = parseInt(document.getElementById('bulkSplitCount')?.value) || 3;
        const analysisDiv = document.getElementById('textAnalysis');
        
        if (!analysisDiv || !enableBulk) return;
        
        const charCount = text.length;
        const wordCount = text.split(/\s+/).filter(word => word.length > 0).length;
        const estimatedMinutes = Math.round(wordCount / 180);
        const estimatedHours = estimatedMinutes / 60;
        
        const charsPerTrack = Math.round(charCount / splitCount);
        const wordsPerTrack = Math.round(wordCount / splitCount);
        const minutesPerTrack = Math.round(estimatedMinutes / splitCount);
        
        let analysisHTML = `
            <div style="margin-bottom: 8px;"><strong>Text Analysis:</strong></div>
            <div>• Total: ${charCount.toLocaleString()} characters, ${wordCount.toLocaleString()} words</div>
            <div>• Estimated duration: ~${estimatedMinutes}min (${estimatedHours.toFixed(1)}h)</div>
            <div>• Per track: ~${charsPerTrack.toLocaleString()} chars, ${wordsPerTrack.toLocaleString()} words (~${minutesPerTrack}min each)</div>
        `;
        
        if (charCount > 2000000) {
            analysisHTML += '<div style="color: #ef4444; margin-top: 4px;">⚠️ Exceeds 2M character limit - please reduce text</div>';
        } else if (charCount < 10000) {
            analysisHTML += '<div style="color: #f59e0b; margin-top: 4px;">⚠️ Minimum 10k characters recommended for bulk generation</div>';
        } else if (minutesPerTrack > 360) {
            analysisHTML += '<div style="color: #f59e0b; margin-top: 4px;">⚠️ Each track will be over 6 hours - consider more splits</div>';
        } else if (minutesPerTrack < 30) {
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
            this.resetTTSProgress();
            
            const enableBulk = document.getElementById('enableBulkGeneration');
            const bulkControls = document.getElementById('bulkControls');
            if (enableBulk) enableBulk.checked = false;
            if (bulkControls) bulkControls.style.display = 'none';
            
            setTimeout(() => this.ttsTitle?.focus(), 100);
        }
    }

    closeTTSModal() {
        if (this.ttsModal) {
            this.ttsModal.classList.remove('active');
            this.ttsModal.style.display = 'none';
            document.body.style.overflow = '';
            this.ttsForm?.reset();
            this.resetTTSProgress();
            this.clearTTSProgress();
            this.clearBulkProgress();
        }
    }

    resetTTSProgress() {
        if (this.ttsProgressEl) {
            this.ttsProgressEl.classList.remove('active');
            const progressFill = this.ttsProgressEl.querySelector('.progress-fill');
            const progressText = this.ttsProgressEl.querySelector('.progress-text');
            
            if (progressFill) progressFill.style.width = '0%';
            if (progressText) progressText.textContent = 'Processing text...';
        }
        
        this.ttsProgress = { isProcessing: false, currentJob: null, progress: 0, status: '' };
    }

    updateTTSProgress(progress, status = '') {
        if (!this.ttsProgressEl) return;
        
        this.ttsProgressEl.classList.add('active');
        
        const progressFill = this.ttsProgressEl.querySelector('.progress-fill');
        const progressText = this.ttsProgressEl.querySelector('.progress-text');
        
        if (progressFill) {
            progressFill.style.transition = 'width 0.5s ease';
            const clampedProgress = Math.min(100, Math.max(0, progress || 0));
            progressFill.style.width = `${clampedProgress}%`;
        }
        
        if (progressText && status) {
            progressText.textContent = status;
        }
        
        this.ttsProgress.progress = progress || 0;
        this.ttsProgress.status = status || '';
    }

    async createTTSTrack() {
        const title = this.ttsTitle?.value?.trim();
        const text = this.ttsText?.value?.trim();
        const voice = this.ttsVoice?.value || 'en-US-AvaNeural';
        
        const bulkCheckbox = document.getElementById('enableBulkGeneration');
        const bulkSplitInput = document.getElementById('bulkSplitCount');
        const bulkTitleInput = document.getElementById('bulkSeriesTitle');
        
        const enableBulk = bulkCheckbox?.checked;
        const bulkSplitCount = parseInt(bulkSplitInput?.value) || 1;
        const bulkSeriesTitle = bulkTitleInput?.value?.trim();

        if (!title) {
            this.albumDetails.showToast('Please enter a track title');
            this.ttsTitle?.focus();
            return;
        }

        if (!text || text.length < 10) {
            this.albumDetails.showToast('Text must be at least 10 characters long');
            this.ttsText?.focus();
            return;
        }

        if (enableBulk) {
            if (text.length < 10000) {
                this.albumDetails.showToast('Text must be at least 10,000 characters for bulk generation');
                this.ttsText?.focus();
                return;
            }
            
            if (text.length > 2000000) {
                this.albumDetails.showToast('Text exceeds 2 million character limit');
                this.ttsText?.focus();
                return;
            }
            
            if (bulkSplitCount < 2 || bulkSplitCount > 20) {
                this.albumDetails.showToast('Split count must be between 2 and 20');
                document.getElementById('bulkSplitCount')?.focus();
                return;
            }
            
            if (!bulkSeriesTitle) {
                this.albumDetails.showToast('Please enter a series title for bulk generation');
                document.getElementById('bulkSeriesTitle')?.focus();
                return;
            }
        } else if (text.length > 500000) {
            this.albumDetails.showToast('Text is too long. Please use bulk generation for content over 500k characters.');
            return;
        }

        const submitButton = this.ttsForm.querySelector('button[type="submit"]');
        const originalText = submitButton.textContent;

        try {
            submitButton.disabled = true;
            
            if (enableBulk) {
                submitButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Starting Bulk Generation...';
            } else {
                submitButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Creating...';
            }
            
            this.ttsProgress.isProcessing = true;

            this.updateTTSProgress(10, enableBulk ? 'Initializing bulk TTS processing...' : 'Initializing TTS processing...');

            const requestBody = { 
                title, 
                text, 
                voice 
            };
            
            if (enableBulk && bulkSplitCount > 1) {
                requestBody.bulk_split_count = bulkSplitCount;
                requestBody.bulk_series_title = bulkSeriesTitle;
            }

            const response = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}/tracks/create-tts`, {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                let errorMessage = `HTTP ${response.status}: Failed to create TTS track`;
                
                try {
                    const errorData = await response.json();
                    if (errorData.detail) {
                        errorMessage = errorData.detail;
                    } else if (errorData.message) {
                        errorMessage = errorData.message;
                    }
                } catch (parseError) {
                    errorMessage = `${response.status} ${response.statusText}`;
                }
                
                throw new Error(errorMessage);
            }

            const result = await response.json();
            
            if (result.status === 'bulk_queued') {
                this.handleBulkTTSQueued(result);
            } else {
                this.updateTTSProgress(20, 'Text processing started...');
                const trackId = result.track_id;
                this.ttsProgress.currentJob = trackId;
                this.activeTTSJobs.set(trackId, {
                    title: title,
                    startTime: Date.now(),
                    trackId: trackId
                });
                await this.startTTSProgressPolling(trackId);
            }

        } catch (error) {
            this.updateTTSProgress(0, `Error: ${error.message}`);
            this.albumDetails.showToast(`Failed to create TTS track: ${error.message}`, 'error');
            setTimeout(() => this.resetTTSProgress(), 5000);
        } finally {
            submitButton.disabled = false;
            submitButton.innerHTML = originalText;
            this.ttsProgress.isProcessing = false;
        }
    }

    handleBulkTTSQueued(result) {
        const totalTracks = result.total_tracks;
        const estimatedHours = Math.round(result.estimated_total_duration / 3600 * 10) / 10;
        
        this.updateTTSProgress(15, `Queued ${totalTracks} tracks (~${estimatedHours}h total audio)`);
        
        this.albumDetails.showToast(`Bulk generation started: ${totalTracks} tracks queued`, 'success');
        
        this.bulkJobs.set(result.bulk_queue_id, {
            totalTracks: totalTracks,
            seriesTitle: result.series_title,
            startTime: Date.now(),
            status: 'queued'
        });
        
        this.startBulkProgressPolling(result.bulk_queue_id);
    }

    async startBulkProgressPolling(bulkQueueId) {
        this.stopBulkProgressPolling(bulkQueueId);
        this.progressErrorCounts.set(`bulk_${bulkQueueId}`, 0);
        
        const intervalId = setInterval(async () => {
            try {
                await this.pollBulkProgress(bulkQueueId);
            } catch (error) {
                const errorCount = (this.progressErrorCounts.get(`bulk_${bulkQueueId}`) || 0) + 1;
                this.progressErrorCounts.set(`bulk_${bulkQueueId}`, errorCount);
                
                if (errorCount >= this.maxProgressErrors) {
                    this.stopBulkProgressPolling(bulkQueueId);
                    this.handleBulkTTSError(bulkQueueId, { error: 'Progress polling failed after multiple attempts' });
                }
            }
        }, 3000);
        
        this.bulkProgressIntervals.set(bulkQueueId, intervalId);
        this.pollBulkProgress(bulkQueueId);
    }

    async pollBulkProgress(bulkQueueId) {
        try {
            const response = await fetch(`/api/tts/bulk-progress/${bulkQueueId}`);
            
            if (!response.ok) {
                if (response.status === 404) {
                    throw new Error('Bulk TTS job not found');
                } else if (response.status === 403) {
                    throw new Error('Access denied to bulk job');
                } else if (response.status >= 500) {
                    throw new Error(`Server error: ${response.status}`);
                } else {
                    throw new Error(`HTTP ${response.status}`);
                }
            }
            
            const progress = await response.json();
            
            if (!this.isValidBulkProgressData(progress)) {
                throw new Error('Invalid bulk progress data received');
            }
            
            this.progressErrorCounts.set(`bulk_${bulkQueueId}`, 0);
            this.handleBulkProgressUpdate(progress);
            
            if (progress.status === 'completed' || progress.status === 'partial_success') {
                await this.handleBulkTTSCompletion(progress);
            } else if (progress.status === 'failed') {
                this.handleBulkTTSError(bulkQueueId, progress);
            }
            
        } catch (error) {
            throw error;
        }
    }

    isValidBulkProgressData(progress) {
        if (!progress || typeof progress !== 'object') {
            return false;
        }
        
        const requiredFields = ['bulk_queue_id', 'status', 'progress_percentage', 'completed_segments', 'total_segments'];
        for (const field of requiredFields) {
            if (!(field in progress)) {
                return false;
            }
        }
        
        if (typeof progress.progress_percentage !== 'number' || 
            progress.progress_percentage < 0 || 
            progress.progress_percentage > 100) {
            return false;
        }
        
        return true;
    }

    handleBulkProgressUpdate(progress) {
        const percentage = Math.min(Math.max(progress.progress_percentage || 0, 0), 100);
        const completed = progress.completed_segments || 0;
        const total = progress.total_segments || 1;
        const failed = progress.failed_segments || 0;
        
        let message = `Processing ${progress.series_title || 'bulk job'}: ${completed}/${total} tracks`;
        if (failed > 0) {
            message += ` (${failed} failed)`;
        }
        
        if (progress.estimated_remaining && progress.estimated_remaining > 60) {
            const remainingMinutes = Math.round(progress.estimated_remaining / 60);
            const remainingHours = Math.floor(remainingMinutes / 60);
            const remainingMins = remainingMinutes % 60;
            
            if (remainingHours > 0) {
                message += ` - ~${remainingHours}h ${remainingMins}m remaining`;
            } else {
                message += ` - ~${remainingMins}m remaining`;
            }
        }
        
        this.updateTTSProgress(percentage, message);
        
        const bulkJob = this.bulkJobs.get(progress.bulk_queue_id);
        if (bulkJob) {
            bulkJob.status = progress.status;
            bulkJob.completed = completed;
            bulkJob.failed = failed;
            bulkJob.total = total;
        }
    }

    async handleBulkTTSCompletion(progress) {
        this.stopBulkProgressPolling(progress.bulk_queue_id);
        
        const completed = progress.completed_segments || 0;
        const failed = progress.failed_segments || 0;
        const total = progress.total_segments || 1;
        
        if (failed === 0) {
            this.updateTTSProgress(100, `All ${completed} tracks completed successfully!`);
            this.albumDetails.showToast(`Bulk generation complete: ${completed} tracks created!`, 'success');
        } else {
            this.updateTTSProgress(100, `Completed: ${completed} success, ${failed} failed`);
            this.albumDetails.showToast(`Bulk generation finished: ${completed} success, ${failed} failed`, 'warning');
        }
        
        this.bulkJobs.delete(progress.bulk_queue_id);
        
        setTimeout(() => {
            this.closeTTSModal();
            window.location.reload();
        }, 3000);
    }

    handleBulkTTSError(bulkQueueId, progress) {
        this.stopBulkProgressPolling(bulkQueueId);
        
        const errorMessage = progress.error || 'Bulk generation failed';
        this.updateTTSProgress(0, `Error: ${errorMessage}`);
        this.albumDetails.showToast(`Bulk TTS failed: ${errorMessage}`, 'error');
        
        this.bulkJobs.delete(bulkQueueId);
        
        setTimeout(() => this.resetTTSProgress(), 5000);
    }

    stopBulkProgressPolling(bulkQueueId) {
        const intervalId = this.bulkProgressIntervals.get(bulkQueueId);
        if (intervalId) {
            clearInterval(intervalId);
            this.bulkProgressIntervals.delete(bulkQueueId);
            this.progressErrorCounts.delete(`bulk_${bulkQueueId}`);
        }
    }

    clearBulkProgress() {
        this.bulkProgressIntervals.forEach((intervalId, bulkQueueId) => {
            clearInterval(intervalId);
        });
        this.bulkProgressIntervals.clear();
        this.bulkJobs.clear();
    }

    async startTTSProgressPolling(trackId) {
        this.stopTTSProgressPolling(trackId);
        this.progressErrorCounts.set(trackId, 0);
        
        const intervalId = setInterval(async () => {
            try {
                await this.pollTTSProgress(trackId);
            } catch (error) {
                const errorCount = (this.progressErrorCounts.get(trackId) || 0) + 1;
                this.progressErrorCounts.set(trackId, errorCount);
                
                if (errorCount >= this.maxProgressErrors) {
                    this.stopTTSProgressPolling(trackId);
                    this.handleTTSError(trackId, { error: 'Progress polling failed after multiple attempts' });
                }
            }
        }, 2000);
        
        this.ttsProgressIntervals.set(trackId, intervalId);
        this.pollTTSProgress(trackId);
    }

    async pollTTSProgress(trackId) {
        try {
            const response = await fetch(`/api/tts/progress/${trackId}`);
            
            if (!response.ok) {
                if (response.status === 404) {
                    throw new Error('TTS track not found');
                } else if (response.status === 403) {
                    throw new Error('Access denied');
                } else if (response.status >= 500) {
                    throw new Error(`Server error: ${response.status}`);
                } else {
                    throw new Error(`HTTP ${response.status}`);
                }
            }
            
            const progress = await response.json();
            
            if (!this.isValidProgressData(progress)) {
                throw new Error('Invalid progress data received');
            }
            
            this.progressErrorCounts.set(trackId, 0);
            this.handleTTSProgressUpdate(trackId, progress);
            
            if (progress.status === 'ready' || progress.progress >= 100) {
                await this.handleTTSCompletion(trackId, progress);
            } else if (progress.status === 'error' || progress.status === 'failed') {
                this.handleTTSError(trackId, progress);
            }
            
        } catch (error) {
            throw error;
        }
    }

    isValidProgressData(progress) {
        if (!progress || typeof progress !== 'object') {
            return false;
        }
        
        const requiredFields = ['track_id', 'status', 'progress'];
        for (const field of requiredFields) {
            if (!(field in progress)) {
                return false;
            }
        }
        
        if (typeof progress.progress !== 'number' || progress.progress < 0 || progress.progress > 100) {
            return false;
        }
        
        return true;
    }

    handleTTSProgressUpdate(trackId, progress) {
        const progressPercentage = Math.min(Math.max(progress.progress || 0, 0), 100);
        let message = 'Processing...';
        
        try {
            if (progress.current_phase) {
                const phaseNames = {
                    'initializing': 'Initializing...',
                    'text_processing': 'Processing text...',
                    'audio_generation': 'Generating audio...',
                    'hls_processing': 'Creating segments...',
                    'word_mapping': 'Mapping words...'
                };
                
                message = phaseNames[progress.current_phase] || progress.current_phase;
                
                if (progress.chunks_processed && progress.total_chunks) {
                    message += ` (${progress.chunks_processed}/${progress.total_chunks})`;
                }
                
                if (progress.estimated_time_remaining && progress.estimated_time_remaining > 0) {
                    const minutes = Math.floor(progress.estimated_time_remaining / 60);
                    const seconds = progress.estimated_time_remaining % 60;
                    if (minutes > 0) {
                        message += ` - ~${minutes}m ${seconds}s remaining`;
                    } else {
                        message += ` - ~${seconds}s remaining`;
                    }
                }
            }
        } catch (error) {
            message = `${Math.round(progressPercentage)}% complete`;
        }
        
        this.updateTTSProgress(progressPercentage, message);
        this.updateTrackSymbols(trackId, 'processing');
    }

    async handleTTSCompletion(trackId, progress) {
        this.stopTTSProgressPolling(trackId);
        this.updateTTSProgress(100, 'Complete! Redirecting...');
        
        try {
            const trackResponse = await fetch(`/api/tracks/${trackId}/metadata`);
            if (trackResponse.ok) {
                const metadata = await trackResponse.json();
                await this.addTTSTrackToList(metadata.track);
            }
        } catch (error) {
            // Silent fail for metadata fetch
        }
        
        this.albumDetails.showToast('Dynamic audio track created successfully!', 'success');
        this.updateTrackSymbols(trackId, 'ready');
        
        setTimeout(() => {
            this.closeTTSModal();
            window.location.reload();
        }, 2000);
    }

    handleTTSError(trackId, progress) {
        this.stopTTSProgressPolling(trackId);
        
        let errorMessage = 'Unknown error';
        if (progress.error) {
            errorMessage = progress.error;
        } else if (progress.detailed_progress?.status_message) {
            errorMessage = progress.detailed_progress.status_message;
        } else if (progress.status) {
            errorMessage = `Processing ${progress.status}`;
        }
        
        this.updateTTSProgress(0, `Error: ${errorMessage}`);
        this.albumDetails.showToast(`TTS creation failed: ${errorMessage}`, 'error');
        
        setTimeout(() => this.resetTTSProgress(), 5000);
    }

    updateTrackSymbols(trackId, status) {
        const trackElement = document.querySelector(`.track-item[data-track-id="${trackId}"]`);
        if (!trackElement) return;
        
        const symbolsContainer = trackElement.querySelector('.track-symbols');
        if (!symbolsContainer) return;
        
        const voiceSymbol = symbolsContainer.querySelector('.voice-symbol');
        if (!voiceSymbol) return;
        
        if (status === 'processing') {
            voiceSymbol.innerHTML = '⏳';
            voiceSymbol.style.cursor = 'default';
            voiceSymbol.onclick = null;
        } else if (status === 'ready') {
            voiceSymbol.innerHTML = '🔄';
            voiceSymbol.style.cursor = 'pointer';
            voiceSymbol.onclick = (e) => {
                e.stopPropagation();
                this.albumDetails.handleVoiceChange(trackId, trackElement.querySelector('.track-title').textContent);
            };
        }
    }

    stopTTSProgressPolling(trackId) {
        const intervalId = this.ttsProgressIntervals.get(trackId);
        if (intervalId) {
            clearInterval(intervalId);
            this.ttsProgressIntervals.delete(trackId);
            this.progressErrorCounts.delete(trackId);
        }
    }

    clearTTSProgress() {
        this.ttsProgressIntervals.forEach((intervalId, trackId) => {
            clearInterval(intervalId);
        });
        this.ttsProgressIntervals.clear();
        this.progressErrorCounts.clear();
    }

    checkExistingTTSProgress() {
        const ttsItems = document.querySelectorAll('[data-track-type="tts"]');
        ttsItems.forEach(item => {
            const trackId = item.dataset.trackId;
            if (trackId) {
                this.checkTTSTrackStatus(trackId);
            }
        });
    }

    async checkTTSTrackStatus(trackId) {
        try {
            const response = await fetch(`/api/tts/progress/${trackId}`);
            if (response.ok) {
                const progress = await response.json();
                
                if (this.isValidProgressData(progress)) {
                    if (progress.status === 'processing' && progress.progress < 100) {
                        this.startTTSProgressPolling(trackId);
                    }
                }
            }
        } catch (error) {
            // Silent fail for existing tracks
        }
    }

    async addTTSTrackToList(trackData) {
        try {
            if (!trackData.track_type && (trackData.source_text || trackData.tts_status)) {
                trackData.track_type = 'tts';
            }
            
            if (trackData.track_type === 'tts') {
                trackData._elementTrackType = 'tts';
            }
            
            if (trackData.track_type === 'tts' || trackData.is_tts_track) {
                try {
                    const response = await fetch(`/api/tracks/${trackData.id}/metadata`);
                    if (response.ok) {
                        const metadata = await response.json();
                        trackData.duration = metadata.track?.duration || trackData.duration;
                    }
                } catch (error) {
                    // Silent fail for duration fetch
                }
            }
            
            this.albumDetails.tracks.push(trackData);
            const trackElement = await this.albumDetails.createTrackElement(trackData);
            
            if (this.albumDetails.tracksList) {
                const emptyMessage = this.albumDetails.tracksList.querySelector('.empty-tracks');
                if (emptyMessage) emptyMessage.remove();
                this.albumDetails.tracksList.appendChild(trackElement);
                
                setTimeout(() => {
                    this.albumDetails.addBadgesToExistingTracks();
                }, 100);
            }
            
        } catch (error) {
            this.albumDetails.showToast('Error adding track to list', 'error');
        }
    }

    addStyles() {
        const style = document.createElement('style');
        style.textContent = `
            .tts-progress {
                margin-top: 1rem;
                padding: 1rem;
                background: rgba(255, 255, 255, 0.05);
                border-radius: 8px;
                display: none;
            }
            
            .tts-progress.active {
                display: block;
            }
            
            .tts-progress .progress-container {
                margin-bottom: 0.5rem;
            }
            
            .tts-progress .progress-bar-bg {
                width: 100%;
                height: 8px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 4px;
                overflow: hidden;
            }
            
            .tts-progress .progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #3b82f6, #06b6d4);
                border-radius: 4px;
                transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
                position: relative;
            }
            
            .tts-progress .progress-fill::before {
                content: '';
                position: absolute;
                top: 0;
                left: -100%;
                width: 100%;
                height: 100%;
                background: linear-gradient(90deg, 
                    transparent 0%, 
                    rgba(255, 255, 255, 0.3) 50%, 
                    transparent 100%
                );
                animation: shimmer 2.5s infinite;
            }
            
            .tts-progress .progress-text {
                margin-top: 0.5rem;
                font-size: 0.9rem;
                color: var(--text-2);
                text-align: center;
            }
            
            .bulk-options {
                animation: fadeIn 0.3s ease;
            }
            
            .bulk-enable-label {
                font-weight: 500;
                user-select: none;
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
            
            @keyframes shimmer {
                0% { left: -100%; }
                100% { left: 100%; }
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