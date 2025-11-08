/**
 * Collection page SPA functionality
 * Handles collection-specific features while preserving SPA navigation
 */


(function() {
    // Expose public API
    window.CollectionSPA = {
        initialize: initializeCollectionPage,
        fetchData: fetchCollectionData,
        setupSearch: setupCollectionSearch,
        setupEventHandlers: setupCollectionEventHandlers
    };

    /**
     * Initialize collection page components
     * Fetches and displays album collection data
     */
    function initializeCollectionPage() {
        try {
            console.log('🎵 Setting up collection page components');
            
            // Only fetch data if we're actually on the collection page
            // This prevents collection code from running on non-collection pages
            var path = window.location.pathname;
            if (path !== '/collection' && !path.startsWith('/collection/')) {
                console.log('Not on collection page, skipping collection initialization');
                return;
            }
            
            // Setup search functionality
            setupCollectionSearch();
            
            // Check if we already have server-rendered albums
            var albumsGrid = document.getElementById('albumsGrid');
            var hasServerRenderedContent = albumsGrid && 
                albumsGrid.querySelectorAll('.album-card').length > 0;
            
            if (hasServerRenderedContent) {
                console.log('Using pre-rendered album collection');
                // Just set up event handlers for existing content
                setupCollectionEventHandlers();
            } else {
                // Fetch album data if not already rendered
                console.log('No pre-rendered content found, fetching album data');
                fetchCollectionData();
            }
            
        } catch (error) {
            console.error('Error initializing collection page:', error);
        }
    }
    
    /**
     * Setup collection page event handlers
     */
    function setupCollectionEventHandlers() {
        console.log('Setting up collection event handlers');
        
        // Setup album card navigation
        var albumCards = document.querySelectorAll('.album-card');
        console.log('Found ' + albumCards.length + ' album cards to initialize');
        
        for (var i = 0; i < albumCards.length; i++) {
            var card = albumCards[i];
            var albumId = card.dataset.albumId;
            if (!albumId) {
                console.warn('Album card found without album ID');
                continue;
            }
            
            // Set up cover container clicks
            var coverContainer = card.querySelector('.cover-container');
            if (coverContainer && !coverContainer.dataset.spaInitialized) {
                coverContainer.dataset.spaInitialized = 'true';
                console.log('Initializing cover container for album: ' + albumId);
                
                // Remove any existing onclick handler but save it in case we need it
                var originalOnClick = coverContainer.getAttribute('onclick');
                if (originalOnClick) {
                    coverContainer.removeAttribute('onclick');
                }
                
                // Create a closure to preserve albumId
                (function(id) {
                    coverContainer.addEventListener('click', function(e) {
                        e.preventDefault();
                        console.log('Album cover clicked: ' + id);
                        
                        // Use window.spaRouter if available, otherwise fall back to normal navigation
                        if (window.spaRouter && typeof window.spaRouter.navigateTo === 'function') {
                            window.spaRouter.navigateTo('/album/' + id);
                        } else if (typeof window.navigateToAlbum === 'function') {
                            window.navigateToAlbum(id);
                        } else {
                            console.warn('No navigation method found, using direct link');
                            window.location.href = '/album/' + id;
                        }
                    });
                })(albumId);
            }
            
            // Setup favorite buttons
            var favoriteBtn = card.querySelector('.btn-icon.favorite');
            if (favoriteBtn && !favoriteBtn.dataset.spaInitialized) {
                favoriteBtn.dataset.spaInitialized = 'true';
                
                // For favorite buttons, we keep the original onclick handler
                // which should call toggleFavorite(albumId, event)
            }
        }
        
        // Make sure modals work correctly
        var modals = document.querySelectorAll('.modal');
        for (var j = 0; j < modals.length; j++) {
            modals[j].addEventListener('click', function(e) {
                if (e.target === this) {
                    this.classList.remove('active');
                }
            });
        }
        
        // Keyboard handling for modals
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                var activeModals = document.querySelectorAll('.modal.active');
                for (var k = 0; k < activeModals.length; k++) {
                    activeModals[k].classList.remove('active');
                }
            }
        });
        
        // Set up search functionality
        setupCollectionSearch();
        
        // Set up bulk edit functionality
        var selectAllCheckbox = document.getElementById('selectAllAlbums');
        if (selectAllCheckbox) {
            selectAllCheckbox.addEventListener('change', function(e) {
                var albumCheckboxes = document.querySelectorAll('input[name="selected_albums"]');
                for (var i = 0; i < albumCheckboxes.length; i++) {
                    albumCheckboxes[i].checked = e.target.checked;
                }
            });
        }
        
        // Set up create album form
        var createAlbumForm = document.getElementById('createAlbumForm');
        if (createAlbumForm && !createAlbumForm.dataset.spaInitialized) {
            createAlbumForm.dataset.spaInitialized = 'true';
            createAlbumForm.addEventListener('submit', function(e) {
                e.preventDefault();
                console.log('Create album form submitted');
                // The form submission logic is already in the original event handler
            });
        }
        
        // Set up edit album form
        var editAlbumForm = document.getElementById('editAlbumForm');
        if (editAlbumForm && !editAlbumForm.dataset.spaInitialized) {
            editAlbumForm.dataset.spaInitialized = 'true';
            editAlbumForm.addEventListener('submit', function(e) {
                e.preventDefault();
                console.log('Edit album form submitted');
                // The form submission logic is already in the original event handler
            });
        }
        
        // Set up bulk edit form
        var bulkEditForm = document.getElementById('bulkEditForm');
        if (bulkEditForm && !bulkEditForm.dataset.spaInitialized) {
            bulkEditForm.dataset.spaInitialized = 'true';
            bulkEditForm.addEventListener('submit', function(e) {
                e.preventDefault();
                console.log('Bulk edit form submitted');
                // The form submission logic is already in the original event handler
            });
        }
    }
    
    /**
     * Fetch collection data from API and update the page
     */
    function fetchCollectionData() {
        var albumsGrid = document.getElementById('albumsGrid');
        if (!albumsGrid) {
            console.warn('Albums grid not found');
            return;
        }
        
        // Check if there's already server-rendered content
        if (albumsGrid.children.length > 0 && 
            albumsGrid.querySelectorAll('.album-card').length > 0) {
            console.log('Using pre-existing album content');
            setupCollectionEventHandlers();
            return;
        }
        
        // Add loading indicator if no content
        if (albumsGrid.children.length === 0 || 
            (albumsGrid.children.length === 1 && albumsGrid.querySelector('.no-albums'))) {
            albumsGrid.innerHTML = '<div class="loading-albums">Loading your collection...</div>';
        }
        
        // Get user permissions from global state if available
        var userPermissions = window.userPermissions || {
            can_view: true,
            can_create: false,
            can_rename: false,
            can_delete: false,
            can_download: false
        };
        
        console.log('Fetching collection data from API');
        
        // Fetch collection data
        fetch('/api/my-albums/')
            .then(function(response) {
                if (!response.ok) {
                    throw new Error('Failed to load collection');
                }
                return response.json();
            })
            .then(function(albums) {
                // Handle empty collection
                if (!albums || albums.length === 0) {
                    albumsGrid.innerHTML = 
                        '<p class="no-albums">' +
                        (userPermissions.can_create ? 
                            'No albums found. Create one to get started!' : 
                            'No albums available.') +
                        '</p>';
                    return;
                }
                
                console.log('Received ' + albums.length + ' albums from API');
                
                // Render albums grid
                var html = albums.map(function(album) {
                    var tierRestriction = album.tier_restrictions && album.tier_restrictions.is_restricted 
                        ? '<div class="access-level restricted">' +
                          '<i class="fas fa-crown"></i>' +
                          album.tier_restrictions.minimum_tier + ' and above' +
                          '</div>'
                        : '<div class="access-level">' +
                          '<i class="fas fa-globe"></i>' +
                          'Public' +
                          '</div>';
                        
                    return '<div class="album-card" data-album-id="' + album.id + '">' +
                        '<div class="album-buttons">' +
                            (userPermissions.can_download && (!userPermissions.downloads_blocked || userPermissions.is_creator) ?
                            '<button ' +
                                'class="btn-icon download" ' +
                                'data-album-id="' + album.id + '" ' +
                                'data-album-title="' + album.title + '" ' +
                                'onclick="downloadCollection(\'' + album.id + '\', \'' + album.title + '\', event)" ' +
                                'title="Download Collection" ' +
                            '>' +
                                '<i class="fas fa-download"></i>' +
                            '</button>' : 
                            '<button ' +
                                'class="btn-icon download blocked" ' +
                                'disabled ' +
                                'title="Downloads are currently disabled" ' +
                            '>' +
                                '<i class="fas fa-lock"></i>' +
                            '</button>') +
                            
                            '<button ' +
                                'class="btn-icon favorite ' + (album.is_favorite ? 'in-collection' : '') + '" ' +
                                'onclick="toggleFavorite(\'' + album.id + '\', event)" ' +
                                'data-album-id="' + album.id + '" ' +
                                'title="' + (album.is_favorite ? 'Remove from' : 'Add to') + ' favorites" ' +
                            '>' +
                                '<i class="fas fa-heart"></i>' +
                            '</button>' +
                            
                            (userPermissions.can_rename ? 
                            '<button class="btn-icon edit" onclick="openEditModal(\'' + album.id + '\', event)" title="Edit Album">' +
                                '<i class="fas fa-edit"></i>' +
                            '</button>' : '') +
                            
                            (userPermissions.can_delete ? 
                            '<button class="btn-icon delete" onclick="openDeleteModal(\'' + album.id + '\', \'' + album.title + '\', event)" title="Delete Album">' +
                                '<i class="fas fa-trash"></i>' +
                            '</button>' : '') +
                        '</div>' +

                        '<div class="cover-container" data-album-id="' + album.id + '">' +
                            '<img src="' + album.cover_path + '" ' +
                                 'alt="' + album.title + '" ' +
                                 'class="album-cover" ' +
                                 'onerror="handleImageError(this)" ' +
                                 'data-album-title="' + album.title + '">' +
                        '</div>' +

                        '<div class="album-info">' +
                            '<h3>' + album.title + '</h3>' +
                            '<div class="album-meta">' +
                                '<div class="track-count">' +
                                    '<i class="fas fa-music"></i>' +
                                    album.track_count + ' tracks' +
                                '</div>' +
                                
                                tierRestriction +

                                (userPermissions.can_download && window.user && window.user.download_info ?
                                '<div class="download-info">' +
                                    '<i class="fas fa-download"></i>' +
                                    '<span>' +
                                        'Albums: ' + window.user.download_info.albums.downloads_remaining + ' | ' +
                                        'Tracks: ' + window.user.download_info.tracks.downloads_remaining +
                                    '</span>' +
                                '</div>' : '') +
                            '</div>' +
                        '</div>' +
                    '</div>';
                }).join('');
                
                albumsGrid.innerHTML = html;
                
                // Add custom styling for loading if not already present
                if (!document.getElementById('collection-spa-styles')) {
                    var style = document.createElement('style');
                    style.id = 'collection-spa-styles';
                    style.textContent = 
                        '.loading-albums {' +
                            'padding: 2rem;' +
                            'text-align: center;' +
                            'color: var(--text-secondary);' +
                            'font-size: 1.1rem;' +
                        '}' +
                        
                        '.error-message {' +
                            'padding: 1.5rem;' +
                            'text-align: center;' +
                            'color: #EF4444;' +
                            'background: rgba(239, 68, 68, 0.1);' +
                            'border-radius: 0.5rem;' +
                            'margin: 1rem 0;' +
                        '}';
                    document.head.appendChild(style);
                }
                
                console.log('Album collection rendered, setting up event handlers');
                
                // Setup event handlers after fetching data
                setupCollectionEventHandlers();
            })
            .catch(function(error) {
                console.error('Error fetching collection data:', error);
                albumsGrid.innerHTML = 
                    '<div class="error-message">' +
                        '<i class="fas fa-exclamation-circle"></i>' +
                        'Failed to load your collection. Please try refreshing the page.' +
                    '</div>';
            });
    }
    
    /**
     * Set up search functionality for collection page
     */
    function setupCollectionSearch() {
        var searchInput = document.getElementById('albumSearch');
        var clearButton = document.getElementById('clearSearch');
        var albumsGrid = document.getElementById('albumsGrid');
        var searchStatus = document.getElementById('searchStatus');
        
        if (!searchInput || !albumsGrid) {
            console.log('Search elements not found, skipping search setup');
            return;
        }
        
        console.log('Setting up collection search functionality');
        
        var debounceTimeout;
        
        function performSearch() {
            var query = searchInput.value.toLowerCase().trim();
            var albums = albumsGrid.querySelectorAll('.album-card');
            var visibleCount = 0;
            
            // Show/hide clear button
            if (clearButton) {
                clearButton.style.display = query ? 'block' : 'none';
            }
            
            for (var i = 0; i < albums.length; i++) {
                var album = albums[i];
                var titleEl = album.querySelector('.album-info h3');
                var title = titleEl ? titleEl.textContent.toLowerCase() : '';
                
                if (!title) continue;
                
                var matches = title.includes(query);
                
                if (matches) {
                    album.style.display = '';
                    visibleCount++;
                } else {
                    album.style.display = 'none';
                }
            }
            
            // Update search status if it exists
            if (searchStatus) {
                if (query) {
                    searchStatus.textContent = 'Found ' + visibleCount + ' album' + (visibleCount !== 1 ? 's' : '');
                } else {
                    searchStatus.textContent = '';
                }
            }
        }
        
        // If the search input already has an event listener, don't add another one
        if (!searchInput.dataset.spaInitialized) {
            searchInput.dataset.spaInitialized = 'true';
            
            // Set up search input handler with debounce
            searchInput.addEventListener('input', function() {
                clearTimeout(debounceTimeout);
                debounceTimeout = setTimeout(performSearch, 300);
            });
            
            // Set up clear button handler
            if (clearButton && !clearButton.dataset.spaInitialized) {
                clearButton.dataset.spaInitialized = 'true';
                
                clearButton.addEventListener('click', function() {
                    searchInput.value = '';
                    performSearch();
                    searchInput.focus();
                });
            }
            
            // Handle escape key
            searchInput.addEventListener('keydown', function(e) {
                if (e.key === 'Escape') {
                    searchInput.value = '';
                    performSearch();
                    searchInput.blur();
                }
            });
        } else {
            console.log('Search functionality already initialized');
        }
        
        // Run search immediately if there's a value in the input
        if (searchInput.value.trim()) {
            performSearch();
        }
    }
})();