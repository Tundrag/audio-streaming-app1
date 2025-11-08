// my-albums-shared-spa.js - Universal controller for My Albums page (SSR and SPA modes)

export class MyAlbumsController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.albums = [];
        this.bootstrapData = null;
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }

        return this.generateHTML();
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`📀 MyAlbums: Mounting in ${this.mode} mode...`);

        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM if available
            this.hydrateFromDOM();
        }

        await this.loadAlbums();
        this.setupEventListeners();

        console.log('✅ MyAlbums: Mounted successfully');
    }

    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        const bootstrapScript = document.getElementById('my-albums-bootstrap-data');
        if (bootstrapScript) {
            try {
                this.bootstrapData = JSON.parse(bootstrapScript.textContent);
                if (this.bootstrapData.albums) {
                    this.albums = this.bootstrapData.albums;
                    console.log('📦 Hydrated my-albums data from DOM');
                }
            } catch (error) {
                console.error('Error parsing bootstrap data:', error);
            }
        }
    }

    // ✅ Generate HTML for SPA mode
    generateHTML() {
        return `
            <div class="my-albums-page">
                <div class="page-header">
                    <h1>My Albums</h1>
                    <div class="album-stats" id="albumStats"></div>
                </div>
                <div class="albums-grid" id="albumsGrid">
                    <div class="loading">
                        <i class="fas fa-spinner"></i>
                        <p>Loading your albums...</p>
                    </div>
                </div>
            </div>
        `;
    }

    async loadAlbums() {
        // Skip if we already have hydrated data
        if (this.albums.length > 0 && this.mode === 'ssr') {
            console.log('✅ Using hydrated albums data');
            this.renderAlbums();
            this.updateStats();
            return;
        }

        try {
            const response = await fetch('/api/my-albums');
            if (!response.ok) {
                throw new Error('Failed to load albums');
            }

            this.albums = await response.json();
            console.log(`📀 Loaded ${this.albums.length} albums`);

            this.renderAlbums();
            this.updateStats();

        } catch (error) {
            console.error('❌ Error loading albums:', error);
            this.renderError();
        }
    }

    renderAlbums() {
        const container = document.getElementById('albumsGrid');
        if (!container) return;

        if (this.albums.length === 0) {
            container.innerHTML = `
                <div class="empty-collection">
                    <i class="fas fa-music"></i>
                    <h2>Your Collection is Empty</h2>
                    <p>Start adding albums from the collection to keep track of your favorites.</p>
                    <a href="/collection" class="btn-primary" data-spa-link>Browse Collection</a>
                </div>
            `;
            return;
        }

        container.innerHTML = this.albums.map(album => this.createAlbumCard(album)).join('');
    }

    createAlbumCard(album) {
        const addedDate = new Date(album.added_at).toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'long',
            day: 'numeric'
        });

        const trackCount = album.track_count || album.tracks?.length || 0;

        return `
            <div class="album-card" data-album-id="${album.id}">
                <div class="album-cover-container">
                    ${album.is_favorite ? '<div class="favorite-badge"><i class="fas fa-heart"></i> Favorite</div>' : ''}
                    <img src="${album.cover_path}"
                         alt="${this.escapeHtml(album.title)}"
                         class="album-cover"
                         onerror="this.src='/static/images/default-album.jpg'">
                    <div class="album-actions" onclick="event.stopPropagation();">
                        <button
                            class="btn-icon remove"
                            data-album-id="${album.id}"
                            title="Remove from collection">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                </div>
                <div class="album-info">
                    <h3>${this.escapeHtml(album.title)}</h3>
                    <div class="album-meta">
                        <span>${trackCount} tracks</span>
                        ${album.view_count ? `
                            <span title="Times viewed">
                                <i class="fas fa-eye"></i> ${album.view_count}
                            </span>
                        ` : ''}
                    </div>
                    <div class="added-date">
                        Added ${addedDate}
                    </div>
                </div>
            </div>
        `;
    }

    updateStats() {
        const statsContainer = document.getElementById('albumStats');
        if (!statsContainer) return;

        const favoriteCount = this.albums.filter(a => a.is_favorite).length;

        statsContainer.innerHTML = `
            ${this.albums.length} album${this.albums.length !== 1 ? 's' : ''}
            ${favoriteCount > 0 ? ` · ${favoriteCount} favorite${favoriteCount !== 1 ? 's' : ''}` : ''}
        `;
    }

    setupEventListeners() {
        // Album card clicks (navigate to album)
        document.querySelectorAll('.album-card').forEach(card => {
            card.addEventListener('click', (e) => {
                if (e.target.closest('.album-actions')) return;
                const albumId = card.dataset.albumId;
                this.navigateToAlbum(albumId);
            });
        });

        // Remove buttons
        document.querySelectorAll('.btn-icon.remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const albumId = btn.dataset.albumId;
                this.removeFromCollection(albumId);
            });
        });

        // Handle SPA links
        document.querySelectorAll('[data-spa-link]').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const href = link.getAttribute('href');
                if (window.spaRouter) {
                    window.spaRouter.navigate(href);
                }
            });
        });
    }

    async removeFromCollection(albumId) {
        if (!confirm('Are you sure you want to remove this album from your collection?')) {
            return;
        }

        const albumCard = document.querySelector(`[data-album-id="${albumId}"]`);
        if (!albumCard) return;

        try {
            const response = await fetch(`/api/my-albums/${albumId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to remove album');
            }

            // Add removal animation
            albumCard.classList.add('removing');

            // Remove from local array
            this.albums = this.albums.filter(a => a.id !== albumId);

            // Remove card after animation
            setTimeout(() => {
                albumCard.remove();

                // Update stats
                this.updateStats();

                // Show empty state if no albums left
                if (this.albums.length === 0) {
                    this.renderAlbums();
                }
            }, 300);

            if (window.showToast) {
                window.showToast('Album removed from collection');
            }

            console.log(`✅ Removed album ${albumId} from collection`);

        } catch (error) {
            console.error('❌ Error removing album:', error);
            alert('Failed to remove album: ' + error.message);
        }
    }

    navigateToAlbum(albumId) {
        if (!albumId) return;

        console.log(`🎵 Navigating to album: ${albumId}`);

        if (window.spaRouter) {
            window.spaRouter.navigate(`/album/${albumId}`);
        } else {
            window.location.href = `/album/${albumId}`;
        }
    }

    renderError() {
        const container = document.getElementById('albumsGrid');
        if (container) {
            container.innerHTML = `
                <div class="empty-collection">
                    <i class="fas fa-exclamation-triangle"></i>
                    <h2>Error Loading Albums</h2>
                    <p>There was a problem loading your collection.</p>
                    <button class="btn-primary" onclick="location.reload()">Retry</button>
                </div>
            `;
        }
    }

    escapeHtml(unsafe) {
        if (unsafe === null || unsafe === undefined) return '';
        return String(unsafe)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    async destroy() {
        console.log('🧹 MyAlbums: Destroying...');
        // No cleanup needed as event listeners are added directly to elements
        // and will be garbage collected when elements are removed
        this.albums = [];
    }
}
