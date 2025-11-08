/**
 * Enhanced Global Viewport Manager
 * Handles navigation height calculations and viewport fixes across the entire application
 * Place this in /static/js/global-viewport-manager.js
 */

(function() {
    'use strict';

    class GlobalViewportManager {
        constructor() {
            this.config = {
                navSelector: '.main-header',
                announcementSelector: '.announcement-banner[style*="display: flex"], .grace-period-message',
                containerSelector: '.container',
                spaContainers: [
                    '.forum-spa-container',
                    '#forumSPA',
                    '.app-spa-container',
                    '.player-container',
                    '.catalog-container',
                    '.modal-spa-container',
                    '[data-viewport-fix="true"]',
                    '[data-spa-container="true"]'
                ],
                scrollableAreas: [
                    '.thread-list',
                    '.discussion-messages',
                    '.album-list',
                    '.track-list',
                    '.scrollable-content',
                    '.carousel-container',
                    '.message-list',
                    '.notification-list',
                    '[data-scrollable="true"]'
                ],
                excludeFromBodyScroll: [
                    'modal',
                    'dropdown',
                    'tooltip',
                    'popover'
                ]
            };
            
            this.measurements = {
                navHeight: 0,
                announcementHeight: 0,
                totalNavHeight: 0,
                lastViewportHeight: window.innerHeight
            };
            
            this.state = {
                initialized: false,
                spaActive: false,
                preventBodyScroll: false,
                resizeTimeout: null,
                measureTimeout: null
            };
            
            this.init();
        }
        
        init() {
            console.log('🚀 Initializing Global Viewport Manager...');
            
            // Wait for DOM to be ready
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', () => this.setup());
            } else {
                this.setup();
            }
        }
        
        setup() {
            // Initial measurement
            this.measureAndUpdate();
            
            // Set up observers and listeners
            this.setupResizeObserver();
            this.setupMutationObserver();
            this.setupEventListeners();
            
            // Apply initial fixes
            this.applyGlobalFixes();
            
            // Check for SPA containers
            this.detectAndFixContainers();
            
            // Mark as initialized
            this.state.initialized = true;
            document.body.setAttribute('data-viewport-manager', 'ready');
            
            console.log('✅ Global Viewport Manager initialized');
            this.logDebugInfo();
        }
        
        measureAndUpdate() {
            const oldMeasurements = { ...this.measurements };
            
            // Measure navigation
            const nav = document.querySelector(this.config.navSelector);
            if (nav) {
                const navRect = nav.getBoundingClientRect();
                const navStyles = window.getComputedStyle(nav);
                this.measurements.navHeight = navRect.height + 
                    parseFloat(navStyles.marginTop || 0) + 
                    parseFloat(navStyles.marginBottom || 0);
            } else {
                this.measurements.navHeight = 60; // Default fallback
            }
            
            // Measure announcement banners (visible ones only)
            this.measurements.announcementHeight = 0;
            const announcements = document.querySelectorAll(this.config.announcementSelector);
            announcements.forEach(announcement => {
                if (announcement.offsetHeight > 0 && 
                    !announcement.style.display?.includes('none') &&
                    window.getComputedStyle(announcement).display !== 'none') {
                    this.measurements.announcementHeight += announcement.offsetHeight;
                }
            });
            
            // Calculate total
            this.measurements.totalNavHeight = 
                this.measurements.navHeight + this.measurements.announcementHeight;
            
            // Check for significant changes
            const hasChanged = 
                Math.abs(oldMeasurements.totalNavHeight - this.measurements.totalNavHeight) > 2;
            
            if (hasChanged || !this.state.initialized) {
                // Update CSS variables
                this.updateCSSVariables();
                
                // Apply container fixes
                this.updateContainers();
                
                // Trigger custom event
                window.dispatchEvent(new CustomEvent('viewport:updated', {
                    detail: this.measurements
                }));
                
                console.log('📏 Viewport measurements updated:', this.measurements);
            }
        }
        
        updateCSSVariables() {
            const root = document.documentElement;
            
            // Set CSS variables
            root.style.setProperty('--nav-height', `${this.measurements.navHeight}px`);
            root.style.setProperty('--announcement-height', `${this.measurements.announcementHeight}px`);
            root.style.setProperty('--total-nav-height', `${this.measurements.totalNavHeight}px`);
            root.style.setProperty('--viewport-height', `${window.innerHeight}px`);
            root.style.setProperty('--available-height', `${window.innerHeight - this.measurements.totalNavHeight}px`);
            
            // Add data attributes for debugging
            root.setAttribute('data-nav-height', this.measurements.navHeight);
            root.setAttribute('data-total-nav-height', this.measurements.totalNavHeight);
            root.setAttribute('data-viewport-height', window.innerHeight);
        }
        
        applyGlobalFixes() {
            // Ensure navigation is fixed if it exists
            const nav = document.querySelector(this.config.navSelector);
            if (nav && !nav.style.position?.includes('fixed')) {
                nav.style.position = 'fixed';
                nav.style.top = '0';
                nav.style.left = '0';
                nav.style.right = '0';
                nav.style.zIndex = '1000';
                nav.style.transition = 'top 0.3s ease';
            }
            
            // Handle announcement banner positioning
            let topOffset = 0;
            const announcements = document.querySelectorAll(this.config.announcementSelector);
            announcements.forEach((announcement, index) => {
                if (announcement.offsetHeight > 0) {
                    announcement.style.position = 'fixed';
                    announcement.style.top = `${topOffset}px`;
                    announcement.style.left = '0';
                    announcement.style.right = '0';
                    announcement.style.zIndex = `${1001 + index}`;
                    topOffset += announcement.offsetHeight;
                }
            });
            
            // Adjust nav position if announcements exist
            if (nav && this.measurements.announcementHeight > 0) {
                nav.style.top = `${this.measurements.announcementHeight}px`;
            }
            
            // Adjust main container padding
            const container = document.querySelector(this.config.containerSelector);
            if (container) {
                container.style.paddingTop = `${this.measurements.totalNavHeight}px`;
                container.style.transition = 'padding-top 0.3s ease';
            }
        }
        
        detectAndFixContainers() {
            let foundSPA = false;
            
            // Check for SPA containers that need viewport fixes
            this.config.spaContainers.forEach(selector => {
                const containers = document.querySelectorAll(selector);
                containers.forEach(container => {
                    if (container.offsetHeight > 0) { // Only fix visible containers
                        this.fixSPAContainer(container);
                        foundSPA = true;
                    }
                });
            });
            
            // Update SPA state
            if (foundSPA !== this.state.spaActive) {
                this.state.spaActive = foundSPA;
                this.updateBodyScrollBehavior();
            }
        }
        
        fixSPAContainer(container) {
            if (!container || container.getAttribute('data-viewport-fixed') === 'true') return;
            
            // Apply viewport fix styles
            container.style.position = 'fixed';
            container.style.top = `${this.measurements.totalNavHeight}px`;
            container.style.left = '0';
            container.style.right = '0';
            container.style.bottom = '0';
            container.style.width = '100%';
            container.style.height = `calc(100vh - ${this.measurements.totalNavHeight}px)`;
            container.style.overflow = 'hidden';
            container.style.zIndex = '1';
            container.style.display = 'flex';
            container.style.flexDirection = 'column';
            container.style.boxSizing = 'border-box';
            
            // Mark as fixed
            container.setAttribute('data-viewport-fixed', 'true');
            
            // Fix scrollable children
            this.fixScrollableChildren(container);
            
            console.log('🔧 Fixed SPA container:', container.className || container.id || 'unnamed');
        }
        
        fixScrollableChildren(container) {
            this.config.scrollableAreas.forEach(selector => {
                const scrollables = container.querySelectorAll(selector);
                scrollables.forEach(scrollable => {
                    // Ensure proper scrolling
                    scrollable.style.overflowY = 'auto';
                    scrollable.style.overflowX = 'hidden';
                    scrollable.style.flex = '1';
                    scrollable.style.minHeight = '0';
                    scrollable.style.maxHeight = 'none';
                    
                    // Prevent momentum scrolling issues on iOS
                    scrollable.style.webkitOverflowScrolling = 'touch';
                    
                    // Mark as scrollable
                    scrollable.setAttribute('data-scrollable-fixed', 'true');
                });
            });
            
            // Special handling for common layout patterns
            const messagesWrapper = container.querySelector('.messages-wrapper');
            if (messagesWrapper) {
                messagesWrapper.style.flex = '1';
                messagesWrapper.style.minHeight = '0';
                messagesWrapper.style.display = 'flex';
                messagesWrapper.style.flexDirection = 'column';
            }
            
            const threadListContainer = container.querySelector('.thread-list-container');
            if (threadListContainer) {
                threadListContainer.style.flex = '1';
                threadListContainer.style.minHeight = '0';
                threadListContainer.style.overflow = 'hidden';
                threadListContainer.style.display = 'flex';
                threadListContainer.style.flexDirection = 'column';
            }
        }
        
        updateContainers() {
            // Re-apply fixes to all detected containers
            const fixedContainers = document.querySelectorAll('[data-viewport-fixed="true"]');
            fixedContainers.forEach(container => {
                container.style.top = `${this.measurements.totalNavHeight}px`;
                container.style.height = `calc(100vh - ${this.measurements.totalNavHeight}px)`;
            });
            
            // Update regular containers
            const container = document.querySelector(this.config.containerSelector);
            if (container) {
                container.style.paddingTop = `${this.measurements.totalNavHeight}px`;
            }
        }
        
        updateBodyScrollBehavior() {
            if (this.state.spaActive && !this.hasActiveModal()) {
                // Prevent body scroll when SPA is active
                document.body.style.overflow = 'hidden';
                document.body.style.position = 'fixed';
                document.body.style.width = '100%';
                document.body.setAttribute('data-spa-active', 'true');
                this.state.preventBodyScroll = true;
            } else if (!this.hasActiveModal()) {
                // Restore body scroll
                document.body.style.overflow = '';
                document.body.style.position = '';
                document.body.style.width = '';
                document.body.removeAttribute('data-spa-active');
                this.state.preventBodyScroll = false;
            }
        }
        
        hasActiveModal() {
            // Check for active modals that should allow body scroll
            return document.querySelector('.modal.active, .modal.show, [data-modal-active="true"]') !== null;
        }
        
        setupResizeObserver() {
            if (!window.ResizeObserver) return;
            
            const observer = new ResizeObserver((entries) => {
                // Debounce measurements
                clearTimeout(this.state.measureTimeout);
                this.state.measureTimeout = setTimeout(() => {
                    this.measureAndUpdate();
                }, 100);
            });
            
            // Observe navigation
            const nav = document.querySelector(this.config.navSelector);
            if (nav) observer.observe(nav);
            
            // Observe announcements
            document.querySelectorAll(this.config.announcementSelector).forEach(announcement => {
                observer.observe(announcement);
            });
            
            this.resizeObserver = observer;
        }
        
        setupMutationObserver() {
            const observer = new MutationObserver((mutations) => {
                let shouldUpdate = false;
                let shouldDetectContainers = false;
                
                mutations.forEach((mutation) => {
                    // Check for announcement visibility changes
                    if (mutation.type === 'attributes' && 
                        (mutation.target.classList.contains('announcement-banner') ||
                         mutation.target.classList.contains('grace-period-message'))) {
                        shouldUpdate = true;
                    }
                    
                    // Check for new SPA containers
                    if (mutation.type === 'childList') {
                        mutation.addedNodes.forEach((node) => {
                            if (node.nodeType === 1) {
                                // Check if it's a SPA container
                                this.config.spaContainers.forEach(selector => {
                                    if (node.matches && node.matches(selector)) {
                                        shouldDetectContainers = true;
                                    }
                                    // Also check children
                                    if (node.querySelector && node.querySelector(selector)) {
                                        shouldDetectContainers = true;
                                    }
                                });
                            }
                        });
                        
                        // Check for removed containers
                        mutation.removedNodes.forEach((node) => {
                            if (node.nodeType === 1 && 
                                node.getAttribute && 
                                node.getAttribute('data-viewport-fixed')) {
                                shouldDetectContainers = true;
                            }
                        });
                    }
                });
                
                if (shouldUpdate) {
                    setTimeout(() => this.measureAndUpdate(), 100);
                }
                
                if (shouldDetectContainers) {
                    setTimeout(() => this.detectAndFixContainers(), 200);
                }
            });
            
            observer.observe(document.body, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['style', 'class', 'data-modal-active']
            });
            
            this.mutationObserver = observer;
        }
        
        setupEventListeners() {
            // Window resize with debouncing
            window.addEventListener('resize', () => {
                clearTimeout(this.state.resizeTimeout);
                this.state.resizeTimeout = setTimeout(() => {
                    const newHeight = window.innerHeight;
                    if (Math.abs(newHeight - this.measurements.lastViewportHeight) > 50) {
                        this.measurements.lastViewportHeight = newHeight;
                        this.measureAndUpdate();
                    }
                }, 150);
            });
            
            // Page visibility change
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) {
                    setTimeout(() => this.measureAndUpdate(), 300);
                }
            });
            
            // Navigation events
            window.addEventListener('popstate', () => {
                setTimeout(() => {
                    this.detectAndFixContainers();
                    this.measureAndUpdate();
                }, 200);
            });
            
            // Custom events
            window.addEventListener('viewport:recalculate', () => {
                this.measureAndUpdate();
            });
            
            window.addEventListener('viewport:fix-container', (e) => {
                if (e.detail && e.detail.selector) {
                    this.fixContainer(e.detail.selector);
                }
            });
            
            // Announcement banner interactions
            document.addEventListener('click', (e) => {
                if (e.target.closest('.announcement-dismiss-btn, #announcementDismissBtn, #broadcastDismissBtn')) {
                    setTimeout(() => {
                        this.measureAndUpdate();
                    }, 300);
                }
            });
            
            // Modal events
            document.addEventListener('show.bs.modal', () => {
                this.updateBodyScrollBehavior();
            });
            
            document.addEventListener('hide.bs.modal', () => {
                setTimeout(() => this.updateBodyScrollBehavior(), 300);
            });
        }
        
        // Public API methods
        recalculate() {
            console.log('🔄 Manual recalculation triggered');
            this.measureAndUpdate();
            this.detectAndFixContainers();
        }
        
        fixContainer(containerSelector) {
            const containers = typeof containerSelector === 'string' 
                ? document.querySelectorAll(containerSelector)
                : [containerSelector];
                
            containers.forEach(container => {
                if (container && container.nodeType === 1) {
                    this.fixSPAContainer(container);
                }
            });
        }
        
        getMeasurements() {
            return {
                ...this.measurements,
                viewportHeight: window.innerHeight,
                availableHeight: window.innerHeight - this.measurements.totalNavHeight,
                spaActive: this.state.spaActive,
                preventBodyScroll: this.state.preventBodyScroll
            };
        }
        
        logDebugInfo() {
            console.group('🎯 Viewport Manager Debug Info');
            console.log('Measurements:', this.measurements);
            console.log('State:', this.state);
            console.log('Fixed containers:', document.querySelectorAll('[data-viewport-fixed="true"]').length);
            console.log('Scrollable areas:', document.querySelectorAll('[data-scrollable-fixed="true"]').length);
            console.groupEnd();
        }
        
        destroy() {
            // Cleanup observers
            if (this.resizeObserver) {
                this.resizeObserver.disconnect();
            }
            if (this.mutationObserver) {
                this.mutationObserver.disconnect();
            }
            
            // Reset body styles
            document.body.style.overflow = '';
            document.body.style.position = '';
            document.body.style.width = '';
            document.body.removeAttribute('data-spa-active');
            document.body.removeAttribute('data-viewport-manager');
            
            // Reset fixed containers
            const fixedContainers = document.querySelectorAll('[data-viewport-fixed="true"]');
            fixedContainers.forEach(container => {
                container.style.position = '';
                container.style.top = '';
                container.style.height = '';
                container.style.width = '';
                container.style.left = '';
                container.style.right = '';
                container.style.bottom = '';
                container.removeAttribute('data-viewport-fixed');
            });
            
            console.log('🧹 Global Viewport Manager destroyed');
        }
    }
    
    // Auto-initialize
    function initGlobalViewportManager() {
        // Create global instance
        window.GlobalViewportManager = new GlobalViewportManager();
        
        // Add utility functions to window for easy access
        window.fixViewport = () => {
            window.GlobalViewportManager.recalculate();
            console.log('✅ Viewport recalculated');
        };
        
        window.fixContainer = (selector) => {
            window.GlobalViewportManager.fixContainer(selector);
            console.log('✅ Container fixed:', selector);
        };
        
        window.getViewportInfo = () => {
            const info = window.GlobalViewportManager.getMeasurements();
            console.table(info);
            return info;
        };
        
        // Trigger initial fix for known patterns
        setTimeout(() => {
            // Auto-fix forum containers
            if (document.querySelector('#forumSPA, .forum-spa-container')) {
                window.GlobalViewportManager.fixContainer('#forumSPA');
                window.GlobalViewportManager.fixContainer('.forum-spa-container');
            }
            
            // Auto-fix other common SPA patterns
            window.GlobalViewportManager.detectAndFixContainers();
        }, 500);
        
        console.log('🚀 Global Viewport Manager ready');
        console.log('📝 Available commands:');
        console.log('   - window.fixViewport() : Recalculate all measurements');
        console.log('   - window.fixContainer(selector) : Fix a specific container');
        console.log('   - window.getViewportInfo() : Get current measurements');
    }
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initGlobalViewportManager);
    } else {
        initGlobalViewportManager();
    }
    
})();