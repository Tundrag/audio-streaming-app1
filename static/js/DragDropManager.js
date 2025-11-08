// DragDropManager.js - Smooth and responsive drag & drop
class DragDropManager {
    constructor(albumDetailPage) {
        console.log('🎯 Initializing Smooth Drag & Drop Manager');
        
        this.parentPage = albumDetailPage;
        this.tracksList = albumDetailPage.tracksList;
        this.albumId = albumDetailPage.albumId;
        this.userPermissions = albumDetailPage.userPermissions;
        
        // Drag state
        this.isDragging = false;
        this.draggedElement = null;
        this.ghostElement = null;
        this.dropPreview = null;
        this.initialPosition = { x: 0, y: 0 };
        this.currentPosition = { x: 0, y: 0 };
        this.dragOffset = { x: 0, y: 0 };
        
        // Auto-scroll state
        this.autoScrollInterval = null;
        this.scrollSpeed = 0;
        this.scrollZone = 50; // pixels from edge to trigger auto-scroll
        this.maxScrollSpeed = 15;
        
        // Animation and timing
        this.animationFrame = null;
        this.lastMoveTime = 0;
        this.moveThrottle = 16; // ~60fps
        
        // Touch support
        this.touchStartTime = 0;
        this.touchMoved = false;
        
        if (!this.userPermissions.can_create || !this.tracksList) {
            console.log('🚫 Drag & drop disabled - no permissions or track list');
            return;
        }
        
        this.initializeDragDrop();
        this.addDragDropStyles();
        
        console.log('✅ Smooth Drag & Drop Manager initialized');
    }

    initializeDragDrop() {
        // Add drag handles and make tracks draggable
        this.updateDragHandles();
        
        // Add global event listeners for better performance
        document.addEventListener('mousemove', this.handleMouseMove.bind(this), { passive: false });
        document.addEventListener('mouseup', this.handleMouseUp.bind(this));
        document.addEventListener('touchmove', this.handleTouchMove.bind(this), { passive: false });
        document.addEventListener('touchend', this.handleTouchEnd.bind(this));
        
        // Prevent default drag behavior on images and other elements
        this.tracksList.addEventListener('dragstart', (e) => {
            if (!e.target.closest('.drag-handle')) {
                e.preventDefault();
            }
        });
        
        // Add resize observer to handle dynamic content changes
        if (window.ResizeObserver) {
            this.resizeObserver = new ResizeObserver(() => {
                this.updateScrollContainer();
            });
            this.resizeObserver.observe(this.tracksList);
        }
    }

    updateDragHandles() {
        const trackItems = this.tracksList.querySelectorAll('.track-item');
        
        trackItems.forEach(trackItem => {
            let dragHandle = trackItem.querySelector('.drag-handle');
            
            if (!dragHandle) {
                // Create drag handle if it doesn't exist
                dragHandle = document.createElement('div');
                dragHandle.className = 'drag-handle';
                dragHandle.innerHTML = '<i class="fas fa-grip-vertical"></i>';
                trackItem.insertBefore(dragHandle, trackItem.firstChild);
            }
            
            // Remove existing listeners to avoid duplicates
            dragHandle.removeEventListener('mousedown', this.handleMouseDown);
            dragHandle.removeEventListener('touchstart', this.handleTouchStart);
            
            // Add new listeners
            dragHandle.addEventListener('mousedown', this.handleMouseDown.bind(this));
            dragHandle.addEventListener('touchstart', this.handleTouchStart.bind(this), { passive: false });
            
            // Make the track item draggable
            trackItem.setAttribute('draggable', 'false'); // We'll handle dragging manually
        });
    }

    // ========================================
    // MOUSE EVENTS
    // ========================================

    handleMouseDown(e) {
        e.preventDefault();
        e.stopPropagation();
        
        const trackItem = e.target.closest('.track-item');
        if (!trackItem) return;
        
        this.startDrag(trackItem, e.clientX, e.clientY);
    }

    handleMouseMove(e) {
        if (!this.isDragging) return;
        
        e.preventDefault();
        e.stopPropagation();
        
        const now = Date.now();
        if (now - this.lastMoveTime < this.moveThrottle) return;
        this.lastMoveTime = now;
        
        this.updateDragPosition(e.clientX, e.clientY);
    }

    handleMouseUp(e) {
        if (!this.isDragging) return;
        
        e.preventDefault();
        e.stopPropagation();
        
        this.endDrag(e.clientX, e.clientY);
    }

    // ========================================
    // TOUCH EVENTS
    // ========================================

    handleTouchStart(e) {
        e.preventDefault();
        e.stopPropagation();
        
        const trackItem = e.target.closest('.track-item');
        if (!trackItem) return;
        
        const touch = e.touches[0];
        this.touchStartTime = Date.now();
        this.touchMoved = false;
        
        // Small delay to distinguish from tap
        setTimeout(() => {
            if (!this.touchMoved && Date.now() - this.touchStartTime > 200) {
                this.startDrag(trackItem, touch.clientX, touch.clientY);
            }
        }, 200);
    }

    handleTouchMove(e) {
        this.touchMoved = true;
        
        if (!this.isDragging) return;
        
        e.preventDefault();
        e.stopPropagation();
        
        const touch = e.touches[0];
        const now = Date.now();
        if (now - this.lastMoveTime < this.moveThrottle) return;
        this.lastMoveTime = now;
        
        this.updateDragPosition(touch.clientX, touch.clientY);
    }

    handleTouchEnd(e) {
        if (!this.isDragging) return;
        
        e.preventDefault();
        e.stopPropagation();
        
        const touch = e.changedTouches[0];
        this.endDrag(touch.clientX, touch.clientY);
    }

    // ========================================
    // DRAG LIFECYCLE
    // ========================================

    startDrag(trackItem, clientX, clientY) {
        if (this.isDragging) return;
        
        console.log('🎯 Starting drag for track:', trackItem.dataset.trackId);
        
        this.isDragging = true;
        this.draggedElement = trackItem;
        
        const rect = trackItem.getBoundingClientRect();
        this.dragOffset = {
            x: clientX - rect.left,
            y: clientY - rect.top
        };
        
        this.initialPosition = { x: clientX, y: clientY };
        this.currentPosition = { x: clientX, y: clientY };
        
        // Create ghost element
        this.createGhostElement(trackItem);
        
        // Create drop preview
        this.createDropPreview();
        
        // Add dragging class
        trackItem.classList.add('dragging');
        document.body.classList.add('drag-active');
        
        // Disable text selection during drag
        document.body.style.userSelect = 'none';
        document.body.style.webkitUserSelect = 'none';
        
        // Start auto-scroll monitoring
        this.startAutoScroll();
        
        // Update ghost position
        this.updateGhostPosition(clientX, clientY);
    }

    updateDragPosition(clientX, clientY) {
        if (!this.isDragging) return;
        
        this.currentPosition = { x: clientX, y: clientY };
        
        // Update ghost position with smooth animation
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
        
        this.animationFrame = requestAnimationFrame(() => {
            this.updateGhostPosition(clientX, clientY);
            this.updateDropPreview(clientX, clientY);
            this.updateAutoScroll(clientY);
        });
    }

    endDrag(clientX, clientY) {
        if (!this.isDragging) return;
        
        console.log('🎯 Ending drag');
        
        this.isDragging = false;
        
        // Stop auto-scroll
        this.stopAutoScroll();
        
        // Cancel any pending animation frames
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
        }
        
        // Find drop target
        const dropTarget = this.findDropTarget(clientX, clientY);
        
        // Perform drop with animation
        this.performDrop(dropTarget).then(() => {
            this.cleanup();
        });
    }

    async performDrop(dropTarget) {
        if (!dropTarget || dropTarget === this.draggedElement) {
            // No valid drop target, animate back to original position
            await this.animateBack();
            return;
        }
        
        console.log('🎯 Performing drop');
        
        // Determine drop position
        const dropRect = dropTarget.getBoundingClientRect();
        const dragRect = this.draggedElement.getBoundingClientRect();
        const insertAfter = this.currentPosition.y > dropRect.top + dropRect.height / 2;
        
        // Visual feedback during drop
        this.showDropFeedback(dropTarget, insertAfter);
        
        // Perform DOM manipulation
        try {
            if (insertAfter) {
                dropTarget.parentNode.insertBefore(this.draggedElement, dropTarget.nextSibling);
            } else {
                dropTarget.parentNode.insertBefore(this.draggedElement, dropTarget);
            }
            
            // Update track orders on server
            await this.updateTrackOrders();
            
            // Success feedback
            this.parentPage.showToast('Track order updated', 'success');
            
        } catch (error) {
            console.error('Drop error:', error);
            this.parentPage.showToast('Failed to update track order', 'error');
            
            // Revert DOM changes if API call failed
            // (You might want to store original order and revert here)
        }
    }

    async animateBack() {
        if (!this.ghostElement) return;
        
        const originalRect = this.draggedElement.getBoundingClientRect();
        
        // Animate ghost back to original position
        this.ghostElement.style.transition = 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
        this.ghostElement.style.transform = `translate(${originalRect.left - this.dragOffset.x}px, ${originalRect.top - this.dragOffset.y}px)`;
        
        return new Promise(resolve => {
            setTimeout(resolve, 300);
        });
    }

    // ========================================
    // VISUAL ELEMENTS
    // ========================================

    createGhostElement(trackItem) {
        this.ghostElement = trackItem.cloneNode(true);
        this.ghostElement.className = 'track-item drag-ghost';
        
        // Remove interactive elements from ghost
        this.ghostElement.querySelectorAll('button, input, a').forEach(el => {
            el.style.pointerEvents = 'none';
        });
        
        // Style the ghost
        const rect = trackItem.getBoundingClientRect();
        Object.assign(this.ghostElement.style, {
            position: 'fixed',
            top: '0',
            left: '0',
            width: `${rect.width}px`,
            height: `${rect.height}px`,
            zIndex: '9999',
            pointerEvents: 'none',
            transform: `translate(${rect.left - this.dragOffset.x}px, ${rect.top - this.dragOffset.y}px)`,
            transition: 'none',
            opacity: '0.9',
            boxShadow: '0 10px 30px rgba(0,0,0,0.3)',
            borderRadius: '8px'
        });
        
        document.body.appendChild(this.ghostElement);
    }

    createDropPreview() {
        this.dropPreview = document.createElement('div');
        this.dropPreview.className = 'drop-preview';
        this.dropPreview.style.display = 'none';
        this.tracksList.appendChild(this.dropPreview);
    }

    updateGhostPosition(clientX, clientY) {
        if (!this.ghostElement) return;
        
        const x = clientX - this.dragOffset.x;
        const y = clientY - this.dragOffset.y;
        
        this.ghostElement.style.transform = `translate(${x}px, ${y}px)`;
    }

    updateDropPreview(clientX, clientY) {
        const dropTarget = this.findDropTarget(clientX, clientY);
        
        if (!dropTarget || dropTarget === this.draggedElement) {
            this.dropPreview.style.display = 'none';
            this.removeDropHighlights();
            return;
        }
        
        const dropRect = dropTarget.getBoundingClientRect();
        const insertAfter = clientY > dropRect.top + dropRect.height / 2;
        
        // Update drop preview position
        const previewTop = insertAfter ? 
            dropRect.bottom - this.tracksList.getBoundingClientRect().top :
            dropRect.top - this.tracksList.getBoundingClientRect().top;
        
        Object.assign(this.dropPreview.style, {
            display: 'block',
            top: `${previewTop}px`,
            left: '0',
            right: '0'
        });
        
        // Highlight drop target
        this.removeDropHighlights();
        dropTarget.classList.add('drop-target');
    }

    findDropTarget(clientX, clientY) {
        // Temporarily hide ghost to get accurate elementFromPoint
        const ghostDisplay = this.ghostElement?.style.display;
        if (this.ghostElement) {
            this.ghostElement.style.display = 'none';
        }
        
        const elementAtPoint = document.elementFromPoint(clientX, clientY);
        let dropTarget = elementAtPoint?.closest('.track-item');
        
        // Restore ghost visibility
        if (this.ghostElement) {
            this.ghostElement.style.display = ghostDisplay || 'block';
        }
        
        // Don't allow dropping on the dragged element itself
        if (dropTarget === this.draggedElement) {
            dropTarget = null;
        }
        
        return dropTarget;
    }

    showDropFeedback(dropTarget, insertAfter) {
        dropTarget.classList.add(insertAfter ? 'drop-after' : 'drop-before');
        
        setTimeout(() => {
            dropTarget.classList.remove('drop-after', 'drop-before');
        }, 300);
    }

    removeDropHighlights() {
        this.tracksList.querySelectorAll('.drop-target').forEach(el => {
            el.classList.remove('drop-target');
        });
    }

    // ========================================
    // AUTO-SCROLL
    // ========================================

    startAutoScroll() {
        this.updateScrollContainer();
        
        if (this.autoScrollInterval) {
            clearInterval(this.autoScrollInterval);
        }
        
        this.autoScrollInterval = setInterval(() => {
            this.performAutoScroll();
        }, 16); // ~60fps
    }

    stopAutoScroll() {
        if (this.autoScrollInterval) {
            clearInterval(this.autoScrollInterval);
            this.autoScrollInterval = null;
        }
        this.scrollSpeed = 0;
    }

    updateScrollContainer() {
        // Find the scrollable container (could be window or a specific element)
        this.scrollContainer = this.tracksList.closest('.scrollable-container') || window;
        this.scrollElement = this.scrollContainer === window ? document.documentElement : this.scrollContainer;
    }

    updateAutoScroll(clientY) {
        const containerRect = this.scrollContainer === window ? 
            { top: 0, bottom: window.innerHeight } :
            this.scrollContainer.getBoundingClientRect();
        
        const distanceFromTop = clientY - containerRect.top;
        const distanceFromBottom = containerRect.bottom - clientY;
        
        if (distanceFromTop < this.scrollZone) {
            // Scroll up
            const intensity = (this.scrollZone - distanceFromTop) / this.scrollZone;
            this.scrollSpeed = -this.maxScrollSpeed * intensity;
        } else if (distanceFromBottom < this.scrollZone) {
            // Scroll down
            const intensity = (this.scrollZone - distanceFromBottom) / this.scrollZone;
            this.scrollSpeed = this.maxScrollSpeed * intensity;
        } else {
            // No scroll
            this.scrollSpeed = 0;
        }
    }

    performAutoScroll() {
        if (Math.abs(this.scrollSpeed) < 0.1) return;
        
        const currentScroll = this.scrollElement.scrollTop;
        const newScroll = currentScroll + this.scrollSpeed;
        
        this.scrollElement.scrollTop = Math.max(0, newScroll);
        
        // Update ghost position to account for scroll
        if (this.ghostElement) {
            const currentTransform = this.ghostElement.style.transform;
            const match = currentTransform.match(/translate\(([^,]+),\s*([^)]+)\)/);
            if (match) {
                const x = parseFloat(match[1]);
                const y = parseFloat(match[2]) - this.scrollSpeed;
                this.ghostElement.style.transform = `translate(${x}px, ${y}px)`;
            }
        }
    }

    // ========================================
    // SERVER COMMUNICATION
    // ========================================

    async updateTrackOrders() {
        try {
            const tracks = Array.from(this.tracksList.querySelectorAll('.track-item'));
            const trackOrders = tracks.map((track, index) => ({
                id: track.dataset.trackId,
                order: index + 1
            }));

            const response = await fetch(`/api/albums/${this.albumId}/tracks/reorder`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tracks: trackOrders })
            });

            if (!response.ok) {
                throw new Error('Failed to update track orders');
            }
            
            console.log('✅ Track orders updated successfully');
            
        } catch (error) {
            console.error('❌ Failed to update track orders:', error);
            throw error;
        }
    }

    // ========================================
    // CLEANUP
    // ========================================

    cleanup() {
        // Remove ghost element
        if (this.ghostElement) {
            this.ghostElement.remove();
            this.ghostElement = null;
        }
        
        // Remove drop preview
        if (this.dropPreview) {
            this.dropPreview.remove();
            this.dropPreview = null;
        }
        
        // Remove classes
        if (this.draggedElement) {
            this.draggedElement.classList.remove('dragging');
            this.draggedElement = null;
        }
        
        document.body.classList.remove('drag-active');
        this.removeDropHighlights();
        
        // Restore text selection
        document.body.style.userSelect = '';
        document.body.style.webkitUserSelect = '';
        
        // Stop auto-scroll
        this.stopAutoScroll();
        
        // Reset state
        this.isDragging = false;
        this.scrollSpeed = 0;
        
        console.log('🧹 Drag & drop cleanup complete');
    }

    // ========================================
    // PUBLIC METHODS
    // ========================================

    refreshDragHandles() {
        console.log('🔄 Refreshing drag handles');
        this.updateDragHandles();
    }

    destroy() {
        console.log('🗑️ Destroying Drag & Drop Manager');
        
        this.cleanup();
        
        // Remove global event listeners
        document.removeEventListener('mousemove', this.handleMouseMove);
        document.removeEventListener('mouseup', this.handleMouseUp);
        document.removeEventListener('touchmove', this.handleTouchMove);
        document.removeEventListener('touchend', this.handleTouchEnd);
        
        // Disconnect resize observer
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
        }
    }

    // ========================================
    // STYLES
    // ========================================

    addDragDropStyles() {
        const style = document.createElement('style');
        style.textContent = `
            /* Enhanced Drag & Drop Styles */
            .drag-handle {
                display: flex;
                align-items: center;
                justify-content: center;
                width: 20px;
                margin-right: 8px;
                cursor: grab;
                color: var(--text-2);
                opacity: 0.6;
                transition: all 0.2s ease;
                user-select: none;
                -webkit-user-select: none;
            }
            
            .drag-handle:hover {
                opacity: 1;
                color: var(--primary);
                transform: scale(1.1);
            }
            
            .drag-handle:active {
                cursor: grabbing;
                transform: scale(0.95);
            }
            
            .track-item.dragging {
                opacity: 0.5;
                transform: scale(0.98);
                transition: opacity 0.2s ease, transform 0.2s ease;
            }
            
            .track-item.drag-ghost {
                opacity: 0.9 !important;
                transform: rotate(2deg) !important;
                box-shadow: 0 15px 35px rgba(0,0,0,0.4) !important;
                background: var(--surface) !important;
                border: 2px solid var(--primary) !important;
                z-index: 9999 !important;
            }
            
            .track-item.drop-target {
                background: rgba(var(--primary-rgb), 0.1);
                border-color: var(--primary);
                transform: translateX(4px);
                transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            }
            
            .track-item.drop-before {
                border-top: 3px solid var(--primary);
                animation: dropHighlight 0.3s ease;
            }
            
            .track-item.drop-after {
                border-bottom: 3px solid var(--primary);
                animation: dropHighlight 0.3s ease;
            }
            
            .drop-preview {
                position: absolute;
                height: 3px;
                background: linear-gradient(90deg, var(--primary), var(--primary-light));
                border-radius: 2px;
                box-shadow: 0 0 10px var(--primary);
                animation: dropPreviewPulse 1s infinite;
                z-index: 100;
            }
            
            body.drag-active {
                cursor: grabbing !important;
            }
            
            body.drag-active * {
                cursor: grabbing !important;
                user-select: none !important;
                -webkit-user-select: none !important;
            }
            
            /* Mobile optimizations */
            @media (max-width: 768px) {
                .drag-handle {
                    width: 24px;
                    opacity: 0.8;
                }
                
                .track-item.drag-ghost {
                    transform: rotate(1deg) scale(1.02) !important;
                }
            }
            
            /* Animations */
            @keyframes dropHighlight {
                0% { box-shadow: 0 0 0 var(--primary); }
                50% { box-shadow: 0 0 20px var(--primary); }
                100% { box-shadow: 0 0 0 var(--primary); }
            }
            
            @keyframes dropPreviewPulse {
                0%, 100% { opacity: 0.8; transform: scaleY(1); }
                50% { opacity: 1; transform: scaleY(1.5); }
            }
            
            /* Smooth transitions for all track items */
            .track-item:not(.dragging):not(.drag-ghost) {
                transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1),
                           background 0.2s ease,
                           border-color 0.2s ease;
            }
            
            /* Enhanced visual feedback */
            .track-item {
                position: relative;
                overflow: visible;
            }
            
            .track-item::before {
                content: '';
                position: absolute;
                top: -2px;
                left: -2px;
                right: -2px;
                bottom: -2px;
                background: linear-gradient(45deg, var(--primary), var(--primary-light));
                border-radius: inherit;
                opacity: 0;
                z-index: -1;
                transition: opacity 0.2s ease;
            }
            
            .track-item.drop-target::before {
                opacity: 0.2;
            }
        `;
        document.head.appendChild(style);
    }
}