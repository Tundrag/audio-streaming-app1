/**
 * Collection Manager - Handles infinite scroll, search, and album management
 * Updated with server-side search functionality
 * Wrapped in module pattern to prevent redeclaration
 */
(function() {
    'use strict';
    
    // Only define if not already defined
    if (window.CollectionManager) {
        // console.log('CollectionManager already exists, skipping redefinition');
        return;
    }

class CollectionManager {
    constructor() {
        this.config = {
            // Different page sizes for mobile vs desktop
            mobilePageSize: 8,
            desktopPageSize: 16,
            searchDebounceMs: 300,
            endpoint: '/api/collection/albums'
        };

        this.state = {
            // Normal browsing state
            currentPage: 1,
            hasNextPage: true,
            isLoading: false,
            allAlbums: [],
            totalAlbums: 0,
            
            // Search state
            searchQuery: '',
            searchPage: 1,
            searchHasNext: true,
            searchAlbums: [],
            searchTotal: 0,
            isSearchMode: false,
            
            // Common state
            currentAlbumId: null
        };

        this.elements = {};
        this.searchDebounceTimer = null;
        this.intersectionObserver = null;

        this.init();
    }

    init() {
        this.cacheElements();
        
        // Verify critical elements exist
        if (!this.elements.albumsGrid) {
            // console.error('CollectionManager: Critical DOM elements not found. Retrying in 100ms...');
            setTimeout(() => {
                this.cacheElements();
                if (this.elements.albumsGrid) {
                    this.setupEventListeners();
                    this.setupIntersectionObserver();
                    this.loadInitialAlbums();
                } else {
                    // console.error('CollectionManager: Failed to find DOM elements after retry');
                }
            }, 100);
            return;
        }
        
        this.setupEventListeners();
        this.setupIntersectionObserver();
        this.loadInitialAlbums();
    }


    cacheElements() {
        this.elements = {
            albumsGrid: document.getElementById('albumsGrid'),
            loadingIndicator: document.getElementById('loadingIndicator'),
            loadMoreContainer: document.getElementById('loadMoreContainer'),
            loadMoreBtn: document.getElementById('loadMoreBtn'),
            noAlbumsMessage: document.getElementById('noAlbumsMessage'),
            searchInput: document.getElementById('albumSearch'),
            clearSearchBtn: document.getElementById('clearSearch'),
            searchStatus: document.getElementById('searchStatus')
        };
    }

    escapeHtml(text) {
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, m => map[m]);
    }

    escapeForAttribute(text) {
        return text
            .replace(/\\/g, '\\\\')
            .replace(/'/g, "\\'")
            .replace(/"/g, '\\"')
            .replace(/\n/g, '\\n')
            .replace(/\r/g, '\\r')
            .replace(/\t/g, '\\t');
    }


    setupEventListeners() {
        // Search functionality
        if (this.elements.searchInput) {
            this.elements.searchInput.addEventListener('input', (e) => {
                this.handleSearch(e.target.value);
            });

            this.elements.searchInput.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    this.clearSearch();
                }
                if (e.key === 'Enter') {
                    e.preventDefault(); // Prevent form submission if in a form
                }
            });
        }

        if (this.elements.clearSearchBtn) {
            this.elements.clearSearchBtn.addEventListener('click', () => {
                this.clearSearch();
            });
        }

        // Load more button
        if (this.elements.loadMoreBtn) {
            this.elements.loadMoreBtn.addEventListener('click', () => {
                this.loadMoreAlbums();
            });
        }

        // Modal close handlers
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeAllModals();
            }
        });

        // Modal backdrop clicks
        document.querySelectorAll('.modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.classList.remove('active');
                }
            });
        });
    }

    setupIntersectionObserver() {
        // Create a sentinel element for infinite scroll
        const sentinel = document.createElement('div');
        sentinel.id = 'scrollSentinel';
        sentinel.style.height = '1px';
        document.body.appendChild(sentinel);

        this.intersectionObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting && this.canLoadMore() && !this.state.isLoading) {
                    this.loadMoreAlbums();
                }
            });
        }, {
            rootMargin: '100px'
        });

        this.intersectionObserver.observe(sentinel);
    }

    canLoadMore() {
        return this.state.isSearchMode ? this.state.searchHasNext : this.state.hasNextPage;
    }

    getPageSize() {
        const isMobile = window.innerWidth <= 768;
        return isMobile ? this.config.mobilePageSize : this.config.desktopPageSize;
    }

    async loadInitialAlbums() {
        this.showLoading();
        await this.loadAlbums(1, true);
        this.hideLoading();
    }

    async loadMoreAlbums() {
        if (this.state.isLoading || !this.canLoadMore()) return;

        this.state.isLoading = true;
        this.setLoadMoreButtonLoading(true);

        try {
            const nextPage = this.state.isSearchMode ? 
                this.state.searchPage + 1 : 
                this.state.currentPage + 1;
            await this.loadAlbums(nextPage, false);
        } catch (error) {
            // console.error('Error loading more albums:', error);
            this.showToast('Error loading more albums', 'error');
        } finally {
            this.state.isLoading = false;
            this.setLoadMoreButtonLoading(false);
        }
    }

    async loadAlbums(page, isInitial = false, searchQuery = null) {
        try {
            const pageSize = this.getPageSize();
            
            // Determine if this is a search request
            const isSearchRequest = searchQuery !== null || this.state.isSearchMode;
            const actualSearchQuery = searchQuery !== null ? searchQuery : this.state.searchQuery;
            
            // Build URL with search parameter if needed
            let url = `${this.config.endpoint}?page=${page}&per_page=${pageSize}`;
            if (actualSearchQuery && actualSearchQuery.trim()) {
                url += `&search=${encodeURIComponent(actualSearchQuery.trim())}`;
            }
            
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();
            
            // Handle search vs normal browsing
            if (isSearchRequest) {
                this.handleSearchResults(data, page, isInitial);
            } else {
                this.handleBrowsingResults(data, page, isInitial);
            }

            this.renderCurrentView(isInitial);
            this.updateLoadMoreVisibility();
            this.updateNoAlbumsMessage();
            this.updateSearchStatus(data);

            // Check for scheduled visibility changes and show countdowns
            if (window.checkAllCardSchedules) {
                setTimeout(() => window.checkAllCardSchedules(), 100);
            }

        } catch (error) {
            // console.error('Error loading albums:', error);
            this.showToast('Error loading albums', 'error');
            if (isInitial) {
                this.updateNoAlbumsMessage();
            }
        }
    }

    handleSearchResults(data, page, isInitial) {
        const albums = data.albums || [];
        
        if (isInitial) {
            this.state.searchAlbums = albums;
            this.state.searchPage = 1;
        } else {
            this.state.searchAlbums.push(...albums);
            this.state.searchPage = page;
        }
        
        this.state.searchHasNext = data.pagination?.has_next || false;
        this.state.searchTotal = data.pagination?.total || 0;
        this.state.isSearchMode = true;
    }

    handleBrowsingResults(data, page, isInitial) {
        const albums = data.albums || [];
        
        if (isInitial) {
            this.state.allAlbums = albums;
            this.state.currentPage = 1;
        } else {
            this.state.allAlbums.push(...albums);
            this.state.currentPage = page;
        }
        
        this.state.hasNextPage = data.pagination?.has_next || false;
        this.state.totalAlbums = data.pagination?.total || 0;
        this.state.isSearchMode = false;
    }

    renderCurrentView(clearExisting = false) {
        if (clearExisting) {
            this.elements.albumsGrid.innerHTML = '';
        }

        const albumsToRender = this.state.isSearchMode ? 
            this.state.searchAlbums : 
            this.state.allAlbums;
        
        // Only render new albums if not clearing existing
        const startIndex = clearExisting ? 0 : this.elements.albumsGrid.children.length;
        const albumsToAdd = clearExisting ? albumsToRender : albumsToRender.slice(startIndex);

        albumsToAdd.forEach(album => {
            const albumElement = this.createAlbumElement(album);
            this.elements.albumsGrid.appendChild(albumElement);
        });
    }

    handleSearch(query) {
        clearTimeout(this.searchDebounceTimer);
        
        this.searchDebounceTimer = setTimeout(() => {
            this.performSearch(query.trim());
        }, this.config.searchDebounceMs);
    }

    async performSearch(query) {
        // Update search UI
        this.elements.clearSearchBtn.style.display = query ? 'block' : 'none';
        
        if (!query) {
            // Clear search - return to normal browsing
            // console.log('Empty query - clearing search');
            await this.clearSearchMode();
            return;
        }

        // Store search query and perform server-side search
        this.state.searchQuery = query;
        this.showLoading();
        
        try {
            // console.log(`Searching for: "${query}"`);
            await this.loadAlbums(1, true, query);
        } catch (error) {
            // console.error('Search error:', error);
            this.showToast('Search failed', 'error');
        } finally {
            this.hideLoading();
        }
    }

    async clearSearchMode() {
        // console.log('Clearing search mode...');
        
        this.state.searchQuery = '';
        this.state.isSearchMode = false;
        this.state.searchAlbums = [];
        this.state.searchPage = 1;
        this.state.searchHasNext = true;
        this.state.searchTotal = 0;
        
        this.elements.searchStatus.textContent = '';
        
        // SIMPLE BUT EFFECTIVE APPROACH:
        // Always reload the normal browsing state to ensure proper pagination
        this.showLoading();
        
        // Reset to page 1 and reload - this ensures clean state
        this.state.currentPage = 1;
        this.state.hasNextPage = true;
        this.state.allAlbums = [];
        
        await this.loadAlbums(1, true);
        this.hideLoading();
        
        // console.log('Search mode cleared, normal browsing restored');
    }

    // Add this new method to ensure intersection observer is working
    ensureIntersectionObserver() {
        // Check if sentinel element exists and is being observed
        const sentinel = document.getElementById('scrollSentinel');
        if (sentinel && this.intersectionObserver) {
            // Temporarily disconnect and reconnect to ensure it's working
            this.intersectionObserver.unobserve(sentinel);
            setTimeout(() => {
                this.intersectionObserver.observe(sentinel);
            }, 100);
        }
    }

    // UPDATED: Improved clearSearch method
    clearSearch() {
        this.elements.searchInput.value = '';
        this.elements.clearSearchBtn.style.display = 'none';
        this.clearSearchMode();
        this.elements.searchInput.focus();
    }
    updateSearchStatus(data) {
        if (!this.state.isSearchMode) {
            this.elements.searchStatus.textContent = '';
            return;
        }

        const searchInfo = data.search || {};
        const totalResults = searchInfo.result_count || 0;
        const query = searchInfo.query || this.state.searchQuery;
        
        if (totalResults === 0) {
            this.elements.searchStatus.textContent = `No albums found for "${query}"`;
        } else {
            this.elements.searchStatus.textContent = `Found ${totalResults} album${totalResults !== 1 ? 's' : ''} for "${query}"`;
        }
    }

    updateLoadMoreVisibility() {
        const shouldShow = this.canLoadMore();
        this.elements.loadMoreContainer.style.display = shouldShow ? 'flex' : 'none';
    }

    updateNoAlbumsMessage() {
        const currentAlbums = this.state.isSearchMode ? 
            this.state.searchAlbums : 
            this.state.allAlbums;
            
        const hasAlbums = currentAlbums.length > 0;
        this.elements.noAlbumsMessage.style.display = hasAlbums ? 'none' : 'block';
        
        // Update message text based on search mode
        if (!hasAlbums && this.state.isSearchMode) {
            this.elements.noAlbumsMessage.innerHTML = `No albums found for "${this.state.searchQuery}"`;
        } else if (!hasAlbums) {
            const permissions = window.collectionConfig.permissions;
            this.elements.noAlbumsMessage.innerHTML = permissions.can_create ? 
                'No albums found. Create one to get started!' : 
                'No albums available.';
        }
    }

createAlbumElement(album) {
    const albumCard = document.createElement('div');
    albumCard.className = 'album-card';
    albumCard.dataset.albumId = album.id;

    const permissions = window.collectionConfig.permissions;

    const escapedTitle = this.escapeForAttribute(album.title);
    const escapedId = this.escapeForAttribute(album.id);

    // Debug: Log album visibility status
    console.log(`[Collection] Creating card for album "${album.title}", visibility_status:`, album.visibility_status);

    // Visibility badge HTML - always show full text for albums (collections), even on mobile
    const badgeTitle = album.visibility_status === 'hidden_from_all' ? 'Hidden from All' : 'Hidden from Users';
    const visibilityBadgeHtml = album.visibility_status && album.visibility_status !== 'visible' ? `
        <div class="visibility-badge-info visibility-${album.visibility_status}">
            <i class="fas fa-eye-slash"></i>
            <span class="badge-text">${badgeTitle}</span>
        </div>
    ` : '';

    if (visibilityBadgeHtml) {
        console.log(`[Collection] Added visibility badge for album "${album.title}"`);
    }

    // ✅ Add data-spa-link attribute
    const coverHtml = `
        <div class="cover-container">
            <a href="/album/${escapedId}"
               data-spa-link
               onclick="event.preventDefault(); navigateToAlbum('${escapedId}')">
                <img src="${album.cover_path || '/static/images/default-album.jpg'}"
                     alt="${this.escapeHtml(album.title)}"
                     class="album-cover"
                     onerror="handleImageError(this)"
                     data-album-title="${this.escapeHtml(album.title)}"
                     style="display: block;">
                <div class="album-cover-placeholder" style="display: none;">
                    <span class="album-initial">${album.title.charAt(0).toUpperCase()}</span>
                </div>
            </a>
        </div>
    `;

        albumCard.innerHTML = `
            <!-- Album Buttons -->
            <div class="album-buttons">
                ${permissions.can_download ? `
                    ${(!permissions.downloads_blocked || permissions.is_creator) ? `
                        <button 
                            class="btn-icon download" 
                            onclick="downloadCollection('${escapedId}', '${escapedTitle}', event)"  
                            title="Download Collection"
                        >
                            <i class="fas fa-download"></i>
                        </button>
                    ` : `
                        <button 
                            class="btn-icon download blocked" 
                            disabled
                            title="Downloads are currently disabled"
                        >
                            <i class="fas fa-lock"></i>
                        </button>
                    `}
                ` : ''}

                <button 
                    class="btn-icon favorite ${album.is_favorite ? 'in-collection' : ''}" 
                    onclick="toggleFavorite('${escapedId}', event)"
                    data-album-id="${album.id}"
                    title="${album.is_favorite ? 'Remove from' : 'Add to'} favorites"
                >
                    <i class="fas fa-heart"></i>
                </button>

                ${permissions.can_rename ? `
                    <button class="btn-icon edit" onclick="openEditModal('${escapedId}', event)" title="Edit Album">
                        <i class="fas fa-edit"></i>
                    </button>
                ` : ''}
                
                ${permissions.can_delete ? `
                    <button class="btn-icon delete" onclick="openDeleteModal('${escapedId}', '${escapedTitle}', event)" title="Delete Album">
                        <i class="fas fa-trash"></i>
                    </button>
                ` : ''}
            </div>

            ${coverHtml}

            <!-- Album Info -->
            <div class="album-info">
                <!-- Visibility badge - positioned at top of info section -->
                ${visibilityBadgeHtml}

                <!-- Schedule countdown indicator - below badge (will be populated if schedule exists) -->
                <div class="card-schedule-indicator" id="schedule-${escapedId}" style="display: none;">
                    <i class="fas fa-clock"></i>
                    <span class="schedule-text">
                        Scheduled: <strong class="schedule-target"></strong> in <strong class="schedule-countdown"></strong>
                    </span>
                </div>

                <h3>${this.escapeHtml(album.title)}</h3>

                <div class="album-meta">
                    <div class="track-count">
                        <i class="fas fa-music"></i>
                        ${album.track_count || 0} tracks
                    </div>

                    ${album.tier_restrictions && album.tier_restrictions.is_restricted ? `
                        <div class="access-level restricted">
                            <i class="fas fa-crown"></i>
                            ${album.tier_restrictions.minimum_tier} and above
                        </div>
                    ` : `
                        <div class="access-level">
                            <i class="fas fa-globe"></i>
                            Public
                        </div>
                    `}

                    ${permissions.can_download && window.collectionConfig.user.download_info ? `
                        <div class="download-info">
                            <i class="fas fa-download"></i>
                            <span>
                                Albums: ${window.collectionConfig.user.download_info.albums.downloads_remaining} | 
                                Tracks: ${window.collectionConfig.user.download_info.tracks.downloads_remaining}
                            </span>
                        </div>
                    ` : ''}
                </div>
            </div>
        `;

        return albumCard;
    }

    setLoadMoreButtonLoading(loading) {
        const btn = this.elements.loadMoreBtn;
        if (!btn) return;

        if (loading) {
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
        } else {
            btn.disabled = false;
            const moreText = this.state.isSearchMode ? 'Load More Results' : 'Load More Albums';
            btn.innerHTML = `<i class="fas fa-plus"></i> ${moreText}`;
        }
    }

    showLoading() {
        this.elements.loadingIndicator.style.display = 'flex';
        this.elements.albumsGrid.style.display = 'none';
    }

    hideLoading() {
        this.elements.loadingIndicator.style.display = 'none';
        this.elements.albumsGrid.style.display = 'grid';
    }

    closeAllModals() {
        document.querySelectorAll('.modal.active').forEach(modal => {
            modal.classList.remove('active');
        });
    }

    showToast(message, type = 'info') {
        // Remove any existing toasts
        document.querySelectorAll('.quick-toast').forEach(t => t.remove());
        
        // Create toast
        const toast = document.createElement('div');
        toast.className = 'quick-toast';
        toast.innerHTML = `<span>${message}</span>`;
        
        // Set styles for LEFT CORNER positioning
        Object.assign(toast.style, {
            position: 'fixed',
            top: '80px',           // Below header
            left: '20px',          // LEFT side instead of center
            transform: 'none',     // Remove center transform
            backgroundColor: type === 'success' ? '#10B981' : type === 'error' ? '#EF4444' : '#333',
            color: 'white',
            padding: '12px 24px',
            borderRadius: '6px',
            fontSize: '14px',
            zIndex: '99999',
            opacity: '1',
            display: 'block',
            fontFamily: 'inherit',
            boxShadow: '0 2px 10px rgba(0,0,0,0.3)',
            maxWidth: '300px',     // Prevent too wide on mobile
            wordWrap: 'break-word' // Handle long text
        });

        document.body.appendChild(toast);

        // Remove after 3 seconds
        setTimeout(() => {
            if (toast.parentNode) {
                toast.remove();
            }
        }, 3000);
    }

    escapeHtml(text) {
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, m => map[m]);
    }

    // Public method to remove album from UI after deletion
    removeAlbumFromUI(albumId) {
        const albumCard = document.querySelector(`[data-album-id="${albumId}"]`);
        if (albumCard) {
            albumCard.style.animation = 'fadeOut 0.3s ease';
            setTimeout(() => {
                albumCard.remove();
                
                // Remove from both normal and search state
                this.state.allAlbums = this.state.allAlbums.filter(album => album.id !== albumId);
                this.state.searchAlbums = this.state.searchAlbums.filter(album => album.id !== albumId);
                
                this.updateNoAlbumsMessage();
            }, 300);
        }
    }
    // Public method to update album in UI after edit
    updateAlbumInUI(albumId, updatedData) {
        // Update in both states
        const albumIndex = this.state.allAlbums.findIndex(album => album.id === albumId);
        if (albumIndex !== -1) {
            this.state.allAlbums[albumIndex] = { ...this.state.allAlbums[albumIndex], ...updatedData };
        }

        const searchIndex = this.state.searchAlbums.findIndex(album => album.id === albumId);
        if (searchIndex !== -1) {
            this.state.searchAlbums[searchIndex] = { ...this.state.searchAlbums[searchIndex], ...updatedData };
        }

        // Re-render if currently visible
        const albumCard = document.querySelector(`[data-album-id="${albumId}"]`);
        if (albumCard) {
            const currentAlbums = this.state.isSearchMode ? this.state.searchAlbums : this.state.allAlbums;
            const updatedAlbum = currentAlbums.find(album => album.id === albumId);
            if (updatedAlbum) {
                const newElement = this.createAlbumElement(updatedAlbum);
                albumCard.parentNode.replaceChild(newElement, albumCard);
            }
        }
    }
}

// Keep all the existing classes (CollectionActions, CollectionModals, CollectionForms) unchanged
// They remain the same as in the original code...

/**
 * Collection Actions - Handles downloads, favorites, etc.
 */
class CollectionActions {
    static async toggleFavorite(albumId, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }

        const button = event.currentTarget;
        if (button.disabled || button.dataset.processing === 'true') {
            return;
        }

        try {
            button.disabled = true;
            button.dataset.processing = 'true';
            const originalHtml = button.innerHTML;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            
            const response = await fetch(`/api/my-albums/${albumId}/favorite`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin'
            });

            if (response.status === 303) {
                window.location.href = '/login';
                return;
            }

            const result = await response.json();

            // Update all button states for this album
            document.querySelectorAll(`.favorite[data-album-id="${albumId}"]`).forEach(btn => {
                btn.classList.toggle('in-collection', result.is_favorite);
                btn.title = result.is_favorite ? 'Remove from favorites' : 'Add to favorites';
            });

            // Update state in collection manager
            if (window.collectionManager) {
                const album = window.collectionManager.state.allAlbums.find(a => a.id === albumId);
                if (album) {
                    album.is_favorite = result.is_favorite;
                }
                const searchAlbum = window.collectionManager.state.searchAlbums.find(a => a.id === albumId);
                if (searchAlbum) {
                    searchAlbum.is_favorite = result.is_favorite;
                }
            }

            // Show success message
            window.collectionManager?.showToast(result.message, 'success');

        } catch (error) {
            // console.error('Error:', error);
            window.collectionManager?.showToast('Failed to update favorites', 'error');
        } finally {
            button.disabled = false;
            button.dataset.processing = 'false';
            button.innerHTML = '<i class="fas fa-heart"></i>';
        }
    }

    static async downloadCollection(albumId, albumTitle, event) {
        if (event) {
            event.stopPropagation();
            event.preventDefault();
        }

        const button = event.currentTarget;
        if (button.disabled || button.classList.contains('loading')) {
            return;
        }

        button.disabled = true;
        button.classList.add('loading');

        try {
            if (!albumId) {
                throw new Error('Missing album ID');
            }

            // Check tier access first
            const accessResponse = await fetch(`/api/albums/${albumId}/check-access`);



            
            if (accessResponse.status === 403) {
                const accessData = await accessResponse.json();
                const message = accessData.error?.message || 'This content requires a higher tier subscription';
                if (window.showUpgradeModal) {
                    window.showUpgradeModal(message);
                } else {
                    window.collectionManager?.showToast(message, 'error');
                }
                throw new Error(message);
            }

            if (!accessResponse.ok) {
                throw new Error('Failed to verify access permissions');
            }

            // Create progress overlay
            const albumCard = button.closest('.album-card');
            let progressContainer = albumCard.querySelector('.download-progress');
            
            if (!progressContainer) {
                progressContainer = this.createProgressElement();
                albumCard.appendChild(progressContainer);
            }

            // Get the progress elements for future updates
            const progressElements = {
                circle: progressContainer.querySelector('.progress-ring-circle'),
                text: progressContainer.querySelector('.progress-text'),
                stage: progressContainer.querySelector('.progress-stage'),
                status: progressContainer.querySelector('.progress-status'),
                substatus: progressContainer.querySelector('.progress-substatus')
            };

            // Start the download
            const response = await fetch(`/api/albums/${albumId}/download`);
            
            if (response.status === 403) {
                const errorData = await response.json();
                const errorMessage = errorData?.message || 'No downloads remaining. Please upgrade your tier for more downloads.';
                
                if (window.showUpgradeModal) {
                    window.showUpgradeModal(errorMessage);
                } else {
                    window.collectionManager?.showToast(errorMessage, 'error');
                }
                throw new Error(errorMessage);
            }

            if (!response.ok) {
                throw new Error(await response.text());
            }

            const initialData = await response.json();
            let downloadId = initialData?.status?.download_id || `${initialData.user_id || '1'}_${albumId}`;

            // Initial UI update
            const initialNormalizedStatus = this.normalizeStatus(initialData);
            this.updateProgressUI(initialNormalizedStatus, progressElements);

            // Poll for status
            await this.pollDownloadStatus(albumId, downloadId, albumTitle, progressElements);

        } catch (error) {
            // console.error('Download process error:', error);
            window.collectionManager?.showToast(error.message || 'Download failed', 'error');
        } finally {
            button.disabled = false;
            button.classList.remove('loading');
        }
    }

    static createProgressElement() {
        const container = document.createElement('div');
        container.className = 'download-progress';
        
        const radius = 16;
        const circumference = 2 * Math.PI * radius;
        
        container.innerHTML = `
            <div class="progress-wrapper">
                <div class="progress-circle">
                    <svg class="progress-ring" width="40" height="40">
                        <circle class="progress-ring-circle-bg" cx="20" cy="20" r="${radius}"/>
                        <circle class="progress-ring-circle" cx="20" cy="20" r="${radius}" 
                            style="stroke-dasharray: ${circumference}; stroke-dashoffset: ${circumference}; stroke: #9CA3AF;"/>
                    </svg>
                    <span class="progress-text">0%</span>
                </div>
                <div class="progress-details">
                    <div class="progress-stage">INITIALIZING</div>
                    <div class="progress-status">Starting download...</div>
                    <div class="progress-substatus"></div>
                </div>
            </div>
        `;
        
        return container;
    }

    static normalizeStatus(status) {
        const normalized = {
            status: 'unknown',
            stage: 'unknown',
            stage_detail: 'Processing...',
            progress: 0,
            processed_size: 0,
            total_size: 0,
            rate: 0
        };
        
        const rawStatus = (status.status || status.stage || '').toString().toLowerCase();
        const baseStage = rawStatus.replace('downloadstage.', '').replace('download_stage.', '');
        
        normalized.status = baseStage;
        normalized.stage = baseStage;
        normalized.stage_detail = status.stage_detail || '';
        normalized.progress = typeof status.progress === 'number' ? status.progress : 0;
        normalized.processed_size = status.processed_size || 0;
        normalized.total_size = status.total_size || 0;
        normalized.rate = status.rate || 0;
        normalized.current_track = status.current_track || status.track_number || 0;
        normalized.total_tracks = status.total_tracks || 0;
        normalized.error = status.error || null;
        normalized.download_path = status.download_path || null;
        
        return normalized;
    }

    static updateProgressUI(status, elements) {
        const { circle, text, stage, status: statusElement, substatus } = elements;
        
        let baseStage = (status.stage || status.status || '')
            .toString()
            .toLowerCase()
            .replace('downloadstage.', '')
            .replace('download_stage.', '');
        
        const stageConfig = {
            queued: { color: '#9CA3AF', displayName: 'QUEUED', defaultStatus: 'Waiting in queue...' },
            initialization: { color: '#60A5FA', displayName: 'INITIALIZING', defaultStatus: 'Starting download...' },
            downloading: { color: '#3B82F6', displayName: 'DOWNLOADING', defaultStatus: 'Downloading tracks...' },
            compression: { color: '#8B5CF6', displayName: 'COMPRESSING', defaultStatus: 'Creating ZIP file...' },
            streaming: { color: '#6366F1', displayName: 'PREPARING', defaultStatus: 'Preparing download...' },
            completed: { color: '#10B981', displayName: 'COMPLETED', defaultStatus: 'Download ready' },
            error: { color: '#EF4444', displayName: 'ERROR', defaultStatus: 'Download failed' }
        };

        const config = stageConfig[baseStage] || stageConfig.queued;
        let currentProgress = status.progress || 0;
        let statusText = status.stage_detail || config.defaultStatus;
        let substatusText = '';

        // Handle stage-specific updates
        switch (baseStage) {
            case 'downloading':
                if (status.current_track && status.total_tracks) {
                    substatusText = `Track ${status.current_track}/${status.total_tracks}`;
                    if (status.processed_size && status.total_size) {
                        substatusText += ` • ${this.formatSize(status.processed_size)} / ${this.formatSize(status.total_size)}`;
                    }
                }
                break;
            case 'completed':
                currentProgress = 100;
                if (status.processed_size) {
                    substatusText = `Complete • ${this.formatSize(status.processed_size)}`;
                }
                break;
            case 'error':
                substatusText = status.error || 'Download failed';
                break;
        }

        // Update progress circle
        this.updateProgressCircle(circle, currentProgress, config.color);
        
        // Update text elements
        stage.textContent = config.displayName;
        stage.style.color = config.color;
        statusElement.textContent = statusText;
        substatus.textContent = substatusText;
        text.textContent = `${Math.round(currentProgress)}%`;
    }

    static updateProgressCircle(circle, progress, color) {
        const validProgress = Math.max(0, Math.min(100, progress));
        const radius = 16;
        const circumference = 2 * Math.PI * radius;
        const offset = circumference * (1 - (validProgress / 100));
        
        circle.setAttribute('stroke-dasharray', `${circumference} ${circumference}`);
        circle.setAttribute('stroke-dashoffset', offset);
        circle.setAttribute('stroke', color);
    }

    static formatSize(bytes) {
        if (!bytes) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }
        return `${size.toFixed(1)} ${units[unitIndex]}`;
    }

    static async pollDownloadStatus(albumId, downloadId, albumTitle, progressElements) {
        let completed = false;
        const maxAttempts = 300;
        let attempts = 0;

        while (!completed && attempts < maxAttempts) {
            await new Promise(resolve => setTimeout(resolve, 1000));
            attempts++;
            
            try {
                const statusResponse = await fetch(`/api/downloads/status?album_id=${albumId}`);
                
                if (!statusResponse.ok) {
                    continue;
                }
                
                const status = await statusResponse.json();

                if (status.status === 'error') {
                    throw new Error(status.error || 'Download failed');
                }

                const normalizedStatus = this.normalizeStatus(status);
                this.updateProgressUI(normalizedStatus, progressElements);

                if (normalizedStatus.status === 'completed') {
                    const downloadUrl = normalizedStatus.download_path || `/api/files/${downloadId}`;
                    
                    // Create temporary link and trigger download
                    const link = document.createElement('a');
                    link.href = downloadUrl;
                    link.download = `${albumTitle.replace(/[^a-z0-9]/gi, '_').toLowerCase()}.zip`;
                    
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);

                    completed = true;
                    window.collectionManager?.showToast('Download complete!', 'success');
                    
                    // Remove progress overlay after delay
                    setTimeout(() => {
                        const progressContainer = progressElements.circle.closest('.download-progress');
                        if (progressContainer) {
                            progressContainer.style.animation = 'fadeOut 0.3s ease forwards';
                            setTimeout(() => progressContainer.remove(), 300);
                        }
                    }, 3000);
                }
            } catch (pollError) {
                // console.error('Error during status polling:', pollError);
                throw pollError;
            }
        }

        if (!completed && attempts >= maxAttempts) {
            throw new Error('Download timed out. Please try again later.');
        }
    }
}

/**
 * Collection Modals - Handles all modal operations
 */
class CollectionModals {
    static openModal() {
        const modal = document.getElementById('createAlbumModal');
        if (modal) {
            modal.classList.add('active');
            document.body.style.overflow = 'hidden';
        }
    }

    static closeModal() {
        const modal = document.getElementById('createAlbumModal');
        if (modal) {
            modal.classList.remove('active');
            document.getElementById('createAlbumForm')?.reset();
            document.body.style.overflow = '';
        }
    }

    static openEditModal(albumId, event) {
        if (event) {
            event.stopPropagation();
        }

        const modal = document.getElementById('editAlbumModal');
        const albumCard = document.querySelector(`[data-album-id="${albumId}"]`);

        if (!modal || !albumCard) return;

        const albumTitle = albumCard.querySelector('.album-info h3').textContent;

        // Set album title
        document.getElementById('editAlbumTitle').value = albumTitle;

        // Get current tier restrictions
        const restrictionDiv = albumCard.querySelector('.access-level.restricted');
        const currentTier = restrictionDiv ?
            restrictionDiv.textContent.replace(' and above', '').trim() : '';

        // Set tier selection
        const tierSelect = document.getElementById('editAlbumTiers');
        if (tierSelect) {
            tierSelect.value = currentTier || '';
        }

        // Get and set current visibility status from album data
        const visibilitySelect = document.getElementById('editAlbumVisibility');
        if (visibilitySelect) {
            // Try to find visibility from state
            const album = window.collectionManager?.state.allAlbums.find(a => a.id === albumId) ||
                         window.collectionManager?.state.searchAlbums.find(a => a.id === albumId);
            const currentVisibility = album?.visibility_status || 'visible';
            visibilitySelect.value = currentVisibility;
        }

        // Store album ID for submission
        document.getElementById('editAlbumForm').dataset.albumId = albumId;
        modal.dataset.albumId = albumId; // For schedule modal
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';

        // Check for existing scheduled visibility changes
        if (typeof checkExistingSchedule === 'function') {
            checkExistingSchedule(albumId);
        }
    }

    static closeEditModal() {
        const modal = document.getElementById('editAlbumModal');
        if (modal) {
            modal.classList.remove('active');
            document.getElementById('editAlbumForm')?.reset();
            document.body.style.overflow = '';
        }
    }

    static openDeleteModal(albumId, albumTitle, event) {
        if (event) {
            event.stopPropagation();
        }

        const modal = document.getElementById('deleteAlbumModal');
        if (modal) {
            document.getElementById('deleteAlbumTitle').textContent = albumTitle;
            modal.dataset.albumId = albumId;
            modal.classList.add('active');
            document.body.style.overflow = 'hidden';
        }
    }

    static closeDeleteModal() {
        const modal = document.getElementById('deleteAlbumModal');
        if (modal) {
            modal.classList.remove('active');
            document.body.style.overflow = '';
        }
    }

    static async confirmDeleteAlbum() {
        const modal = document.getElementById('deleteAlbumModal');
        const albumId = modal.dataset.albumId;
        const button = modal.querySelector('.btn-primary');
        const originalText = button.textContent;

        try {
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting...';

            const response = await fetch(`/api/albums/${albumId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                throw new Error(await response.text());
            }

            // Remove from UI
            window.collectionManager?.removeAlbumFromUI(albumId);

            // Close modal
            this.closeDeleteModal();

            // Show success notification
            window.collectionManager?.showToast('Album deleted successfully', 'success');

        } catch (error) {
            // console.error('Error:', error);
            window.collectionManager?.showToast(error.message || 'Error deleting album', 'error');
            
            // Reset button
            button.disabled = false;
            button.textContent = originalText;
        }
    }

    static async openBulkEditModal() {
        const modal = document.getElementById('bulkEditModal');
        const albumList = document.getElementById('albumSelectionList');
        
        if (!modal || !albumList) return;

        // Show loading state
        albumList.innerHTML = `
            <div style="display: flex; align-items: center; justify-content: center; padding: 2rem;">
                <div style="width: 20px; height: 20px; border: 2px solid #E5E7EB; border-top: 2px solid #3B82F6; border-radius: 50%; animation: spin 1s linear infinite; margin-right: 0.5rem;"></div>
                <span>Loading all albums...</span>
            </div>
        `;
        
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';

        try {
            // Fetch ALL albums using pagination
            const allAlbums = await this.fetchAllAlbums();

            // Clear loading state
            albumList.innerHTML = '';

            if (allAlbums.length === 0) {
                albumList.innerHTML = `
                    <div class="empty-state">
                        <i class="fas fa-music" style="font-size: 2rem; color: #9CA3AF; margin-bottom: 0.5rem;"></i>
                        <p>No albums available</p>
                    </div>
                `;
                return;
            }

            // Create album selection items with better styling
            allAlbums.forEach((album, index) => {
                const currentTier = album.tier_restrictions?.minimum_tier || 'Public';
                
                const albumItem = document.createElement('div');
                albumItem.className = 'bulk-album-item';
                albumItem.innerHTML = `
                    <label class="bulk-album-label">
                        <input type="checkbox" name="selected_albums" value="${album.id}" class="bulk-album-checkbox">
                        <div class="bulk-album-info">
                            <div class="bulk-album-title">${this.escapeHtml(album.title)}</div>
                            <div class="bulk-album-meta">
                                <span class="track-count">${album.track_count || 0} tracks</span>
                                <span class="current-tier-badge" data-tier="${currentTier}">
                                    Current: ${currentTier}
                                </span>
                            </div>
                        </div>
                    </label>
                `;
                
                albumList.appendChild(albumItem);
            });

            // Update the counter
            this.updateBulkSelectionCounter();

            // console.log(`Loaded ${allAlbums.length} albums for bulk edit`);

        } catch (error) {
            // console.error('Error loading albums for bulk edit:', error);
            albumList.innerHTML = `
                <div class="error-state">
                    <i class="fas fa-exclamation-triangle" style="color: #EF4444; margin-bottom: 0.5rem;"></i>
                    <p>Failed to load albums: ${error.message}</p>
                    <button onclick="CollectionModals.openBulkEditModal()" class="retry-btn">Retry</button>
                </div>
            `;
        }
    }

    static async fetchAllAlbums() {
        let allAlbums = [];
        let currentPage = 1;
        let hasNextPage = true;
        const perPage = 50; // Use larger page size for bulk operations

        // Update loading message
        const albumList = document.getElementById('albumSelectionList');
        
        while (hasNextPage) {
            try {
                if (albumList) {
                    albumList.innerHTML = `
                        <div style="display: flex; align-items: center; justify-content: center; padding: 2rem;">
                            <div style="width: 20px; height: 20px; border: 2px solid #E5E7EB; border-top: 2px solid #3B82F6; border-radius: 50%; animation: spin 1s linear infinite; margin-right: 0.5rem;"></div>
                            <span>Loading albums... (${allAlbums.length} loaded)</span>
                        </div>
                    `;
                }

                const response = await fetch(`/api/collection/albums?page=${currentPage}&per_page=${perPage}`);
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                const data = await response.json();
                const pageAlbums = data.albums || [];
                
                // Add albums from this page
                allAlbums.push(...pageAlbums);
                
                // Check if there are more pages
                hasNextPage = data.pagination?.has_next || false;
                currentPage++;

                // Safety check to prevent infinite loops
                if (currentPage > 100) {
                    // console.warn('Reached maximum page limit (100) while fetching albums');
                    break;
                }

                // Add a small delay to prevent overwhelming the server
                if (hasNextPage) {
                    await new Promise(resolve => setTimeout(resolve, 100));
                }

            } catch (error) {
                // console.error(`Error fetching page ${currentPage}:`, error);
                throw new Error(`Failed to load page ${currentPage}: ${error.message}`);
            }
        }

        return allAlbums;
    }
    
    static updateBulkSelectionCounter() {
        const checkboxes = document.querySelectorAll('input[name="selected_albums"]');
        const checkedCount = document.querySelectorAll('input[name="selected_albums"]:checked').length;
        
        // Update select all checkbox state
        const selectAllCheckbox = document.getElementById('selectAllAlbums');
        if (selectAllCheckbox) {
            selectAllCheckbox.indeterminate = checkedCount > 0 && checkedCount < checkboxes.length;
            selectAllCheckbox.checked = checkedCount === checkboxes.length && checkboxes.length > 0;
        }

        // Update submit button text
        const submitBtn = document.querySelector('#bulkEditForm button[type="submit"]');
        if (submitBtn) {
            submitBtn.textContent = checkedCount > 0 ? 
                `Update ${checkedCount} Album${checkedCount !== 1 ? 's' : ''}` : 
                'Update Selected Albums';
            submitBtn.disabled = checkedCount === 0;
        }
    }

    static escapeHtml(text) {
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, m => map[m]);
    }

    static closeBulkEditModal() {
        const modal = document.getElementById('bulkEditModal');
        if (modal) {
            modal.classList.remove('active');
            document.body.style.overflow = '';
        }
    }
}

/**
 * Form Handlers
 */
class CollectionForms {
    static setupForms() {
        // Create Album Form
        const createForm = document.getElementById('createAlbumForm');
        if (createForm) {
            createForm.addEventListener('submit', this.handleCreateAlbum);
        }

        // Edit Album Form
        const editForm = document.getElementById('editAlbumForm');
        if (editForm) {
            editForm.addEventListener('submit', this.handleEditAlbum);
        }

        // Bulk Edit Form
        const bulkForm = document.getElementById('bulkEditForm');
        if (bulkForm) {
            bulkForm.addEventListener('submit', this.handleBulkEdit);
        }

        // Select All functionality
        const selectAllCheckbox = document.getElementById('selectAllAlbums');
        if (selectAllCheckbox) {
            selectAllCheckbox.addEventListener('change', (e) => {
                const albumCheckboxes = document.querySelectorAll('input[name="selected_albums"]');
                albumCheckboxes.forEach(checkbox => {
                    checkbox.checked = e.target.checked;
                });
                CollectionModals.updateBulkSelectionCounter();
            });
        }

        // Add listener for individual checkbox changes
        document.addEventListener('change', (e) => {
            if (e.target.name === 'selected_albums') {
                CollectionModals.updateBulkSelectionCounter();
            }
        });
    }
    
    static async handleCreateAlbum(e) {
        e.preventDefault();

        const permissions = window.collectionConfig.permissions;
        if (!permissions.can_create) {
            window.collectionManager?.showToast('You do not have permission to create albums', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('title', document.getElementById('albumTitle').value.trim());
        formData.append('cover', document.getElementById('albumCover').files[0]);

        // Add tier restrictions
        const tierSelect = document.getElementById('createAlbumTiers');
        const selectedOption = tierSelect.selectedOptions[0];

        if (selectedOption && selectedOption.value) {
            const tierData = {
                minimum_tier: selectedOption.value,
                amount_cents: parseInt(selectedOption.dataset.amount) || 0
            };
            formData.append('tier_data', JSON.stringify(tierData));
        }

        // Add visibility status
        const visibilitySelect = document.getElementById('createAlbumVisibility');
        if (visibilitySelect && visibilitySelect.value) {
            formData.append('visibility_status', visibilitySelect.value);
        }

        try {
            const button = e.target.querySelector('button[type="submit"]');
            const originalText = button.innerHTML;
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Creating...';

            const response = await fetch('/api/albums/', {
                method: 'POST',
                body: formData
            });

            if (response.status === 303) {
                window.location.href = '/login';
                return;
            }

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Error creating album');
            }

            // Reload the collection
            window.location.reload();

        } catch (error) {
            // console.error('Error:', error);
            window.collectionManager?.showToast(error.message || 'Error creating album', 'error');
            
            const button = e.target.querySelector('button[type="submit"]');
            button.disabled = false;
            button.innerHTML = 'Create Album';
        }
    }

    static async handleEditAlbum(e) {
        e.preventDefault();

        const albumId = e.target.dataset.albumId;
        const formData = new FormData();

        formData.append('title', document.getElementById('editAlbumTitle').value.trim());
        const coverInput = document.getElementById('editAlbumCover');
        if (coverInput.files.length > 0) {
            formData.append('cover', coverInput.files[0]);
        }

        // Add visibility status
        const visibilitySelect = document.getElementById('editAlbumVisibility');
        if (visibilitySelect && visibilitySelect.value) {
            formData.append('visibility_status', visibilitySelect.value);
        }

        try {
            // Update album details
            const albumResponse = await fetch(`/api/albums/${albumId}`, {
                method: 'PATCH',
                body: formData
            });

            if (!albumResponse.ok) {
                const error = await albumResponse.json();
                throw new Error(error.detail || 'Error updating album');
            }

            // Update tier access
            const tierSelect = document.getElementById('editAlbumTiers');
            const selectedOption = tierSelect.selectedOptions[0];

            const tierData = {
                minimum_tier: selectedOption.value || null,
                amount_cents: selectedOption.dataset.amount ? parseInt(selectedOption.dataset.amount) : 0
            };

            const tierResponse = await fetch(`/api/albums/${albumId}/tier-access`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(tierData)
            });

            if (!tierResponse.ok) {
                const error = await tierResponse.json();
                throw new Error(error.detail || 'Error updating tier access');
            }

            // Update UI
            const updatedData = {
                title: document.getElementById('editAlbumTitle').value.trim(),
                tier_restrictions: tierData.minimum_tier ? {
                    is_restricted: true,
                    minimum_tier: tierData.minimum_tier
                } : null,
                visibility_status: visibilitySelect ? visibilitySelect.value : 'visible'
            };

            window.collectionManager?.updateAlbumInUI(albumId, updatedData);
            CollectionModals.closeEditModal();
            window.collectionManager?.showToast('Album updated successfully', 'success');

        } catch (error) {
            // console.error('Error:', error);
            window.collectionManager?.showToast(error.message || 'Error updating album', 'error');
        }
    }

    static async handleBulkEdit(e) {
        e.preventDefault();
        
        const selectedAlbums = Array.from(
            document.querySelectorAll('input[name="selected_albums"]:checked')
        ).map(cb => cb.value);
        
        if (selectedAlbums.length === 0) {
            window.collectionManager?.showToast('Please select at least one album', 'error');
            return;
        }
        
        const tierSelect = document.getElementById('bulkTierSelect');
        const selectedOption = tierSelect.selectedOptions[0];
        
        const tierData = {
            minimum_tier: selectedOption.value || null,
            amount_cents: selectedOption.dataset.amount ? parseInt(selectedOption.dataset.amount) : 0
        };
        
        try {
            const button = e.target.querySelector('button[type="submit"]');
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Updating...';
            
            const response = await fetch('/api/albums/bulk-tier-update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    album_ids: selectedAlbums,
                    tier_data: tierData
                })
            });
            
            if (!response.ok) {
                throw new Error('Failed to update albums');
            }
            
            const result = await response.json();
            
            CollectionModals.closeBulkEditModal();
            window.collectionManager?.showToast(`Updated ${result.updated_count} albums`, 'success');
            
            // Reload to show changes
            setTimeout(() => window.location.reload(), 1000);
            
        } catch (error) {
            // console.error('Error:', error);
            window.collectionManager?.showToast(error.message || 'Error updating albums', 'error');
        }
    }
}

// Global functions for backward compatibility and template integration
window.openModal = function() {
    CollectionModals.openModal();
};

window.closeModal = function() {
    CollectionModals.closeModal();
};

window.openEditModal = function(albumId, event) {
    CollectionModals.openEditModal(albumId, event);
};

window.closeEditModal = function() {
    CollectionModals.closeEditModal();
};

window.openDeleteModal = function(albumId, albumTitle, event) {
    CollectionModals.openDeleteModal(albumId, albumTitle, event);
};

window.closeDeleteModal = function() {
    CollectionModals.closeDeleteModal();
};

window.confirmDeleteAlbum = function() {
    CollectionModals.confirmDeleteAlbum();
};

window.openBulkEditModal = function() {
    CollectionModals.openBulkEditModal();
};

window.closeBulkEditModal = function() {
    CollectionModals.closeBulkEditModal();
};

// Template compatibility functions
window.toggleFavorite = function(albumId, event) {
    CollectionActions.toggleFavorite(albumId, event);
};

window.downloadCollection = function(albumId, albumTitle, event) {
    CollectionActions.downloadCollection(albumId, albumTitle, event);
};

window.showToast = function(message, type = 'info') {
    if (window.collectionManager) {
        window.collectionManager.showToast(message, type);
    } else {
        // Fallback toast if collection manager not available
        const toast = document.createElement('div');
        toast.style.cssText = `
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            background-color: ${type === 'success' ? '#10B981' : type === 'error' ? '#EF4444' : 'rgba(0, 0, 0, 0.8)'};
            color: white;
            padding: 12px 24px;
            border-radius: 6px;
            font-size: 14px;
            z-index: 1000;
            opacity: 1;
        `;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }
};

// Image error handler for template compatibility
window.handleImageError = function(img) {
    const container = img.closest('.cover-container');
    const title = img.dataset.albumTitle || img.alt || 'Album';
    img.style.display = 'none';
    
    // Create placeholder if it doesn't exist
    let placeholder = container.querySelector('.album-cover-placeholder');
    if (!placeholder) {
        placeholder = document.createElement('div');
        placeholder.className = 'album-cover-placeholder';
        placeholder.innerHTML = `<span class="album-initial">${title.charAt(0).toUpperCase()}</span>`;
        container.appendChild(placeholder);
    }
    placeholder.style.display = 'flex';
};

// Navigation function for album access
window.navigateToAlbum = async function(albumId) {
    if (!albumId) {
        console.error('❌ No albumId provided');
        window.collectionManager?.showToast('Invalid album ID', 'error');
        return;
    }

    try {
        const checkUrl = `/api/albums/${encodeURIComponent(albumId)}/check-access`;
        const response = await fetch(checkUrl);

        let data = {};
        try {
            data = await response.json();
        } catch (jsonError) {
            console.error('❌ JSON parse error:', jsonError);
        }

        if (response.status === 403) {
            const message = data.error?.message || 'This content requires a higher tier subscription';
            if (window.showUpgradeModal) {
                showUpgradeModal(message);
            } else {
                window.collectionManager?.showToast(message, 'error');
            }
            return;
        }

        if (!response.ok) {
            console.error('❌ Response not OK:', response.status, data.error?.message);
            window.collectionManager?.showToast(data.error?.message || 'Error accessing album', 'error');
            return;
        }

        // ✅ Use SPA navigation if router is available
        if (window.spaRouter) {
            const targetUrl = `/album/${encodeURIComponent(albumId)}`;
            window.spaRouter.navigate(targetUrl);
        } else {
            console.warn('⚠️ No SPA router found, using traditional navigation');
            // Fallback to traditional navigation
            location.href = `/album/${encodeURIComponent(albumId)}`;
        }

    } catch (error) {
        console.error('❌ Error checking album access:', error);
        window.collectionManager?.showToast('Error accessing album', 'error');
    }
};  

// Export classes to window
window.CollectionManager = CollectionManager;
window.CollectionActions = CollectionActions;
window.CollectionModals = CollectionModals;
window.CollectionForms = CollectionForms;

// Initialize everything when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Initialize the collection manager
    window.collectionManager = new CollectionManager();
    
    // Setup forms
    CollectionForms.setupForms();
    
    // console.log('Collection management system initialized');
});

// Handle window resize for responsive page sizes
window.addEventListener('resize', () => {
    // Debounce resize events
    clearTimeout(window.resizeTimeout);
    window.resizeTimeout = setTimeout(() => {
        if (window.collectionManager) {
            // console.log('Window resized, page size:', window.collectionManager.getPageSize());
        }
    }, 250);
});

})(); // End of module wrapper