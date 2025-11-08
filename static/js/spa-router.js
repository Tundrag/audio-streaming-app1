// spa-router.js - Complete Single Page Application Router with SSR support

class SPARouter {
    constructor() {
        this.routes = new Map();
        this.patterns = [];
        this.currentRoute = null;
        this.currentController = null;
        this.isNavigating = false;
        this.contentContainer = null;
        this.cacheVersion = this.extractCacheVersion();
        this.init();
    }
    
    extractCacheVersion() {
        const scripts = document.querySelectorAll('script[src*="spa-router.js"]');
        for (const script of scripts) {
            const match = script.src.match(/spa-router\.js\?v=(\d+)/);
            if (match) {
                // console.log(`📦 SPA Router: Using cache version ${match[1]}`);
                const version = match[1];
                window.APP_VERSION = version; // ✅ Make globally available
                return version;
            }
        }

        const anyScript = document.querySelector('script[src*="?v="]');
        if (anyScript) {
            const match = anyScript.src.match(/\?v=(\d+)/);
            if (match) {
                // console.log(`📦 SPA Router: Using fallback cache version ${match[1]}`);
                const version = match[1];
                window.APP_VERSION = version; // ✅ Make globally available
                return version;
            }
        }

        // console.warn('⚠️ SPA Router: No cache version found');
        const version = Date.now().toString();
        window.APP_VERSION = version; // ✅ Make globally available
        return version;
    }
    
    init() {
        this.contentContainer = document.querySelector('main');
        if (!this.contentContainer) {
            // console.error('Main content container not found');
            return;
        }
        
        window.addEventListener('popstate', (e) => this.handlePopState(e));
        document.addEventListener('click', (e) => this.handleLinkClick(e));
        this.registerDefaultRoutes();
        
        const currentPath = window.location.pathname;
        if (this.shouldHandleRoute(currentPath)) {
            // ✅ FIX: Check if page is already SSR'd to prevent destroying it
            const hasSSRContent = document.querySelector('[id$="-bootstrap-data"]') ||
                                   this.contentContainer.dataset.ssrLoaded === 'true' ||
                                   document.querySelector('#forumSPA');

            if (hasSSRContent) {
                // Page is already SSR'd - just mark as current route, don't re-render
                // console.log(`✅ SPA Router: Page already SSR'd at ${currentPath}, skipping initial navigation`);
                this.currentRoute = currentPath;
                this.updateActiveLinks(currentPath);

                // Store reference to existing controller if available
                if (window.teamManagement) {
                    this.currentController = window.teamManagement;
                } else if (window.benefitsController) {
                    this.currentController = window.benefitsController;
                } else if (window.playerController) {
                    this.currentController = window.playerController;
                } else if (window.forum) {
                    this.currentController = window.forum;
                }
            } else {
                // No SSR content - treat as SPA navigation
                // console.log(`🔄 SPA Router: Detected initial SPA route: ${currentPath}`);
                this.navigate(currentPath, false);
            }
        }
    }
    
    registerDefaultRoutes() {
        const v = this.cacheVersion;
        
        // ===== USER PAGES =====
        
        // Home
        this.register('/home', () => 
            import(`/static/js/home-spa.js?v=${v}`).then(m => new m.HomeSPA())
        );

        // Catalog
        this.register('/catalog', () =>  
            import(`/static/js/catalog-spa.js?v=${v}`).then(m => new m.CatalogSPA())
        );

        // Collection
        this.register('/collection', () =>  
            import(`/static/js/collection-spa.js?v=${v}`).then(m => new m.CollectionSPA())
        );

        // My Albums
        this.register('/my-albums', () =>
            import(`/static/js/my-albums-spa.js?v=${v}`).then(m => new m.MyAlbumsSPA())
        );

        // Continue Listening
        this.register('/continue-listening', () =>
            import(`/static/js/continue-listening-spa.js?v=${v}`).then(m => new m.ContinueListeningSPA())
        );

        // My Book Requests
        this.register('/my-book-requests', () =>  
            import(`/static/js/my-book-requests-spa.js?v=${v}`).then(m => new m.MyBookRequestsSPA())
        );

        // Manage Book Requests (Admin)
        this.register('/admin/book-requests', () =>  
            import(`/static/js/manage-book-requests-spa.js?v=${v}`).then(m => new m.ManageBookRequestsSPA())
        );

        // My Benefits
        this.register('/my-benefits', () =>  
            import(`/static/js/my-benefits-spa.js?v=${v}`).then(m => new m.MyBenefitsSPA())
        );

        // Benefits Management (Creator)
        this.register('/creator/benefits', () =>  
            import(`/static/js/benefits-management-spa.js?v=${v}`).then(m => new m.BenefitsManagementSPA())
        );

        // My Downloads
        this.register('/my-downloads', () =>  
            import(`/static/js/my-downloads-spa.js?v=${v}`).then(m => new m.MyDownloadsSPA())
        );

        // Activity Logs
        this.register('/activity-logs', () => 
            import(`/static/js/activity-logs-spa.js?v=${v}`).then(m => new m.ActivityLogsSPA())
        );

        // Statistics (Creator only)
        this.register('/statistics', () =>  
            import(`/static/js/statistics-spa.js?v=${v}`).then(m => new m.StatisticsSPA('spa'))
        );

        // Team Management (Creator only)
        this.register('/creator/team', () =>
            import(`/static/js/team-spa.js?v=${v}`).then(m => new m.TeamSPA())
        );

        this.register('/support', () =>
            import(`/static/js/support-spa.js?v=${v}`).then(m => new m.SupportSPA())
        );

        // Forum - register /forum as SPA route, /api/forum is API endpoint
        const forumFactory = () => import(`/static/js/forum-spa-wrapper.js?v=${v}`).then(m => new m.ForumSPAWrapper());
        this.register('/forum', forumFactory);

        // ===== PATTERN ROUTES (Dynamic segments) =====

        // Album Detail (e.g., /album/123 or /album/abc-def-ghi)
        this.registerPattern(/^\/album\/([^/]+)$/, (matches) => {
            const albumId = matches[1];
            // console.log(`SPA Router: Creating AlbumSPA for album ${albumId}`);
            return import(`/static/js/album-spa.js?v=${v}`).then(m => new m.AlbumSPA(albumId));
        });

        // Player Page (e.g., /player/123 with optional ?voice=xyz query parameter)
        this.registerPattern(/^\/player\/([^/?]+)/, (matches) => {
            const trackId = matches[1];
            // console.log(`SPA Router: Creating PlayerSPA for track ${trackId}`);
            return import(`/static/js/player-spa.js?v=${v}`).then(m => new m.PlayerSPA(trackId));
        });

        // console.log(`SPA Router: Registered all routes with cache version ${v}`);
    }
    
    register(path, controllerFactory) {
        this.routes.set(path, controllerFactory);
        // console.log(`SPA Router: Registered route ${path}`);
    }
    
    registerPattern(pattern, controllerFactory) {
        this.patterns.push({ pattern, controllerFactory });
        // console.log(`SPA Router: Registered pattern route ${pattern}`);
    }
    
    shouldHandleRoute(path) {
        // Don't handle certain paths
        if (path.startsWith('/api/') ||
            path.startsWith('/admin/') ||
            path.startsWith('/static/') ||
            path === '/login' ||
            path === '/logout' ||
            path === '/register') {
            return false;
        }

        if (this.routes.has(path)) {
            return true;
        }
        
        for (const { pattern } of this.patterns) {
            if (pattern.test(path)) {
                return true;
            }
        }
        
        return false;
    }
    
    findControllerFactory(path) {
        if (this.routes.has(path)) {
            return this.routes.get(path);
        }
        
        for (const { pattern, controllerFactory } of this.patterns) {
            const matches = path.match(pattern);
            if (matches) {
                // console.log(`SPA Router: Pattern matched for ${path}`, matches);
                return () => controllerFactory(matches);
            }
        }
        
        return null;
    }
    
    async navigate(path, pushState = true) {
        if (this.isNavigating) return;
        if (this.currentRoute === path && pushState) return;

        this.isNavigating = true;

        try {
            // ✅ CRITICAL: If leaving player page, cleanup player-page class
            if (this.currentRoute && this.currentRoute.startsWith('/player/') && !path.startsWith('/player/')) {
                // console.log('🧹 SPA Router: Leaving player page, cleaning up');

                // ✅ DON'T remove player.css - it's shared between SSR and SPA
                // Instead, just remove the body class so styles don't apply
                document.body.classList.remove('player-page');
                // console.log('✅ SPA Router: Removed player-page class from body');

                // ✅ CRITICAL: Force miniplayer to show if track is loaded
                if (window.persistentPlayer && window.persistentPlayer.currentTrackId) {
                    // console.log('🎵 SPA Router: Forcing miniplayer update after leaving player page');
                    window.persistentPlayer.isPlayerPage = false;
                    window.persistentPlayer.updatePlayerState();
                }
            }

            // ✅ Clean up previous controller
            if (this.currentController && typeof this.currentController.destroy === 'function') {
                // console.log(`🧹 SPA Router: Destroying previous controller`);
                await this.currentController.destroy();
                this.currentController = null;
            } else if (window.playerController && typeof window.playerController.destroy === 'function') {
                // ✅ FALLBACK: Destroy SSR playerController if router didn't capture it
                // console.log('🧹 SPA Router: Destroying orphaned window.playerController');
                await window.playerController.destroy();
                window.playerController = null;
            }

            const controllerFactory = this.findControllerFactory(path);
            
            if (controllerFactory) {
                await this.loadSPARoute(path, controllerFactory, pushState);
            } else {
                await this.loadTraditionalRoute(path, pushState);
            }
            
            this.currentRoute = path;
        } catch (error) {
            console.error('❌ SPA Router: Navigation error:', error);
            this.showError('Failed to load page');
        } finally {
            this.isNavigating = false;
        }
    }
    
    async loadSPARoute(path, controllerFactory, pushState) {
        this.showLoadingState();

        try {
            const controller = await controllerFactory();

            // Load required CSS before rendering
            if (typeof controller.getRequiredStyles === 'function') {
                await this.loadRequiredStyles(controller.getRequiredStyles());
            }

            const html = await controller.render();

            // ✅ Clear SSR markers before inserting new content
            this.contentContainer.removeAttribute('data-ssr-loaded');
            this.contentContainer.innerHTML = html;

            if (typeof controller.mount === 'function') {
                await controller.mount();
            }

            this.currentController = controller;

            // ✅ Update page title in header
            this.updatePageTitle(controller, path);

            if (pushState) {
                history.pushState({ path, spa: true }, '', path);
            }

            this.updateActiveLinks(path);
            window.scrollTo(0, 0);
        } catch (error) {
            console.error(`❌ SPA Router: Failed to load ${path}:`, error);
            this.showError(`Failed to load ${path}: ${error.message}`);
            throw error;
        }
    }

    async loadRequiredStyles(stylesheets) {
        const head = document.head;
        const existingLinks = new Set(
            Array.from(head.querySelectorAll('link[rel="stylesheet"]'))
                .map(link => {
                    try {
                        return new URL(link.href).pathname;
                    } catch {
                        return link.href.split('?')[0];
                    }
                })
        );
        
        const promises = stylesheets.map(href => {
            let cleanHref;
            try {
                cleanHref = new URL(href, window.location.origin).pathname;
            } catch {
                cleanHref = href.split('?')[0];
            }
            
            if (existingLinks.has(cleanHref)) {
                // console.log(`📦 CSS already loaded: ${cleanHref}`);
                return Promise.resolve();
            }
            
            return new Promise((resolve, reject) => {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = href;
                link.onload = () => {
                    // console.log(`✅ CSS loaded: ${href}`);
                    resolve();
                };
                link.onerror = () => {
                    // console.error(`❌ CSS failed to load: ${href}`);
                    reject(new Error(`Failed to load stylesheet: ${href}`));
                };
                head.appendChild(link);
            });
        });
        
        return Promise.all(promises);
    }
    
    async loadTraditionalRoute(path, pushState) {
        if (pushState) {
            window.location.href = path;
        } else {
            location.replace(path);
        }
    }
    
    handleLinkClick(e) {
        // Support both data-spa-link and data-spa-href attributes
        const link = e.target.closest('a[data-spa-link], a[data-spa-href]');
        if (!link) return;

        // Use data-spa-href first, fallback to href
        const href = link.getAttribute('data-spa-href') || link.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('http')) return;

        e.preventDefault();
        this.navigate(href);
    }
    
    handlePopState(e) {
        if (e.state && e.state.spa) {
            this.navigate(e.state.path, false);
        } else {
            const currentPath = window.location.pathname;
            if (this.shouldHandleRoute(currentPath)) {
                this.navigate(currentPath, false);
            } else {
                window.location.reload();
            }
        }
    }
    
    updateActiveLinks(currentPath) {
        // Update all navigation links (desktop, mobile, sidebar, dropdown)
        document.querySelectorAll('.nav-link, .side-nav-item, .dropdown-item, .mobile-nav-icon').forEach(link => {
            const href = link.getAttribute('href');
            if (href === currentPath) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
    }

    updatePageTitle(controller, path) {
        let pageTitle = '';

        // Try to get title from controller
        if (controller && typeof controller.getPageTitle === 'function') {
            pageTitle = controller.getPageTitle();
        } else {
            // Fallback: derive title from path
            const titleMap = {
                '/home': 'Home',
                '/catalog': 'Catalog',
                '/collection': 'My Collection',
                '/my-albums': 'My Albums',
                '/continue-listening': 'Continue Listening',
                '/my-downloads': 'My Downloads',
                '/my-book-requests': 'My Book Requests',
                '/my-benefits': 'My Benefits',
                '/support': 'Support',
                '/team': 'Team',
                '/activity-logs': 'Activity Logs',
                '/forum': 'Forum',
                '/manage-book-requests': 'Manage Book Requests',
                '/benefits-management': 'Benefits Management',
                '/creator-management': 'Creator Management',
                '/statistics': 'Statistics'
            };

            pageTitle = titleMap[path] || 'Page';
        }

        // Update the h1 in the header
        const headerTitle = document.querySelector('.main-header h1');
        if (headerTitle) {
            headerTitle.textContent = pageTitle;
        }

        // Also update document title
        document.title = pageTitle;
    }

    showLoadingState() {
        this.contentContainer.innerHTML = `
            <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 400px; color: var(--text-muted);">
                <i class="fas fa-spinner fa-spin" style="font-size: 3rem; margin-bottom: 20px;"></i>
                <p>Loading...</p>
            </div>
        `;
    }
    
    showError(message) {
        this.contentContainer.innerHTML = `
            <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 400px; color: #ef4444;">
                <i class="fas fa-exclamation-circle" style="font-size: 3rem; margin-bottom: 20px;"></i>
                <p>${message}</p>
                <button onclick="location.reload()" class="btn btn-primary" style="margin-top: 20px;">Reload Page</button>
            </div>
        `;
    }

    // Helper method to programmatically navigate
    navigateTo(path) {
        this.navigate(path, true);
    }

    // Get current route
    getCurrentRoute() {
        return this.currentRoute;
    }

    // Get current controller
    getCurrentController() {
        return this.currentController;
    }

    // Check if route is registered
    isRouteRegistered(path) {
        return this.shouldHandleRoute(path);
    }

    // Get all registered routes (for debugging)
    getRegisteredRoutes() {a
        return {
            exactRoutes: Array.from(this.routes.keys()),
            patternRoutes: this.patterns.map(p => p.pattern.toString())
        };
    }
}

// Initialize the router globally
window.spaRouter = new SPARouter();

// Make navigation available globally
window.navigateTo = (path) => {
    if (window.spaRouter) {
        window.spaRouter.navigateTo(path);
    }
};

// console.log('✅ SPA Router initialized and ready');