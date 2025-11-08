// Complete AJAX Navigation System for Music Streaming App
// This replaces or enhances your existing ajax-navigation.js

class AjaxNavigationManager {
    constructor() {
        this.isNavigating = false;
        this.currentUrl = window.location.pathname;
        this.loadingOverlay = null;
        this.navigationCache = new Map();
        this.maxCacheSize = 5;
        
        // Track initialization state
        this.initialized = false;
        
        console.log('🚀 AJAX Navigation Manager initialized');
    }

    init() {
        if (this.initialized) {
            console.log('⚠️ AJAX Navigation already initialized');
            return;
        }
        
        this.initialized = true;
        this.setupEventListeners();
        this.setupPopstateHandler();
        this.createLoadingOverlay();
        this.updateActiveNavigation();
        
        console.log('✅ AJAX Navigation setup complete');
    }

    setupEventListeners() {
        // Handle all navigation clicks
        document.addEventListener('click', this.handleNavigationClick.bind(this), true);
        
        // Handle form submissions that should be AJAX
        document.addEventListener('submit', this.handleFormSubmit.bind(this));
        
        // Update navigation when page changes
        window.addEventListener('locationchange', this.updateActiveNavigation.bind(this));
    }

    handleNavigationClick(event) {
        const link = event.target.closest('a');
        if (!link) return;

        const href = link.getAttribute('href');
        
        // Skip if not a navigation link we should handle
        if (!this.shouldHandleLink(link, href)) {
            return;
        }

        // Prevent default navigation
        event.preventDefault();
        event.stopPropagation();
        
        console.log(`🔗 AJAX navigating to: ${href}`);
        
        // Navigate using AJAX
        this.navigateTo(href).catch(error => {
            console.error('Navigation failed:', error);
            // Fallback to regular navigation
            window.location.href = href;
        });
    }

    shouldHandleLink(link, href) {
        // Skip if no href or invalid
        if (!href || href.startsWith('#') || href === 'javascript:void(0)' || href === '#') {
            return false;
        }
        
        // Skip external links
        if (href.startsWith('http') && !href.includes(window.location.host)) {
            return false;
        }
        
        // Skip if has target="_blank" or download attribute
        if (link.target === '_blank' || link.hasAttribute('download')) {
            return false;
        }
        
        // Skip if has data-no-ajax attribute
        if (link.hasAttribute('data-no-ajax')) {
            return false;
        }
        
        // Skip logout and special links
        if (href.includes('/logout') || href.includes('/api/')) {
            return false;
        }
        
        // Handle these specific navigation paths
        const navigationPaths = [
            '/home', '/catalog', '/collection', '/my-albums', '/my-benefits', 
            '/my-downloads', '/my-book-requests', '/support', '/statistics',
            '/admin/', '/creator/', '/album/', '/artist/', '/track/'
        ];
        
        return navigationPaths.some(path => href.startsWith(path));
    }

    async navigateTo(url, options = {}) {
        if (this.isNavigating) {
            console.log('🔄 Navigation already in progress');
            return;
        }

        const {
            pushState = true,
            showLoading = true,
            skipCache = false
        } = options;

        try {
            this.isNavigating = true;
            
            if (showLoading) {
                this.showLoadingOverlay();
            }

            // Check cache first
            let responseData = null;
            const cacheKey = url;
            
            if (!skipCache && this.navigationCache.has(cacheKey)) {
                console.log(`📋 Using cached content for: ${url}`);
                responseData = this.navigationCache.get(cacheKey);
            } else {
                console.log(`🌐 Fetching fresh content for: ${url}`);
                responseData = await this.fetchPageContent(url);
                
                // Cache the response
                this.cacheResponse(cacheKey, responseData);
            }

            // Update the page
            await this.updatePageContent(responseData, url);
            
            // Update browser history
            if (pushState && url !== this.currentUrl) {
                const title = responseData.title || document.title;
                history.pushState({ url, title }, title, url);
                
                // Dispatch custom event
                window.dispatchEvent(new CustomEvent('locationchange', {
                    detail: { url, title, method: 'ajax' }
                }));
            }
            
            this.currentUrl = url;
            console.log(`✅ Successfully navigated to: ${url}`);
            
        } catch (error) {
            console.error('❌ Navigation error:', error);
            throw error;
        } finally {
            this.isNavigating = false;
            this.hideLoadingOverlay();
        }
    }

    async fetchPageContent(url) {
        const response = await fetch(url, {
            method: 'GET',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Cache-Control': 'no-cache'
            },
            credentials: 'same-origin'
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const html = await response.text();
        return this.parsePageContent(html);
    }

    parsePageContent(html) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        
        // Extract content and metadata
        const main = doc.querySelector('main');
        const title = doc.querySelector('title')?.textContent || 'Album Catalog';
        const pageTitle = doc.querySelector('.main-header h1')?.textContent || '';
        
        if (!main) {
            throw new Error('Could not find main content in response');
        }

        return {
            content: main.innerHTML,
            title: title,
            pageTitle: pageTitle,
            url: window.location.pathname
        };
    }

    async updatePageContent(data, url) {
        const main = document.querySelector('main');
        if (!main) {
            throw new Error('Could not find main element to update');
        }

        // Update main content
        main.innerHTML = data.content;
        
        // Update page title
        if (data.title) {
            document.title = data.title;
        }
        
        // Update header title
        const headerTitle = document.querySelector('.main-header h1');
        if (headerTitle && data.pageTitle) {
            headerTitle.textContent = data.pageTitle;
        }
        
        // Update active navigation
        this.updateActiveNavigation();
        
        // Re-initialize page-specific JavaScript
        await this.reinitializePageScripts(url);
        
        // Dispatch page loaded events
        this.dispatchPageEvents(url);
    }

    async reinitializePageScripts(url) {
        try {
            // Determine page type from URL
            const pageType = this.getPageTypeFromUrl(url);
            
            console.log(`🔧 Reinitializing scripts for page type: ${pageType}`);
            
            // Reinitialize common functionality
            this.reinitializeCommonScripts();
            
            // Page-specific initialization
            switch (pageType) {
                case 'home':
                    if (typeof window.initializeHomePage === 'function') {
                        setTimeout(() => window.initializeHomePage(), 100);
                    }
                    break;
                    
                case 'catalog':
                    if (typeof window.initializeCatalogPage === 'function') {
                        setTimeout(() => window.initializeCatalogPage(), 100);
                    }
                    break;
                    
                case 'album':
                    if (typeof window.initializeAlbumPage === 'function') {
                        setTimeout(() => window.initializeAlbumPage(), 100);
                    }
                    break;
                    
                case 'collection':
                    if (typeof window.initializeCollectionPage === 'function') {
                        setTimeout(() => window.initializeCollectionPage(), 100);
                    }
                    break;
            }
            
        } catch (error) {
            console.error('Error reinitializing scripts:', error);
        }
    }

    reinitializeCommonScripts() {
        // Reinitialize tooltips, dropdowns, etc.
        if (window.bootstrap) {
            // Bootstrap tooltips
            const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
            tooltipTriggerList.map(function (tooltipTriggerEl) {
                return new window.bootstrap.Tooltip(tooltipTriggerEl);
            });
            
            // Bootstrap dropdowns
            const dropdownElementList = [].slice.call(document.querySelectorAll('.dropdown-toggle'));
            dropdownElementList.map(function (dropdownToggleEl) {
                return new window.bootstrap.Dropdown(dropdownToggleEl);
            });
        }
        
        // Reinitialize any other common functionality
        if (typeof window.initializeCommonFeatures === 'function') {
            window.initializeCommonFeatures();
        }
    }

    getPageTypeFromUrl(url) {
        if (url === '/' || url === '/home') return 'home';
        if (url.startsWith('/catalog')) return 'catalog';
        if (url.startsWith('/album/')) return 'album';
        if (url.startsWith('/collection')) return 'collection';
        if (url.startsWith('/my-albums')) return 'my-albums';
        if (url.startsWith('/my-benefits')) return 'my-benefits';
        if (url.startsWith('/support')) return 'support';
        return 'general';
    }

    dispatchPageEvents(url) {
        const pageType = this.getPageTypeFromUrl(url);
        
        // Dispatch generic events
        document.dispatchEvent(new CustomEvent('ajaxPageInit', {
            detail: { url, type: pageType }
        }));
        
        setTimeout(() => {
            document.dispatchEvent(new CustomEvent('ajaxPageLoaded', {
                detail: { url, type: pageType }
            }));
        }, 100);
    }

    updateActiveNavigation() {
        const currentPath = window.location.pathname;
        
        // Update desktop navigation
        document.querySelectorAll('.nav-link').forEach(link => {
            const href = link.getAttribute('href');
            if (href === currentPath || (currentPath === '/' && href === '/home')) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
        
        // Update mobile navigation
        document.querySelectorAll('.side-nav-item').forEach(link => {
            const href = link.getAttribute('href');
            if (href === currentPath || (currentPath === '/' && href === '/home')) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
        
        // Update mobile quick nav
        document.querySelectorAll('.mobile-nav-icon').forEach(link => {
            const href = link.getAttribute('href');
            if (href === currentPath || (currentPath === '/' && href === '/home')) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
        
        // Update dropdown toggles
        this.updateDropdownActiveStates(currentPath);
    }

    updateDropdownActiveStates(currentPath) {
        // Benefits dropdown
        const benefitsPaths = ['/my-benefits', '/my-downloads', '/my-book-requests', '/my-albums'];
        const benefitsToggle = document.querySelector('.dropdown-toggle[href="#"]:has(.fa-gift)');
        if (benefitsToggle) {
            if (benefitsPaths.includes(currentPath)) {
                benefitsToggle.classList.add('active');
            } else {
                benefitsToggle.classList.remove('active');
            }
        }
        
        // Admin dropdown
        const adminPaths = ['/admin/', '/creator/', '/statistics'];
        const adminToggle = document.querySelector('#adminDropdown');
        if (adminToggle) {
            if (adminPaths.some(path => currentPath.startsWith(path))) {
                adminToggle.classList.add('active');
            } else {
                adminToggle.classList.remove('active');
            }
        }
    }

    setupPopstateHandler() {
        window.addEventListener('popstate', (event) => {
            if (event.state && event.state.url) {
                console.log(`🔙 Popstate navigation to: ${event.state.url}`);
                this.navigateTo(event.state.url, { pushState: false });
            }
        });
    }

    createLoadingOverlay() {
        this.loadingOverlay = document.createElement('div');
        this.loadingOverlay.id = 'ajaxLoadingOverlay';
        this.loadingOverlay.innerHTML = `
            <div class="ajax-loading-content">
                <div class="ajax-loading-spinner">
                    <i class="fas fa-spinner fa-spin"></i>
                </div>
                <div class="ajax-loading-text">Loading...</div>
            </div>
        `;
        
        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            #ajaxLoadingOverlay {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background-color: rgba(0, 0, 0, 0.5);
                z-index: 9999;
                display: none;
                align-items: center;
                justify-content: center;
                backdrop-filter: blur(2px);
            }
            .ajax-loading-content {
                background: var(--bg-color, #1f2937);
                color: var(--text-color, white);
                padding: 2rem;
                border-radius: 8px;
                text-align: center;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
            }
            .ajax-loading-spinner {
                font-size: 2rem;
                margin-bottom: 1rem;
                color: #3b82f6;
            }
            .ajax-loading-text {
                font-size: 1.1rem;
                font-weight: 500;
            }
        `;
        document.head.appendChild(style);
        document.body.appendChild(this.loadingOverlay);
    }

    showLoadingOverlay() {
        if (this.loadingOverlay) {
            this.loadingOverlay.style.display = 'flex';
        }
    }

    hideLoadingOverlay() {
        if (this.loadingOverlay) {
            this.loadingOverlay.style.display = 'none';
        }
    }

    cacheResponse(key, data) {
        // Implement LRU cache
        if (this.navigationCache.size >= this.maxCacheSize) {
            const firstKey = this.navigationCache.keys().next().value;
            this.navigationCache.delete(firstKey);
        }
        this.navigationCache.set(key, data);
    }

    // Public API methods
    refresh() {
        return this.navigateTo(this.currentUrl, { skipCache: true });
    }

    clearCache() {
        this.navigationCache.clear();
        console.log('🧹 Navigation cache cleared');
    }

    preloadPage(url) {
        if (!this.navigationCache.has(url)) {
            this.fetchPageContent(url)
                .then(data => this.cacheResponse(url, data))
                .catch(error => console.log(`Preload failed for ${url}:`, error));
        }
    }
}

// Global initialization
window.ajaxNav = new AjaxNavigationManager();

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.ajaxNav.init();
    });
} else {
    window.ajaxNav.init();
}

// Expose navigation methods globally
window.navigateToAlbum = function(albumId) {
    if (window.ajaxNav) {
        window.ajaxNav.navigateTo(`/album/${albumId}`);
    } else {
        window.location.href = `/album/${albumId}`;
    }
};

window.navigateToHome = function() {
    if (window.ajaxNav) {
        window.ajaxNav.navigateTo('/home');
    } else {
        window.location.href = '/home';
    }
};

window.navigateToCatalog = function() {
    if (window.ajaxNav) {
        window.ajaxNav.navigateTo('/catalog');
    } else {
        window.location.href = '/catalog';
    }
};

// Debug helpers (remove in production)
window.ajaxNavDebug = {
    cache: () => window.ajaxNav.navigationCache,
    clearCache: () => window.ajaxNav.clearCache(),
    refresh: () => window.ajaxNav.refresh(),
    preload: (url) => window.ajaxNav.preloadPage(url)
};

console.log('🎵 AJAX Navigation System loaded');