if (typeof AudioUpload === 'undefined') {
class AudioUpload {
    constructor(albumDetails) {
        this.albumDetails = albumDetails;
        this.albumId = albumDetails.albumId;
        this.activeUploads = [];
        
        this.initializeDOMElements();
        this.initializeEventListeners();
        this.addStyles();
    }

    initializeDOMElements() {
        this.addTrackModal = document.getElementById('addTrackModal');
        this.addTrackForm = document.getElementById('addTrackForm');
        this.addTrackBtn = document.querySelector('.add-track-btn');
        this.uploadStatus = document.getElementById('uploadStatus');
    }

    initializeEventListeners() {
        this.addTrackForm?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const files = document.getElementById('trackFiles').files;
            if (this.validateTrackFiles(files)) {
                await this.addTracks(files);
            }
        });

        this.addTrackBtn?.addEventListener('click', (e) => {
            e.preventDefault();
            this.openAddTrackModal();
        });

        document.addEventListener('click', (e) => {
            if (e.target.closest('[data-action="open-add-track-modal"]')) {
                e.preventDefault();
                this.openAddTrackModal();
            }
        }, true);

        this.addTrackModal?.addEventListener('click', (e) => {
            if (e.target === this.addTrackModal) this.closeAddTrackModal();
        });
    }

    openAddTrackModal() {
        if (this.addTrackModal) {
            this.addTrackModal.classList.add('active');
            this.addTrackModal.style.display = 'flex';
            document.body.style.overflow = 'hidden';

            // Debug: Log modal and dropdown dimensions
            setTimeout(() => {
                const modalContent = this.addTrackModal.querySelector('.modal-content');
                const dropdown = this.addTrackModal.querySelector('.visibility-select');
                const formGroup = this.addTrackModal.querySelector('.form-group');
                const isMobile = window.innerWidth <= 768;

                console.log('🔍 [Add Track Modal] Dimensions:');
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

    closeAddTrackModal() {
        if (this.addTrackModal) {
            this.addTrackModal.classList.remove('active');
            this.addTrackModal.style.display = 'none';
            document.body.style.overflow = '';
            this.addTrackForm?.reset();
            if (this.uploadStatus) this.uploadStatus.innerHTML = '';
            
            this.activeUploads.forEach(upload => {
                upload.cancelled = true;
                upload.controller?.abort();
            });
            this.activeUploads = [];
        }
    }

    validateTrackFiles(files) {
        if (!files?.length) {
            alert('Please select at least one audio file');
            return false;
        }

        const invalidFiles = Array.from(files).filter(file => 
            !file.type.includes('audio/mpeg') && !file.name.toLowerCase().endsWith('.mp3')
        );

        if (invalidFiles.length > 0) {
            alert('Some files are not MP3. Please select only MP3 files.');
            return false;
        }

        return true;
    }

    async addTracks(files) {
        const submitButton = this.addTrackForm.querySelector('button[type="submit"]');

        try {
            submitButton.disabled = true;
            submitButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Uploading...';

            this.uploadStatus.innerHTML = Array.from(files).map(file => `
                <div class="upload-status-item" id="status-${file.name}">
                    <div class="upload-header">
                        <span class="filename">${file.name}</span>
                        <span class="status">Waiting...</span>
                    </div>
                    <div class="progress-bar-container">
                        <div class="progress-bar"></div>
                    </div>
                    <button class="btn-cancel-upload" data-filename="${file.name}">Cancel</button>
                </div>
            `).join('');

            document.querySelectorAll('.btn-cancel-upload').forEach(btn => {
                btn.addEventListener('click', async (e) => {
                    const filename = e.target.dataset.filename;
                    const upload = this.activeUploads.find(u => u.filename === filename);
                    if (upload) {
                        upload.cancelled = true;
                        upload.controller?.abort();
                        this.updateUploadStatus(filename, 'Cancelled', 0, 'cancelled');
                    }
                });
            });

            let completedUploads = 0;

            const uploadPromises = Array.from(files).map(async (file) => {
                const uploadId = `upload_${Date.now()}_${Math.random().toString(36).substring(2, 15)}`;
                
                const uploadEntry = {
                    filename: file.name,
                    uploadId,
                    cancelled: false,
                    controller: new AbortController()
                };
                this.activeUploads.push(uploadEntry);

                try {
                    this.updateUploadStatus(file.name, 'Preparing...', 0);
                    
                    const initResponse = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}/tracks/init-upload`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ filename: file.name, fileSize: file.size, uploadId }),
                        signal: uploadEntry.controller.signal
                    });

                    if (!initResponse.ok) throw new Error(`Init failed: ${await initResponse.text()}`);
                    
                    const { trackId } = await initResponse.json();
                    uploadEntry.trackId = trackId;

                    const CHUNK_SIZE = 5 * 1024 * 1024;
                    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

                    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
                        if (uploadEntry.cancelled) throw new Error("Upload cancelled");

                        const chunk = file.slice(chunkIndex * CHUNK_SIZE, Math.min(file.size, (chunkIndex + 1) * CHUNK_SIZE));
                        const formData = new FormData();
                        formData.append('chunk', chunk);
                        formData.append('chunkIndex', chunkIndex);
                        formData.append('totalChunks', totalChunks);
                        formData.append('uploadId', uploadId);

                        const chunkResponse = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}/tracks/upload-chunk`, {
                            method: 'POST',
                            body: formData,
                            signal: uploadEntry.controller.signal
                        });

                        if (!chunkResponse.ok) throw new Error(`Chunk failed: ${await chunkResponse.text()}`);
                        
                        const progress = ((chunkIndex + 1) / totalChunks) * 100;
                        this.updateUploadStatus(file.name, `Uploading: ${Math.round(progress)}%`, progress);
                    }

                    this.updateUploadStatus(file.name, 'Finalizing...', 100);
                    
                    const finalizeResponse = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}/tracks/finalize-upload`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ uploadId, trackId }),
                        signal: uploadEntry.controller.signal
                    });

                    if (!finalizeResponse.ok) throw new Error(`Finalize failed: ${await finalizeResponse.text()}`);
                    
                    const newTrack = await finalizeResponse.json();
                    this.updateUploadStatus(file.name, 'Complete!', 100, 'success');
                    this.albumDetails.tracks.push(newTrack);
                    
                    const trackElement = await this.albumDetails.createTrackElement(newTrack);
                    this.albumDetails.tracksList.appendChild(trackElement);
                    
                    completedUploads++;

                } catch (error) {
                    const status = error.message.includes("cancelled") ? 'cancelled' : 'error';
                    const message = error.message.includes("cancelled") ? 'Cancelled' : `Failed: ${error.message}`;
                    this.updateUploadStatus(file.name, message, 0, status);
                }
            });

            await Promise.all(uploadPromises);

            if (completedUploads > 0) {
                setTimeout(() => this.closeAddTrackModal(), 2000);
            }

        } catch (error) {
            this.albumDetails.showToast('Upload error. Please try again.', 'error');
        } finally {
            submitButton.disabled = false;
            submitButton.innerHTML = 'Add Tracks';
            this.activeUploads = [];
        }
    }

    updateUploadStatus(filename, status, progress = 0, state = '') {
        const statusElement = document.getElementById(`status-${filename}`);
        if (!statusElement) return;

        statusElement.querySelector('.status').textContent = status;
        
        const progressBar = statusElement.querySelector('.progress-bar');
        if (progressBar) {
            progressBar.style.transition = 'width 0.3s ease';
            progressBar.style.width = `${progress}%`;
        }

        if (state) {
            statusElement.className = `upload-status-item ${state}`;
            if (['cancelled', 'error', 'success'].includes(state)) {
                const cancelBtn = statusElement.querySelector('.btn-cancel-upload');
                cancelBtn.disabled = true;
                cancelBtn.style.opacity = '0.5';
            }
        }
    }

    addStyles() {
        const style = document.createElement('style');
        style.textContent = `
            .upload-status-item {
                padding: 12px;
                margin: 8px 0;
                border-radius: 8px;
                background: var(--card-bg, rgba(255, 255, 255, 0.1));
                border: 1px solid var(--border-color, rgba(255, 255, 255, 0.1));
                transition: all 0.3s ease;
            }
            
            .upload-status-item.success {
                background: rgba(34, 197, 94, 0.1);
                border-color: rgba(34, 197, 94, 0.3);
            }
            
            .upload-status-item.error {
                background: rgba(239, 68, 68, 0.1);
                border-color: rgba(239, 68, 68, 0.3);
            }
            
            .upload-status-item.cancelled {
                background: rgba(245, 158, 11, 0.1);
                border-color: rgba(245, 158, 11, 0.3);
                opacity: 0.7;
            }
            
            .upload-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 8px;
            }
            
            .filename {
                font-weight: 500;
                color: var(--text-color);
                font-size: 0.9rem;
            }
            
            .status {
                font-size: 0.8rem;
                opacity: 0.8;
                color: var(--text-2);
            }
            
            .progress-bar-container {
                width: 100%;
                height: 6px;
                background: var(--progress-bg, rgba(255, 255, 255, 0.1));
                border-radius: 3px;
                overflow: hidden;
                margin-bottom: 8px;
            }
            
            .progress-bar {
                height: 100%;
                background: linear-gradient(90deg, var(--primary), #4ade80);
                border-radius: 3px;
                transition: width 0.3s ease;
                min-width: 2px;
            }
            
            .btn-cancel-upload {
                padding: 4px 8px;
                background: rgba(239, 68, 68, 0.1);
                border: 1px solid rgba(239, 68, 68, 0.3);
                color: #ef4444;
                border-radius: 4px;
                cursor: pointer;
                font-size: 0.75rem;
                transition: all 0.2s ease;
            }
            
            .btn-cancel-upload:hover:not(:disabled) {
                background: rgba(239, 68, 68, 0.2);
                transform: translateY(-1px);
            }
            
            .btn-cancel-upload:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
            
            .modal.active {
                display: flex !important;
            }
            
            .modal-content {
                max-height: 80vh;
                overflow-y: auto;
                background: var(--modal-bg);
                color: var(--text-color);
            }
            
            .upload-status {
                max-height: 400px;
                overflow-y: auto;
                margin-top: 1rem;
            }

            /* Light theme overrides */
            [data-theme="light"] .upload-status-item {
                background: rgba(0, 0, 0, 0.05);
                border-color: rgba(0, 0, 0, 0.1);
            }

            [data-theme="light"] .filename {
                color: #1a202c;
            }

            [data-theme="light"] .status {
                color: rgba(0, 0, 0, 0.7);
            }

            [data-theme="light"] .progress-bar-container {
                background: rgba(0, 0, 0, 0.1);
            }
            
            @media (max-width: 768px) {
                .add-track-btn {
                    max-width: 200px;
                    width: auto;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    padding: 8px 16px;
                    margin: 4px auto;
                }
                
                .album-actions {
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    gap: 8px;
                    width: 100%;
                }
            }
        `;
        document.head.appendChild(style);
    }
}
window.AudioUpload = AudioUpload;

}