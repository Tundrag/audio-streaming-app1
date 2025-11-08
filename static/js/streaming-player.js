// streaming-player.js

class StreamingPlayer {
    constructor() {
        this.audio = new Audio();
        this.mediaSource = null;
        this.sourceBuffer = null;
        this.chunks = [];
        this.isLoading = false;
        this.isSeeking = false;
        this.currentTrackId = null;
        this.bufferingTimeout = null;
        this.bufferingThreshold = 30; // Buffer 30 seconds ahead
        this.maxBufferSize = 2 * 60; // 2 minutes max buffer
        this.retryAttempts = 3;
        this.setupMediaSource();
        this.setupEventListeners();
    }

    setupMediaSource() {
        this.mediaSource = new MediaSource();
        this.audio.src = URL.createObjectURL(this.mediaSource);

        this.mediaSource.addEventListener('sourceopen', () => {
            // Use correct MIME type for MP3
            this.sourceBuffer = this.mediaSource.addSourceBuffer('audio/mpeg');
            
            this.sourceBuffer.addEventListener('updateend', () => {
                // Continue loading if we have more chunks
                if (this.chunks.length > 0 && !this.isLoading) {
                    this.appendNextChunk();
                }
                
                // Check buffer and load more if needed
                this.checkBuffer();
            });
        });
    }

    setupEventListeners() {
        // Playback monitoring
        this.audio.addEventListener('timeupdate', () => this.checkBuffer());
        this.audio.addEventListener('waiting', () => this.handleBuffering());
        this.audio.addEventListener('playing', () => this.clearBufferingTimeout());
        
        // Error handling
        this.audio.addEventListener('error', (e) => this.handleError(e));
        
        // Seeking handling
        this.audio.addEventListener('seeking', () => {
            this.isSeeking = true;
            this.handleSeeking();
        });
        
        this.audio.addEventListener('seeked', () => {
            this.isSeeking = false;
            this.checkBuffer();
        });
    }

    async loadTrack(trackId, startTime = 0) {
        try {
            this.currentTrackId = trackId;
            this.resetBuffers();

            // Start loading from the specified time
            await this.loadChunks(startTime);
            
            if (startTime > 0) {
                this.audio.currentTime = startTime;
            }

            return true;
        } catch (error) {
            console.error('Error loading track:', error);
            this.handleError(error);
            return false;
        }
    }

    async loadChunks(startTime = 0) {
        if (this.isLoading || !this.currentTrackId) return;

        try {
            this.isLoading = true;
            const startByte = this.calculateByteOffset(startTime);

            const response = await fetch(`/api/stream/${this.currentTrackId}`, {
                headers: {
                    'Range': `bytes=${startByte}-`
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const reader = response.body.getReader();
            
            while (true) {
                const {value, done} = await reader.read();
                
                if (done) break;
                
                // Add chunk to queue
                this.chunks.push(value);
                
                // Start appending if we're not already processing
                if (this.sourceBuffer && !this.sourceBuffer.updating) {
                    await this.appendNextChunk();
                }

                // Check if we have enough buffer
                if (this.getBufferedAmount() > this.maxBufferSize) {
                    break;
                }
            }

        } catch (error) {
            console.error('Error loading chunks:', error);
            throw error;
        } finally {
            this.isLoading = false;
        }
    }

    async appendNextChunk() {
        if (!this.sourceBuffer || this.sourceBuffer.updating || this.chunks.length === 0) {
            return;
        }

        try {
            const chunk = this.chunks.shift();
            this.sourceBuffer.appendBuffer(chunk);
        } catch (error) {
            console.error('Error appending chunk:', error);
            
            // Handle QuotaExceededError
            if (error.name === 'QuotaExceededError') {
                await this.removeOldBuffers();
                // Re-add the chunk we failed to append
                this.chunks.unshift(chunk);
            }
        }
    }

    async removeOldBuffers() {
        if (!this.sourceBuffer || this.sourceBuffer.updating) {
            return;
        }

        const currentTime = this.audio.currentTime;
        const buffered = this.sourceBuffer.buffered;

        // Remove buffers more than 1 minute behind current playback
        for (let i = 0; i < buffered.length; i++) {
            if (buffered.end(i) < currentTime - 60) {
                await this.removeBuffer(buffered.start(i), buffered.end(i));
            }
        }
    }

    async removeBuffer(start, end) {
        return new Promise((resolve, reject) => {
            try {
                const removeCallback = () => {
                    this.sourceBuffer.removeEventListener('updateend', removeCallback);
                    resolve();
                };
                
                this.sourceBuffer.addEventListener('updateend', removeCallback);
                this.sourceBuffer.remove(start, end);
            } catch (error) {
                reject(error);
            }
        });
    }

    getBufferedAmount() {
        if (!this.sourceBuffer || !this.sourceBuffer.buffered.length) {
            return 0;
        }

        const currentTime = this.audio.currentTime;
        const buffered = this.sourceBuffer.buffered;
        
        for (let i = 0; i < buffered.length; i++) {
            if (buffered.start(i) <= currentTime && currentTime <= buffered.end(i)) {
                return buffered.end(i) - currentTime;
            }
        }

        return 0;
    }

    checkBuffer() {
        // Don't check while seeking
        if (this.isSeeking) return;

        const bufferedAmount = this.getBufferedAmount();
        
        // Start loading more if buffer is getting low
        if (bufferedAmount < this.bufferingThreshold && !this.isLoading) {
            const currentTime = this.audio.currentTime;
            this.loadChunks(currentTime + bufferedAmount);
        }
    }

    handleBuffering() {
        // Show buffering UI after a short delay
        this.bufferingTimeout = setTimeout(() => {
            this.showBufferingIndicator();
        }, 500);

        // Try to load more content
        this.checkBuffer();
    }

    clearBufferingTimeout() {
        if (this.bufferingTimeout) {
            clearTimeout(this.bufferingTimeout);
            this.bufferingTimeout = null;
        }
        this.hideBufferingIndicator();
    }

    async handleSeeking() {
        try {
            // Clear existing buffers
            await this.removeOldBuffers();
            
            // Load chunks from new position
            const newTime = this.audio.currentTime;
            await this.loadChunks(newTime);
        } catch (error) {
            console.error('Error handling seek:', error);
        }
    }

    calculateByteOffset(timeSeconds) {
        // Approximate byte offset based on time
        // Assumes average bitrate of 128 kbps
        const BYTES_PER_SECOND = 128 * 1024 / 8;
        return Math.floor(timeSeconds * BYTES_PER_SECOND);
    }

    resetBuffers() {
        this.chunks = [];
        if (this.sourceBuffer && !this.sourceBuffer.updating) {
            try {
                this.sourceBuffer.abort();
                this.sourceBuffer.remove(0, Infinity);
            } catch (error) {
                console.warn('Error resetting buffers:', error);
            }
        }
    }

    showBufferingIndicator() {
        // Implementation depends on your UI
        const indicator = document.getElementById('bufferingIndicator');
        if (indicator) {
            indicator.style.display = 'block';
        }
    }

    hideBufferingIndicator() {
        const indicator = document.getElementById('bufferingIndicator');
        if (indicator) {
            indicator.style.display = 'none';
        }
    }

    handleError(error) {
        console.error('Playback error:', error);
        // Implement error UI notification
        const errorMessage = document.createElement('div');
        errorMessage.className = 'playback-error';
        errorMessage.textContent = 'Error playing track. Please try again.';
        document.body.appendChild(errorMessage);
        
        setTimeout(() => {
            errorMessage.remove();
        }, 3000);
    }

    // Public control methods
    async play() {
        try {
            await this.audio.play();
        } catch (error) {
            console.error('Error playing:', error);
            this.handleError(error);
        }
    }

    pause() {
        this.audio.pause();
    }

    async seek(time) {
        this.audio.currentTime = time;
    }

    setVolume(volume) {
        this.audio.volume = Math.max(0, Math.min(1, volume));
    }

    getCurrentTime() {
        return this.audio.currentTime;
    }

    getDuration() {
        return this.audio.duration;
    }
}

// Initialize player and export
const streamingPlayer = new StreamingPlayer();
export default streamingPlayer;