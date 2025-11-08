/**
 * SPA Router for Music Streaming App
 * Handles navigation without page reloads while preserving audio playback
 */

(function() {
    // Store page cache
    var pageCache = {};
    
    // Track if a navigation is in progress
    var navigationInProgress = false;
    
    // Track loaded scripts to avoid duplicates
    window.loadedScripts = window.loadedScripts || new Set();
    
    /**
     * Navigate to a URL using SPA approach
     * @param {string} url - The URL to navigate to
     * @param {boolean} pushState - Whether to update browser history
     */
    async function navigateTo(url, pushState) {
        if (pushState === undefined) pushState = true;
        
        // Prevent multiple navigations at once
        if (navigationInProgress) return;
        navigationInProgress = true;
        
        console.log("🚀 SPA Navigation: " + url);
        
        // Show loading indicator
        document.body.classList.add('loading');
        
        try {
            // Check cache first - but never for pages that need fresh content
            var content;
            if (pageCache[url] && !url.includes('/catalog') && !url.includes('/directory') && 
                !url.includes('/collection') && url !== '/' && url !== '/home') {
                console.log("📦 Using cached version of " + url);
                content = pageCache[url];
            } else {
                // Fetch the page
                var response = await fetch(url, {
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });
                
                if (!response.ok) {
                    throw new Error("Failed to fetch " + url + ": " + response.status);
                }
                
                var html = await response.text();
                
                // Parse the HTML
                var parser = new DOMParser();
                var doc = parser.parseFromString(html, 'text/html');
                
                // Extract just the content we need
                content = {
                    title: doc.title,
                    pageTitle: doc.querySelector('.main-header .header-left h1') ? 
                               doc.querySelector('.main-header .header-left h1').textContent : '',
                    mainContent: doc.querySelector('#spa-content') ? 
                                doc.querySelector('#spa-content').innerHTML : 
                                (doc.querySelector('main') ? doc.querySelector('main').innerHTML : ''),
                    extraCss: Array.from(doc.querySelectorAll('style')).map(function(s) { 
                        return s.textContent; 
                    }),
                    extraJs: Array.from(doc.querySelectorAll('script')).filter(function(s) {
                        return (s.hasAttribute('src') && 
                            s.getAttribute('src').includes('extra')) || 
                        !s.hasAttribute('src');
                    }).map(function(s) {
                        return { 
                            src: s.getAttribute('src') || null, 
                            content: s.hasAttribute('src') ? null : s.textContent 
                        };
                    })
                };
                
                // Cache the result (except for dynamic pages which need fresh data)
                if (!url.includes('/catalog') && !url.includes('/directory') && 
                    !url.includes('/collection') && url !== '/' && url !== '/home') {
                    pageCache[url] = content;
                }
            }
            
            // Update the page
            document.title = content.title;
            
            // Update page title if exists
            var pageTitle = document.querySelector('.main-header .header-left h1');
            if (pageTitle && content.pageTitle) {
                pageTitle.textContent = content.pageTitle;
            }
            
            // Update main content
            var contentContainer = document.getElementById('spa-content');
            if (contentContainer) {
                contentContainer.innerHTML = content.mainContent;
            } else {
                throw new Error('SPA content container not found');
            }
            
            // Apply extra CSS if present
            applyExtraCss(content.extraCss);
            
            // Execute scripts
            executeScripts(content.extraJs, url);
            
            // Update navigation state
            updateActiveNavLinks(url);
            
            // Update browser history
            if (pushState) {
                window.history.pushState({ url: url }, '', url);
            }
            
            // Dispatch content loaded event
            window.dispatchEvent(new CustomEvent('spaContentLoaded', { 
                detail: { url: url, title: content.title } 
            }));
            
        } catch (error) {
            console.error('SPA Navigation Error:', error);
            
            // Show error toast
            showToast("Navigation failed: " + error.message, 'error');
            
            // If it's a critical error, redirect traditionally
            if (error.message.includes('container not found') || 
                error.message.includes('Failed to fetch')) {
                window.location.href = url;
            }
        } finally {
            // Hide loading indicator
            document.body.classList.remove('loading');
            navigationInProgress = false;
        }
    }
    
    /**
     * Apply extracted CSS from the loaded page
     * @param {Array} styles - Array of CSS content
     */
    function applyExtraCss(styles) {
        if (!styles || !styles.length) return;
        
        // First, remove any previously added dynamic styles
        var existingStyles = document.querySelectorAll('style[data-spa-added="true"]');
        for (var i = 0; i < existingStyles.length; i++) {
            existingStyles[i].remove();
        }
        
        // Add new styles
        styles.forEach(function(styleContent) {
            var styleEl = document.createElement('style');
            styleEl.setAttribute('data-spa-added', 'true');
            styleEl.textContent = styleContent;
            document.head.appendChild(styleEl);
        });
    }
    
    /**
     * Execute scripts from the loaded page
     * @param {Array} scripts - Scripts to execute
     * @param {string} url - Current URL
     */
    function executeScripts(scripts, url) {
        if (!scripts || !scripts.length) return;
        
        scripts.forEach(function(script) {
            if (script.src) {
                // External script - check if already loaded
                if (!document.querySelector('script[src="' + script.src + '"]')) {
                    var newScript = document.createElement('script');
                    newScript.src = script.src;
                    document.body.appendChild(newScript);
                }
            } else if (script.content) {
                // Inline script - execute
                try {
                    eval(script.content);
                } catch (e) {
                    console.error('Error executing inline script:', e);
                }
            }
        });

        // Initialize specific page components based on URL
        setTimeout(function() {
            var isHomePage = url === '/' || url === '/home';
            var isDirectoryPage = url === '/catalog' || url.includes('/directory');
            var isCollectionPage = url === '/collection' || url.startsWith('/collection/');
            
            console.log('Page type detection:', { 
                isHomePage: isHomePage, 
                isDirectoryPage: isDirectoryPage, 
                isCollectionPage: isCollectionPage,
                url: url
            });
            
            if (isHomePage) {
                console.log('📱 Initializing home page components');
                initializeHomePage();
            } else if (isDirectoryPage) {
                console.log('📚 Initializing directory page components');
                initializeDirectoryPage();
            }
            // Collection page initialization removed to prevent conflicts
        }, 100);
    }
    
    /**
     * Update active links in navigation
     * @param {string} url - Current URL
     */
    function updateActiveNavLinks(url) {
        var navLinks = document.querySelectorAll('.nav-link');
        for (var i = 0; i < navLinks.length; i++) {
            var href = navLinks[i].getAttribute('href');
            // Match exact paths and also handle base paths (e.g. /collection and /collection/123)
            var isActive = href === url || 
                          (href !== '/' && url.startsWith(href + '/'));
            
            if (isActive) {
                navLinks[i].classList.add('active');
            } else {
                navLinks[i].classList.remove('active');
            }
        }
    }
    
    /**
     * Show a toast message
     * @param {string} message - Message to display
     * @param {string} type - Toast type (success, error)
     */
    function showToast(message, type) {
        if (type === undefined) type = 'info';
        
        // Create toast if it doesn't exist
        var toast = document.querySelector('.spa-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.className = 'spa-toast';
            document.body.appendChild(toast);
        }
        
        // Set toast class and content
        toast.className = 'spa-toast ' + type;
        toast.textContent = message;
        
        // Show and then hide
        toast.classList.add('visible');
        setTimeout(function() {
            toast.classList.remove('visible');
        }, 3000);
    }

    /**
     * Initialize home page components
     * Sets up carousels and loads dynamic content
     */
    function initializeHomePage() {
        try {
            console.log('🏠 Setting up home page carousels and dynamic content');
            
            // Initialize all carousels
            setupCarousels();
            
            // Load dynamic content if needed
            checkAndLoadHomePageContent();
            
            // Set up album card clicks for SPA navigation
            setupAlbumCardNavigation();
            
            // Set up continue listening section
            setupContinueListening();
            
        } catch (error) {
            console.error('Error initializing home page:', error);
        }
    }
    
    /**
     * Check if we need to load content dynamically and do so if required
     */
    function checkAndLoadHomePageContent() {
        // Check popular tracks
        var popularTracksCarousel = document.getElementById('popularTracksCarousel');
        var recentUpdatesCarousel = document.getElementById('recentUpdatesCarousel');
        var recentAdditionsCarousel = document.getElementById('recentAdditionsCarousel');
        
        // Check if elements exist before proceeding
        if (!popularTracksCarousel && !recentUpdatesCarousel && !recentAdditionsCarousel) {
            console.warn('Home page carousel elements not found');
            return;
        }
        
        // Add loading indicators if needed and not already present
        if (popularTracksCarousel) {
            var popularLoading = popularTracksCarousel.querySelector('.loading');
            // Check if it has no content or just a loading element
            var popularHasCards = popularTracksCarousel.querySelectorAll('.album-preview-card').length > 0;
            
            if (!popularHasCards && !popularLoading) {
                // Clear and add loading indicator
                popularTracksCarousel.innerHTML = '<div class="loading">Loading popular tracks...</div>';
            }
        }
        
        if (recentUpdatesCarousel) {
            var updatesLoading = recentUpdatesCarousel.querySelector('.loading');
            var updatesHasCards = recentUpdatesCarousel.querySelectorAll('.album-preview-card').length > 0;
            
            if (!updatesHasCards && !updatesLoading) {
                recentUpdatesCarousel.innerHTML = '<div class="loading">Loading recent updates...</div>';
            }
        }
        
        if (recentAdditionsCarousel) {
            var additionsLoading = recentAdditionsCarousel.querySelector('.loading');
            var additionsHasCards = recentAdditionsCarousel.querySelectorAll('.album-preview-card').length > 0;
            
            if (!additionsHasCards && !additionsLoading) {
                recentAdditionsCarousel.innerHTML = '<div class="loading">Loading recent additions...</div>';
            }
        }
        
        // Now fetch data for each carousel that needs it
        fetchHomePageData();
    }
    
    /**
     * Set up all carousels on the home page
     */
    function setupCarousels() {
        var scrollStep = 200;
        var smallScrollStep = 132;
        
        // Popular Tracks carousel
        var popularCarousel = document.getElementById('popularTracksCarousel');
        if (popularCarousel) {
            var prevBtn = popularCarousel.parentElement.querySelector('.carousel-button.prev');
            var nextBtn = popularCarousel.parentElement.querySelector('.carousel-button.next');
            
            if (prevBtn && nextBtn) {
                // Clone buttons to remove existing event listeners
                var newPrevBtn = prevBtn.cloneNode(true);
                var newNextBtn = nextBtn.cloneNode(true);
                prevBtn.parentNode.replaceChild(newPrevBtn, prevBtn);
                nextBtn.parentNode.replaceChild(newNextBtn, nextBtn);
                
                // Add event listeners to new buttons
                newPrevBtn.addEventListener('click', function() {
                    popularCarousel.scrollBy({
                        left: -scrollStep,
                        behavior: 'smooth'
                    });
                });
                
                newNextBtn.addEventListener('click', function() {
                    popularCarousel.scrollBy({
                        left: scrollStep,
                        behavior: 'smooth'
                    });
                });
            }
        }
        
        // Small carousels (Recently Updated, Recent Additions, My Albums)
        var smallCarousels = [
            'recentUpdatesCarousel',
            'recentAdditionsCarousel',
            'myAlbumsCarousel'
        ];
        
        smallCarousels.forEach(function(carouselId) {
            var carousel = document.getElementById(carouselId);
            if (!carousel) return;
            
            var prevBtn = carousel.parentElement.querySelector('.carousel-button.prev');
            var nextBtn = carousel.parentElement.querySelector('.carousel-button.next');
            
            if (prevBtn && nextBtn) {
                // Clone buttons to remove existing event listeners
                var newPrevBtn = prevBtn.cloneNode(true);
                var newNextBtn = nextBtn.cloneNode(true);
                prevBtn.parentNode.replaceChild(newPrevBtn, prevBtn);
                nextBtn.parentNode.replaceChild(newNextBtn, nextBtn);
                
                // Add event listeners to new buttons
                newPrevBtn.addEventListener('click', function() {
                    carousel.scrollBy({
                        left: -smallScrollStep,
                        behavior: 'smooth'
                    });
                });
                
                newNextBtn.addEventListener('click', function() {
                    carousel.scrollBy({
                        left: smallScrollStep,
                        behavior: 'smooth'
                    });
                });
                
                // Update button states on scroll
                carousel.addEventListener('scroll', function() {
                    newPrevBtn.style.opacity = carousel.scrollLeft <= 0 ? '0.5' : '1';
                    newNextBtn.style.opacity = 
                        (carousel.scrollLeft + carousel.clientWidth) >= carousel.scrollWidth ? '0.5' : '1';
                });
            }
        });
    }
    
    /**
     * Fetch dynamic data for the home page
     */
    function fetchHomePageData() {
        // Fetch Popular Tracks
        var popularTracksCarousel = document.getElementById('popularTracksCarousel');
        if (popularTracksCarousel) {
            // Check if it already has content
            var hasContent = popularTracksCarousel.querySelectorAll('.album-preview-card').length > 0;
            var hasLoading = popularTracksCarousel.querySelector('.loading') !== null;
            
            if (!hasContent && hasLoading) {
                // Use API if available
                fetch('/api/popular-tracks')
                    .then(function(response) { return response.json(); })
                    .then(function(data) {
                        popularTracksCarousel.innerHTML = ''; // Clear loading
                        
                        data.forEach(function(track) {
                            var card = document.createElement('div');
                            card.className = 'album-preview-card';
                            card.dataset.albumId = track.id;
                            card.innerHTML = 
                                '<div class="album-cover-container">' +
                                '    <img src="' + track.cover_path + '"' + 
                                '         alt="' + track.title + '"' + 
                                '         class="album-cover"' +
                                '         onerror="this.src=\'/static/images/default-album.jpg\'">' +
                                '    <div class="album-hover">' +
                                '        <i class="fas fa-play"></i>' +
                                '    </div>' +
                                '</div>' +
                                '<div class="album-info">' +
                                '    <h3>' + track.title + '</h3>' +
                                '    <p>' + track.album_title + '</p>' +
                                (track.total_plays ? '<div class="plays">' + track.total_plays + ' plays</div>' : '') +
                                '</div>';
                            
                            card.addEventListener('click', function(e) {
                                e.preventDefault();
                                navigateTo('/album/' + track.id);
                            });
                            
                            popularTracksCarousel.appendChild(card);
                        });
                        
                        // Now that we've loaded content, set up auto-scroll if needed
                        if (data.length > 0) {
                            setTimeout(function() {
                                autoScrollCarousel(popularTracksCarousel);
                            }, 1000);
                        }
                    })
                    .catch(function(err) {
                        console.error('Error fetching popular tracks:', err);
                        // Fallback to refreshing the page if API fails
                        popularTracksCarousel.innerHTML = 
                            '<div class="api-error">' +
                            '    <p>Failed to load popular tracks</p>' +
                            '    <button onclick="window.location.reload()">Reload</button>' +
                            '</div>';
                    });
            }
        }
        
        // Fetch Recent Updates
        var recentUpdatesCarousel = document.getElementById('recentUpdatesCarousel');
        if (recentUpdatesCarousel && recentUpdatesCarousel.querySelector('.loading')) {
            fetch('/api/albums/recent-updates')
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    recentUpdatesCarousel.innerHTML = ''; // Clear loading
                    
                    data.forEach(function(album) {
                        var card = document.createElement('div');
                        card.className = 'album-preview-card';
                        card.dataset.albumId = album.id;
                        card.innerHTML = 
                            '<div class="album-cover-container">' +
                            '    <img src="' + album.cover_path + '" alt="' + album.title + '" class="album-cover" onerror="this.src=\'/static/images/default-album.jpg\'">' +
                            '    <div class="album-hover">' +
                            '        <i class="fas fa-play"></i>' +
                            '    </div>' +
                            '</div>' +
                            '<div class="album-info">' +
                            '    <h3>' + album.title + '</h3>' +
                            '    <p>' + album.track_count + ' tracks</p>' +
                            '    <div class="album-meta">' +
                            '        <span class="latest-update">Updated ' + (album.latest_update ? album.latest_update.split("T")[0] : 'Recently') + '</span>' +
                            (album.latest_track ? '<span class="new-track">Latest: ' + album.latest_track.title + '</span>' : '') +
                            '    </div>' +
                            '</div>';
                        
                        card.addEventListener('click', function(e) {
                            e.preventDefault();
                            navigateTo('/album/' + album.id);
                        });
                        
                        recentUpdatesCarousel.appendChild(card);
                    });
                })
                .catch(function(err) { console.error('Error fetching recent updates:', err); });
        }
        
        // Fetch Recent Additions
        var recentAdditionsCarousel = document.getElementById('recentAdditionsCarousel');
        if (recentAdditionsCarousel && recentAdditionsCarousel.querySelector('.loading')) {
            fetch('/api/albums/recent-additions')
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    recentAdditionsCarousel.innerHTML = ''; // Clear loading
                    
                    data.forEach(function(album) {
                        var card = document.createElement('div');
                        card.className = 'album-preview-card';
                        card.dataset.albumId = album.id;
                        card.innerHTML = 
                            '<div class="album-cover-container">' +
                            '    <img src="' + album.cover_path + '" alt="' + album.title + '" class="album-cover" onerror="this.src=\'/static/images/default-album.jpg\'">' +
                            '    <div class="album-hover">' +
                            '        <i class="fas fa-play"></i>' +
                            '    </div>' +
                            '</div>' +
                            '<div class="album-info">' +
                            '    <h3>' + album.title + '</h3>' +
                            '    <p>' + album.track_count + ' tracks</p>' +
                            '    <div class="album-meta">' +
                            '        <span class="time-added">Added ' + (album.created_at ? album.created_at.split("T")[0] : 'Recently') + '</span>' +
                            '    </div>' +
                            '</div>';
                        
                        card.addEventListener('click', function(e) {
                            e.preventDefault();
                            navigateTo('/album/' + album.id);
                        });
                        
                        recentAdditionsCarousel.appendChild(card);
                    });
                })
                .catch(function(err) { console.error('Error fetching recent additions:', err); });
        }
    }
    
    /**
     * Auto-scroll a carousel for Popular Tracks
     */
    function autoScrollCarousel(carousel) {
        if (!carousel) return;
        
        var scrollStep = 200;
        var autoScrollInterval = 5000; // Auto scroll every 5 seconds
        
        // Only set up auto-scroll if not already set
        if (!carousel.dataset.autoScrollSet) {
            carousel.dataset.autoScrollSet = 'true';
            
            // Set up interval for auto-scrolling
            var scrollIntervalId = setInterval(function() {
                // Only auto-scroll if user hasn't interacted recently
                if (!carousel.dataset.userInteracted) {
                    if (carousel.scrollLeft + carousel.clientWidth >= carousel.scrollWidth) {
                        // Reached the end, scroll back to start
                        carousel.scrollTo({ left: 0, behavior: 'smooth' });
                    } else {
                        // Scroll forward
                        carousel.scrollBy({ left: scrollStep, behavior: 'smooth' });
                    }
                }
            }, autoScrollInterval);
            
            // Store interval ID for cleanup
            carousel.dataset.scrollIntervalId = scrollIntervalId;
            
            // Set up interaction detection
            carousel.addEventListener('mouseenter', function() {
                carousel.dataset.userInteracted = 'true';
            });
            
            carousel.addEventListener('mouseleave', function() {
                // Reset after a delay
                setTimeout(function() {
                    carousel.dataset.userInteracted = '';
                }, 5000);
            });
            
            // Clean up on page change
            window.addEventListener('spaContentLoaded', function() {
                clearInterval(scrollIntervalId);
            });
        }
    }
    
    /**
     * Set up album card navigation for existing cards
     */
    function setupAlbumCardNavigation() {
        // Update existing album cards to use SPA navigation
        var albumCards = document.querySelectorAll('.album-preview-card');
        for (var i = 0; i < albumCards.length; i++) {
            var card = albumCards[i];
            // Only update if not already set up
            if (!card.dataset.spaInitialized) {
                card.dataset.spaInitialized = 'true';
                
                // Remove existing onclick handlers
                var originalOnClick = card.getAttribute('onclick');
                if (originalOnClick) {
                    card.removeAttribute('onclick');
                }
                
                // Get album ID from data attribute or parse from onclick
                var albumId = card.dataset.albumId;
                if (!albumId && originalOnClick) {
                    var match = originalOnClick.match(/\/album\/([^'")]+)/);
                    if (match) {
                        albumId = match[1];
                        card.dataset.albumId = albumId;
                    }
                }
                
                if (albumId) {
                    (function(id) {
                        card.addEventListener('click', function(e) {
                            e.preventDefault();
                            e.stopPropagation();
                            navigateTo('/album/' + id);
                        });
                    })(albumId);
                }
            }
        }
    }
    
    /**
     * Set up continue listening section
     */
    function setupContinueListening() {
        var progressTracks = document.querySelectorAll('.progress-track');
        for (var i = 0; i < progressTracks.length; i++) {
            var track = progressTracks[i];
            var resumeBtn = track.querySelector('.resume-btn');
            if (resumeBtn) {
                // Replace the direct click with a custom handler that preserves SPA
                (function(trk) {
                    resumeBtn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        var trackId = trk.dataset.trackId;
                        var position = trk.dataset.position || 0;
                        
                        // This will work with your persistent-player.js
                        if (window.Player && typeof window.Player.playTrack === 'function') {
                            window.Player.playTrack(trackId, position);
                        } else {
                            console.log('Player not initialized yet, navigating to track page');
                            navigateTo('/track/' + trackId);
                        }
                    });
                })(track);
            }
        }
    }
    
    /**
     * Initialize directory page components
     */
    function initializeDirectoryPage() {
        console.log('Initializing directory page...');
        
        var alphabetFilter = document.getElementById('alphabetFilter');
        var albumSections = document.getElementById('albumSections');
        var searchInput = document.getElementById('albumSearch');
        
        if (!alphabetFilter || !albumSections || !searchInput) {
            console.warn('Directory elements not found');
            return;
        }
        
        // Get all album links
        var allAlbumLinks = document.querySelectorAll('.album-link');
        
        // Setup SPA navigation for all album links
        for (var i = 0; i < allAlbumLinks.length; i++) {
            var link = allAlbumLinks[i];
            // Only set up if not already initialized
            if (!link.dataset.spaInitialized) {
                link.dataset.spaInitialized = 'true';
                
                // Get album ID
                var albumId = link.dataset.albumId || link.getAttribute('href').split('/').pop();
                
                // Remove default click behavior
                (function(id) {
                    link.addEventListener('click', function(e) {
                        e.preventDefault();
                        if (window.spaRouter) {
                            window.spaRouter.navigateTo('/album/' + id);
                        } else {
                            // Use function from this closure if window.spaRouter not available
                            navigateTo('/album/' + id);
                        }
                    });
                })(albumId);
            }
        }

        // Filter by letter
        alphabetFilter.addEventListener('click', function(e) {
            if (e.target.classList.contains('letter-btn')) {
                // Update active state
                var letterBtns = document.querySelectorAll('.letter-btn');
                for (var i = 0; i < letterBtns.length; i++) {
                    letterBtns[i].classList.remove('active');
                }
                e.target.classList.add('active');

                var selectedLetter = e.target.dataset.letter;
                filterAlbums(selectedLetter, searchInput.value);
            }
        });

        // Search functionality
        var searchTimeout;
        searchInput.addEventListener('input', function(e) {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(function() {
                var searchQuery = e.target.value.toLowerCase();
                var activeLetter = document.querySelector('.letter-btn.active');
                var activeLetterValue = activeLetter ? activeLetter.dataset.letter : 'all';
                filterAlbums(activeLetterValue, searchQuery);
            }, 200);
        });

        function filterAlbums(letter, searchQuery) {
            var sections = document.querySelectorAll('.letter-section');
            
            for (var i = 0; i < sections.length; i++) {
                var section = sections[i];
                var albumLinks = section.querySelectorAll('.album-link');
                var hasVisibleAlbums = false;

                for (var j = 0; j < albumLinks.length; j++) {
                    var album = albumLinks[j];
                    var albumTitle = album.textContent.trim().toLowerCase();
                    var matchesSearch = albumTitle.includes(searchQuery);
                    var matchesLetter = letter === 'all' || section.dataset.letter === letter;

                    var listItem = album.parentElement;
                    if (matchesSearch && matchesLetter) {
                        listItem.style.display = '';
                        hasVisibleAlbums = true;
                    } else {
                        listItem.style.display = 'none';
                    }
                }

                section.style.display = hasVisibleAlbums ? '' : 'none';
            }

            updateLetterButtonStates();
        }

        function updateLetterButtonStates() {
            var letterBtns = document.querySelectorAll('.letter-btn');
            var searchQuery = searchInput.value.toLowerCase();

            for (var i = 0; i < letterBtns.length; i++) {
                var btn = letterBtns[i];
                var letter = btn.dataset.letter;
                if (letter === 'all') continue;

                var hasAlbums = Array.from(allAlbumLinks).some(function(album) {
                    var albumTitle = album.textContent.trim().toLowerCase();
                    return albumTitle.charAt(0).toUpperCase() === letter && albumTitle.includes(searchQuery);
                });

                if (hasAlbums) {
                    btn.classList.remove('disabled');
                } else {
                    btn.classList.add('disabled');
                }
            }
        }
    }
    
    /**
     * Navigate to an album page
     * @param {string} albumId - The album ID to navigate to
     */
    window.navigateToAlbum = async function(albumId) {
        try {
            // Check access first
            var response = await fetch('/api/albums/' + encodeURIComponent(albumId) + '/check-access');
            
            // Try to parse JSON if available
            var data = {};
            try {
                data = await response.json();
            } catch (jsonError) {
                console.error('JSON parse error:', jsonError);
            }
            
            if (response.status === 403) {
                showToast(data.error ? data.error.message : 'This content requires a higher tier subscription', 'error');
                return;
            }
            
            if (!response.ok) {
                showToast(data.error ? data.error.message : 'Error accessing album', 'error');
                return;
            }
            
            // If access is granted, navigate to album using SPA
            navigateTo('/album/' + encodeURIComponent(albumId));
            
        } catch (error) {
            console.error('Error checking album access:', error);
            showToast('Error accessing album', 'error');
        }
    };
    
    /**
     * Initialize SPA navigation
     */
    function initSpaNavigation() {
        // Add toast styles if not already present
        if (!document.getElementById('spa-styles')) {
            var style = document.createElement('style');
            style.id = 'spa-styles';
            style.textContent = 
                '.spa-toast {' +
                '    position: fixed;' +
                '    bottom: 20px;' +
                '    left: 50%;' +
                '    transform: translateX(-50%) translateY(100px);' +
                '    background: rgba(0, 0, 0, 0.8);' +
                '    color: white;' +
                '    padding: 10px 20px;' +
                '    border-radius: 4px;' +
                '    font-size: 14px;' +
                '    z-index: 9999;' +
                '    transition: transform 0.3s ease;' +
                '    pointer-events: none;' +
                '}' +
                '.spa-toast.visible {' +
                '    transform: translateX(-50%) translateY(0);' +
                '}' +
                '.spa-toast.error {' +
                '    background: rgba(220, 53, 69, 0.9);' +
                '}' +
                '.spa-toast.success {' +
                '    background: rgba(40, 167, 69, 0.9);' +
                '}' +
                '.api-error {' +
                '    padding: 1rem;' +
                '    text-align: center;' +
                '    color: #EF4444;' +
                '}' +
                '.api-error button {' +
                '    margin-top: 0.5rem;' +
                '    padding: 0.25rem 0.75rem;' +
                '    background: #EF4444;' +
                '    color: white;' +
                '    border: none;' +
                '    border-radius: 0.25rem;' +
                '    cursor: pointer;' +
                '}' +
                '.loading-albums {' +
                '    padding: 2rem;' +
                '    text-align: center;' +
                '    color: var(--text-secondary);' +
                '    font-size: 1.1rem;' +
                '}';
            document.head.appendChild(style);
        }
        
        // Handle link clicks for SPA navigation
        document.addEventListener('click', function(event) {
            // Find closest link element
            var link = event.target.closest('a');
            
            // Skip if not a link or modified click
            if (!link || event.ctrlKey || event.metaKey || event.shiftKey) {
                return;
            }
            
            var href = link.getAttribute('href');
            
            // Skip navigation for:
            // - No href
            // - Hash links
            // - External links
            // - API links
            // - Static links
            // - Media files
            // - Logout
            // - Links with targets or downloads
            if (!href || 
                href.startsWith('#') || 
                href.startsWith('http') ||
                href.includes('/api/') ||
                href.includes('/static/') ||
                href.includes('.mp3') ||
                href.includes('.zip') ||
                href.includes('download') ||
                href === '/logout' ||
                link.hasAttribute('target') || 
                link.hasAttribute('download') ||
                link.hasAttribute('data-no-spa')) {
                return;
            }
            
            // Handle internal navigation
            event.preventDefault();
            navigateTo(href);
        });
        
        // Handle browser back/forward navigation
        window.addEventListener('popstate', function(event) {
            if (event.state && event.state.url) {
                navigateTo(event.state.url, false);
            }
        });
        
        // Init specific page components based on current URL
        var path = window.location.pathname;
        var isHomePage = path === '/' || path === '/home';
        var isDirectoryPage = path === '/catalog' || path.includes('/directory');
        
        console.log('Initial page detection:', { 
            isHomePage: isHomePage, 
            isDirectoryPage: isDirectoryPage,
            path: path
        });
        
        if (isHomePage) {
            setTimeout(function() { initializeHomePage(); }, 100);
        } else if (isDirectoryPage) {
            setTimeout(function() { initializeDirectoryPage(); }, 100);
        }
        // Collection initialization removed to prevent conflicts
        
        // Initial page state
        var currentPath = window.location.pathname;
        window.history.replaceState({ url: currentPath }, '', currentPath);
        
        console.log('🔄 SPA Navigation initialized');
    }
    
    // Initialize when DOM is loaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSpaNavigation);
    } else {
        initSpaNavigation();
    }
    
    // Listen for SPA content loaded event
    window.addEventListener('spaContentLoaded', function(event) {
        var url = event.detail.url;
        console.log('SPA content loaded for URL:', url);
        
        // Initialize page-specific components
        var isHomePage = url === '/' || url === '/home';
        var isDirectoryPage = url === '/catalog' || url.includes('/directory');
        
        if (isHomePage) {
            initializeHomePage();
        } else if (isDirectoryPage) {
            initializeDirectoryPage();
        }
        // Collection initialization removed to prevent conflicts
    });
    
    // Handle image error
    window.handleImageError = function(image) {
        var title = image.dataset.albumTitle || '';
        var container = image.closest('.cover-container');
        if (container) {
            container.innerHTML = 
                '<div class="album-cover-placeholder">' +
                '    <span class="album-initial">' + title.charAt(0).toUpperCase() + '</span>' +
                '</div>';
        } else {
            image.src = '/static/images/default-album.jpg';
        }
    };
    
    // Expose API for direct use
    window.spaRouter = {
        navigateTo: navigateTo,
        clearCache: function() { 
            Object.keys(pageCache).forEach(function(key) {
                delete pageCache[key];
            });
        }
    };
})();