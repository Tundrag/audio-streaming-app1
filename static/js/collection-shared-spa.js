// collection-shared-spa.js - Universal controller for Collection page (SSR and SPA modes)

export class CollectionController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.config = null;
        this.collectionManager = null;
        this.scriptsLoaded = false;
        this.bootstrapData = null;
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }

        // Fetch initial data and config
        try {
            const response = await fetch('/api/collection/data');
            if (!response.ok) throw new Error('Failed to load collection data');

            const data = await response.json();
            this.config = data.config;

            return this.generateHTML(data);
        } catch (error) {
            // console.error('Error loading collection data:', error);
            return this.generateErrorHTML(error.message);
        }
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        // console.log(`📚 Collection: Mounting in ${this.mode} mode...`);

        // ✅ CRITICAL: Inject styles into head FIRST
        this.injectStyles();

        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM if available
            this.hydrateFromDOM();
        }

        // CRITICAL: Set window.collectionConfig BEFORE initializing CollectionManager
        if (this.config) {
            window.collectionConfig = this.config;
            // console.log('Collection: Config set', this.config);
        } else {
            // console.error('Collection: No config available!');
            return;
        }

        // Check if CollectionManager already exists
        if (!window.CollectionManager) {
            // console.log('Collection: Loading collection.js...');
            await this.loadCollectionScript();
        } else {
            // console.log('Collection: collection.js already loaded, reusing existing');
        }

        // Wait for DOM to settle and albumsGrid to exist
        await this.waitForElement('#albumsGrid', 1000);

        // Initialize Collection Manager
        if (window.CollectionManager) {
            this.collectionManager = new window.CollectionManager();
            window.collectionManager = this.collectionManager;

            // Setup forms
            if (window.CollectionForms) {
                window.CollectionForms.setupForms();
            }

            // console.log('✅ Collection: Collection Manager initialized');
        } else {
            // console.error('Collection: CollectionManager not available after load attempt');
        }
    }

    // ✅ Inject styles into head to ensure they're applied
    injectStyles() {
        const styleId = 'collection-spa-styles';

        // Remove existing style tag if it exists
        const existingStyle = document.getElementById(styleId);
        if (existingStyle) {
            existingStyle.remove();
        }

        // Create new style tag
        const style = document.createElement('style');
        style.id = styleId;
        style.textContent = this.getStyles();
        document.head.appendChild(style);

        // console.log('✅ Collection: Styles injected into head');
    }

    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        const bootstrapScript = document.getElementById('collection-bootstrap-data');
        if (bootstrapScript) {
            try {
                this.bootstrapData = JSON.parse(bootstrapScript.textContent);
                this.config = this.bootstrapData.config || this.bootstrapData;
                // console.log('📦 Hydrated collection data from DOM');
            } catch (error) {
                // console.error('Error parsing bootstrap data:', error);
            }
        }

        // Also try to read from window.collectionConfig if available
        if (!this.config && window.collectionConfig) {
            this.config = window.collectionConfig;
            // console.log('📦 Using existing window.collectionConfig');
        }
    }

    // ✅ Generate HTML for SPA mode
    generateHTML(data) {
        const { config } = data;
        const permissions = config.permissions;
        const availableTiers = config.available_tiers || [];

        return `
            <!-- Collection Header -->
            <div class="section-header">
                <h2 class="blue-text">Available Albums</h2>

                <!-- Search Bar -->
                <div class="collection-search">
                    <div class="search-wrapper">
                        <input type="text"
                               id="albumSearch"
                               class="search-input"
                               placeholder="Search albums...">
                        <i class="fas fa-search search-icon"></i>
                        <button id="clearSearch" class="clear-search" style="display: none;">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div id="searchStatus" class="search-status"></div>
                </div>

                <div class="header-actions">
                    ${permissions.can_rename ? `
                        <button onclick="openBulkEditModal()" class="btn btn-secondary">
                            <i class="fas fa-edit"></i> Bulk Edit Tiers
                        </button>
                    ` : ''}

                    ${permissions.can_create ? `
                        <button onclick="openModal()" class="add-album-btn">
                            <i class="fas fa-plus"></i> Create Album
                        </button>
                    ` : ''}
                </div>
            </div>

            <!-- Albums Grid -->
            <div id="albumsGrid" class="albums-grid">
                <!-- Albums will be loaded here dynamically -->
            </div>

            <!-- Loading Indicator -->
            <div id="loadingIndicator" class="loading-container" style="display: none;">
                <div class="loading-spinner"></div>
                <p>Loading albums...</p>
            </div>

            <!-- Load More Button -->
            <div id="loadMoreContainer" class="load-more-container" style="display: none;">
                <button id="loadMoreBtn" class="load-more-btn">
                    <i class="fas fa-plus"></i>
                    Load More Albums
                </button>
            </div>

            <!-- No Albums Message -->
            <div id="noAlbumsMessage" class="no-albums" style="display: none;">
                ${permissions.can_create ?
                    'No albums found. Create one to get started!' :
                    'No albums available.'}
            </div>

            ${this.generateModals(permissions, availableTiers)}

            <script>
                // Pass server data to JavaScript
                window.collectionConfig = ${JSON.stringify(config)};
            </script>
        `;
    }

    generateModals(permissions, availableTiers) {
        return `
            <!-- Create Album Modal -->
            ${permissions.can_create ? `
                <div id="createAlbumModal" class="modal">
                    <div class="modal-content">
                        <h2>Create New Album</h2>
                        <form id="createAlbumForm">
                            <div class="form-group">
                                <label for="albumTitle">Album Title</label>
                                <input type="text" id="albumTitle" required>
                            </div>
                            <div class="form-group">
                                <label for="albumCover">Album Cover</label>
                                <input type="file" id="albumCover" accept="image/*" required>
                            </div>

                            <div class="form-group">
                                <label for="createAlbumTiers">Tier Access</label>
                                <select id="createAlbumTiers" class="tier-select">
                                    <option value="">Public Access (No Restrictions)</option>
                                    ${availableTiers.map(tier => `
                                        <option value="${this.escapeHtml(tier.title)}" data-amount="${tier.amount_cents}">
                                            ${this.escapeHtml(tier.title)} - $${(tier.amount_cents/100).toFixed(2)}/month
                                            (${tier.patron_count} patrons)
                                        </option>
                                    `).join('')}
                                </select>
                                <div class="help-text">
                                    Select tier requirement for this album
                                </div>
                            </div>

                            <div class="form-group">
                                <label for="createAlbumVisibility">Visibility</label>
                                <select id="createAlbumVisibility" class="visibility-select">
                                    <option value="visible" selected>Visible to all authorized users</option>
                                    <option value="hidden_from_users">Hidden from users (Team can see)</option>
                                    ${permissions.is_creator ? '<option value="hidden_from_all">Hidden from everyone including Team</option>' : ''}
                                </select>
                                <div class="help-text">Control who can see this album${permissions.is_team && !permissions.is_creator ? ' (Team members cannot hide from team)' : ''}</div>
                            </div>

                            <div class="modal-buttons">
                                <button type="button" class="btn-cancel" onclick="closeModal()">Cancel</button>
                                <button type="submit" class="btn-primary">Create Album</button>
                            </div>
                        </form>
                    </div>
                </div>
            ` : ''}

            <!-- Edit Album Modal -->
            ${permissions.can_rename ? `
                <div id="editAlbumModal" class="modal">
                    <div class="modal-content">
                        <h2>Edit Album</h2>
                        <form id="editAlbumForm">
                            <div class="form-group">
                                <label for="editAlbumTitle">Album Title</label>
                                <input type="text" id="editAlbumTitle" required>
                            </div>
                            <div class="form-group">
                                <label for="editAlbumCover">New Album Cover</label>
                                <input type="file" id="editAlbumCover" accept="image/*">
                                <div class="help-text">Leave empty to keep current cover</div>
                            </div>

                            <div class="form-group">
                                <label for="editAlbumTiers">Tier Access</label>
                                <select id="editAlbumTiers" class="tier-select">
                                    <option value="">Public Access (No Restrictions)</option>
                                    ${availableTiers.map(tier => `
                                        <option value="${this.escapeHtml(tier.title)}" data-amount="${tier.amount_cents}">
                                            ${this.escapeHtml(tier.title)} - $${(tier.amount_cents/100).toFixed(2)}/month
                                            (${tier.patron_count} patrons)
                                        </option>
                                    `).join('')}
                                </select>
                                <div class="help-text">
                                    Select tier requirement for this album
                                </div>
                            </div>

                            <div class="form-group">
                                <label for="editAlbumVisibility">Visibility</label>
                                <select id="editAlbumVisibility" class="visibility-select">
                                    <option value="visible">Visible to all authorized users</option>
                                    <option value="hidden_from_users">Hidden from users (Team can see)</option>
                                    ${permissions.is_creator ? '<option value="hidden_from_all">Hidden from everyone including Team</option>' : ''}
                                </select>
                                <div class="help-text">Control who can see this album${permissions.is_team && !permissions.is_creator ? ' (Team members cannot hide from team)' : ''}</div>
                            </div>

                            <!-- Scheduled Visibility Display -->
                            <div id="editAlbumScheduleDisplay" class="schedule-display" style="display: none;">
                                <div class="schedule-info">
                                    <i class="fas fa-clock"></i>
                                    <span class="schedule-text">Scheduled to become <strong class="schedule-target-status"></strong> in <strong class="schedule-countdown"></strong></span>
                                    <button type="button" class="btn-icon cancel-schedule" title="Cancel schedule">
                                        <i class="fas fa-times"></i>
                                    </button>
                                </div>
                            </div>

                            ${(permissions.is_creator || permissions.is_team) ? `
                                <button type="button" class="btn-schedule" id="scheduleVisibilityBtn" onclick="openScheduleModal()">
                                    <i class="fas fa-clock"></i> Schedule Visibility Change
                                </button>
                            ` : ''}

                            <div class="modal-buttons">
                                <button type="button" class="btn-cancel" onclick="closeEditModal()">Cancel</button>
                                <button type="submit" class="btn-primary">Save Changes</button>
                            </div>
                        </form>
                    </div>
                </div>
            ` : ''}

            <!-- Delete Album Modal -->
            ${permissions.can_delete ? `
                <div id="deleteAlbumModal" class="modal">
                    <div class="modal-content">
                        <h2>Delete Album</h2>
                        <p>Are you sure you want to delete "<span id="deleteAlbumTitle"></span>"?</p>
                        <p style="color: #EF4444; margin-top: 1rem;">
                            <i class="fas fa-exclamation-triangle"></i>
                            This action cannot be undone and will delete all tracks in this album.
                        </p>
                        <div class="modal-buttons">
                            <button type="button" class="btn-cancel" onclick="closeDeleteModal()">Cancel</button>
                            <button type="button" class="btn-primary" style="background-color: #EF4444;" onclick="confirmDeleteAlbum()">Delete Album</button>
                        </div>
                    </div>
                </div>
            ` : ''}

            <!-- Bulk Edit Modal -->
            ${permissions.can_rename ? `
                <div id="bulkEditModal" class="modal">
                    <div class="modal-content">
                        <h2>Bulk Edit Tier Access</h2>
                        <form id="bulkEditForm">
                            <div class="form-group">
                                <div class="bulk-select-all">
                                    <input type="checkbox" id="selectAllAlbums">
                                    <label for="selectAllAlbums">Select All Albums</label>
                                </div>
                                <div id="albumSelectionList" class="album-selection-list">
                                    <!-- Will be populated dynamically -->
                                </div>
                            </div>

                            <div class="form-group">
                                <label for="bulkTierSelect">New Tier Access</label>
                                <select id="bulkTierSelect" class="tier-select">
                                    <option value="">Public Access (No Restrictions)</option>
                                    ${availableTiers.map(tier => `
                                        <option value="${this.escapeHtml(tier.title)}" data-amount="${tier.amount_cents}">
                                            ${this.escapeHtml(tier.title)} - $${(tier.amount_cents/100).toFixed(2)}/month
                                            (${tier.patron_count} patrons)
                                        </option>
                                    `).join('')}
                                </select>
                                <div class="help-text">
                                    Select new tier access level for selected albums
                                </div>
                            </div>

                            <div class="modal-buttons">
                                <button type="button" class="btn-cancel" onclick="closeBulkEditModal()">Cancel</button>
                                <button type="submit" class="btn-primary" disabled>Update Selected Albums</button>
                            </div>
                        </form>
                    </div>
                </div>
            ` : ''}

            <!-- Schedule Visibility Modal -->
            ${(permissions.is_creator || permissions.is_team) ? `
                <div id="scheduleVisibilityModal" class="modal">
                    <div class="modal-content">
                        <h2><i class="fas fa-clock"></i> Schedule Visibility Change</h2>

                        <div class="schedule-current-status">
                            <span class="label">Current visibility:</span>
                            <span class="status-badge" id="scheduleCurrentStatusBadge"></span>
                        </div>

                        <form id="scheduleVisibilityForm">
                            <div class="form-group">
                                <label for="scheduleDateTime">
                                    <i class="fas fa-calendar-alt"></i> Schedule Date & Time
                                </label>
                                <input type="datetime-local" id="scheduleDateTime" required>
                                <div class="help-text">
                                    Select when the visibility should automatically change (your local time)
                                </div>
                            </div>

                            <div class="form-group">
                                <label for="scheduleTargetStatus">
                                    <i class="fas fa-eye"></i> Change To
                                </label>
                                <select id="scheduleTargetStatus" class="visibility-select" required>
                                    <option value="">-- Select visibility status --</option>
                                    <option value="visible">Visible to all authorized users</option>
                                    <option value="hidden_from_users">Hidden from users (Team can see)</option>
                                    ${permissions.is_creator ? '<option value="hidden_from_all">Hidden from everyone including Team</option>' : ''}
                                </select>
                                <div class="help-text">
                                    The album will automatically change to this visibility at the scheduled time
                                </div>
                            </div>

                            <div class="schedule-preview" id="schedulePreview" style="display: none;">
                                <i class="fas fa-info-circle"></i>
                                <span id="schedulePreviewText"></span>
                            </div>

                            <div class="modal-buttons">
                                <button type="button" class="btn-cancel" onclick="closeScheduleModal()">Cancel</button>
                                <button type="submit" class="btn-primary">
                                    <i class="fas fa-clock"></i> Schedule Change
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            ` : ''}
        `;
    }

    async loadCollectionScript() {
        return new Promise((resolve, reject) => {
            // FIRST: Check if the class already exists globally
            if (window.CollectionManager) {
                // console.log('Collection: CollectionManager already exists globally');
                resolve();
                return;
            }

            // SECOND: Check if script tag already exists in DOM
            const existingScript = document.querySelector('script[src*="collection.js"]');
            if (existingScript) {
                // console.log('Collection: collection.js script already in DOM');
                // Script exists, wait a moment for it to execute if needed
                setTimeout(() => {
                    if (window.CollectionManager) {
                        // console.log('Collection: CollectionManager now available');
                        resolve();
                    } else {
                        // console.warn('Collection: Script exists but CollectionManager not available');
                        reject(new Error('CollectionManager not available after wait'));
                    }
                }, 100);
                return;
            }

            // THIRD: Script doesn't exist, create it
            // console.log('Collection: Creating new collection.js script tag');
            const script = document.createElement('script');
            script.src = '/static/js/collection.js?v=' + Date.now(); // Cache bust
            script.onload = () => {
                // console.log('Collection: collection.js script loaded successfully');
                // Wait a tick for the script to execute
                setTimeout(() => {
                    if (window.CollectionManager) {
                        resolve();
                    } else {
                        reject(new Error('CollectionManager not available after script load'));
                    }
                }, 50);
            };
            script.onerror = () => {
                // console.error('Collection: Failed to load collection.js');
                reject(new Error('Failed to load collection.js'));
            };
            document.head.appendChild(script);
        });
    }

    escapeHtml(text) {
        if (!text) return '';
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return String(text).replace(/[&<>"']/g, m => map[m]);
    }

    generateErrorHTML(errorMessage) {
        return `
            <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 400px; color: #ef4444;">
                <i class="fas fa-exclamation-circle" style="font-size: 3rem; margin-bottom: 20px;"></i>
                <p>Error loading collection: ${this.escapeHtml(errorMessage)}</p>
                <button onclick="location.reload()" class="btn btn-primary" style="margin-top: 20px;">Reload Page</button>
            </div>
        `;
    }

    // Wait for a DOM element to exist
    async waitForElement(selector, timeout = 5000) {
        const startTime = Date.now();
        while (Date.now() - startTime < timeout) {
            const element = document.querySelector(selector);
            if (element) {
                return element;
            }
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        throw new Error(`Element ${selector} not found within ${timeout}ms`);
    }

    async destroy() {
        // console.log('🧹 Collection: Destroying...');

        // Clean up collection manager
        if (this.collectionManager) {
            // Disconnect intersection observer
            if (this.collectionManager.intersectionObserver) {
                this.collectionManager.intersectionObserver.disconnect();
            }

            // Clear timers
            if (this.collectionManager.searchDebounceTimer) {
                clearTimeout(this.collectionManager.searchDebounceTimer);
            }

            // Clear window reference
            window.collectionManager = null;
            this.collectionManager = null;
        }

        // Close any open modals
        document.querySelectorAll('.modal.active').forEach(modal => {
            modal.classList.remove('active');
        });

        // Restore body overflow
        document.body.style.overflow = '';

        // Clear window config
        window.collectionConfig = null;

        // Remove scroll sentinel
        const sentinel = document.getElementById('scrollSentinel');
        if (sentinel) {
            sentinel.remove();
        }

        // Remove injected styles
        const styleTag = document.getElementById('collection-spa-styles');
        if (styleTag) {
            styleTag.remove();
        }

        // console.log('✅ Collection: Cleanup complete');
    }

    getStyles() {
        // Return all the CSS from collection.html (truncated for brevity - full CSS would be here)
        return `
            /* Collection Styles */
            .section-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 1.5rem;
                margin-top: 2rem;
                flex-wrap: wrap;
                gap: 1rem;
            }

            .section-header h2 {
                font-size: 1.5rem;
                margin: 0;
                color: #3B82F6;
            }

            @media (max-width: 768px) {
                .section-header {
                    flex-direction: column;
                    align-items: stretch;
                    margin-top: 1.5rem;
                }

                .section-header h2 {
                    font-size: 1.25rem;
                }
            }

            .blue-text {
                color: #3B82F6;
            }

            /* Search Bar */
            .collection-search {
                margin-bottom: 1rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 1rem;
            }

            .search-wrapper {
                position: relative;
                display: flex;
                align-items: center;
                width: 100%;
                max-width: 500px;
                border-radius: 999px;
                overflow: hidden;
                background-color: var(--background-secondary);
                border: 1px solid #CBD5E0;
                transition: all 0.3s ease;
            }

            .search-input {
                width: 100%;
                padding: 0.5rem 2.5rem 0.5rem 3rem;
                border: none;
                font-size: 1rem;
                background-color: transparent;
                color: var(--text);
            }

            .search-input:focus {
                outline: none;
            }

            .search-input::placeholder {
                color: #A0AEC0;
            }

            .search-icon {
                position: absolute;
                left: 1rem;
                font-size: 1rem;
                color: #A0AEC0;
            }

            .clear-search {
                position: absolute;
                right: 1rem;
                background: transparent;
                border: none;
                cursor: pointer;
                font-size: 1rem;
                color: #A0AEC0;
                display: none;
            }

            .search-status {
                font-size: 0.875rem;
                color: #6B7280;
                margin-left: 1rem;
            }

            .header-actions {
                display: flex;
                gap: 1rem;
                align-items: center;
            }

            .add-album-btn, .btn {
                background-color: #3B82F6;
                color: #FFFFFF;
                border: none;
                padding: 0.5rem 1rem;
                border-radius: 0.375rem;
                cursor: pointer;
                transition: background-color 0.3s ease;
                display: flex;
                align-items: center;
                gap: 0.5rem;
                font-size: 1rem;
                text-decoration: none;
            }

            .add-album-btn:hover, .btn:hover {
                background-color: #2563EB;
            }

            .btn-secondary {
                background-color: #6B7280;
            }

            .btn-secondary:hover {
                background-color: #4B5563;
            }

            /* Albums Grid */
            .albums-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
                gap: 1rem;
                min-height: 400px;
                align-items: start;
                justify-items: center;
                max-width: 1400px;
                margin: 0 auto;
            }

            @media (min-width: 768px) {
                .albums-grid {
                    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                    gap: 1.25rem;
                }
            }

            @media (min-width: 1024px) {
                .albums-grid {
                    grid-template-columns: repeat(6, 1fr);
                    gap: 1.5rem;
                    max-width: 1200px;
                }
            }

            @media (min-width: 1400px) {
                .albums-grid {
                    grid-template-columns: repeat(6, 1fr);
                    gap: 1.5rem;
                    max-width: 1400px;
                }
            }

            @media (max-width: 480px) {
                .albums-grid {
                    grid-template-columns: repeat(2, 1fr);
                    gap: 0.75rem;
                    max-width: none;
                }

                .album-card {
                    max-width: none;
                }
            }

            /* Album Card */
            .album-card {
                position: relative;
                background-color: #FFFFFF;
                color: #1A202C;
                border: 1px solid #E2E8F0;
                border-radius: 0.5rem;
                overflow: hidden;
                transition: all 0.2s ease-in-out;
                cursor: pointer;
                width: 100%;
                max-width: 280px;
                margin: 0 auto;
            }

            .album-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
            }

            [data-theme="dark"] .album-card {
                background-color: #1F2937;
                border-color: #374151;
                color: #F3F4F6;
            }

            [data-theme="dark"] .album-card:hover {
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                border-color: #4B5563;
            }

            /* Album Buttons */
            .album-buttons {
                padding: 0.75rem;
                border-bottom: 1px solid #E5E7EB;
                background-color: #FFFFFF;
                display: flex;
                gap: 0.375rem;
                justify-content: center;
                transition: all 0.2s ease;
            }

            [data-theme="dark"] .album-buttons {
                background-color: #1F2937;
                border-bottom-color: #374151;
            }

            .btn-icon {
                border-radius: 8px;
                padding: 0.5rem;
                transition: all 0.2s ease;
                background-color: #F3F4F6;
                color: #6B7280;
                border: none;
                cursor: pointer;
                font-size: 0.875rem;
            }

            [data-theme="dark"] .btn-icon {
                background-color: #374151;
                color: #E5E7EB;
            }

            .btn-icon.download {
                color: #059669;
            }

            [data-theme="dark"] .btn-icon.download {
                color: #10B981;
            }

            .btn-icon.favorite {
                color: #9CA3AF;
            }

            .btn-icon.favorite.in-collection {
                color: #DC2626;
            }

            [data-theme="dark"] .btn-icon.favorite.in-collection {
                color: #EF4444;
            }

            .btn-icon.edit {
                color: #2563EB;
            }

            [data-theme="dark"] .btn-icon.edit {
                color: #60A5FA;
            }

            .btn-icon.delete {
                color: #DC2626;
            }

            [data-theme="dark"] .btn-icon.delete {
                color: #EF4444;
            }

            .btn-icon:hover {
                transform: translateY(-1px);
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            }

            .btn-icon:disabled {
                background-color: #E5E7EB;
                color: #9CA3AF;
                cursor: not-allowed;
                transform: none;
                box-shadow: none;
            }

            /* Cover Container */
            .cover-container {
                position: relative;
                width: 100%;
                height: 0;
                padding-bottom: 100%;
                overflow: hidden;
                background-color: #374151;
            }

            .album-cover {
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                object-fit: cover;
                transition: transform 0.3s ease;
            }

            .album-cover:hover {
                transform: scale(1.05);
            }

            .album-cover-placeholder {
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background-color: #374151;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #F3F4F6;
            }

            .album-initial {
                font-size: 3rem;
                font-weight: bold;
                color: #60A5FA;
            }

            /* Album Info */
            .album-info {
                padding: 0.75rem;
                background-color: #FFFFFF;
            }

            [data-theme="dark"] .album-info {
                background-color: #1F2937;
            }

            .album-info h3 {
                margin: 0 0 0.5rem 0;
                font-size: 1rem;
                font-weight: 600;
                color: #1F2937;
                line-height: 1.2;
                display: -webkit-box;
                -webkit-line-clamp: 2;
                -webkit-box-orient: vertical;
                overflow: hidden;
                text-overflow: ellipsis;
                word-wrap: break-word;
                word-break: break-word;
                max-height: 2.4em;
            }

            [data-theme="dark"] .album-info h3 {
                color: #F3F4F6;
            }

            .album-meta {
                display: flex;
                flex-direction: column;
                gap: 0.375rem;
                font-size: 0.8rem;
                color: #6B7280;
            }

            [data-theme="dark"] .album-meta {
                color: #D1D5DB;
            }

            .track-count {
                display: flex;
                align-items: center;
                gap: 0.25rem;
            }

            .access-level {
                display: flex;
                align-items: center;
                gap: 0.25rem;
                font-size: 0.75rem;
                padding: 0.125rem 0.375rem;
                border-radius: 9999px;
                background-color: rgba(59, 130, 246, 0.1);
                color: rgb(59, 130, 246) !important;
                width: fit-content;
            }

            .access-level.restricted {
                background-color: rgba(59, 130, 246, 0.1);
                color: rgb(59, 130, 246) !important;
                font-weight: 500;
            }

            .access-level i {
                color: rgb(59, 130, 246) !important;
                font-size: 0.75rem;
            }

            .download-info {
                display: flex;
                align-items: center;
                gap: 0.25rem;
                color: #9CA3AF;
                font-size: 0.75rem;
            }

            /* Loading States */
            .loading-container {
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 3rem;
            }

            .loading-spinner {
                width: 40px;
                height: 40px;
                border: 4px solid #E5E7EB;
                border-top: 4px solid #3B82F6;
                border-radius: 50%;
                animation: spin 1s linear infinite;
                margin-bottom: 1rem;
            }

            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }

            .load-more-container {
                display: flex;
                justify-content: center;
                padding: 2rem;
            }

            .load-more-btn {
                background-color: #3B82F6;
                color: white;
                border: none;
                padding: 0.75rem 2rem;
                border-radius: 0.5rem;
                cursor: pointer;
                font-size: 1rem;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }

            .load-more-btn:hover {
                background-color: #2563EB;
                transform: translateY(-1px);
            }

            .load-more-btn:disabled {
                background-color: #9CA3AF;
                cursor: not-allowed;
                transform: none;
            }

            .no-albums {
                text-align: center;
                padding: 3rem;
                color: #6B7280;
                font-size: 1.1rem;
            }

            /* Modal Styles */
            .modal {
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background-color: rgba(0, 0, 0, 0.5);
                z-index: 1100;
                justify-content: center;
                align-items: center;
            }

            .modal.active {
                display: flex;
            }

            .modal-content {
                background-color: var(--bg-color);
                color: var(--text-color);
                border-radius: 8px;
                padding: 25px;
                width: 90%;
                max-width: 500px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
                animation: modalFadeIn 0.3s ease;
            }

            @keyframes modalFadeIn {
                from { opacity: 0; transform: translateY(-20px); }
                to { opacity: 1; transform: translateY(0); }
            }

            [data-theme="dark"] .modal-content {
                background-color: #2D3748;
                color: #F7FAFC;
                border: 1px solid #4A5568;
            }

            .form-group {
                margin-bottom: 1rem;
            }

            .form-group label {
                display: block;
                margin-bottom: 0.5rem;
                font-weight: 500;
            }

            .form-group input,
            .form-group select {
                width: 100%;
                padding: 0.5rem;
                border: 1px solid #D1D5DB;
                border-radius: 0.375rem;
                font-size: 1rem;
                background-color: var(--background-secondary);
                color: var(--text);
            }

            [data-theme="dark"] .form-group input,
            [data-theme="dark"] .form-group select {
                background-color: #4A5568;
                color: #F7FAFC;
                border: 1px solid #718096;
            }

            .tier-select {
                width: 100%;
                padding: 0.5rem;
                border: 1px solid #4B5563;
                border-radius: 0.375rem;
                font-size: 0.95rem;
                background-color: var(--background-secondary);
                color: var(--text);
            }

            .help-text {
                margin-top: 0.5rem;
                color: #9CA3AF;
                font-size: 0.875rem;
                line-height: 1.4;
            }

            .modal-buttons {
                display: flex;
                justify-content: flex-end;
                gap: 10px;
                margin-top: 1.5rem;
            }

            .btn-primary, .btn-cancel {
                padding: 0.5rem 1rem;
                border-radius: 0.375rem;
                cursor: pointer;
                font-weight: 500;
                transition: all 0.2s ease;
                border: none;
            }

            .btn-primary {
                background-color: #3b82f6;
                color: white;
            }

            .btn-primary:hover {
                background-color: #2563eb;
            }

            .btn-cancel {
                background-color: transparent;
                color: var(--text-color);
                border: 1px solid var(--border-color);
            }

            /* Visibility Badge - Now in album-info section */
            .visibility-badge-info {
                display: flex;
                align-items: center;
                gap: 0.5rem;
                padding: 0.5rem 0.75rem;
                border-radius: 0.5rem;
                font-size: 0.85rem;
                font-weight: 500;
                margin-bottom: 0.5rem;
                width: fit-content;
            }

            .visibility-badge-info i {
                font-size: 0.9rem;
            }

            .visibility-badge-info .badge-text {
                display: inline; /* Always show text for albums/collections */
            }

            .visibility-badge-info.visibility-hidden_from_users {
                background: rgba(245, 158, 11, 0.15);
                color: #F59E0B;
                border: 1px solid rgba(245, 158, 11, 0.3);
            }

            [data-theme="dark"] .visibility-badge-info.visibility-hidden_from_users {
                background: rgba(245, 158, 11, 0.2);
                color: #FCD34D;
            }

            .visibility-badge-info.visibility-hidden_from_all {
                background: rgba(239, 68, 68, 0.15);
                color: #EF4444;
                border: 1px solid rgba(239, 68, 68, 0.3);
            }

            [data-theme="dark"] .visibility-badge-info.visibility-hidden_from_all {
                background: rgba(239, 68, 68, 0.2);
                color: #FCA5A5;
            }

            .visibility-select {
                width: 100%;
                padding: 0.5rem;
                border: 1px solid #4B5563;
                border-radius: 0.375rem;
                font-size: 0.95rem;
                background-color: var(--background-secondary);
                color: var(--text);
            }

            /* Scheduled Visibility Styles */
            .btn-schedule {
                width: 100%;
                padding: 0.75rem 1rem;
                margin-top: 1rem;
                background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%);
                color: white;
                border: none;
                border-radius: 0.5rem;
                font-size: 0.95rem;
                font-weight: 500;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.5rem;
                transition: all 0.3s ease;
            }

            .btn-schedule:hover {
                background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%);
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(59, 130, 246, 0.4);
            }

            .schedule-display {
                margin-top: 1rem;
                padding: 1rem;
                background: rgba(59, 130, 246, 0.1);
                border: 1px solid rgba(59, 130, 246, 0.3);
                border-radius: 0.5rem;
            }

            .schedule-info {
                display: flex;
                align-items: center;
                gap: 0.75rem;
            }

            .schedule-info i {
                color: #3B82F6;
                font-size: 1.2rem;
            }

            .schedule-text {
                flex: 1;
                font-size: 0.9rem;
                color: var(--text);
            }

            .schedule-text strong {
                color: #3B82F6;
                font-weight: 600;
            }

            .schedule-info .cancel-schedule {
                background: rgba(239, 68, 68, 0.2);
                color: #EF4444;
                padding: 0.5rem;
                border-radius: 0.375rem;
                transition: all 0.2s ease;
            }

            .schedule-info .cancel-schedule:hover {
                background: rgba(239, 68, 68, 0.3);
                transform: scale(1.1);
            }

            .schedule-current-status {
                display: flex;
                align-items: center;
                gap: 0.5rem;
                padding: 1rem;
                background: rgba(107, 114, 128, 0.1);
                border-radius: 0.5rem;
                margin-bottom: 1.5rem;
            }

            .schedule-current-status .label {
                font-size: 0.9rem;
                color: #9CA3AF;
            }

            .schedule-current-status .status-badge {
                padding: 0.25rem 0.75rem;
                border-radius: 1rem;
                font-size: 0.85rem;
                font-weight: 500;
            }

            .schedule-current-status .status-badge.visible {
                background: rgba(34, 197, 94, 0.2);
                color: #22C55E;
            }

            .schedule-current-status .status-badge.hidden_from_users {
                background: rgba(245, 158, 11, 0.2);
                color: #F59E0B;
            }

            .schedule-current-status .status-badge.hidden_from_all {
                background: rgba(239, 68, 68, 0.2);
                color: #EF4444;
            }

            .schedule-preview {
                padding: 1rem;
                background: rgba(59, 130, 246, 0.1);
                border-left: 3px solid #3B82F6;
                border-radius: 0.375rem;
                margin-top: 1rem;
                display: flex;
                align-items: flex-start;
                gap: 0.75rem;
            }

            .schedule-preview i {
                color: #3B82F6;
                margin-top: 0.2rem;
            }

            .schedule-preview span {
                flex: 1;
                font-size: 0.9rem;
                color: var(--text);
            }

            #scheduleDateTime {
                width: 100%;
                padding: 0.75rem;
                border: 1px solid #4B5563;
                border-radius: 0.375rem;
                font-size: 0.95rem;
                background-color: var(--background-secondary);
                color: var(--text);
                font-family: inherit;
            }

            #scheduleDateTime:focus {
                outline: none;
                border-color: #3B82F6;
                box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
            }

            /* Album Card Schedule Indicator - Below visibility badge */
            .card-schedule-indicator {
                display: flex;
                align-items: center;
                gap: 0.5rem;
                padding: 0.5rem 0.75rem;
                margin-bottom: 0.5rem;
                background: linear-gradient(135deg, rgba(59, 130, 246, 0.15) 0%, rgba(37, 99, 235, 0.1) 100%);
                border-left: 3px solid #3B82F6;
                border-radius: 0.375rem;
                font-size: 0.85rem;
                color: var(--text);
                width: fit-content;
            }

            [data-theme="dark"] .card-schedule-indicator {
                background: linear-gradient(135deg, rgba(59, 130, 246, 0.2) 0%, rgba(37, 99, 235, 0.15) 100%);
            }

            .card-schedule-indicator i {
                color: #3B82F6;
                font-size: 1rem;
                flex-shrink: 0;
            }

            [data-theme="dark"] .card-schedule-indicator i {
                color: #60A5FA;
            }

            .card-schedule-indicator .schedule-text {
                flex: 1;
                line-height: 1.4;
            }

            .card-schedule-indicator strong {
                color: #3B82F6;
                font-weight: 600;
            }

            [data-theme="dark"] .card-schedule-indicator strong {
                color: #60A5FA;
            }

            /* Mobile responsive for card indicator */
            @media (max-width: 768px) {
                .card-schedule-indicator {
                    font-size: 0.8rem;
                    padding: 0.4rem 0.6rem;
                    gap: 0.4rem;
                }

                .card-schedule-indicator i {
                    font-size: 0.9rem;
                }
            }
        `;
    }
}

// ========================================
// SCHEDULED VISIBILITY FUNCTIONS
// ========================================

let currentScheduleAlbumId = null;
let countdownInterval = null;

// Debug: Confirm scheduled visibility functions are loaded
console.log('✅ Scheduled visibility functions loaded');

// Open schedule modal
window.openScheduleModal = function() {
    const modal = document.getElementById('scheduleVisibilityModal');
    const editModal = document.getElementById('editAlbumModal');
    const albumId = editModal?.dataset?.albumId;

    if (!albumId) return;

    currentScheduleAlbumId = albumId;

    const visibilitySelect = document.getElementById('editAlbumVisibility');
    const currentStatus = visibilitySelect.value;

    const statusBadge = document.getElementById('scheduleCurrentStatusBadge');
    if (statusBadge) {
        statusBadge.textContent = getVisibilityLabel(currentStatus);
        statusBadge.className = `status-badge ${currentStatus}`;
    }

    const now = new Date();
    now.setMinutes(now.getMinutes() + 6);
    const minDateTime = now.toISOString().slice(0, 16);
    const datetimeInput = document.getElementById('scheduleDateTime');

    if (datetimeInput) {
        datetimeInput.min = minDateTime;
        datetimeInput.value = '';
    }

    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    } else {
        return;
    }

    const form = document.getElementById('scheduleVisibilityForm');
    if (form) {
        form.onsubmit = handleScheduleSubmit;
    }

    const targetStatusSelect = document.getElementById('scheduleTargetStatus');
    if (datetimeInput && targetStatusSelect) {
        datetimeInput.oninput = updateSchedulePreview;
        targetStatusSelect.onchange = updateSchedulePreview;
    }
}

// Close schedule modal
window.closeScheduleModal = function() {
    const modal = document.getElementById('scheduleVisibilityModal');

    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }

    const form = document.getElementById('scheduleVisibilityForm');
    if (form) {
        form.reset();
    }

    const preview = document.getElementById('schedulePreview');
    if (preview) {
        preview.style.display = 'none';
    }

    currentScheduleAlbumId = null;
}

// Update schedule preview
function updateSchedulePreview() {
    const datetime = document.getElementById('scheduleDateTime').value;
    const targetStatus = document.getElementById('scheduleTargetStatus').value;
    const preview = document.getElementById('schedulePreview');
    const previewText = document.getElementById('schedulePreviewText');

    if (datetime && targetStatus) {
        const scheduledDate = new Date(datetime);
        const now = new Date();
        const diff = scheduledDate - now;

        const hours = Math.floor(diff / (1000 * 60 * 60));
        const days = Math.floor(hours / 24);

        let timeText = '';
        if (days > 0) {
            timeText = `${days} day${days > 1 ? 's' : ''}`;
        } else if (hours > 0) {
            timeText = `${hours} hour${hours > 1 ? 's' : ''}`;
        } else {
            const minutes = Math.floor(diff / (1000 * 60));
            timeText = `${minutes} minute${minutes > 1 ? 's' : ''}`;
        }

        const statusLabel = getVisibilityLabel(targetStatus);
        previewText.textContent = `The album will automatically change to "${statusLabel}" in approximately ${timeText} (${scheduledDate.toLocaleString()})`;
        preview.style.display = 'flex';
    } else {
        preview.style.display = 'none';
    }
}

// Handle schedule form submit
async function handleScheduleSubmit(e) {
    e.preventDefault();

    const datetime = document.getElementById('scheduleDateTime').value;
    const targetStatus = document.getElementById('scheduleTargetStatus').value;

    if (!datetime || !targetStatus || !currentScheduleAlbumId) {
        console.error('Missing required fields');
        showNotification('Please fill in all required fields', 'error');
        return;
    }

    // Validate that the scheduled time is at least 5 minutes in the future
    const scheduledTime = new Date(datetime);
    const now = new Date();
    const minutesInFuture = (scheduledTime - now) / (1000 * 60);

    if (minutesInFuture < 5) {
        showNotification('Scheduled time must be at least 5 minutes in the future', 'error');
        return;
    }

    // Convert to UTC ISO string
    const scheduledAt = scheduledTime.toISOString();

    try {
        const response = await fetch(`/api/albums/${currentScheduleAlbumId}/schedule-visibility`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                scheduled_at: scheduledAt,
                visibility_status: targetStatus
            })
        });

        const data = await response.json();

        if (response.ok) {
            // Close schedule modal
            closeScheduleModal();

            // Show success message
            showNotification('Visibility change scheduled successfully!', 'success');

            // Update display in edit modal with album ID
            updateScheduleDisplay(data.schedule, currentScheduleAlbumId);
        } else {
            // Handle validation errors (422)
            let errorMessage = 'Failed to schedule visibility change';

            if (response.status === 422 && data.detail) {
                // Pydantic validation error
                if (Array.isArray(data.detail)) {
                    // Format: [{loc: [...], msg: "...", type: "..."}]
                    errorMessage = data.detail.map(err => err.msg).join(', ');
                } else if (typeof data.detail === 'string') {
                    errorMessage = data.detail;
                }
            } else if (data.detail) {
                errorMessage = data.detail;
            }

            console.error('❌ [Schedule] Error:', errorMessage);
            showNotification(errorMessage, 'error');
        }
    } catch (error) {
        console.error('❌ [Schedule] Exception:', error);
        showNotification('Failed to schedule visibility change', 'error');
    }
}

// Update schedule display in edit modal
function updateScheduleDisplay(schedule, albumId = null) {
    const display = document.getElementById('editAlbumScheduleDisplay');
    const scheduleBtn = document.getElementById('scheduleVisibilityBtn');
    const visibilitySelect = document.getElementById('editAlbumVisibility');
    const visibilityHelpText = visibilitySelect?.parentElement?.querySelector('.help-text');

    if (!display || !scheduleBtn) return;

    if (schedule) {
        const editModal = document.getElementById('editAlbumModal');
        const finalAlbumId = albumId || editModal?.dataset?.albumId || currentScheduleAlbumId;

        if (!finalAlbumId || finalAlbumId === 'null' || finalAlbumId === 'undefined') {
            return;
        }

        display.style.display = 'block';
        scheduleBtn.style.display = 'none';

        // DISABLE visibility dropdown - user must cancel schedule first
        if (visibilitySelect) {
            visibilitySelect.disabled = true;
            visibilitySelect.style.opacity = '0.6';
            visibilitySelect.style.cursor = 'not-allowed';
        }
        if (visibilityHelpText) {
            visibilityHelpText.innerHTML = '<strong style="color: #F59E0B;">⚠️ Cancel the scheduled change below to modify visibility manually</strong>';
        }

        const targetStatusText = display.querySelector('.schedule-target-status');
        const countdownText = display.querySelector('.schedule-countdown');

        targetStatusText.textContent = getVisibilityLabel(schedule.visibility_status);
        countdownText.textContent = schedule.countdown;

        const cancelBtn = display.querySelector('.cancel-schedule');
        cancelBtn.onclick = () => cancelSchedule(finalAlbumId);

        startCountdownUpdate(schedule.countdown_seconds);
    } else {
        display.style.display = 'none';
        scheduleBtn.style.display = 'block';
        stopCountdownUpdate();

        // RE-ENABLE visibility dropdown
        if (visibilitySelect) {
            visibilitySelect.disabled = false;
            visibilitySelect.style.opacity = '1';
            visibilitySelect.style.cursor = 'pointer';
        }
        if (visibilityHelpText) {
            const permissions = window.collectionConfig?.permissions;
            const defaultHelpText = permissions?.is_team && !permissions?.is_creator
                ? 'Control who can see this album (Team members cannot hide from team)'
                : 'Control who can see this album';
            visibilityHelpText.innerHTML = defaultHelpText;
        }
    }
}

// Start countdown timer
function startCountdownUpdate(seconds) {
    stopCountdownUpdate(); // Clear any existing interval

    let remainingSeconds = seconds;

    countdownInterval = setInterval(() => {
        remainingSeconds--;

        if (remainingSeconds <= 0) {
            stopCountdownUpdate();
            // Refresh the page to show updated visibility
            location.reload();
            return;
        }

        const countdownText = document.querySelector('.schedule-countdown');
        if (countdownText) {
            countdownText.textContent = formatCountdown(remainingSeconds);
        }
    }, 1000);
}

// Stop countdown timer
function stopCountdownUpdate() {
    if (countdownInterval) {
        clearInterval(countdownInterval);
        countdownInterval = null;
    }
}

// Format countdown
function formatCountdown(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    if (minutes > 0) return `${minutes}m ${secs}s`;
    return `${secs}s`;
}

// Cancel schedule
async function cancelSchedule(albumId) {
    console.log('🕐 [Schedule] cancelSchedule called with albumId:', albumId);

    // Validate album ID
    if (!albumId || albumId === 'null' || albumId === 'undefined') {
        console.error('❌ [Schedule] Invalid album ID for cancel:', albumId);
        alert('Error: Invalid album ID. Please close and reopen the edit modal.');
        return;
    }

    if (!confirm('Are you sure you want to cancel the scheduled visibility change?')) {
        return;
    }

    try {
        console.log('🕐 [Schedule] Sending DELETE request for album:', albumId);
        const response = await fetch(`/api/albums/${albumId}/schedule-visibility`, {
            method: 'DELETE'
        });

        const data = await response.json();
        console.log('🕐 [Schedule] Cancel response:', response.status, data);

        if (response.ok) {
            showNotification('Scheduled visibility change cancelled', 'success');
            updateScheduleDisplay(null);
        } else {
            showNotification(data.detail || 'Failed to cancel schedule', 'error');
        }
    } catch (error) {
        console.error('❌ [Schedule] Error cancelling schedule:', error);
        showNotification('Failed to cancel schedule', 'error');
    }
}

// Check for existing schedule when opening edit modal
window.checkExistingSchedule = async function(albumId) {
    if (!albumId || albumId === 'null' || albumId === 'undefined') {
        updateScheduleDisplay(null, albumId);
        return;
    }

    try {
        const response = await fetch(`/api/albums/${albumId}/schedule-visibility`);

        if (response.status === 403 || response.status === 404) {
            updateScheduleDisplay(null, albumId);
            return;
        }

        const data = await response.json();

        if (response.ok && data.has_schedule) {
            updateScheduleDisplay(data.schedule, albumId);
        } else {
            updateScheduleDisplay(null, albumId);
        }
    } catch (error) {
        console.error('Error checking schedule:', error);
        updateScheduleDisplay(null, albumId);
    }
}

// Get visibility label
function getVisibilityLabel(status) {
    const labels = {
        'visible': 'Visible to all',
        'hidden_from_users': 'Hidden from users',
        'hidden_from_all': 'Hidden from all'
    };
    return labels[status] || status;
}

// Show notification (reuse existing notification system if available)
function showNotification(message, type = 'info') {
    // Try to use existing notification system
    if (window.showNotification) {
        window.showNotification(message, type);
        return;
    }

    // Fallback: simple alert
    alert(message);
}

// ========================================
// ALBUM CARD COUNTDOWN DISPLAY
// ========================================

let cardCountdownIntervals = {};

// Check and display schedules for all album cards
window.checkAllCardSchedules = async function() {
    const permissions = window.collectionConfig?.permissions;
    if (!permissions || (!permissions.is_creator && !permissions.is_team)) {
        return;
    }

    const albumCards = document.querySelectorAll('.album-card[data-album-id]');

    for (const card of albumCards) {
        const albumId = card.dataset.albumId;
        if (albumId) {
            await checkCardSchedule(albumId);
        }
    }
};

// Check schedule for a specific album card
async function checkCardSchedule(albumId) {
    try {
        const response = await fetch(`/api/albums/${albumId}/schedule-visibility`);

        // Handle permission denied (403) - regular users cannot see schedules
        if (response.status === 403) {
            hideCardScheduleDisplay(albumId);
            return;
        }

        const data = await response.json();

        if (response.ok && data.has_schedule) {
            updateCardScheduleDisplay(albumId, data.schedule);
        } else {
            hideCardScheduleDisplay(albumId);
        }
    } catch (error) {
        console.error(`Error checking schedule for album ${albumId}:`, error);
        hideCardScheduleDisplay(albumId);
    }
}

// Update schedule display on album card
function updateCardScheduleDisplay(albumId, schedule) {
    const indicator = document.getElementById(`schedule-${albumId}`);
    if (!indicator) return;

    const targetText = indicator.querySelector('.schedule-target');
    const countdownText = indicator.querySelector('.schedule-countdown');

    if (targetText && countdownText) {
        targetText.textContent = getVisibilityLabel(schedule.visibility_status);
        countdownText.textContent = schedule.countdown;

        indicator.style.display = 'flex';

        // Start countdown update for this card
        startCardCountdown(albumId, schedule.countdown_seconds);
    }
}

// Hide schedule display on album card
function hideCardScheduleDisplay(albumId) {
    const indicator = document.getElementById(`schedule-${albumId}`);
    if (indicator) {
        indicator.style.display = 'none';
    }

    // Stop countdown for this card
    stopCardCountdown(albumId);
}

// Start countdown timer for a card
function startCardCountdown(albumId, seconds) {
    // Stop existing countdown if any
    stopCardCountdown(albumId);

    let remainingSeconds = seconds;

    cardCountdownIntervals[albumId] = setInterval(() => {
        remainingSeconds--;

        if (remainingSeconds <= 0) {
            stopCardCountdown(albumId);
            // Refresh the page to show updated visibility
            location.reload();
            return;
        }

        const indicator = document.getElementById(`schedule-${albumId}`);
        if (indicator) {
            const countdownText = indicator.querySelector('.schedule-countdown');
            if (countdownText) {
                countdownText.textContent = formatCountdown(remainingSeconds);
            }
        }
    }, 1000);
}

// Stop countdown timer for a card
function stopCardCountdown(albumId) {
    if (cardCountdownIntervals[albumId]) {
        clearInterval(cardCountdownIntervals[albumId]);
        delete cardCountdownIntervals[albumId];
    }
}

// Initialize card schedules when page loads
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        setTimeout(() => {
            if (window.checkAllCardSchedules) {
                window.checkAllCardSchedules();
            }
        }, 500); // Small delay to ensure cards are rendered
    });
} else {
    // Document already loaded
    setTimeout(() => {
        if (window.checkAllCardSchedules) {
            window.checkAllCardSchedules();
        }
    }, 500);
}
