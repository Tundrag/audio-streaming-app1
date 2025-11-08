// DocumentExtractionManager.js - Complete Implementation with Exact Touch Behavior Match
if (typeof DocumentExtractionManager === 'undefined') {

class DocumentExtractionManager {
    constructor(ttsManager) {
        this.ttsManager = ttsManager;
        this.currentSessionId = null;
        this.currentDocType = null;
        this.selectedIndices = new Set();
        this.pollingActive = false;
        
        // Drag selection state variables - exact match to text extractor
        this.isDragging = false;
        this.baselineIndex = null;
        this.initialState = null;
        this.baselineMidY = null;
        this.originalStates = null;
        
        // Auto-scroll state variables - for continuous scrolling
        this.autoScrollInterval = null;
        this.autoScrollDirection = 0; // -1 for up, 1 for down, 0 for none
        this.autoScrollSpeed = 0;
        
        // Touch state tracking variables - exact match to text extractor
        this.touchStartY = null;
        this.touchStartX = null;
        this.touchStartItem = null;
        this.touchScrolling = false;
        this.touchMoving = false;
        this.touchMoveThreshold = 15; // Exact same threshold
        this.lastTouchIndex = null;
        this.longPressTimer = null;
        this.longPressThreshold = 200; // Exact same threshold
        this.isLongPress = false;
        
        this.initializeElements();
        this.createStandaloneModal();
        this.initializeEventListeners();
        this.addStyles();
        this.initializeDragSelection();
    }

    createStandaloneModal() {
        // Create the standalone modal HTML with reorganized footer
        const modalHTML = `
            <div class="modal fade" id="documentChapterModal" tabindex="-1" aria-labelledby="documentChapterModalLabel" aria-hidden="true">
                <div class="modal-dialog modal-xl modal-dialog-scrollable">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title" id="documentChapterModalLabel">Select Content</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body">
                            <div class="text-center mb-3">
                                <h6 id="documentTitle">Document Title</h6>
                                <small class="text-muted">Tip: Long press and drag to select multiple chapters.</small>
                            </div>
                            <div class="chapter-counter mb-3" id="chapterCounter">0 of 0 sections selected</div>
                            <div id="chapterList" class="chapter-list"></div>
                        </div>
                        <div class="modal-footer">
                            <div class="footer-left">
                                <button type="button" id="selectAllChaptersBtn" class="btn btn-outline-primary btn-sm">Select All</button>
                                <button type="button" id="unselectAllChaptersBtn" class="btn btn-outline-secondary btn-sm">Unselect All</button>
                            </div>
                            <div class="footer-right">
                                <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal" id="cancelSelectionBtn">Close</button>
                                <button type="button" id="applySelectionBtn" class="btn btn-primary btn-sm">Apply Selection</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Add the modal to the document body
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        
        // Initialize the Bootstrap modal
        this.chapterModal = new bootstrap.Modal(document.getElementById('documentChapterModal'));
        
        // Update element references
        this.chapterSelection = document.getElementById('documentChapterModal');
        this.chapterList = document.getElementById('chapterList');
        this.applySelectionBtn = document.getElementById('applySelectionBtn');
        this.selectAllBtn = document.getElementById('selectAllChaptersBtn');
        this.unselectAllBtn = document.getElementById('unselectAllChaptersBtn');
    }

    initializeElements() {
        this.createDocumentUploadElements();
        
        this.ttsModal = document.getElementById('ttsModal');
        this.ttsText = document.getElementById('ttsText');
        this.documentFileInput = document.getElementById('documentFileInput');
        this.uploadDocBtn = document.getElementById('uploadDocBtn');
        
        // These will be set after createStandaloneModal() is called
        this.chapterSelection = null;
        this.chapterList = null;
        this.applySelectionBtn = null;
        this.selectAllBtn = null;
        this.unselectAllBtn = null;
    }

    createDocumentUploadElements() {
        const ttsForm = document.getElementById('ttsForm');
        if (!ttsForm) return;
        
        const textArea = ttsForm.querySelector('#ttsText');
        if (!textArea) return;
        
        // Find the parent form-group to insert after it
        const formGroup = textArea.closest('.form-group');
        
        // Only create the upload section, not the chapter selection
        const documentUploadHTML = `
            <div id="documentUploadSection" class="document-upload-section" style="margin-top: 1rem; display: none;">
                <div class="upload-zone" id="documentUploadZone">
                    <div class="upload-content">
                        <div id="defaultUploadState">
                            <i class="fas fa-cloud-upload-alt text-primary mb-3" style="font-size: 2rem;"></i>
                            <h6>Upload Document</h6>
                            <p class="text-muted small mb-3">
                                Drop files here or click to browse<br>
                                <small>Supports: EPUB, PDF, DOCX, DOC, TXT</small>
                            </p>
                            <button type="button" class="btn btn-outline-primary btn-sm" onclick="document.getElementById('documentFileInput').click()">
                                <i class="fas fa-folder-open"></i> Browse Files
                            </button>
                        </div>
                        <div id="fileSelectedState" style="display: none;">
                            <i class="fas fa-file text-success mb-2 floating-icon" style="font-size: 2rem;"></i>
                            <div class="filename-display font-weight-bold"></div>
                            <small class="text-muted">Click to change file</small>
                        </div>
                    </div>
                    <input type="file" id="documentFileInput" accept=".epub,.pdf,.docx,.doc,.txt" style="display: none;">
                </div>
                <div class="mt-2">
                    <button type="button" id="uploadDocBtn" class="btn btn-primary btn-sm" disabled>
                        <i class="fas fa-upload"></i> Extract Content
                    </button>
                    <button type="button" id="toggleManualInput" class="btn btn-link btn-sm">
                        Switch to manual input
                    </button>
                </div>
            </div>
            
            <div class="document-toggle mt-2">
                <button type="button" id="showDocumentUpload" class="btn btn-link btn-sm">
                    <i class="fas fa-file-upload"></i> Upload Document Instead
                </button>
            </div>
        `;
        
        // Insert after the entire form-group, not just the textarea
        if (formGroup) {
            formGroup.insertAdjacentHTML('afterend', documentUploadHTML);
        } else {
            // Fallback if form-group not found
            textArea.insertAdjacentHTML('afterend', documentUploadHTML);
        }
    }
    initializeEventListeners() {
        const showDocumentBtn = document.getElementById('showDocumentUpload');
        const toggleManualBtn = document.getElementById('toggleManualInput');
        const documentSection = document.getElementById('documentUploadSection');
        
        showDocumentBtn?.addEventListener('click', () => {
            if (documentSection) {
                documentSection.style.display = documentSection.style.display === 'none' ? 'block' : 'none';
                showDocumentBtn.innerHTML = documentSection.style.display === 'none' 
                    ? '<i class="fas fa-file-upload"></i> Upload Document Instead'
                    : '<i class="fas fa-keyboard"></i> Manual Input Instead';
            }
        });
        
        toggleManualBtn?.addEventListener('click', () => {
            if (documentSection) {
                documentSection.style.display = 'none';
                showDocumentBtn.innerHTML = '<i class="fas fa-file-upload"></i> Upload Document Instead';
            }
        });

        if (this.documentFileInput) {
            this.documentFileInput.addEventListener('change', (e) => this.handleFileSelection(e));
        }

        const uploadZone = document.getElementById('documentUploadZone');
        if (uploadZone) {
            uploadZone.addEventListener('click', () => this.documentFileInput?.click());
            uploadZone.addEventListener('dragover', (e) => this.handleDragOver(e));
            uploadZone.addEventListener('drop', (e) => this.handleDrop(e));
        }

        if (this.uploadDocBtn) {
            this.uploadDocBtn.addEventListener('click', () => this.handleDocumentUpload());
        }

        // Event listeners for modal elements (added after modal creation)
        setTimeout(() => {
            if (this.selectAllBtn) {
                this.selectAllBtn.addEventListener('click', () => this.selectAllChapters());
            }
            if (this.unselectAllBtn) {
                this.unselectAllBtn.addEventListener('click', () => this.unselectAllChapters());
            }
            if (this.applySelectionBtn) {
                this.applySelectionBtn.addEventListener('click', () => this.applySelection());
            }

            const cancelBtn = document.getElementById('cancelSelectionBtn');
            if (cancelBtn) {
                cancelBtn.addEventListener('click', () => this.cancelSelection());
            }
        }, 100);
    }

    initializeDragSelection() {
        // Global drag reset handlers - exact match to text extractor
        document.addEventListener('pointerup', () => this.resetDrag());
        document.addEventListener('pointercancel', () => this.resetDrag());
        
        // Global pointermove handler for mouse drag selection - exact match to text extractor
        document.addEventListener('pointermove', (e) => this.handleGlobalPointerMove(e), { passive: false });
    }

    // Drag selection methods - exact match to text extractor
    resetDrag() {
        this.isDragging = false;
        this.baselineIndex = null;
        this.initialState = null;
        this.baselineMidY = null;
        this.isLongPress = false;
        
        // Clean up auto-scroll
        this.stopAutoScroll();
        
        // Clean up original states
        this.originalStates = null;

        // Restore chapter items' default scrolling
        document.querySelectorAll('.chapter-item').forEach(item => {
            item.style.touchAction = 'pan-y';
            item.classList.remove('long-press-active');
            item.classList.remove('drag-select-active');
        });
    }

    startAutoScroll(direction, speed) {
        this.stopAutoScroll();
        this.autoScrollDirection = direction;
        this.autoScrollSpeed = speed;
        
        const modalBody = document.querySelector('#documentChapterModal .modal-body');
        if (!modalBody) return;
        
        this.autoScrollInterval = setInterval(() => {
            if (this.autoScrollDirection < 0) {
                modalBody.scrollTop -= this.autoScrollSpeed;
            } else if (this.autoScrollDirection > 0) {
                modalBody.scrollTop += this.autoScrollSpeed;
            }
        }, 16); // ~60fps for smooth scrolling
    }

    stopAutoScroll() {
        if (this.autoScrollInterval) {
            clearInterval(this.autoScrollInterval);
            this.autoScrollInterval = null;
        }
        this.autoScrollDirection = 0;
        this.autoScrollSpeed = 0;
    }

    handleGlobalPointerMove(e) {
        // Skip this handler for touch events - exact match to text extractor
        if (e.pointerType === 'touch') return;
        
        if (!this.isDragging || this.baselineIndex === null) return;
        
        // Auto-scroll modal if near top or bottom - exact match to text extractor with continuous scrolling
        const modalBody = document.querySelector('#documentChapterModal .modal-body');
        if (modalBody) {
            const rect = modalBody.getBoundingClientRect();
            const scrollMargin = 40;
            let shouldScroll = false;
            
            if (e.clientY < rect.top + scrollMargin) {
                const distance = rect.top + scrollMargin - e.clientY;
                const scrollSpeed = Math.min(distance, 40);
                this.startAutoScroll(-1, scrollSpeed);
                shouldScroll = true;
            } else if (e.clientY > rect.bottom - scrollMargin) {
                const distance = e.clientY - (rect.bottom - scrollMargin);
                const scrollSpeed = Math.min(distance, 40);
                this.startAutoScroll(1, scrollSpeed);
                shouldScroll = true;
            }
            
            if (!shouldScroll) {
                this.stopAutoScroll();
            }
        }
        
        // Determine current chapter item index under pointer - exact match to text extractor
        let currentItem = document.elementFromPoint(e.clientX, e.clientY)?.closest('.chapter-item');
        let currentIndex = null;
        
        if (currentItem) {
            currentIndex = parseInt(currentItem.getAttribute('data-index'));
        } 
        // If pointer is outside the items but still dragging, use the first or last item based on position
        else {
            const items = document.querySelectorAll('.chapter-item');
            if (items.length > 0) {
                if (modalBody) {
                    const rect = modalBody.getBoundingClientRect();
                    if (e.clientY < rect.top) {
                        currentIndex = 0;
                    } else if (e.clientY > rect.bottom) {
                        currentIndex = items.length - 1;
                    }
                } else {
                    const firstRect = items[0].getBoundingClientRect();
                    const lastRect = items[items.length - 1].getBoundingClientRect();
                    
                    if (e.clientY < firstRect.top) {
                        currentIndex = 0;
                    } else if (e.clientY > lastRect.bottom) {
                        currentIndex = items.length - 1;
                    }
                }
            }
        }
        
        if (currentIndex === null) return;
        
        // Store the original state of each item before modifying it - exact match to text extractor
        if (!this.originalStates) {
            this.originalStates = {};
            document.querySelectorAll('.chapter-item').forEach(item => {
                const index = parseInt(item.getAttribute('data-index'));
                const checkbox = item.querySelector('input[type="checkbox"]');
                this.originalStates[index] = checkbox.checked;
            });
        }
        
        // Use the min and max indices between baseline and current - exact match to text extractor
        const items = document.querySelectorAll('.chapter-item');
        const minIndex = Math.min(this.baselineIndex, currentIndex);
        const maxIndex = Math.max(this.baselineIndex, currentIndex);
        
        items.forEach((item, index) => {
            const checkbox = item.querySelector('input[type="checkbox"]');
            // If no range is selected (i.e. the drag went back to baseline) then restore original state
            if (minIndex === maxIndex) {
                checkbox.checked = this.originalStates[index];
                this.handleChapterSelection(index, checkbox.checked);
            } 
            // Toggle items that fall inside the range
            else if (index >= minIndex && index <= maxIndex) {
                checkbox.checked = !this.initialState;
                this.handleChapterSelection(index, checkbox.checked);
            } else {
                checkbox.checked = this.originalStates[index];
                this.handleChapterSelection(index, checkbox.checked);
            }
        });
    }

    // Touch event handlers - exact match to text extractor
    handleTouchStart(e) {
        if (e.touches.length !== 1) return;
        
        const touch = e.touches[0];
        this.touchStartY = touch.clientY;
        this.touchStartX = touch.clientX;
        this.touchStartItem = null;
        this.touchScrolling = false;
        this.touchMoving = false;
        this.lastTouchIndex = null;
        this.isLongPress = false;
        
        if (this.longPressTimer) {
            clearTimeout(this.longPressTimer);
        }
        
        const chapterItem = e.target.closest('.chapter-item');
        if (!chapterItem) return;
        
        const isCheckboxTouch = e.target.type === 'checkbox';
        this.touchStartItem = chapterItem;
        this.touchStartItem.isCheckboxTouch = isCheckboxTouch;
        
        if (isCheckboxTouch) {
            setTimeout(() => {
                if (chapterItem) {
                    const index = parseInt(chapterItem.getAttribute('data-index'));
                    this.handleChapterSelection(index, e.target.checked);
                }
            }, 0);
            return;
        }
        
        this.longPressTimer = setTimeout(() => {
            if (!this.touchMoving) {
                this.isLongPress = true;
                chapterItem.classList.add('long-press-active');
                chapterItem.classList.add('drag-select-active');
                if (navigator.vibrate) {
                    navigator.vibrate(50);
                }
                const checkbox = chapterItem.querySelector('input[type="checkbox"]');
                if (checkbox) {
                    this.initialState = checkbox.checked;
                    this.baselineIndex = parseInt(chapterItem.getAttribute('data-index'));
                    this.lastTouchIndex = this.baselineIndex;
                    this.isDragging = true;
                    this.originalStates = {};
                    document.querySelectorAll('.chapter-item').forEach(item => {
                        const index = parseInt(item.getAttribute('data-index'));
                        const itemCheckbox = item.querySelector('input[type="checkbox"]');
                        if (itemCheckbox) {
                            this.originalStates[index] = itemCheckbox.checked;
                        }
                    });
                }
            }
        }, this.longPressThreshold);
    }

    handleTouchMove(e) {
        if (this.isDragging || this.isLongPress) {
            e.preventDefault();
        }
        
        if (this.longPressTimer) {
            clearTimeout(this.longPressTimer);
            this.longPressTimer = null;
        }
        
        if (this.touchStartY === null || !this.touchStartItem) return;
        if (e.touches.length !== 1) return;
        
        const touch = e.touches[0];
        const moveY = touch.clientY - this.touchStartY;
        const moveX = touch.clientX - this.touchStartX;
        const moveDistance = Math.sqrt(moveY * moveY + moveX * moveX);
        
        if (!this.touchMoving && moveDistance < this.touchMoveThreshold) {
            return;
        }
        
        if (!this.touchMoving) {
            this.touchMoving = true;
            if (!this.isLongPress) {
                if (Math.abs(moveX) > Math.abs(moveY) * 0.8) {
                    this.touchScrolling = true;
                    return;
                }
                if (!this.isDragging) {
                    return;
                }
            }
        }
        
        if (this.touchScrolling || (!this.isDragging && !this.isLongPress)) return;
        
        e.preventDefault();
        
        // Auto-scroll modal if near top or bottom - exact match to text extractor with continuous scrolling
        const modalBody = document.querySelector('#documentChapterModal .modal-body');
        if (modalBody) {
            const rect = modalBody.getBoundingClientRect();
            const scrollMargin = 40;
            let shouldScroll = false;
            
            if (touch.clientY < rect.top + scrollMargin) {
                this.startAutoScroll(-1, 10);
                shouldScroll = true;
            } else if (touch.clientY > rect.bottom - scrollMargin) {
                this.startAutoScroll(1, 10);
                shouldScroll = true;
            }
            
            if (!shouldScroll) {
                this.stopAutoScroll();
            }
        }
        
        let touchTarget = document.elementFromPoint(touch.clientX, touch.clientY);
        let currentItem = touchTarget ? touchTarget.closest('.chapter-item') : null;
        
        if (!currentItem) {
            const aboveElement = document.elementFromPoint(touch.clientX, touch.clientY - 20);
            const belowElement = document.elementFromPoint(touch.clientX, touch.clientY + 20);
            if (aboveElement) currentItem = aboveElement.closest('.chapter-item');
            if (!currentItem && belowElement) currentItem = belowElement.closest('.chapter-item');
        }
        
        if (!currentItem) {
            const modalBody = document.querySelector('#documentChapterModal .modal-body');
            const items = document.querySelectorAll('.chapter-item');
            if (items.length > 0 && modalBody) {
                const rect = modalBody.getBoundingClientRect();
                if (touch.clientY < rect.top) {
                    currentItem = items[0];
                } else if (touch.clientY > rect.bottom) {
                    currentItem = items[items.length - 1];
                }
            }
        }
        
        if (currentItem) {
            const currentIndex = parseInt(currentItem.getAttribute('data-index'));
            if (!isNaN(currentIndex)) {
                this.lastTouchIndex = currentIndex;
                const items = document.querySelectorAll('.chapter-item');
                const minIndex = Math.min(this.baselineIndex, currentIndex);
                const maxIndex = Math.max(this.baselineIndex, currentIndex);
                items.forEach((item, index) => {
                    const itemCheckbox = item.querySelector('input[type="checkbox"]');
                    if (!itemCheckbox) return;
                    if (minIndex === maxIndex) {
                        itemCheckbox.checked = this.originalStates[index];
                        this.handleChapterSelection(index, itemCheckbox.checked);
                    } else if (index >= minIndex && index <= maxIndex) {
                        itemCheckbox.checked = !this.initialState;
                        this.handleChapterSelection(index, itemCheckbox.checked);
                    } else {
                        itemCheckbox.checked = this.originalStates[index];
                        this.handleChapterSelection(index, itemCheckbox.checked);
                    }
                });
            }
        }
    }

    handleTouchEnd(e) {
        // Clear the long press timer - exact match to text extractor
        if (this.longPressTimer) {
            clearTimeout(this.longPressTimer);
            this.longPressTimer = null;
        }
        
        // Stop auto-scroll
        this.stopAutoScroll();
        
        // Remove visual indicators - exact match to text extractor
        document.querySelectorAll('.chapter-item').forEach(item => {
            item.classList.remove('long-press-active');
            item.classList.remove('drag-select-active');
        });
        
        // If it's a simple tap (no drag, no long press), prevent native click and manually toggle
        if (this.touchStartItem && !this.touchMoving && !this.isDragging && !this.isLongPress) {
            e.preventDefault();
            const checkbox = this.touchStartItem.querySelector('input[type="checkbox"]');
            if (checkbox) {
                checkbox.checked = !checkbox.checked;
                const index = parseInt(this.touchStartItem.getAttribute('data-index'));
                this.handleChapterSelection(index, checkbox.checked);
            }
        }
        
        // Reset touch tracking variables - exact match to text extractor
        this.touchStartY = null;
        this.touchStartX = null;
        this.touchStartItem = null;
        this.touchScrolling = false;
        this.touchMoving = false;
        this.isDragging = false;
        this.baselineIndex = null;
        this.initialState = null;
        this.lastTouchIndex = null;
        this.isLongPress = false;
        this.originalStates = null;
    }

    addChapterItemListeners(chapterItem, checkbox) {
        if (!chapterItem.hasAttribute('data-index')) {
            let currentItems = document.querySelectorAll('.chapter-item');
            chapterItem.setAttribute('data-index', currentItems.length);
        }
        
        chapterItem.style.touchAction = 'pan-y';

        // Mouse click handler - exact match to text extractor
        chapterItem.addEventListener('click', (e) => {
            if (e.target.type === 'checkbox') return;
            if (chapterItem.dataset.skipClick) {
                delete chapterItem.dataset.skipClick;
                return;
            }
            checkbox.checked = !checkbox.checked;
            const index = parseInt(chapterItem.getAttribute('data-index'));
            this.handleChapterSelection(index, checkbox.checked);
            e.preventDefault();
        });

        // Mouse drag selection handler - exact match to text extractor
        chapterItem.addEventListener('pointerdown', (e) => {
            if (e.pointerType === 'touch') return;
            if (e.target.type === 'checkbox') return;
            chapterItem.dataset.skipClick = "true";
            chapterItem.setPointerCapture(e.pointerId);
            let rect = chapterItem.getBoundingClientRect();
            this.baselineMidY = rect.top + rect.height / 2;
            this.initialState = checkbox.checked;
            this.baselineIndex = parseInt(chapterItem.getAttribute('data-index'));
            this.originalStates = {};
            document.querySelectorAll('.chapter-item').forEach(item => {
                const index = parseInt(item.getAttribute('data-index'));
                const itemCheckbox = item.querySelector('input[type="checkbox"]');
                this.originalStates[index] = itemCheckbox.checked;
            });
            this.isDragging = true;
            checkbox.checked = !this.initialState;
            this.handleChapterSelection(this.baselineIndex, checkbox.checked);
            e.preventDefault();
        });
        
        chapterItem.addEventListener('pointerup', (e) => {
            if (e.pointerType !== 'touch') {
                chapterItem.releasePointerCapture(e.pointerId);
            }
        });
        
        // Checkbox direct click handler
        checkbox.addEventListener('click', (e) => {
            const index = parseInt(chapterItem.getAttribute('data-index'));
            this.handleChapterSelection(index, checkbox.checked);
        });

        // Touch event handlers - exact match to text extractor
        chapterItem.addEventListener('touchstart', (e) => this.handleTouchStart(e), { passive: true });
        chapterItem.addEventListener('touchmove', (e) => this.handleTouchMove(e), { passive: false });
        chapterItem.addEventListener('touchend', (e) => this.handleTouchEnd(e), { passive: false });
        chapterItem.addEventListener('touchcancel', (e) => this.handleTouchEnd(e), { passive: false });
    }

    handleFileSelection(event) {
        const file = event.target.files[0];
        const defaultState = document.getElementById('defaultUploadState');
        const selectedState = document.getElementById('fileSelectedState');
        const uploadZone = document.getElementById('documentUploadZone');
        
        if (file) {
            defaultState.style.display = 'none';
            selectedState.style.display = 'block';
            
            const filenameDisplay = document.querySelector('.filename-display');
            if (filenameDisplay) {
                filenameDisplay.textContent = file.name;
            }
            
            const fileIcon = selectedState.querySelector('i');
            const fileExt = file.name.split('.').pop().toLowerCase();
            
            if (fileIcon) {
                const iconClasses = {
                    'pdf': 'fas fa-file-pdf text-danger',
                    'epub': 'fas fa-book text-success',
                    'docx': 'fas fa-file-word text-primary',
                    'doc': 'fas fa-file-word text-primary',
                    'txt': 'fas fa-file-alt text-info'
                };
                
                fileIcon.className = (iconClasses[fileExt] || 'fas fa-file text-secondary') + ' mb-2 floating-icon';
            }
            
            if (this.uploadDocBtn) {
                this.uploadDocBtn.disabled = false;
            }
            
            uploadZone.classList.add('has-file');
            
        } else {
            this.resetFileSelection();
        }
    }

    resetFileSelection() {
        const defaultState = document.getElementById('defaultUploadState');
        const selectedState = document.getElementById('fileSelectedState');
        const uploadZone = document.getElementById('documentUploadZone');
        
        if (defaultState && selectedState) {
            defaultState.style.display = 'block';
            selectedState.style.display = 'none';
        }
        
        if (this.uploadDocBtn) {
            this.uploadDocBtn.disabled = true;
        }
        
        if (this.documentFileInput) {
            this.documentFileInput.value = '';
        }
        
        uploadZone?.classList.remove('has-file');
        this.hideChapterSelection();
    }

    handleDragOver(event) {
        event.preventDefault();
        event.currentTarget.classList.add('drag-over');
    }

    handleDrop(event) {
        event.preventDefault();
        event.currentTarget.classList.remove('drag-over');
        
        const files = event.dataTransfer.files;
        if (files.length > 0) {
            this.documentFileInput.files = files;
            this.handleFileSelection({ target: { files } });
        }
    }

    async handleDocumentUpload() {
        const file = this.documentFileInput?.files[0];
        if (!file) {
            this.showToast('Please select a file first', 'error');
            return;
        }

        this.stopPolling();

        const formData = new FormData();
        formData.append('file', file);

        try {
            if (this.uploadDocBtn) {
                this.uploadDocBtn.disabled = true;
                this.uploadDocBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Extracting...';
            }

            this.showProgressToast('Starting document extraction...', 0);

            const response = await fetch('/document/extract_content', {
                method: 'POST',
                body: formData,
                credentials: 'include'
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || `Server error: ${response.status}`);
            }

            const data = await response.json();
            this.currentSessionId = data.session_id;
            this.currentDocType = data.doc_type;

            await this.pollExtractionProgress();

        } catch (error) {
            this.hideProgressToast();
            this.showToast(`Upload failed: ${error.message}`, 'error');
            this.resetUploadState();
        }
    }

    stopPolling() {
        this.pollingActive = false;
    }

    startPolling() {
        this.pollingActive = true;
    }

    async pollExtractionProgress() {
        if (!this.currentSessionId) {
            this.resetUploadState();
            return;
        }

        this.startPolling();
        const pollInterval = 500;
        let attempts = 0;
        const maxAttempts = 240;
        let consecutiveErrors = 0;
        const maxConsecutiveErrors = 5;

        try {
            while (this.pollingActive && attempts < maxAttempts) {
                attempts++;
                
                try {
                    const timestamp = new Date().getTime();
                    const response = await fetch(`/document/extraction_progress?session_id=${this.currentSessionId}&t=${timestamp}`, {
                        credentials: 'include'
                    });
                    
                    if (!response.ok) {
                        consecutiveErrors++;
                        
                        if (consecutiveErrors >= maxConsecutiveErrors) {
                            throw new Error(`Progress check failed after ${maxConsecutiveErrors} attempts: ${response.status}`);
                        }
                        
                        await new Promise(resolve => setTimeout(resolve, pollInterval * 2));
                        continue;
                    }

                    consecutiveErrors = 0;
                    const progress = await response.json();

                    if (progress.failed) {
                        throw new Error(progress.error || 'Extraction failed');
                    }

                    const percentage = progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0;
                    const docTypeName = this.getDocTypeName(this.currentDocType);
                    
                    this.showProgressToast(`Extracting ${docTypeName}...`, percentage);

                    if (progress.completed) {
                        this.hideProgressToast();
                        await this.fetchDocumentContent();
                        return; // Don't reset state - user needs session for chapter selection
                    }

                    if (!this.pollingActive) {
                        break;
                    }

                    await new Promise(resolve => setTimeout(resolve, pollInterval));

                } catch (error) {
                    consecutiveErrors++;
                    
                    if (error.message.includes('session') || error.message.includes('Session')) {
                        throw error;
                    }
                    
                    if (consecutiveErrors >= maxConsecutiveErrors) {
                        throw error;
                    }
                    
                    await new Promise(resolve => setTimeout(resolve, pollInterval * 2));
                }
            }

            if (attempts >= maxAttempts) {
                throw new Error('Extraction timed out. Please try again.');
            }

        } catch (error) {
            this.hideProgressToast();
            this.showToast(`Extraction failed: ${error.message}`, 'error');
            this.resetUploadState(); // Only reset on error
        } finally {
            this.stopPolling();
            this.resetUploadButton(); // Only reset button UI, preserve session
        }
    }

    async fetchDocumentContent() {
        if (!this.currentSessionId) {
            throw new Error('No session ID available');
        }

        try {
            const timestamp = new Date().getTime();
            const response = await fetch(`/document/get_content_info?session_id=${this.currentSessionId}&t=${timestamp}`, {
                credentials: 'include'
            });
            
            if (!response.ok) {
                throw new Error(`Failed to fetch content: ${response.status}`);
            }

            const data = await response.json();
            this.displayChapterSelection(data);
            
        } catch (error) {
            this.showToast(`Failed to load content: ${error.message}`, 'error');
            throw error;
        }
    }

    displayChapterSelection(data) {
        const { chapters, book_title, doc_type } = data;
        
        if (!chapters || chapters.length === 0) {
            this.showToast('No content found in document', 'error');
            this.resetUploadState();
            return;
        }

        // Store session info in backup location to prevent loss
        this.backupSessionId = this.currentSessionId;
        this.backupDocType = this.currentDocType;

        const titleElement = document.getElementById('documentTitle');
        if (titleElement) {
            titleElement.textContent = `${book_title || 'Untitled Document'}`;
        }

        // Update modal title
        const modalLabel = document.getElementById('documentChapterModalLabel');
        if (modalLabel) {
            const typeName = this.getDocTypeName(doc_type);
            modalLabel.textContent = `Select ${typeName.charAt(0).toUpperCase() + typeName.slice(1)}`;
        }

        if (this.chapterList) {
            this.chapterList.innerHTML = '';
            this.selectedIndices.clear();

            chapters.forEach((chapter, index) => {
                const chapterElement = this.createChapterElement(chapter, index);
                this.chapterList.appendChild(chapterElement);
            });
            
            // Select all chapters by default after DOM is ready
            setTimeout(() => {
                this.selectAllChapters();
            }, 100);
        }

        this.showChapterSelection();
        this.showToast('Document extracted successfully! Select chapters to continue.', 'success');
    }

    createChapterElement(chapter, index) {
        const div = document.createElement('div');
        div.className = 'list-group-item chapter-item';
        div.style.touchAction = 'pan-y';
        div.dataset.index = index;

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `chapter-${index}`;
        checkbox.value = index;
        checkbox.checked = true;
        checkbox.className = 'form-check-input me-2';

        const label = document.createElement('label');
        label.htmlFor = `chapter-${index}`;
        label.className = 'form-check-label';
        label.textContent = chapter.title || `Section ${index + 1}`;

        if (chapter.content_preview) {
            const preview = document.createElement('small');
            preview.className = 'text-muted d-block';
            preview.textContent = chapter.content_preview.substring(0, 50) + '...';
            label.appendChild(preview);
        }

        const formCheck = document.createElement('div');
        formCheck.className = 'form-check';
        formCheck.appendChild(checkbox);
        formCheck.appendChild(label);

        div.appendChild(formCheck);

        // Add drag selection event listeners
        this.addChapterItemListeners(div, checkbox);

        return div;
    }

    handleChapterSelection(index, selected) {
        if (selected) {
            this.selectedIndices.add(index);
        } else {
            this.selectedIndices.delete(index);
        }
        
        // Update visual state
        const chapterItem = document.querySelector(`.chapter-item[data-index="${index}"]`);
        if (chapterItem) {
            chapterItem.classList.toggle('selected', selected);
        }
        
        const totalChapters = this.chapterList?.children.length || 0;
        this.updateSelectionCounter(this.selectedIndices.size, totalChapters);
    }

    selectAllChapters() {
        const checkboxes = this.chapterList?.querySelectorAll('input[type="checkbox"]');
        checkboxes?.forEach((checkbox, index) => {
            checkbox.checked = true;
            this.selectedIndices.add(index);
            checkbox.closest('.chapter-item').classList.add('selected');
        });
        
        const totalChapters = this.chapterList?.children.length || 0;
        this.updateSelectionCounter(totalChapters, totalChapters);
    }

    unselectAllChapters() {
        const checkboxes = this.chapterList?.querySelectorAll('input[type="checkbox"]');
        checkboxes?.forEach((checkbox) => {
            checkbox.checked = false;
            checkbox.closest('.chapter-item').classList.remove('selected');
        });
        
        this.selectedIndices.clear();
        const totalChapters = this.chapterList?.children.length || 0;
        this.updateSelectionCounter(0, totalChapters);
    }

    async applySelection() {
        if (this.selectedIndices.size === 0) {
            this.showToast('Please select at least one section', 'warning');
            return;
        }

        // Try to recover session ID if main one was lost
        let sessionId = this.currentSessionId;
        if (!sessionId && this.backupSessionId) {
            sessionId = this.backupSessionId;
            this.currentSessionId = this.backupSessionId;
            this.currentDocType = this.backupDocType;
        }

        if (!sessionId) {
            this.showToast('Session lost. Please upload the document again.', 'error');
            return;
        }

        try {
            this.showToast('Loading selected content...', 'info');
            
            const response = await fetch('/document/get_selected_content', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    selected_indices: Array.from(this.selectedIndices),
                    session_id: sessionId
                }),
                credentials: 'include'
            });

            if (!response.ok) {
                if (response.status === 404) {
                    throw new Error("Document file not found. Your session may have expired. Please upload the file again.");
                } else {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to get selected content');
                }
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let result = '';
            let totalBytesReceived = 0;

            while (true) {
                const { done, value } = await reader.read();
                
                if (done) break;
                
                totalBytesReceived += value.length;
                const chunk = decoder.decode(value, { stream: true });
                result += chunk;
            }

            if (this.ttsText) {
                this.ttsText.value = result;
                const event = new Event('input', { bubbles: true });
                this.ttsText.dispatchEvent(event);
            }

            this.hideChapterSelection();
            this.resetUploadState();
            
            const typeName = this.getDocTypeName(this.currentDocType);
            const sizeMB = (totalBytesReceived / 1024 / 1024).toFixed(1);
            this.showToast(`Selected ${typeName} applied successfully! (${sizeMB}MB)`, 'success');

        } catch (error) {
            if (error.message.includes('404') || error.message.includes('session may have expired')) {
                this.resetUploadState();
                this.showToast(error.message, 'error');
            } else {
                this.showToast(`Failed to apply selection: ${error.message}`, 'error');
            }
        }
    }

    cancelSelection() {
        this.hideChapterSelection();
        this.resetUploadState();
    }

    showChapterSelection() {
        if (this.chapterModal) {
            this.chapterModal.show();
        }
    }

    hideChapterSelection() {
        if (this.chapterModal) {
            this.chapterModal.hide();
        }
    }

    updateSelectionCounter(selected, total) {
        const counter = document.getElementById('chapterCounter');
        if (counter) {
            const typeName = this.getDocTypeName(this.currentDocType);
            counter.textContent = `${selected} of ${total} ${typeName} selected`;
        }
    }

    resetUploadButton() {
        if (this.uploadDocBtn) {
            this.uploadDocBtn.disabled = false;
            this.uploadDocBtn.innerHTML = '<i class="fas fa-upload"></i> Extract Content';
        }
    }

    resetUploadState() {
        this.resetUploadButton();
        this.currentSessionId = null;
        this.currentDocType = null;
        this.selectedIndices.clear();
        this.stopPolling();
    }

    getDocTypeName(docType) {
        const typeNames = {
            'epub': 'chapters',
            'pdf': 'pages', 
            'txt': 'sections',
            'docx': 'sections',
            'doc': 'sections'
        };
        return typeNames[docType] || 'sections';
    }

    showToast(message, type = 'info') {
        if (this.ttsManager?.albumDetails?.showToast) {
            this.ttsManager.albumDetails.showToast(message, type);
        }
    }

    showProgressToast(message, percentage) {
        let progressToast = document.getElementById('document-progress-toast');
        
        if (!progressToast) {
            progressToast = document.createElement('div');
            progressToast.id = 'document-progress-toast';
            progressToast.className = 'document-progress-toast';
            document.body.appendChild(progressToast);
        }
        
        progressToast.innerHTML = `
            <div class="progress-toast-content">
                <div class="progress-toast-text">${message}</div>
                <div class="progress-toast-percentage">${percentage}%</div>
                <div class="progress-toast-bar">
                    <div class="progress-toast-fill" style="width: ${percentage}%"></div>
                </div>
            </div>
        `;
        
        progressToast.style.display = 'block';
    }

    hideProgressToast() {
        const progressToast = document.getElementById('document-progress-toast');
        if (progressToast) {
            progressToast.style.display = 'none';
            setTimeout(() => {
                progressToast.remove();
            }, 300);
        }
    }

    addStyles() {
        if (document.querySelector('style[data-doc-extraction-styles]')) return;

        const style = document.createElement('style');
        style.setAttribute('data-doc-extraction-styles', 'true');
        style.textContent = `
            .document-upload-section {
                border: 1px solid var(--border-color, rgba(255, 255, 255, 0.1));
                border-radius: 8px;
                padding: 1rem;
                background: var(--card-bg, rgba(255, 255, 255, 0.02));
            }

            .upload-zone {
                border: 2px dashed var(--border-color, rgba(255, 255, 255, 0.3));
                border-radius: 8px;
                padding: 2rem;
                text-align: center;
                cursor: pointer;
                transition: all 0.3s ease;
                background: var(--card-bg, rgba(255, 255, 255, 0.02));
            }

            .upload-zone:hover {
                border-color: var(--primary, #3b82f6);
                background: rgba(59, 130, 246, 0.1);
            }

            .upload-zone.drag-over {
                border-color: var(--primary, #3b82f6);
                background: rgba(59, 130, 246, 0.15);
            }

            .upload-zone.has-file {
                border-color: var(--success, #22c55e);
                background: rgba(34, 197, 94, 0.1);
            }

            .floating-icon {
                animation: float 2s ease-in-out infinite;
            }

            @keyframes float {
                0%, 100% { transform: translateY(0px); }
                50% { transform: translateY(-5px); }
            }

            .document-progress-toast {
                position: fixed;
                top: 20px;
                right: 20px;
                background: var(--modal-bg, rgba(0, 0, 0, 0.9));
                color: var(--text-color, white);
                padding: 16px;
                border-radius: 8px;
                z-index: 99999;
                min-width: 300px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                border: 1px solid var(--border-color, rgba(255, 255, 255, 0.2));
                display: none;
            }

            .progress-toast-content {
                display: flex;
                flex-direction: column;
                gap: 8px;
            }

            .progress-toast-text {
                font-size: 14px;
                font-weight: 500;
            }

            .progress-toast-percentage {
                font-size: 18px;
                font-weight: bold;
                color: #3b82f6;
                text-align: center;
            }

            .progress-toast-bar {
                width: 100%;
                height: 8px;
                background: var(--progress-bg, rgba(255, 255, 255, 0.2));
                border-radius: 4px;
                overflow: hidden;
            }

            .progress-toast-fill {
                height: 100%;
                background: linear-gradient(90deg, #3b82f6, #06b6d4);
                border-radius: 4px;
                transition: width 0.3s ease;
            }

            /* Chapter Modal Styles - Enhanced Desktop Size & Mobile Footer */
            #documentChapterModal {
                z-index: 10000 !important;
            }
            
            #documentChapterModal .modal-backdrop {
                z-index: 9999 !important;
                background-color: rgba(0, 0, 0, 0.8) !important;
            }
            
            /* Enhanced Desktop Modal Size - Much Wider */
            #documentChapterModal .modal-xl {
                max-width: 95vw !important;
                width: 95vw !important;
            }
            
            #documentChapterModal .modal-content {
                background: var(--modal-bg, #1a1a1a) !important;
                color: var(--text-color, white) !important;
                border: 1px solid var(--primary, #4a90e2) !important;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.8) !important;
                position: relative !important;
                z-index: 10001 !important;
                height: 90vh !important;
                max-height: 90vh !important;
                display: flex !important;
                flex-direction: column !important;
            }

            #documentChapterModal .modal-header {
                border-bottom: 1px solid var(--border-color, rgba(255, 255, 255, 0.2)) !important;
                background: var(--card-bg, #2a2a2a) !important;
                flex-shrink: 0;
            }

            #documentChapterModal .modal-body {
                overflow-y: auto;
                flex: 1;
                padding: 1.5rem;
                display: flex;
                flex-direction: column;
            }

            /* Always Visible Footer - Compact Single Row Layout */
            #documentChapterModal .modal-footer {
                border-top: 1px solid var(--border-color, rgba(255, 255, 255, 0.2)) !important;
                background: var(--card-bg, #2a2a2a) !important;
                flex-shrink: 0;
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 0.5rem 1rem !important;
                gap: 1rem;
                position: sticky;
                bottom: 0;
                z-index: 10;
            }

            .footer-buttons {
                display: flex;
                gap: 0.5rem;
                align-items: center;
            }

            .footer-actions {
                display: flex;
                gap: 0.5rem;
                align-items: center;
            }

            /* Compact button sizing */
            #documentChapterModal .modal-footer .btn {
                padding: 0.375rem 0.75rem;
                font-size: 0.875rem;
                line-height: 1.2;
            }

            #documentChapterModal .modal-footer .btn-sm {
                padding: 0.25rem 0.5rem;
                font-size: 0.8rem;
            }

            #documentChapterModal .btn-close {
                filter: var(--btn-close-filter, invert(1)) !important;
                opacity: 1 !important;
            }
            
            #documentChapterModal .modal-title {
                color: var(--primary, #4a90e2) !important;
                font-weight: bold !important;
            }

            .chapter-list {
                /* Remove separate scroll container - use main modal scroll */
                border: 1px solid var(--border-color, rgba(255, 255, 255, 0.1));
                border-radius: 4px;
                background: var(--card-bg, rgba(255, 255, 255, 0.02));
                /* Remove height and overflow constraints */
            }

            .chapter-item {
                display: flex;
                align-items: flex-start;
                padding: 0.75rem;
                border-bottom: 1px solid var(--border-color, rgba(255, 255, 255, 0.05));
                transition: background-color 0.2s;
                cursor: pointer;
                gap: 0.75rem;
                position: relative;
                touch-action: pan-y;
            }

            .chapter-item:last-child {
                border-bottom: none;
            }

            .chapter-item:hover {
                background: var(--hover-bg, rgba(255, 255, 255, 0.05));
            }

            .chapter-item.selected {
                background: var(--selected-bg, rgba(59, 130, 246, 0.1));
            }

            .chapter-item.long-press-active {
                background-color: rgba(0, 123, 255, 0.1);
                transition: background-color 0.2s;
            }

            .chapter-item.drag-select-active {
                cursor: grab;
            }

            .chapter-item input[type="checkbox"] {
                margin-top: 0.25rem;
                flex-shrink: 0;
            }

            .chapter-item .form-check-label {
                flex: 1;
                cursor: pointer;
                margin: 0;
                color: var(--text-color, white);
            }

            .chapter-item .text-muted {
                font-size: 0.8rem;
                color: var(--text-2, rgba(255, 255, 255, 0.7)) !important;
                line-height: 1.3;
                max-height: 2.6em;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            .chapter-counter {
                font-size: 0.85rem;
                color: var(--text-2, rgba(255, 255, 255, 0.75));
                text-align: center;
                padding: 0.5rem;
                background: var(--card-bg, rgba(255, 255, 255, 0.05));
                border-radius: 4px;
            }

            .document-toggle {
                text-align: center;
            }

            /* Mobile Responsive - Compact Footer */
            @media (max-width: 768px) {
                .upload-zone {
                    padding: 1.5rem;
                }

                #documentChapterModal .modal-xl {
                    max-width: 98vw !important;
                    width: 98vw !important;
                    margin: 0.5rem auto !important;
                }

                #documentChapterModal .modal-content {
                    height: 95vh !important;
                    max-height: 95vh !important;
                }

                #documentChapterModal .modal-dialog {
                    margin: 0.5rem;
                }

                /* Compact Mobile Footer - Single Row Layout */
                #documentChapterModal .modal-footer {
                    padding: 0.4rem 0.75rem !important;
                    flex-direction: row !important;
                    justify-content: space-between !important;
                    align-items: center !important;
                    gap: 0.5rem !important;
                }

                .footer-buttons {
                    display: flex;
                    gap: 0.25rem;
                }

                .footer-actions {
                    display: flex;
                    gap: 0.25rem;
                }

                .footer-buttons .btn,
                .footer-actions .btn {
                    font-size: 0.7rem !important;
                    padding: 0.2rem 0.4rem !important;
                    line-height: 1.1 !important;
                }
            }

            /* Light Theme Overrides */
            [data-theme="light"] .document-upload-section {
                border-color: rgba(0, 0, 0, 0.1);
                background: rgba(0, 0, 0, 0.02);
            }

            [data-theme="light"] .upload-zone {
                border-color: rgba(0, 0, 0, 0.3);
                background: rgba(0, 0, 0, 0.02);
            }

            [data-theme="light"] .document-progress-toast {
                background: rgba(255, 255, 255, 0.95);
                color: #1a202c;
                border-color: rgba(0, 0, 0, 0.1);
            }

            [data-theme="light"] #documentChapterModal .modal-content {
                background: #ffffff !important;
                color: #1a202c !important;
                border-color: #4a90e2 !important;
            }

            [data-theme="light"] #documentChapterModal .modal-header {
                background: #f8f9fa !important;
                border-bottom-color: rgba(0, 0, 0, 0.1) !important;
            }

            [data-theme="light"] #documentChapterModal .modal-footer {
                background: #f8f9fa !important;
                border-top-color: rgba(0, 0, 0, 0.1) !important;
            }

            [data-theme="light"] #documentChapterModal .btn-close {
                filter: invert(0) !important;
            }

            [data-theme="light"] .chapter-list {
                border-color: rgba(0, 0, 0, 0.1);
                background: rgba(0, 0, 0, 0.02);
            }

            [data-theme="light"] .chapter-item {
                border-bottom-color: rgba(0, 0, 0, 0.05);
            }

            [data-theme="light"] .chapter-item:hover {
                background: rgba(0, 0, 0, 0.05);
            }

            [data-theme="light"] .chapter-item.selected {
                background: rgba(59, 130, 246, 0.1);
            }

            [data-theme="light"] .chapter-item .form-check-label {
                color: #1a202c;
            }

            [data-theme="light"] .chapter-item .text-muted {
                color: rgba(0, 0, 0, 0.7) !important;
            }

            [data-theme="light"] .chapter-counter {
                color: rgba(0, 0, 0, 0.75);
                background: rgba(0, 0, 0, 0.05);
            }

            [data-theme="light"] .progress-toast-bar {
                background: rgba(0, 0, 0, 0.2);
            }

            /* Enhanced Mobile Scrollbar for Textarea */
            #ttsText, .tts-text-area, textarea {
                /* Webkit browsers (Chrome, Safari, Edge) */
                scrollbar-width: auto;
                scrollbar-color: var(--scrollbar-thumb, rgba(255, 255, 255, 0.3)) var(--scrollbar-track, transparent);
            }

            #ttsText::-webkit-scrollbar, 
            .tts-text-area::-webkit-scrollbar, 
            textarea::-webkit-scrollbar {
                width: 12px;
                height: 12px;
            }

            #ttsText::-webkit-scrollbar-track, 
            .tts-text-area::-webkit-scrollbar-track, 
            textarea::-webkit-scrollbar-track {
                background: var(--scrollbar-track, rgba(255, 255, 255, 0.05));
                border-radius: 6px;
            }

            #ttsText::-webkit-scrollbar-thumb, 
            .tts-text-area::-webkit-scrollbar-thumb, 
            textarea::-webkit-scrollbar-thumb {
                background: var(--scrollbar-thumb, rgba(255, 255, 255, 0.3));
                border-radius: 6px;
                border: 2px solid var(--scrollbar-track, rgba(255, 255, 255, 0.05));
                transition: background 0.2s ease;
            }

            #ttsText::-webkit-scrollbar-thumb:hover, 
            .tts-text-area::-webkit-scrollbar-thumb:hover, 
            textarea::-webkit-scrollbar-thumb:hover {
                background: var(--scrollbar-thumb-hover, rgba(255, 255, 255, 0.5));
            }

            #ttsText::-webkit-scrollbar-corner, 
            .tts-text-area::-webkit-scrollbar-corner, 
            textarea::-webkit-scrollbar-corner {
                background: var(--scrollbar-track, rgba(255, 255, 255, 0.05));
            }

            /* Mobile Enhanced Scrollbars */
            @media (max-width: 768px) {
                #ttsText::-webkit-scrollbar, 
                .tts-text-area::-webkit-scrollbar, 
                textarea::-webkit-scrollbar {
                    width: 16px !important;
                    height: 16px !important;
                }

                #ttsText::-webkit-scrollbar-thumb, 
                .tts-text-area::-webkit-scrollbar-thumb, 
                textarea::-webkit-scrollbar-thumb {
                    background: var(--scrollbar-thumb-mobile, rgba(59, 130, 246, 0.6)) !important;
                    border-radius: 8px !important;
                    border: 3px solid var(--scrollbar-track, rgba(255, 255, 255, 0.1)) !important;
                    min-height: 40px;
                }

                #ttsText::-webkit-scrollbar-thumb:active, 
                .tts-text-area::-webkit-scrollbar-thumb:active, 
                textarea::-webkit-scrollbar-thumb:active {
                    background: var(--scrollbar-thumb-active, rgba(59, 130, 246, 0.8)) !important;
                }

                #ttsText::-webkit-scrollbar-track, 
                .tts-text-area::-webkit-scrollbar-track, 
                textarea::-webkit-scrollbar-track {
                    background: var(--scrollbar-track-mobile, rgba(255, 255, 255, 0.1)) !important;
                    border-radius: 8px !important;
                }

                /* Firefox mobile scrollbar */
                #ttsText, .tts-text-area, textarea {
                    scrollbar-width: thick !important;
                    scrollbar-color: var(--scrollbar-thumb-mobile, rgba(59, 130, 246, 0.6)) var(--scrollbar-track-mobile, rgba(255, 255, 255, 0.1)) !important;
                }
            }

            /* Touch-friendly scrolling */
            @media (pointer: coarse) {
                #ttsText, .tts-text-area, textarea {
                    -webkit-overflow-scrolling: touch;
                    scroll-behavior: smooth;
                    overscroll-behavior: contain;
                }
            }

            /* Light theme scrollbar overrides */
            [data-theme="light"] #ttsText::-webkit-scrollbar-track,
            [data-theme="light"] .tts-text-area::-webkit-scrollbar-track,
            [data-theme="light"] textarea::-webkit-scrollbar-track {
                background: rgba(0, 0, 0, 0.05);
            }

            [data-theme="light"] #ttsText::-webkit-scrollbar-thumb,
            [data-theme="light"] .tts-text-area::-webkit-scrollbar-thumb,
            [data-theme="light"] textarea::-webkit-scrollbar-thumb {
                background: rgba(0, 0, 0, 0.3);
                border-color: rgba(0, 0, 0, 0.05);
            }

            [data-theme="light"] #ttsText::-webkit-scrollbar-thumb:hover,
            [data-theme="light"] .tts-text-area::-webkit-scrollbar-thumb:hover,
            [data-theme="light"] textarea::-webkit-scrollbar-thumb:hover {
                background: rgba(0, 0, 0, 0.5);
            }

            @media (max-width: 768px) {
                [data-theme="light"] #ttsText::-webkit-scrollbar-thumb,
                [data-theme="light"] .tts-text-area::-webkit-scrollbar-thumb,
                [data-theme="light"] textarea::-webkit-scrollbar-thumb {
                    background: rgba(59, 130, 246, 0.6) !important;
                    border-color: rgba(0, 0, 0, 0.1) !important;
                }

                [data-theme="light"] #ttsText::-webkit-scrollbar-thumb:active,
                [data-theme="light"] .tts-text-area::-webkit-scrollbar-thumb:active,
                [data-theme="light"] textarea::-webkit-scrollbar-thumb:active {
                    background: rgba(59, 130, 246, 0.8) !important;
                }

                [data-theme="light"] #ttsText::-webkit-scrollbar-track,
                [data-theme="light"] .tts-text-area::-webkit-scrollbar-track,
                [data-theme="light"] textarea::-webkit-scrollbar-track {
                    background: rgba(0, 0, 0, 0.1) !important;
                }

                [data-theme="light"] #ttsText, 
                [data-theme="light"] .tts-text-area, 
                [data-theme="light"] textarea {
                    scrollbar-color: rgba(59, 130, 246, 0.6) rgba(0, 0, 0, 0.1) !important;
                }
            }
        `;
        document.head.appendChild(style);
    }
}
window.DocumentExtractionManager = DocumentExtractionManager;
}