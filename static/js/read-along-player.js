// read-along-player.js - Read-Along Player Controls Controller

class ReadAlongPlayer {
    constructor(readAlongSPA) {
        this.spa = readAlongSPA;
        this.player = null;
        this.audio = null;
        this.voiceExtension = null;
        
        // UI Elements
        this.elements = {};
        
        // State
        this.isDragging = false;
        this.currentSpeed = 1.0;
        
        this.init();
    }

    // ============================================================================
    // INITIALIZATION
    // ============================================================================

    init() {
        
        try {
            this.initializeElements();
            this.waitForPlayer();
            this.setupEventListeners();
            
        } catch (error) {
            console.error('❌ Failed to initialize Read-Along Player Controls:', error);
        }
    }

    initializeElements() {
        const elementIds = [
            'readAlongPlayerControls',
            'readAlongProgressBar',
            'readAlongProgress',
            'readAlongProgressKnob',
            'readAlongCurrentTime',
            'readAlongDuration',
            'readAlongPlayBtn',
            'readAlongPlayIcon',
            'readAlongRewind30',
            'readAlongRewind15',
            'readAlongForward15',
            'readAlongForward30',
            'readAlongSpeedBtn',
            'readAlongSpeedDisplay',
            'readAlongSpeedMenu',
            'readAlongVoiceBtn',
            'readAlongVoiceDisplay'
        ];
        
        this.elements = {};
        elementIds.forEach(id => {
            this.elements[id] = document.getElementById(id);
        });
    }

    waitForPlayer() {
        const checkPlayer = () => {
            if (window.persistentPlayer) {
                this.player = window.persistentPlayer;
                this.audio = this.player.audio;
                this.voiceExtension = this.player.voiceExtension;
                this.setupPlayerIntegration();
            } else {
                setTimeout(checkPlayer, 100);
            }
        };
        checkPlayer();
    }

    setupPlayerIntegration() {
        if (!this.audio) return;
        
        // Set up audio event listeners for UI updates
        this.audio.addEventListener('timeupdate', () => this.updateProgress());
        this.audio.addEventListener('loadedmetadata', () => this.updateDuration());
        this.audio.addEventListener('play', () => this.updatePlayButton());
        this.audio.addEventListener('pause', () => this.updatePlayButton());
        this.audio.addEventListener('ended', () => this.updatePlayButton());
        this.audio.addEventListener('ratechange', () => this.updateSpeedDisplay());
        
        // Initialize UI
        this.updateDuration();
        this.updatePlayButton();
        this.updateSpeedDisplay();
        this.updateVoiceButton();
    }

    setupEventListeners() {
        // Play/Pause button
        if (this.elements.readAlongPlayBtn) {
            this.elements.readAlongPlayBtn.addEventListener('click', () => this.togglePlay());
        }
        
        // Seek buttons
        if (this.elements.readAlongRewind30) {
            this.elements.readAlongRewind30.addEventListener('click', () => this.seek(-30));
        }
        
        if (this.elements.readAlongRewind15) {
            this.elements.readAlongRewind15.addEventListener('click', () => this.seek(-15));
        }
        
        if (this.elements.readAlongForward15) {
            this.elements.readAlongForward15.addEventListener('click', () => this.seek(15));
        }
        
        if (this.elements.readAlongForward30) {
            this.elements.readAlongForward30.addEventListener('click', () => this.seek(30));
        }
        
        // Progress bar
        this.setupProgressBar();
        
        // Speed control
        this.setupSpeedControl();
        
        // Voice control
        this.setupVoiceControl();
    }

    // ============================================================================
    // PROGRESS BAR
    // ============================================================================

    setupProgressBar() {
        if (!this.elements.readAlongProgressBar) return;
        
        const progressBar = this.elements.readAlongProgressBar;
        const progressKnob = this.elements.readAlongProgressKnob;
        
        // Click to seek
        progressBar.addEventListener('click', (e) => {
            if (this.isDragging) return;
            this.handleProgressClick(e);
        });
        
        // Drag to seek
        if (progressKnob) {
            progressKnob.addEventListener('mousedown', (e) => this.startDragging(e));
            progressKnob.addEventListener('touchstart', (e) => this.startDragging(e), { passive: false });
        }
        
        // Global mouse/touch events for dragging
        document.addEventListener('mousemove', (e) => this.handleDrag(e));
        document.addEventListener('touchmove', (e) => this.handleDrag(e), { passive: false });
        document.addEventListener('mouseup', () => this.stopDragging());
        document.addEventListener('touchend', () => this.stopDragging());
    }

    handleProgressClick(e) {
        if (!this.audio || !this.audio.duration) return;
        
        const rect = this.elements.readAlongProgressBar.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const percentage = x / rect.width;
        const newTime = percentage * this.audio.duration;
        
        this.seekToTime(newTime);
    }

    startDragging(e) {
        this.isDragging = true;
        e.preventDefault();
    }

    handleDrag(e) {
        if (!this.isDragging || !this.audio || !this.audio.duration) return;
        
        const rect = this.elements.readAlongProgressBar.getBoundingClientRect();
        const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
        const x = clientX - rect.left;
        const percentage = Math.max(0, Math.min(1, x / rect.width));
        const newTime = percentage * this.audio.duration;
        
        // Update progress visually
        this.updateProgressDisplay(percentage);
        
        // Update time display
        if (this.elements.readAlongCurrentTime) {
            this.elements.readAlongCurrentTime.textContent = this.formatTime(newTime);
        }
        
        // Actually seek the audio
        this.audio.currentTime = newTime;
    }

    stopDragging() {
        this.isDragging = false;
    }

    updateProgress() {
        if (this.isDragging || !this.audio || !this.audio.duration) return;
        
        const percentage = this.audio.currentTime / this.audio.duration;
        this.updateProgressDisplay(percentage);
        
        // Update time display
        if (this.elements.readAlongCurrentTime) {
            this.elements.readAlongCurrentTime.textContent = this.formatTime(this.audio.currentTime);
        }
    }

    updateProgressDisplay(percentage) {
        if (this.elements.readAlongProgress) {
            this.elements.readAlongProgress.style.width = `${percentage * 100}%`;
        }
    }

    updateDuration() {
        if (!this.audio || !this.elements.readAlongDuration) return;
        
        if (this.audio.duration) {
            this.elements.readAlongDuration.textContent = this.formatTime(this.audio.duration);
        }
    }

    // ============================================================================
    // PLAYBACK CONTROLS
    // ============================================================================

    togglePlay() {
        if (!this.player) return;
        
        this.player.togglePlay();
    }

    seek(seconds) {
        if (!this.player) return;
        
        this.player.seek(seconds);
    }

    seekToTime(time) {
        if (!this.audio) return;
        
        this.audio.currentTime = Math.max(0, Math.min(time, this.audio.duration || 0));
    }

    updatePlayButton() {
        if (!this.audio || !this.elements.readAlongPlayIcon) return;
        
        const isPlaying = !this.audio.paused;
        this.elements.readAlongPlayIcon.className = isPlaying ? 'fas fa-pause' : 'fas fa-play';
    }

    // ============================================================================
    // SPEED CONTROL
    // ============================================================================

    setupSpeedControl() {
        if (!this.elements.readAlongSpeedBtn) return;
        
        // Speed button click
        this.elements.readAlongSpeedBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleSpeedMenu();
        });
        
        // Speed options
        document.querySelectorAll('#readAlongSpeedMenu .speed-option').forEach(option => {
            option.addEventListener('click', (e) => {
                e.stopPropagation();
                const speed = parseFloat(option.dataset.speed);
                this.setPlaybackSpeed(speed);
                this.hideSpeedMenu();
            });
        });
        
        // Close menu on outside click
        document.addEventListener('click', () => {
            this.hideSpeedMenu();
        });
    }

    toggleSpeedMenu() {
        if (!this.elements.readAlongSpeedMenu) return;
        
        const menu = this.elements.readAlongSpeedMenu;
        const isVisible = menu.classList.contains('visible');
        
        if (isVisible) {
            this.hideSpeedMenu();
        } else {
            this.showSpeedMenu();
        }
    }

    showSpeedMenu() {
        if (!this.elements.readAlongSpeedMenu) return;
        
        this.elements.readAlongSpeedMenu.style.display = 'block';
        this.elements.readAlongSpeedMenu.classList.add('visible');
    }

    hideSpeedMenu() {
        if (!this.elements.readAlongSpeedMenu) return;
        
        this.elements.readAlongSpeedMenu.classList.remove('visible');
        setTimeout(() => {
            this.elements.readAlongSpeedMenu.style.display = 'none';
        }, 200);
    }

    setPlaybackSpeed(speed) {
        if (!this.player) return;
        
        this.player.setPlaybackSpeed(speed);
        this.currentSpeed = speed;
        this.updateSpeedDisplay();
        
        // Update active speed option
        document.querySelectorAll('#readAlongSpeedMenu .speed-option').forEach(option => {
            const optionSpeed = parseFloat(option.dataset.speed);
            option.classList.toggle('active', optionSpeed === speed);
        });
        
        // Show toast
        this.spa.showToast(`Playback speed: ${speed}x`, 'info', 2000);
    }

    updateSpeedDisplay() {
        if (!this.audio || !this.elements.readAlongSpeedDisplay) return;
        
        const speed = this.audio.playbackRate || 1.0;
        this.elements.readAlongSpeedDisplay.textContent = speed === 1.0 ? '1x' : `${speed}x`;
        this.currentSpeed = speed;
    }

    // ============================================================================
    // VOICE CONTROL
    // ============================================================================

    setupVoiceControl() {
        if (!this.elements.readAlongVoiceBtn) return;
        
        // Voice button click
        this.elements.readAlongVoiceBtn.addEventListener('click', () => {
            this.openVoiceModal();
        });
        
        // Update voice button visibility and info
        this.updateVoiceButton();
        
        // Listen for voice changes
        document.addEventListener('currentWordChanged', () => {
            this.updateVoiceButton();
        });
    }

    updateVoiceButton() {
        if (!this.elements.readAlongVoiceBtn || !this.elements.readAlongVoiceDisplay) return;
        
        const trackType = this.spa.trackData?.track_type;
        const currentVoice = this.spa.voiceId;
        
        if (trackType === 'tts' && currentVoice) {
            this.elements.readAlongVoiceBtn.style.display = 'flex';
            
            // Format voice name for display
            const voiceName = currentVoice.replace(/^en-(US|GB)-/, '').replace('Neural', '');
            this.elements.readAlongVoiceDisplay.textContent = `Voice: ${voiceName}`;
        } else {
            this.elements.readAlongVoiceBtn.style.display = 'none';
        }
    }

    openVoiceModal() {
        if (!this.voiceExtension) {
            this.spa.showToast('Voice switching not available', 'warning');
            return;
        }
        
        // Use the voice extension's modal
        this.voiceExtension.openVoiceModal();
    }

    // ============================================================================
    // UTILITY FUNCTIONS
    // ============================================================================

    formatTime(seconds) {
        if (isNaN(seconds)) return '0:00:00';
        
        const s = Math.floor(seconds);
        const hrs = Math.floor(s / 3600);
        const mins = Math.floor((s % 3600) / 60);
        const secs = s % 60;
        
        return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }

    // ============================================================================
    // PROGRESS OVERLAY (for voice changes)
    // ============================================================================

    showProgressOverlay(title, message, percentage = 0) {
        const overlay = document.getElementById('readAlongProgressOverlay');
        const titleEl = overlay?.querySelector('.progress-title');
        const countEl = document.getElementById('readAlongProgressCount');
        const barEl = document.getElementById('readAlongProgressBarOverlay');
        
        if (overlay) {
            overlay.style.display = 'flex';
        }
        
        if (titleEl) {
            titleEl.textContent = title;
        }
        
        if (countEl) {
            countEl.textContent = message;
        }
        
        if (barEl) {
            barEl.style.width = `${Math.max(0, Math.min(100, percentage))}%`;
        }
    }

    updateProgressOverlay(message, percentage) {
        const countEl = document.getElementById('readAlongProgressCount');
        const barEl = document.getElementById('readAlongProgressBarOverlay');
        
        if (countEl) {
            countEl.textContent = message;
        }
        
        if (barEl) {
            barEl.style.width = `${Math.max(0, Math.min(100, percentage))}%`;
        }
    }

    hideProgressOverlay() {
        const overlay = document.getElementById('readAlongProgressOverlay');
        if (overlay) {
            setTimeout(() => {
                overlay.style.display = 'none';
            }, 1000);
        }
    }

    // ============================================================================
    // CLEANUP
    // ============================================================================

    destroy() {
        try {
            // Remove event listeners
            document.removeEventListener('mousemove', this.handleDrag);
            document.removeEventListener('touchmove', this.handleDrag);
            document.removeEventListener('mouseup', this.stopDragging);
            document.removeEventListener('touchend', this.stopDragging);
            document.removeEventListener('click', this.hideSpeedMenu);
            
            // Clear references
            this.player = null;
            this.audio = null;
            this.voiceExtension = null;
            this.spa = null;
            this.elements = {};
            
            
        } catch (error) {
        }
    }
}

// Auto-initialize when ReadAlongSPA is available
document.addEventListener('DOMContentLoaded', () => {
    const checkSPA = () => {
        if (window.readAlongSPA) {
            window.readAlongPlayer = new ReadAlongPlayer(window.readAlongSPA);
        } else {
            setTimeout(checkSPA, 100);
        }
    };
    
    checkSPA();
});

// Export for global access
window.ReadAlongPlayer = ReadAlongPlayer;