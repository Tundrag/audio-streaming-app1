// /static/js/benefits-management-shared.js

export class BenefitsManagementController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.data = null;
        this.selectedPatronId = null;
        this.selectedPatronType = null;
        this.currentPlatform = 'patreon';
        this.voiceDragState = this.initVoiceDragState();
    }
    
    initVoiceDragState() {
        return {
            isDragging: false,
            baselineIndex: null,
            initialState: null,
            originalStates: null,
            isLongPress: false,
            lastTouchIndex: null,
            lastMouseIndex: null,
            touchStartY: null,
            touchStartX: null,
            touchStartItem: null,
            touchMoving: false,
            longPressTimer: null,
            autoScrolling: false,
            autoScrollDirection: 0,
            autoScrollSpeed: 0,
            autoScrollFrame: null,
            lastTouchPosition: null,
            lastMousePosition: null
        };
    }
    
    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }
        
        // Fetch data for SPA mode
        await this.fetchData();
        
        return this.generateHTML();
    }
    
    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`📊 BenefitsManagement: Mounting in ${this.mode} mode...`);
        
        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM
            this.hydrateFromDOM();
        } else {
            // SPA: Data already fetched in render()
        }
        
        // Initialize everything
        this.initializeInputHandlers();
        this.setupModalHandlers();
        this.setupPatronSearch();
        this.injectVoiceDragStyles();
        
        console.log('✅ BenefitsManagement: Mounted successfully');
    }
    
    destroy() {
        console.log('📊 BenefitsManagement: Destroying...');
        // Cleanup
        if (this.voiceDragState.longPressTimer) {
            clearTimeout(this.voiceDragState.longPressTimer);
        }
        this.stopVoiceAutoScroll();
        return Promise.resolve();
    }
    
    // ✅ Fetch data from API (SPA mode)
    async fetchData() {
        try {
            const response = await fetch('/api/creator/benefits/data');
            if (!response.ok) throw new Error('Failed to fetch benefits data');
            this.data = await response.json();
        } catch (error) {
            console.error('Error fetching benefits data:', error);
            throw error;
        }
    }
    
    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        // Read bootstrap data embedded in the page
        const bootstrapScript = document.getElementById('benefits-bootstrap-data');
        if (bootstrapScript) {
            try {
                this.data = JSON.parse(bootstrapScript.textContent);
            } catch (error) {
                console.error('Error parsing bootstrap data:', error);
            }
        }
    }
    
    // ✅ Generate HTML for SPA mode
    generateHTML() {
        if (!this.data) {
            return '<div>Error loading benefits data</div>';
        }
        
        const { tiers, patreon_users_count, kofi_users_count, team_users_count, guest_trial_users_count } = this.data;
        
        return `
<div class="benefits-container">
    <div class="benefit-card">
        <div class="benefit-header">
            <div class="benefit-header-left">
                <i class="fas fa-search"></i>
                <div>
                    <h2>Patron Search</h2>
                    <p>Search and modify individual patron benefits</p>
                </div>
            </div>
        </div>

        <div class="patron-search-input-group">
            <input type="text" id="patronSearchInput" class="patron-search-input" placeholder="Search patrons by name or email...">
            <button id="patronSearchBtn" class="patron-search-btn">
                <i class="fas fa-search"></i> Search
            </button>
        </div>

        <div id="patronSearchResults"></div>

        <div id="patronBenefitForm" class="patron-benefit-form" style="display: none;">
            <div class="tier-header">
                <div class="tier-info">
                    <h3 id="selectedPatronName"></h3>
                    <span id="selectedPatronTier" class="tier-amount"></span>
                    <span id="selectedPatronType" class="tier-amount"></span>
                </div>
            </div>
            <div class="patron-form-inputs">
                <div class="input-group">
                    <label>Albums per month:</label>
                    <input type="number" id="patronAlbumInput" class="setting-input" min="0">
                </div>
                <div class="input-group">
                    <label>Tracks per month:</label>
                    <input type="number" id="patronTrackInput" class="setting-input" min="0">
                </div>
                <div class="input-group">
                    <label>Book Requests per month:</label>
                    <input type="number" id="patronBookRequestInput" class="setting-input" min="0">
                </div>
                <div class="input-group">
                    <label>Chapters per book request:</label>
                    <input type="number" id="patronChaptersInput" class="setting-input" min="0" placeholder="0">
                </div>
                <div class="input-group">
                    <label>Max sessions:</label>
                    <input type="number" id="patronSessionInput" class="setting-input" min="1" max="5">
                </div>
                <div class="input-group">
                    <label>
                        <input type="checkbox" id="patronReadAlongInput" class="read-along-checkbox">
                        Read-Along Access
                    </label>
                    <small>Allow this patron to use read-along feature for TTS tracks</small>
                </div>
            </div>
            <button id="patronUpdateBtn" class="save-btn">
                <i class="fas fa-save"></i> Save Changes
            </button>
        </div>
        
        <div class="user-summary">
            <div class="user-count patreon">
                <i class="fab fa-patreon"></i>
                <span>${patreon_users_count || 0} Patreon user${patreon_users_count !== 1 ? 's' : ''}</span>
            </div>
            <div class="user-count kofi">
                <i class="fas fa-coffee"></i>
                <span>${kofi_users_count || 0} Ko-fi user${kofi_users_count !== 1 ? 's' : ''}</span>
            </div>
            ${guest_trial_users_count !== undefined ? `
            <div class="user-count guest-trial">
                <i class="fas fa-user-clock"></i>
                <span>${guest_trial_users_count || 0} Guest trial user${guest_trial_users_count !== 1 ? 's' : ''}</span>
            </div>
            ` : ''}
            <div class="user-count team">
                <i class="fas fa-users"></i>
                <span>${team_users_count || 0} Team member${team_users_count !== 1 ? 's' : ''}</span>
            </div>
        </div>
    </div>

    <div class="benefit-card">
        <div class="benefit-header">
            <div class="benefit-header-left">
                <i class="fas fa-sliders-h"></i>
                <div>
                    <h2>Tier Benefits</h2>
                    <p>Configure all benefits for each tier and team members</p>
                </div>
            </div>
            <div class="reset-buttons">
                <button class="reset-all-btn" id="resetSessionsBtn">
                    <i class="fas fa-users-slash"></i> Reset Sessions
                </button>
                <button class="reset-all-btn" id="resetDownloadsBtn">
                    <i class="fas fa-sync"></i> Reset Downloads
                </button>
                <button class="reset-all-btn" id="resetBookRequestsBtn">
                    <i class="fas fa-book"></i> Reset Book Requests
                </button>
            </div>
        </div>
        
        <div class="platform-tabs">
            <div class="platform-tab patreon active" data-platform="patreon">
                <i class="fab fa-patreon"></i> Patreon Tiers
            </div>
            <div class="platform-tab kofi" data-platform="kofi">
                <i class="fas fa-coffee"></i> Ko-fi Tiers
            </div>
        </div>
        
        <div id="patreonSection" class="platform-section active">
            <div class="platform-section-header">
                <h3 class="platform-section-title">
                    <i class="fab fa-patreon" style="color: #f96854;"></i> Patreon Tiers
                </h3>
                <div class="header-actions">
                    <button class="voice-management-btn open-voice-modal">
                        <i class="fas fa-microphone"></i> Voice Management
                    </button>
                    <button class="create-tier-btn patreon open-patreon-modal">
                        <i class="fas fa-plus"></i> Create Patreon Tier
                    </button>
                </div>
            </div>
            
            <div id="patreonTiersContainer">
                ${this.renderTiers(tiers, false)}
            </div>
        </div>
        
        <div id="kofiSection" class="platform-section">
            <div class="platform-section-header">
                <h3 class="platform-section-title">
                    <i class="fas fa-coffee" style="color: #29abe0;"></i> Ko-fi Tiers
                </h3>
                <div class="header-actions">
                    <button class="voice-management-btn open-voice-modal">
                        <i class="fas fa-microphone"></i> Voice Management
                    </button>
                    <button class="create-tier-btn kofi open-kofi-modal">
                        <i class="fas fa-plus"></i> Create Ko-fi Tier
                    </button>
                </div>
            </div>
            
            <div id="kofiTiersContainer">
                ${this.renderTiers(tiers, true)}
            </div>
        </div>
    </div>
</div>

${this.renderModals()}
        `;
    }
    
    renderTiers(tiers, isKofi) {
        const filteredTiers = tiers.filter(tier => tier.is_kofi === isKofi);
        
        if (filteredTiers.length === 0) {
            const platform = isKofi ? 'kofi' : 'patreon';
            const icon = isKofi ? 'fa-coffee' : 'fab fa-patreon';
            const name = isKofi ? 'Ko-fi' : 'Patreon';
            
            return `
                <div class="no-tiers-message">
                    <i class="${icon} fa-3x"></i>
                    <h3>No ${name} tiers found</h3>
                    <p>Create your first ${name} tier to get started</p>
                    <button class="create-tier-btn ${platform} open-${platform}-modal">
                        <i class="fas fa-plus"></i> Create ${name} Tier
                    </button>
                </div>
            `;
        }
        
        return filteredTiers.map(tier => this.renderTier(tier)).join('');
    }
    
    renderTier(tier) {
        const platformClass = tier.is_kofi ? 'kofi' : 'patreon';
        const platformName = tier.is_kofi ? 'Ko-fi' : 'Patreon';
        const amount = (tier.amount_cents / 100).toFixed(2);
        
        return `
            <div class="tier-settings ${!tier.is_active ? 'inactive' : ''}" data-tier-id="${tier.id}" data-tier-title="${this.escapeHtml(tier.title)}">
                <div class="tier-header">
                    <div class="tier-title-group">
                        <div class="tier-info">
                            <h3>${this.escapeHtml(tier.title)}<span class="tier-badge ${platformClass}">${platformName}</span></h3>
                            <span class="tier-amount">$${amount}/month</span>
                        </div>
                        ${tier.is_active ? `
                        <span class="patron-count">
                            <i class="fas fa-users"></i>
                            ${tier.patron_count} patron${tier.patron_count !== 1 ? 's' : ''}
                        </span>
                        ` : ''}
                    </div>
                    <div class="tier-actions">
                        ${tier.is_active ? `
                        <div class="active-sessions">
                            <i class="fas fa-user-clock"></i>
                            ${tier.active_sessions || 0} active session${(tier.active_sessions || 0) !== 1 ? 's' : ''}
                        </div>
                        ` : ''}
                        <button class="delete-tier-btn" data-tier-id="${tier.id}" data-tier-title="${this.escapeHtml(tier.title)}" data-platform="${platformClass}">
                            <i class="fas fa-trash-alt"></i> Delete
                        </button>
                    </div>
                </div>
                
                ${tier.is_active ? `
                <div class="setting-row" data-tier-id="${this.escapeHtml(tier.title)}">
                    <div class="settings-inputs">
                        <div class="input-group">
                            <label>Albums per month:</label>
                            <input type="number" class="setting-input album-input" value="${tier.album_downloads_allowed}" min="0" data-tier-id="${this.escapeHtml(tier.title)}">
                        </div>
                        <div class="input-group">
                            <label>Tracks per month:</label>
                            <input type="number" class="setting-input track-input" value="${tier.track_downloads_allowed}" min="0" data-tier-id="${this.escapeHtml(tier.title)}">
                        </div>
                        <div class="input-group">
                            <label>Book Requests per month:</label>
                            <input type="number" class="setting-input book-request-input" value="${tier.book_requests_allowed || 0}" min="0" data-tier-id="${this.escapeHtml(tier.title)}">
                        </div>
                        <div class="input-group">
                            <label>Chapters per book request:</label>
                            <input type="number" class="setting-input chapters-input" value="${tier.chapters_allowed_per_book_request || 0}" min="0" data-tier-id="${this.escapeHtml(tier.title)}" placeholder="0 = no limit set">
                            <small>Maximum chapters per book request (0 for unspecified)</small>
                        </div>
                        <div class="input-group">
                            <label>Max concurrent sessions:</label>
                            <input type="number" class="setting-input session-input" value="${tier.max_sessions || 1}" min="1" max="5" data-tier-id="${this.escapeHtml(tier.title)}">
                        </div>
                        <div class="input-group">
                            <label>
                                <input type="checkbox" class="read-along-checkbox" ${tier.read_along_access ? 'checked' : ''} data-tier-id="${this.escapeHtml(tier.title)}">
                                Read-Along Access
                            </label>
                            <small>Allow users in this tier to use read-along feature for TTS tracks</small>
                        </div>
                    </div>
                    <button class="save-btn" data-save-tier="${this.escapeHtml(tier.title)}">
                        <i class="fas fa-save"></i> Save Changes
                    </button>
                    <div class="success-message">
                        <i class="fas fa-check"></i> Saved successfully
                    </div>
                </div>
                ` : ''}
            </div>
        `;
    }
    
    renderModals() {
        return `
<!-- Create Tier Modal -->
<div id="createTierModal" class="response-modal">
    <div class="modal-content">
        <div class="modal-header">
            <i class="fab fa-patreon patreon" id="modalIcon"></i>
            <h3 id="createTierModalTitle">Create New Tier</h3>
        </div>
        <div class="modal-body">
            <form id="createTierForm">
                <div class="form-row">
                    <label for="tierNameInput">Tier Name:</label>
                    <input type="text" id="tierNameInput" placeholder="e.g. Basic Supporter" required>
                    <small>Choose a clear name that describes what patrons get in this tier</small>
                </div>
                <div class="form-row">
                    <label for="tierAmountInput">Amount (in USD):</label>
                    <input type="number" id="tierAmountInput" min="0" step="0.01" placeholder="e.g. 5.00" required>
                    <small>Enter price without $ symbol</small>
                </div>
                <div class="form-grid">
                    <div class="form-row">
                        <label for="tierAlbumInput">Albums per month:</label>
                        <input type="number" id="tierAlbumInput" min="0" value="0" placeholder="0">
                    </div>
                    <div class="form-row">
                        <label for="tierTrackInput">Tracks per month:</label>
                        <input type="number" id="tierTrackInput" min="0" value="0" placeholder="0">
                    </div>
                    <div class="form-row">
                        <label for="tierBookRequestInput">Book Requests per month:</label>
                        <input type="number" id="tierBookRequestInput" min="0" value="0" placeholder="0">
                    </div>
                    <div class="form-row">
                        <label for="tierChaptersInput">Chapters per book request:</label>
                        <input type="number" id="tierChaptersInput" min="0" value="0" placeholder="0">
                        <small>Maximum chapters allowed per book request (0 for unspecified)</small>
                    </div>
                    <div class="form-row">
                        <label for="tierSessionInput">Max concurrent sessions:</label>
                        <input type="number" id="tierSessionInput" min="1" max="5" value="1" placeholder="1">
                        <small>Number of devices that can be logged in simultaneously (1-5)</small>
                    </div>
                    <div class="form-row">
                        <label for="tierReadAlongInput">
                            <input type="checkbox" id="tierReadAlongInput" style="margin-right: 0.5rem;">
                            Read-Along Access
                        </label>
                        <small>Allow users in this tier to use read-along feature for TTS tracks</small>
                    </div>
                </div>
            </form>
        </div>
        <div class="modal-footer">
            <button id="modalCancelBtn" class="modal-btn modal-btn-cancel">Cancel</button>
            <button id="modalSubmitBtn" class="modal-btn modal-btn-submit">Create Tier</button>
        </div>
    </div>
</div>

<!-- Delete Tier Modal -->
<div id="deleteTierModal" class="response-modal">
    <div class="modal-content">
        <div class="modal-header">
            <i class="fas fa-exclamation-triangle" style="color: var(--danger-color);"></i>
            <h3>Delete Tier</h3>
        </div>
        <div class="modal-body">
            <p>Are you sure you want to delete the tier "<span id="deleteTierName"></span>"?</p>
            <p style="color: var(--danger-color); margin-top: 1rem;"><strong>Warning:</strong> This will permanently remove this tier and cannot be undone.</p>
            <p id="patronCountWarning" style="color: var(--danger-color); font-weight: bold; margin-top: 0.5rem; display: none;">
                This tier has patrons! After deletion, these patrons will lose access to the benefits associated with this tier.
            </p>
        </div>
        <div class="modal-footer">
            <button id="deleteModalCancelBtn" class="modal-btn modal-btn-cancel">Cancel</button>
            <button id="confirmDeleteTierBtn" class="modal-btn modal-btn-delete">Delete Tier</button>
        </div>
    </div>
</div>

<!-- Voice Management Modal -->
<div id="voiceManagementModal" class="response-modal">
    <div class="modal-content">
        <div class="modal-header">
            <i class="fas fa-microphone voice"></i>
            <h3>Voice Management</h3>
        </div>
        <div class="modal-body">
            <div class="voice-management-tabs">
                <div class="voice-tab active" data-voice-tab="assign">
                    <i class="fas fa-link"></i> Assign Voices to Tiers
                </div>
                <div class="voice-tab" data-voice-tab="manage">
                    <i class="fas fa-plus"></i> Manage Voices
                </div>
            </div>

            <div id="voiceAssignSection" class="voice-section active">
                <div class="voice-assignment-container">
                    <h4>Assign Voices to Tiers</h4>
                    <div class="bulk-select-all">
                        <input type="checkbox" id="selectAllVoices">
                        <label for="selectAllVoices">Select All Voices</label>
                    </div>
                    <div class="voice-selection-list" id="voiceSelectionList"></div>
                    <div class="voice-assignment-form">
                        <div class="form-row">
                            <label for="voiceTierSelect">Assign to Tier:</label>
                            <select id="voiceTierSelect" class="setting-input">
                                <option value="">Select a tier...</option>
                            </select>
                            <small>Select which tier should have access to the selected voices</small>
                        </div>
                        <button type="button" class="add-voice-btn" id="assignVoicesBtn" disabled>
                            <i class="fas fa-link"></i> Assign Selected Voices
                        </button>
                    </div>
                    <div id="voiceAssignmentSuccess" style="display: none; margin-top: 1rem; padding: 1rem; background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3); border-radius: 0.5rem; color: #22C55E;">
                        <i class="fas fa-check-circle"></i>
                        <span id="voiceSuccessMessage">Voices assigned successfully!</span>
                    </div>
                </div>
            </div>

            <div id="voiceManageSection" class="voice-section">
                <div class="add-voice-section">
                    <h4>Add New Voice</h4>
                    <form id="addVoiceForm">
                        <div class="add-voice-form">
                            <div class="input-group">
                                <label for="newVoiceId">Voice ID:</label>
                                <input type="text" id="newVoiceId" class="setting-input" placeholder="e.g. en-US-EmmaNeural" required>
                                <small>Azure Speech Service voice identifier</small>
                            </div>
                            <div class="input-group">
                                <label for="newVoiceDisplayName">Display Name:</label>
                                <input type="text" id="newVoiceDisplayName" class="setting-input" placeholder="e.g. Emma" required>
                            </div>
                            <div class="input-group">
                                <label for="newVoiceLanguage">Language:</label>
                                <select id="newVoiceLanguage" class="setting-input">
                                    <option value="en-US">English (US)</option>
                                    <option value="en-GB">English (UK)</option>
                                    <option value="en-AU">English (Australia)</option>
                                    <option value="fr-FR">French</option>
                                    <option value="de-DE">German</option>
                                    <option value="es-ES">Spanish</option>
                                    <option value="it-IT">Italian</option>
                                    <option value="ja-JP">Japanese</option>
                                </select>
                            </div>
                            <div class="input-group">
                                <label for="newVoiceGender">Gender:</label>
                                <select id="newVoiceGender" class="setting-input">
                                    <option value="female">Female</option>
                                    <option value="male">Male</option>
                                    <option value="neutral">Neutral</option>
                                </select>
                            </div>
                        </div>
                        <button type="submit" class="add-voice-btn">
                            <i class="fas fa-plus"></i> Add Voice
                        </button>
                    </form>
                </div>
                <div id="allVoicesList" style="margin-top: 2rem;">
                    <h4>All Available Voices</h4>
                    <div id="voiceListContainer"></div>
                </div>
            </div>
        </div>
        <div class="modal-footer">
            <button id="voiceModalCancelBtn" class="modal-btn modal-btn-cancel">Close</button>
        </div>
    </div>
</div>
        `;
    }
    
    // ==================== EVENT HANDLERS ====================
    
    initializeInputHandlers() {
        // Disable all save buttons initially
        document.querySelectorAll('.save-btn').forEach(button => {
            button.setAttribute('disabled', 'disabled');
        });
        
        // Track input changes for tier settings
        document.querySelectorAll('.setting-input').forEach(input => {
            input.defaultValue = input.value;
            input.addEventListener('input', () => this.handleInputChange(input));
        });
        
        // Track checkbox changes
        document.querySelectorAll('.read-along-checkbox').forEach(checkbox => {
            checkbox.defaultChecked = checkbox.checked;
            checkbox.addEventListener('change', () => this.handleCheckboxChange(checkbox));
        });
        
        // Save button clicks
        document.addEventListener('click', (e) => {
            const saveBtn = e.target.closest('[data-save-tier]');
            if (saveBtn) {
                this.handleSave(saveBtn);
            }
        });
        
        // Reset buttons
        document.getElementById('resetSessionsBtn')?.addEventListener('click', () => this.handleResetAllSessions());
        document.getElementById('resetDownloadsBtn')?.addEventListener('click', () => this.handleResetAll());
        document.getElementById('resetBookRequestsBtn')?.addEventListener('click', () => this.handleResetAllBookRequests());
        
        // Platform tabs
        document.querySelectorAll('.platform-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const platform = tab.getAttribute('data-platform');
                this.switchPlatform(platform);
            });
        });
    }
    
    handleInputChange(input) {
        const row = input.closest('.setting-row');
        if (!row) return;
        
        const value = parseInt(input.value);
        if (isNaN(value) || value < 0) input.value = 0;
        
        if (input.classList.contains('session-input')) {
            const minVal = parseInt(input.getAttribute('min') || 1);
            const maxVal = parseInt(input.getAttribute('max') || 5);
            if (value < minVal) input.value = minVal;
            if (value > maxVal) input.value = maxVal;
        }
        
        this.updateSaveButtonState(row);
    }
    
    handleCheckboxChange(checkbox) {
        const row = checkbox.closest('.setting-row');
        if (row) {
            this.updateSaveButtonState(row);
        }
    }
    
    updateSaveButtonState(row) {
        const button = row.querySelector('.save-btn');
        if (!button) return;
        
        const hasChanges = Array.from(row.querySelectorAll('.setting-input')).some(input => 
            parseInt(input.value) !== parseInt(input.defaultValue)
        );
        
        const checkboxChanged = Array.from(row.querySelectorAll('.read-along-checkbox')).some(checkbox => 
            checkbox.checked !== checkbox.defaultChecked
        );
        
        button.disabled = !(hasChanges || checkboxChanged);
    }
    
    async handleSave(button) {
        const row = button.closest('.setting-row');
        const inputs = {
            album: row.querySelector('.album-input'),
            track: row.querySelector('.track-input'),
            bookRequest: row.querySelector('.book-request-input'),
            chapters: row.querySelector('.chapters-input'),
            session: row.querySelector('.session-input')
        };
        const readAlongCheckbox = row.querySelector('.read-along-checkbox');
        const tierId = row.dataset.tierId;
        const successMessage = row.querySelector('.success-message');
        
        const values = {
            albumDownloads: parseInt(inputs.album.value),
            trackDownloads: parseInt(inputs.track.value),
            bookRequests: parseInt(inputs.bookRequest.value),
            chaptersPerRequest: parseInt(inputs.chapters.value),
            maxSessions: parseInt(inputs.session.value),
            readAlongAccess: readAlongCheckbox ? readAlongCheckbox.checked : false
        };
        
        try {
            button.disabled = true;
            Object.values(inputs).forEach(input => input.disabled = true);
            if (readAlongCheckbox) readAlongCheckbox.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
            
            const requests = [
                fetch('/api/creator/downloads/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        tier_id: tierId,
                        album_downloads_allowed: values.albumDownloads,
                        track_downloads_allowed: values.trackDownloads
                    })
                }),
                fetch('/api/book-requests/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        tier_id: tierId,
                        book_requests_allowed: values.bookRequests
                    })
                }),
                fetch('/api/book-requests/chapters-settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        tier_id: tierId,
                        chapters_allowed_per_book_request: values.chaptersPerRequest
                    })
                }),
                fetch('/api/creator/sessions/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        tier_id: tierId,
                        max_sessions: values.maxSessions
                    })
                }),
                this.updateTierReadAlongAccess(tierId, values.readAlongAccess)
            ];
            
            const responses = await Promise.all(requests);
            const failed = responses.find(r => !r.ok);
            if (failed) throw new Error('Failed to save settings');
            
            Object.entries(inputs).forEach(([key, input]) => input.defaultValue = input.value);
            if (readAlongCheckbox) readAlongCheckbox.defaultChecked = readAlongCheckbox.checked;
            
            successMessage.classList.add('show');
            setTimeout(() => successMessage.classList.remove('show'), 3000);
            
        } catch (error) {
            console.error('Error:', error);
            alert('Error saving settings. Please try again.');
        } finally {
            Object.values(inputs).forEach(input => input.disabled = false);
            if (readAlongCheckbox) readAlongCheckbox.disabled = false;
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-save"></i> Save Changes';
        }
    }
    
    async updateTierReadAlongAccess(tierId, hasAccess) {
        const response = await fetch(`/api/creator/tiers/${encodeURIComponent(tierId)}/read-along`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ read_along_access: hasAccess })
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to update read-along access');
        }
        
        return response;
    }
    
    // ==================== MODAL HANDLERS ====================
    
    setupModalHandlers() {
        const createModal = document.getElementById('createTierModal');
        const deleteModal = document.getElementById('deleteTierModal');
        const voiceModal = document.getElementById('voiceManagementModal');
        
        // Open create tier modal
        document.querySelectorAll('.open-patreon-modal').forEach(btn => {
            btn.addEventListener('click', () => this.openCreateTierModal('patreon'));
        });
        
        document.querySelectorAll('.open-kofi-modal').forEach(btn => {
            btn.addEventListener('click', () => this.openCreateTierModal('kofi'));
        });
        
        // Open voice management modal
        document.querySelectorAll('.open-voice-modal').forEach(btn => {
            btn.addEventListener('click', () => this.openVoiceManagementModal());
        });
        
        // Delete tier buttons
        document.querySelectorAll('.delete-tier-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const tierId = btn.getAttribute('data-tier-id');
                const tierTitle = btn.getAttribute('data-tier-title');
                const platform = btn.getAttribute('data-platform');
                this.openDeleteTierModal(tierId, tierTitle, platform);
            });
        });
        
        // Modal close buttons
        document.getElementById('modalCancelBtn')?.addEventListener('click', () => {
            createModal.style.display = 'none';
        });
        
        document.getElementById('deleteModalCancelBtn')?.addEventListener('click', () => {
            deleteModal.style.display = 'none';
        });
        
        document.getElementById('voiceModalCancelBtn')?.addEventListener('click', () => {
            voiceModal.style.display = 'none';
        });
        
        // Click outside to close
        [createModal, deleteModal, voiceModal].forEach(modal => {
            modal?.addEventListener('click', (e) => {
                if (e.target === modal) modal.style.display = 'none';
            });
        });
        
        // Submit buttons
        document.getElementById('modalSubmitBtn')?.addEventListener('click', () => this.submitCreateTierForm());
        document.getElementById('confirmDeleteTierBtn')?.addEventListener('click', () => this.confirmDeleteTier());
        
        // Voice tabs
        document.querySelectorAll('.voice-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                const tabName = tab.getAttribute('data-voice-tab');
                this.switchVoiceTab(tabName);
            });
        });
        
        // Add voice form
        document.getElementById('addVoiceForm')?.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleAddVoice();
        });
        
        // Select all voices checkbox
        document.getElementById('selectAllVoices')?.addEventListener('change', (e) => {
            const voiceCheckboxes = document.querySelectorAll('input[name="selected_voices"]');
            voiceCheckboxes.forEach(checkbox => {
                checkbox.checked = e.target.checked;
                const voiceItem = checkbox.closest('.voice-select-item');
                if (voiceItem) voiceItem.classList.toggle('selected', checkbox.checked);
            });
            this.updateVoiceSelectionCounter();
        });
        
        // Assign voices button
        document.getElementById('assignVoicesBtn')?.addEventListener('click', () => this.assignVoicesToTier());
        
        // Voice checkbox changes
        document.addEventListener('change', (e) => {
            if (e.target.name === 'selected_voices') {
                this.updateVoiceSelectionCounter();
            }
        });
        
        // Escape key to close modals
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                [createModal, deleteModal, voiceModal].forEach(modal => {
                    if (modal && modal.style.display === 'flex') {
                        modal.style.display = 'none';
                    }
                });
            }
        });
    }
    
    openCreateTierModal(platform) {
        this.currentPlatform = platform;
        const platformName = platform === 'patreon' ? 'Patreon' : 'Ko-fi';
        const modal = document.getElementById('createTierModal');
        const modalTitle = document.getElementById('createTierModalTitle');
        const modalIcon = document.getElementById('modalIcon');
        const submitBtn = document.getElementById('modalSubmitBtn');
        const form = document.getElementById('createTierForm');
        
        modalTitle.textContent = `Create New ${platformName} Tier`;
        modalIcon.className = platform === 'patreon' ? 'fab fa-patreon patreon' : 'fas fa-coffee kofi';
        submitBtn.classList.remove('patreon', 'kofi');
        submitBtn.classList.add(platform);
        submitBtn.textContent = `Create ${platformName} Tier`;
        
        form.querySelectorAll('input').forEach(input => {
            input.classList.remove('patreon', 'kofi');
            input.classList.add(platform);
        });
        
        form.reset();
        modal.style.display = 'flex';
        setTimeout(() => document.getElementById('tierNameInput')?.focus(), 100);
    }
    
    async submitCreateTierForm() {
        const tierName = document.getElementById('tierNameInput').value.trim();
        const tierAmount = parseFloat(document.getElementById('tierAmountInput').value);
        
        if (!tierName) {
            alert('Please enter a tier name');
            document.getElementById('tierNameInput')?.focus();
            return;
        }
        
        if (isNaN(tierAmount) || tierAmount < 0) {
            alert('Please enter a valid amount');
            document.getElementById('tierAmountInput')?.focus();
            return;
        }
        
        const tierData = {
            title: tierName,
            platform_type: this.currentPlatform.toUpperCase(),
            amount_cents: Math.round(tierAmount * 100),
            album_downloads_allowed: parseInt(document.getElementById('tierAlbumInput').value) || 0,
            track_downloads_allowed: parseInt(document.getElementById('tierTrackInput').value) || 0,
            book_requests_allowed: parseInt(document.getElementById('tierBookRequestInput').value) || 0,
            chapters_allowed_per_book_request: parseInt(document.getElementById('tierChaptersInput').value) || 0,
            max_sessions: parseInt(document.getElementById('tierSessionInput').value) || 1,
            read_along_access: document.getElementById('tierReadAlongInput').checked,
            is_active: true
        };
        
        const submitBtn = document.getElementById('modalSubmitBtn');
        const originalText = submitBtn.textContent;
        
        try {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Creating...';
            
            const response = await fetch('/api/platforms/tiers', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(tierData)
            });
            
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to create tier');
            }
            
            window.location.reload();
        } catch (error) {
            console.error('Error creating tier:', error);
            alert(`Error creating tier: ${error.message}`);
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
        }
    }
    
    openDeleteTierModal(tierId, tierName, platform) {
        const modal = document.getElementById('deleteTierModal');
        const deleteTierName = document.getElementById('deleteTierName');
        const confirmBtn = document.getElementById('confirmDeleteTierBtn');
        const patronCountWarning = document.getElementById('patronCountWarning');
        
        deleteTierName.textContent = tierName;
        confirmBtn.setAttribute('data-tier-id', tierId);
        confirmBtn.setAttribute('data-platform', platform);
        
        const tierElement = document.querySelector(`.tier-settings[data-tier-id="${tierId}"]`);
        if (tierElement) {
            const patronCountElement = tierElement.querySelector('.patron-count');
            if (patronCountElement) {
                const patronText = patronCountElement.textContent.trim();
                const patronMatch = patronText.match(/(\d+)\s+patron/);
                patronCountWarning.style.display = (patronMatch && parseInt(patronMatch[1]) > 0) ? 'block' : 'none';
            } else {
                patronCountWarning.style.display = 'none';
            }
        } else {
            patronCountWarning.style.display = 'none';
        }
        
        modal.style.display = 'flex';
    }
    
    async confirmDeleteTier() {
        const confirmBtn = document.getElementById('confirmDeleteTierBtn');
        const tierId = confirmBtn.getAttribute('data-tier-id');
        const originalBtnText = confirmBtn.textContent;
        
        try {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting...';
            
            const response = await fetch(`/api/platforms/tier/${tierId}`, {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'}
            });
            
            if (!response.ok) throw new Error('Failed to delete tier');
            
            window.location.reload();
        } catch (error) {
            console.error('Error deleting tier:', error);
            alert(`Error deleting tier: ${error.message}`);
            confirmBtn.disabled = false;
            confirmBtn.textContent = originalBtnText;
            document.getElementById('deleteTierModal').style.display = 'none';
        }
    }
    
    // ==================== PATRON SEARCH ====================
    
    setupPatronSearch() {
        const searchBtn = document.getElementById('patronSearchBtn');
        const searchInput = document.getElementById('patronSearchInput');
        
        searchBtn?.addEventListener('click', () => this.handlePatronSearch());
        
        searchInput?.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.handlePatronSearch();
            }
        });
        
        // Patron form inputs
        const patronInputs = ['patronAlbumInput', 'patronTrackInput', 'patronBookRequestInput', 'patronChaptersInput', 'patronSessionInput'];
        patronInputs.forEach(inputId => {
            const input = document.getElementById(inputId);
            input?.addEventListener('input', () => this.handlePatronInputChange(input));
        });
        
        // Patron read-along checkbox
        const patronReadAlongInput = document.getElementById('patronReadAlongInput');
        patronReadAlongInput?.addEventListener('change', () => this.handlePatronCheckboxChange());
        
        // Update button
        document.getElementById('patronUpdateBtn')?.addEventListener('click', () => this.handlePatronUpdate());
    }
    
    async handlePatronSearch() {
        const searchInput = document.getElementById('patronSearchInput');
        const resultsDiv = document.getElementById('patronSearchResults');
        const query = searchInput.value.trim();
        
        if (!query) return;
        
        try {
            resultsDiv.innerHTML = '<div class="patron-result-item">Searching...</div>';
            
            const response = await fetch(`/api/creator/patrons/search?q=${encodeURIComponent(query)}`);
            if (!response.ok) throw new Error('Search failed');
            
            const patrons = await response.json();
            
            if (patrons.length === 0) {
                resultsDiv.innerHTML = '<div class="patron-result-item">No patrons found</div>';
                return;
            }
            
            resultsDiv.innerHTML = patrons.map(patron => this.renderPatronResult(patron)).join('');
            
            // Add click handlers
            resultsDiv.querySelectorAll('.patron-result-item').forEach(item => {
                item.addEventListener('click', () => {
                    const patronId = item.getAttribute('data-patron-id');
                    const patronType = item.getAttribute('data-patron-type');
                    this.handlePatronSelect(patronId, patronType);
                });
            });
            
        } catch (error) {
            console.error('Search error:', error);
            resultsDiv.innerHTML = '<div class="patron-result-item">Error searching patrons</div>';
        }
    }
    
    renderPatronResult(patron) {
        let icon = 'fa-user', platformClass = 'patreon', platformName = 'Patreon';
        
        if (patron.role_type === 'team') {
            icon = 'fa-user-tie';
            platformClass = 'team';
            platformName = 'Team';
        } else if (patron.role_type === 'kofi') {
            icon = 'fa-coffee';
            platformClass = 'kofi';
            platformName = 'Ko-fi';
        }
        
        let bookRequestInfo = '';
        if (patron.book_requests) {
            bookRequestInfo = `<div class="download-stat"><span class="download-label">Book Requests:</span><span class="download-count">${patron.book_requests.used || 0}/${patron.book_requests.allowed || 0}</span></div>`;
        }
        
        let readAlongInfo = '';
        if (typeof patron.read_along_access !== 'undefined') {
            readAlongInfo = `<div class="download-stat"><span class="download-label">Read-Along:</span><span class="download-count">${patron.read_along_access ? 'Yes' : 'No'}</span></div>`;
        }
        
        return `
            <div class="patron-result-item" data-patron-id="${patron.id}" data-patron-type="${patron.role_type}">
                <div class="patron-result-info">
                    <i class="fas ${icon}"></i>
                    <span>${this.escapeHtml(patron.name)}</span>
                    <span class="tier-amount">${this.escapeHtml(patron.email)}</span>
                    <span class="user-platform ${platformClass}">${platformName}</span>
                </div>
                <div class="patron-info-details">
                    <div class="tier-info"><span class="tier-title">${this.escapeHtml(patron.tier_title)}</span></div>
                    <div class="downloads-info">
                        <div class="download-stat"><span class="download-label">Albums:</span><span class="download-count">${patron.downloads.albums.used}/${patron.downloads.albums.allowed}</span></div>
                        <div class="download-stat"><span class="download-label">Tracks:</span><span class="download-count">${patron.downloads.tracks.used}/${patron.downloads.tracks.allowed}</span></div>
                        ${bookRequestInfo}
                        <div class="download-stat"><span class="download-label">Chapters/Request:</span><span class="download-count">${patron.chapters_allowed_per_book_request || 'Not set'}</span></div>
                        ${readAlongInfo}
                    </div>
                </div>
            </div>
        `;
    }
    
    async handlePatronSelect(patronId, patronType) {
        this.selectedPatronId = patronId;
        this.selectedPatronType = patronType || 'patreon';
        const formDiv = document.getElementById('patronBenefitForm');
        
        try {
            const response = await fetch(`/api/creator/patrons/${patronId}/benefits`);
            if (!response.ok) throw new Error('Failed to fetch benefits');
            
            const patron = await response.json();
            
            document.getElementById('selectedPatronName').textContent = patron.name;
            document.getElementById('selectedPatronTier').textContent = patron.tier_title;
            
            const typeLabel = document.getElementById('selectedPatronType');
            if (this.selectedPatronType === 'kofi') {
                typeLabel.innerHTML = '<span class="tier-badge kofi">Ko-fi</span>';
            } else if (this.selectedPatronType === 'team') {
                typeLabel.innerHTML = '<span class="tier-badge team">Team</span>';
            } else {
                typeLabel.innerHTML = '<span class="tier-badge patreon">Patreon</span>';
            }
            
            const inputs = {
                'patronAlbumInput': patron.album_downloads_allowed,
                'patronTrackInput': patron.track_downloads_allowed,
                'patronBookRequestInput': patron.book_requests_allowed || 0,
                'patronChaptersInput': patron.chapters_allowed_per_book_request || 0,
                'patronSessionInput': patron.max_sessions || 1
            };
            
            Object.entries(inputs).forEach(([id, value]) => {
                const input = document.getElementById(id);
                if (input) {
                    input.value = value;
                    input.dataset.defaultValue = value;
                }
            });
            
            const readAlongInput = document.getElementById('patronReadAlongInput');
            if (readAlongInput) {
                readAlongInput.checked = patron.read_along_access || false;
                readAlongInput.dataset.defaultChecked = patron.read_along_access || false;
            }
            
            formDiv.style.display = 'block';
            document.getElementById('patronUpdateBtn').disabled = true;
            
        } catch (error) {
            console.error('Error:', error);
            alert('Error loading patron benefits');
        }
    }
    
    handlePatronInputChange(input) {
        const inputId = input.id;
        if (inputId === 'patronSessionInput') {
            const value = parseInt(input.value);
            if (value < 1) input.value = 1;
            if (value > 5) input.value = 5;
        }
        
        this.updatePatronSaveButtonState();
    }
    
    handlePatronCheckboxChange() {
        this.updatePatronSaveButtonState();
    }
    
    updatePatronSaveButtonState() {
        const saveBtn = document.getElementById('patronUpdateBtn');
        if (!saveBtn) return;
        
        const patronInputs = ['patronAlbumInput', 'patronTrackInput', 'patronBookRequestInput', 'patronChaptersInput', 'patronSessionInput'];
        const anyChanged = patronInputs.some(id => {
            const input = document.getElementById(id);
            return input && input.dataset.defaultValue && parseInt(input.value || 0) !== parseInt(input.dataset.defaultValue || 0);
        });
        
        const readAlongInput = document.getElementById('patronReadAlongInput');
        const readAlongChanged = readAlongInput && readAlongInput.checked !== (readAlongInput.dataset.defaultChecked === 'true');
        
        saveBtn.disabled = !(anyChanged || readAlongChanged);
    }
    
    async handlePatronUpdate() {
        if (!this.selectedPatronId) return;
        
        const updateBtn = document.getElementById('patronUpdateBtn');
        const originalBtnHtml = updateBtn.innerHTML;
        
        try {
            updateBtn.disabled = true;
            updateBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
            
            const response = await fetch(`/api/creator/patrons/${this.selectedPatronId}/benefits`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    album_downloads_allowed: parseInt(document.getElementById('patronAlbumInput').value),
                    track_downloads_allowed: parseInt(document.getElementById('patronTrackInput').value),
                    book_requests_allowed: parseInt(document.getElementById('patronBookRequestInput').value || 0),
                    chapters_allowed_per_book_request: parseInt(document.getElementById('patronChaptersInput').value || 0),
                    max_sessions: parseInt(document.getElementById('patronSessionInput').value || 1),
                    read_along_access: document.getElementById('patronReadAlongInput').checked
                })
            });
            
            if (!response.ok) throw new Error('Failed to update benefits');
            
            alert('Benefits updated successfully');
            
            document.querySelectorAll('#patronBenefitForm .setting-input').forEach(input => {
                input.dataset.defaultValue = input.value;
            });
            
            const readAlongInput = document.getElementById('patronReadAlongInput');
            if (readAlongInput) readAlongInput.dataset.defaultChecked = readAlongInput.checked;
            
        } catch (error) {
            console.error('Error:', error);
            alert('Error updating patron benefits');
        } finally {
            updateBtn.disabled = false;
            updateBtn.innerHTML = originalBtnHtml;
        }
    }
    
    // ==================== RESET FUNCTIONS ====================
    
    async handleResetAllSessions() {
        if (!confirm("This will end all active sessions. All users will need to log in again. Continue?")) return;
        
        try {
            const response = await fetch('/api/creator/sessions/reset', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            });
            
            if (!response.ok) throw new Error('Failed to reset sessions');
            
            window.location.reload();
        } catch (error) {
            console.error('Error:', error);
            alert('Error resetting sessions. Please try again.');
        }
    }
    
    async handleResetAll() {
        if (!confirm("This will reset download counts for all patrons back to their allowed amounts. Continue?")) return;
        
        try {
            const response = await fetch('/api/creator/downloads/reset', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            });
            
            if (!response.ok) throw new Error('Failed to reset downloads');
            
            window.location.reload();
        } catch (error) {
            console.error('Error:', error);
            alert('Error resetting downloads. Please try again.');
        }
    }
    
    async handleResetAllBookRequests() {
        if (!confirm("This will mark all pending book requests for the current month as rejected. Users will be able to submit new requests for their monthly quota. Continue?")) return;
        
        try {
            const response = await fetch('/api/creator/book-requests/reset', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            });
            
            if (!response.ok) throw new Error('Failed to reset book requests');
            
            const result = await response.json();
            alert(`Successfully reset ${result.reset_count} pending book requests.`);
        } catch (error) {
            console.error('Error:', error);
            alert('Error resetting book requests. Please try again.');
        }
    }



    // ==================== VOICE MANAGEMENT ====================
    
    switchPlatform(platform) {
        document.querySelectorAll('.platform-tab').forEach(tab => tab.classList.remove('active'));
        document.querySelector(`.platform-tab[data-platform="${platform}"]`)?.classList.add('active');
        
        document.querySelectorAll('.platform-section').forEach(section => section.classList.remove('active'));
        document.getElementById(platform === 'patreon' ? 'patreonSection' : 'kofiSection')?.classList.add('active');
    }
    
    switchVoiceTab(tab) {
        document.querySelectorAll('.voice-tab').forEach(tabEl => tabEl.classList.remove('active'));
        document.querySelector(`.voice-tab[data-voice-tab="${tab}"]`)?.classList.add('active');
        
        document.querySelectorAll('.voice-section').forEach(section => section.classList.remove('active'));
        
        if (tab === 'assign') {
            document.getElementById('voiceAssignSection')?.classList.add('active');
            this.loadVoicesForAssignment();
            this.loadTiersForAssignment();
        } else {
            document.getElementById('voiceManageSection')?.classList.add('active');
            this.loadAllVoices();
        }
    }
    
    async openVoiceManagementModal() {
        document.getElementById('voiceManagementModal').style.display = 'flex';
        await this.loadVoicesForAssignment();
        await this.loadTiersForAssignment();
    }
    
    async loadVoicesForAssignment() {
        try {
            const container = document.getElementById('voiceSelectionList');
            if (!container) return;
            
            container.innerHTML = '<div style="padding: 1rem; text-align: center;">Loading voices...</div>';
            
            const response = await fetch('/api/creator/voices');
            const data = await response.json();
            
            if (data.voices.length === 0) {
                container.innerHTML = '<div style="padding: 1rem; text-align: center;">No voices available</div>';
                return;
            }
            
            let html = '';
            data.voices.forEach((voice, index) => {
                let tierBadgesHtml = '';
                if (voice.assigned_tiers && voice.assigned_tiers.length > 0) {
                    voice.assigned_tiers.forEach(tier => {
                        const badgeClass = tier.is_kofi ? 'kofi' : tier.tier_title.toLowerCase().includes('team') ? 'team' : 'patreon';
                        tierBadgesHtml += `<span class="voice-tier-badge ${badgeClass}">${this.escapeHtml(tier.tier_title)}</span>`;
                    });
                } else {
                    tierBadgesHtml = '<span class="voice-tier-badge default">No Tiers</span>';
                }
                
                html += `
                    <div class="voice-select-item" data-index="${index}">
                        <label class="voice-select-label">
                            <input type="checkbox" name="selected_voices" value="${this.escapeHtml(voice.voice_id)}" class="voice-select-checkbox">
                            <div class="voice-select-info">
                                <div class="voice-select-name">${this.escapeHtml(voice.display_name)}</div>
                                <div class="voice-select-details">
                                    <span>${this.escapeHtml(voice.voice_id)}</span>
                                    <span class="voice-language-badge">${this.escapeHtml(voice.language_code)}</span>
                                    <span>${this.escapeHtml(voice.gender)}</span>
                                </div>
                                ${tierBadgesHtml ? `<div class="voice-tier-badges">${tierBadgesHtml}</div>` : ''}
                            </div>
                        </label>
                    </div>
                `;
            });
            
            container.innerHTML = html;
            
            document.querySelectorAll('.voice-select-item').forEach(voiceItem => {
                const checkbox = voiceItem.querySelector('input[type="checkbox"]');
                this.addVoiceItemListeners(voiceItem, checkbox);
            });
            
            this.initializeVoiceDragSelect();
            this.updateVoiceSelectionCounter();
            
        } catch (error) {
            console.error('Error loading voices:', error);
            const container = document.getElementById('voiceSelectionList');
            if (container) {
                container.innerHTML = '<div style="color: #EF4444; padding: 1rem; text-align: center;">Error loading voices</div>';
            }
        }
    }
    
    async loadTiersForAssignment() {
        try {
            const select = document.getElementById('voiceTierSelect');
            if (!select) return;
            
            select.innerHTML = '<option value="">Select a tier...</option>';
            
            const tiers = [];
            document.querySelectorAll('.tier-settings[data-tier-title]').forEach(tierEl => {
                const tierTitle = tierEl.getAttribute('data-tier-title');
                const tierAmountEl = tierEl.querySelector('.tier-amount');
                const patronCountEl = tierEl.querySelector('.patron-count');
                
                if (tierTitle) {
                    const tierAmount = tierAmountEl ? tierAmountEl.textContent : '$0.00/month';
                    const patronCount = patronCountEl ? patronCountEl.textContent : '0 patrons';
                    tiers.push({ title: tierTitle, amount_display: tierAmount, patron_count: patronCount });
                }
            });
            
            tiers.forEach(tier => {
                const option = document.createElement('option');
                option.value = tier.title;
                option.textContent = `${tier.title} - ${tier.amount_display} (${tier.patron_count})`;
                select.appendChild(option);
            });
            
        } catch (error) {
            console.error('Error loading tiers:', error);
        }
    }
    
    updateVoiceSelectionCounter() {
        const checkboxes = document.querySelectorAll('input[name="selected_voices"]');
        const checkedCount = document.querySelectorAll('input[name="selected_voices"]:checked').length;
        const selectAllCheckbox = document.getElementById('selectAllVoices');
        
        if (selectAllCheckbox) {
            selectAllCheckbox.indeterminate = checkedCount > 0 && checkedCount < checkboxes.length;
            selectAllCheckbox.checked = checkedCount === checkboxes.length && checkboxes.length > 0;
        }
        
        const assignBtn = document.getElementById('assignVoicesBtn');
        if (assignBtn) {
            assignBtn.textContent = checkedCount > 0 ? `Assign ${checkedCount} Voice${checkedCount !== 1 ? 's' : ''}` : 'Assign Selected Voices';
            assignBtn.disabled = checkedCount === 0;
        }
    }
    
    async assignVoicesToTier() {
        const selectedVoices = Array.from(document.querySelectorAll('input[name="selected_voices"]:checked')).map(cb => cb.value);
        const tierSelect = document.getElementById('voiceTierSelect');
        const selectedTier = tierSelect.value;
        
        if (selectedVoices.length === 0) {
            alert('Please select at least one voice');
            return;
        }
        
        if (!selectedTier) {
            alert('Please select a tier');
            return;
        }
        
        try {
            const assignBtn = document.getElementById('assignVoicesBtn');
            const originalText = assignBtn.textContent;
            assignBtn.disabled = true;
            assignBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Assigning...';
            
            const response = await fetch(`/api/creator/tiers/${encodeURIComponent(selectedTier)}/voices`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ action: 'add', voice_ids: selectedVoices })
            });
            
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || `Failed to assign voices: ${response.status}`);
            }
            
            document.querySelectorAll('input[name="selected_voices"]:checked').forEach(cb => {
                cb.checked = false;
                const voiceItem = cb.closest('.voice-select-item');
                if (voiceItem) voiceItem.classList.remove('selected');
            });
            
            tierSelect.value = '';
            
            await this.loadVoicesForAssignment();
            this.updateVoiceSelectionCounter();
            
            const successDiv = document.getElementById('voiceAssignmentSuccess');
            const successMessage = document.getElementById('voiceSuccessMessage');
            successMessage.textContent = `Successfully assigned ${selectedVoices.length} voice(s) to ${selectedTier}`;
            successDiv.style.display = 'block';
            setTimeout(() => successDiv.style.display = 'none', 4000);
            
        } catch (error) {
            console.error('Error assigning voices:', error);
            alert(`Error assigning voices: ${error.message}`);
        } finally {
            const assignBtn = document.getElementById('assignVoicesBtn');
            assignBtn.disabled = false;
            assignBtn.textContent = 'Assign Selected Voices';
        }
    }
    
    async loadAllVoices() {
        try {
            const response = await fetch('/api/creator/voices');
            const data = await response.json();
            const container = document.getElementById('voiceListContainer');
            
            if (!container) return;
            
            if (data.voices.length === 0) {
                container.innerHTML = '<div>No voices available</div>';
                return;
            }
            
            let html = '<div class="all-voices-list">';
            data.voices.forEach(voice => {
                let tierBadgesHtml = '';
                if (voice.assigned_tiers && voice.assigned_tiers.length > 0) {
                    voice.assigned_tiers.forEach(tier => {
                        const badgeClass = tier.is_kofi ? 'kofi' : tier.tier_title.toLowerCase().includes('team') ? 'team' : 'patreon';
                        tierBadgesHtml += `<span class="voice-tier-badge ${badgeClass}" style="margin-left: 0.5rem;">${this.escapeHtml(tier.tier_title)}</span>`;
                    });
                } else {
                    tierBadgesHtml = '<span class="voice-tier-badge default" style="margin-left: 0.5rem;">No Tiers</span>';
                }
                
                html += `
                    <div class="voice-item">
                        <div class="voice-info">
                            <div class="voice-name">${this.escapeHtml(voice.display_name)}${tierBadgesHtml}</div>
                            <div class="voice-details">${this.escapeHtml(voice.voice_id)} • ${this.escapeHtml(voice.language_code)} • ${this.escapeHtml(voice.gender)}</div>
                        </div>
                        <button class="voice-action-btn voice-delete-btn" data-voice-id="${this.escapeHtml(voice.voice_id)}">
                            <i class="fas fa-trash"></i> Delete
                        </button>
                    </div>
                `;
            });
            html += '</div>';
            
            container.innerHTML = html;
            
            // Add delete listeners
            container.querySelectorAll('.voice-delete-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const voiceId = btn.getAttribute('data-voice-id');
                    this.deleteVoice(voiceId);
                });
            });
            
        } catch (error) {
            console.error('Error loading voices:', error);
            const container = document.getElementById('voiceListContainer');
            if (container) {
                container.innerHTML = '<div style="color: #EF4444;">Error loading voices</div>';
            }
        }
    }
    
    async handleAddVoice() {
        try {
            const voiceId = document.getElementById('newVoiceId').value.trim();
            const displayName = document.getElementById('newVoiceDisplayName').value.trim();
            const language = document.getElementById('newVoiceLanguage').value;
            const gender = document.getElementById('newVoiceGender').value;
            
            if (!voiceId || !displayName) {
                alert('Please fill in all required fields');
                return;
            }
            
            const response = await fetch('/api/creator/voices', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    voice_id: voiceId,
                    display_name: displayName,
                    language_code: language,
                    gender: gender
                })
            });
            
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to add voice');
            }
            
            document.getElementById('addVoiceForm').reset();
            await this.loadAllVoices();
            
            if (document.getElementById('voiceAssignSection').classList.contains('active')) {
                await this.loadVoicesForAssignment();
            }
            
            alert('Voice added successfully');
            
        } catch (error) {
            console.error('Error adding voice:', error);
            alert(`Error adding voice: ${error.message}`);
        }
    }
    
    async deleteVoice(voiceId) {
        if (!confirm(`Are you sure you want to delete the voice "${voiceId}"?`)) return;
        
        try {
            const response = await fetch(`/api/creator/voices/${voiceId}`, {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'}
            });
            
            if (!response.ok) throw new Error('Failed to delete voice');
            
            await this.loadAllVoices();
            
            if (document.getElementById('voiceAssignSection').classList.contains('active')) {
                await this.loadVoicesForAssignment();
            }
            
            alert('Voice deleted successfully');
            
        } catch (error) {
            console.error('Error deleting voice:', error);
            alert('Error deleting voice');
        }
    }
    
    // ==================== VOICE DRAG SELECT ====================
    
    injectVoiceDragStyles() {
        if (!document.getElementById('voice-drag-select-styles')) {
            const style = document.createElement('style');
            style.id = 'voice-drag-select-styles';
            style.textContent = '.voice-selection-list.dragging * { user-select: none !important; -webkit-user-select: none !important; }';
            document.head.appendChild(style);
        }
    }
    
    initializeVoiceDragSelect() {
        const voiceList = document.getElementById('voiceSelectionList');
        if (!voiceList) return;
        
        voiceList.addEventListener('touchstart', (e) => this.handleVoiceTouchStart(e), { passive: false });
        voiceList.addEventListener('touchmove', (e) => this.handleVoiceTouchMove(e), { passive: false });
        voiceList.addEventListener('touchend', (e) => this.handleVoiceTouchEnd(e), { passive: false });
        voiceList.addEventListener('touchcancel', (e) => this.handleVoiceTouchEnd(e), { passive: false });
    }
    
    addVoiceItemListeners(voiceItem, checkbox) {
        if (!voiceItem.hasAttribute('data-index')) {
            const currentItems = document.querySelectorAll('.voice-select-item');
            voiceItem.setAttribute('data-index', currentItems.length);
        }
        
        voiceItem.addEventListener('click', (e) => {
            if (e.target.type === 'checkbox') return;
            if (voiceItem.dataset.skipClick) {
                delete voiceItem.dataset.skipClick;
                return;
            }
            checkbox.checked = !checkbox.checked;
            voiceItem.classList.toggle('selected', checkbox.checked);
            this.updateVoiceSelectionCounter();
            e.preventDefault();
        });
        
        voiceItem.addEventListener('pointerdown', (e) => {
            if (e.pointerType === 'touch' || e.target.type === 'checkbox') return;
            voiceItem.dataset.skipClick = "true";
            voiceItem.setPointerCapture(e.pointerId);
            
            this.voiceDragState.initialState = checkbox.checked;
            this.voiceDragState.baselineIndex = parseInt(voiceItem.getAttribute('data-index'));
            this.voiceDragState.originalStates = {};
            
            document.querySelectorAll('.voice-select-item').forEach(item => {
                const index = parseInt(item.getAttribute('data-index'));
                const itemCheckbox = item.querySelector('input[type="checkbox"]');
                this.voiceDragState.originalStates[index] = itemCheckbox.checked;
            });
            
            this.voiceDragState.isDragging = true;
            checkbox.checked = !this.voiceDragState.initialState;
            voiceItem.classList.toggle('selected', checkbox.checked);
            this.updateVoiceSelectionCounter();
            e.preventDefault();
        });
        
        voiceItem.addEventListener('pointerup', (e) => {
            if (e.pointerType !== 'touch') voiceItem.releasePointerCapture(e.pointerId);
        });
        
        checkbox.addEventListener('change', () => {
            voiceItem.classList.toggle('selected', checkbox.checked);
            this.updateVoiceSelectionCounter();
        });
    }
    
    handleVoiceTouchStart(e) {
        if (e.touches.length !== 1) return;
        
        const touch = e.touches[0];
        this.voiceDragState.touchStartY = touch.clientY;
        this.voiceDragState.touchStartX = touch.clientX;
        this.voiceDragState.touchMoving = false;
        this.voiceDragState.isLongPress = false;
        
        if (this.voiceDragState.longPressTimer) {
            clearTimeout(this.voiceDragState.longPressTimer);
        }
        
        const voiceItem = e.target.closest('.voice-select-item');
        if (!voiceItem) return;
        
        const isCheckboxTouch = e.target.type === 'checkbox';
        this.voiceDragState.touchStartItem = voiceItem;
        
        if (isCheckboxTouch) {
            setTimeout(() => this.updateVoiceSelectionCounter(), 0);
            return;
        }
        
        e.preventDefault();
        
        this.voiceDragState.longPressTimer = setTimeout(() => {
            if (!this.voiceDragState.touchMoving) {
                this.startVoiceDragSelect(voiceItem);
            }
        }, 100);
    }
    
    handleVoiceTouchMove(e) {
        if (this.voiceDragState.isDragging || this.voiceDragState.isLongPress) {
            e.preventDefault();
        }
        
        if (this.voiceDragState.longPressTimer) {
            clearTimeout(this.voiceDragState.longPressTimer);
            this.voiceDragState.longPressTimer = null;
        }
        
        if (!this.voiceDragState.touchStartY || !this.voiceDragState.touchStartItem || e.touches.length !== 1) return;
        
        const touch = e.touches[0];
        this.voiceDragState.lastTouchPosition = { x: touch.clientX, y: touch.clientY };
        
        const moveY = touch.clientY - this.voiceDragState.touchStartY;
        const moveX = touch.clientX - this.voiceDragState.touchStartX;
        const moveDistance = Math.sqrt(moveY * moveY + moveX * moveX);
        
        if (!this.voiceDragState.touchMoving && moveDistance < 15) return;
        
        if (!this.voiceDragState.touchMoving) {
            this.voiceDragState.touchMoving = true;
            if (!this.voiceDragState.isLongPress && !this.voiceDragState.isDragging) return;
        }
        
        if (!this.voiceDragState.isDragging && !this.voiceDragState.isLongPress) return;
        
        e.preventDefault();
        this.handleVoiceAutoScroll(touch);
        this.updateVoiceSelectionFromTouch(touch);
    }
    
    handleVoiceTouchEnd(e) {
        if (this.voiceDragState.longPressTimer) {
            clearTimeout(this.voiceDragState.longPressTimer);
            this.voiceDragState.longPressTimer = null;
        }
        
        this.stopVoiceAutoScroll();
        
        document.querySelectorAll('.voice-select-item').forEach(item => {
            item.classList.remove('long-press-active', 'drag-select-active');
        });
        
        const voiceList = document.getElementById('voiceSelectionList');
        if (voiceList) voiceList.classList.remove('dragging');
        
        if (this.voiceDragState.touchStartItem && !this.voiceDragState.touchMoving && 
            !this.voiceDragState.isDragging && !this.voiceDragState.isLongPress) {
            e.preventDefault();
            const checkbox = this.voiceDragState.touchStartItem.querySelector('input[type="checkbox"]');
            if (checkbox) {
                checkbox.checked = !checkbox.checked;
                this.voiceDragState.touchStartItem.classList.toggle('selected', checkbox.checked);
                this.updateVoiceSelectionCounter();
            }
        }
        
        this.resetVoiceDragState();
    }
    
    startVoiceDragSelect(voiceItem) {
        this.voiceDragState.isLongPress = true;
        this.voiceDragState.isDragging = true;
        voiceItem.classList.add('long-press-active', 'drag-select-active');
        
        const voiceList = document.getElementById('voiceSelectionList');
        if (voiceList) voiceList.classList.add('dragging');
        
        if (navigator.vibrate) navigator.vibrate(50);
        
        const checkbox = voiceItem.querySelector('input[type="checkbox"]');
        if (checkbox) {
            this.voiceDragState.initialState = checkbox.checked;
            this.voiceDragState.baselineIndex = parseInt(voiceItem.getAttribute('data-index'));
            this.voiceDragState.lastTouchIndex = this.voiceDragState.baselineIndex;
            this.voiceDragState.originalStates = {};
            
            document.querySelectorAll('.voice-select-item').forEach(item => {
                const index = parseInt(item.getAttribute('data-index'));
                const itemCheckbox = item.querySelector('input[type="checkbox"]');
                if (itemCheckbox) this.voiceDragState.originalStates[index] = itemCheckbox.checked;
            });
            
            checkbox.checked = !this.voiceDragState.initialState;
            voiceItem.classList.toggle('selected', checkbox.checked);
            this.updateVoiceSelectionCounter();
        }
    }
    
    handleVoiceAutoScroll(touch) {
        const voiceList = document.getElementById('voiceSelectionList');
        if (!voiceList) return;
        
        const rect = voiceList.getBoundingClientRect();
        
        if (touch.clientY < rect.top + 40) {
            this.voiceDragState.autoScrollDirection = -1;
            this.voiceDragState.autoScrollSpeed = Math.min(40 - (touch.clientY - rect.top), 40) * 1.5;
            this.startVoiceAutoScroll();
        } else if (touch.clientY > rect.bottom - 40) {
            this.voiceDragState.autoScrollDirection = 1;
            this.voiceDragState.autoScrollSpeed = Math.min(touch.clientY - (rect.bottom - 40), 40) * 1.5;
            this.startVoiceAutoScroll();
        } else {
            this.stopVoiceAutoScroll();
        }
    }
    
    startVoiceAutoScroll() {
        if (!this.voiceDragState.autoScrolling) {
            this.voiceDragState.autoScrolling = true;
            this.animateVoiceAutoScroll();
        }
    }
    
    stopVoiceAutoScroll() {
        this.voiceDragState.autoScrolling = false;
        this.voiceDragState.autoScrollDirection = 0;
        this.voiceDragState.autoScrollSpeed = 0;
        
        if (this.voiceDragState.autoScrollFrame) {
            cancelAnimationFrame(this.voiceDragState.autoScrollFrame);
            this.voiceDragState.autoScrollFrame = null;
        }
    }
    
    animateVoiceAutoScroll() {
        if (!this.voiceDragState.autoScrolling) return;
        
        const voiceList = document.getElementById('voiceSelectionList');
        if (voiceList && this.voiceDragState.autoScrollDirection !== 0) {
            voiceList.scrollTop += this.voiceDragState.autoScrollDirection * this.voiceDragState.autoScrollSpeed;
            
            if (this.voiceDragState.isDragging || this.voiceDragState.isLongPress) {
                this.updateVoiceSelectionFromScroll();
            } else {
                this.stopVoiceAutoScroll();
                return;
            }
        }
        
        this.voiceDragState.autoScrollFrame = requestAnimationFrame(() => this.animateVoiceAutoScroll());
    }
    
    updateVoiceSelectionFromTouch(touch) {
        const touchTarget = document.elementFromPoint(touch.clientX, touch.clientY);
        let currentItem = touchTarget ? touchTarget.closest('.voice-select-item') : null;
        
        if (!currentItem) {
            const aboveElement = document.elementFromPoint(touch.clientX, touch.clientY - 20);
            const belowElement = document.elementFromPoint(touch.clientX, touch.clientY + 20);
            if (aboveElement) currentItem = aboveElement.closest('.voice-select-item');
            if (!currentItem && belowElement) currentItem = belowElement.closest('.voice-select-item');
        }
        
        if (currentItem) {
            const currentIndex = parseInt(currentItem.getAttribute('data-index'));
            if (!isNaN(currentIndex) && currentIndex !== this.voiceDragState.lastTouchIndex) {
                this.voiceDragState.lastTouchIndex = currentIndex;
                this.updateVoiceSelectionRange(this.voiceDragState.baselineIndex, currentIndex);
            }
        }
    }
    
    updateVoiceSelectionFromScroll() {
        if (this.voiceDragState.lastTouchPosition) {
            const touchTarget = document.elementFromPoint(
                this.voiceDragState.lastTouchPosition.x,
                this.voiceDragState.lastTouchPosition.y
            );
            
            if (touchTarget) {
                let currentItem = touchTarget.closest('.voice-select-item');
                if (currentItem) {
                    const currentIndex = parseInt(currentItem.getAttribute('data-index'));
                    if (!isNaN(currentIndex) && currentIndex !== this.voiceDragState.lastTouchIndex) {
                        this.voiceDragState.lastTouchIndex = currentIndex;
                        this.updateVoiceSelectionRange(this.voiceDragState.baselineIndex, currentIndex);
                    }
                }
            }
        }
    }
    
    updateVoiceSelectionRange(fromIndex, toIndex) {
        const items = document.querySelectorAll('.voice-select-item');
        const minIndex = Math.min(fromIndex, toIndex);
        const maxIndex = Math.max(fromIndex, toIndex);
        
        items.forEach((item, index) => {
            const checkbox = item.querySelector('input[type="checkbox"]');
            if (!checkbox) return;
            
            if (minIndex === maxIndex) {
                checkbox.checked = this.voiceDragState.originalStates[index];
                item.classList.toggle('selected', checkbox.checked);
            } else if (index >= minIndex && index <= maxIndex) {
                checkbox.checked = !this.voiceDragState.initialState;
                item.classList.toggle('selected', checkbox.checked);
            } else {
                checkbox.checked = this.voiceDragState.originalStates[index];
                item.classList.toggle('selected', checkbox.checked);
            }
        });
        
        this.updateVoiceSelectionCounter();
    }
    
    resetVoiceDragState() {
        this.voiceDragState = this.initVoiceDragState();
    }
    
    // ==================== UTILITIES ====================
    
    escapeHtml(unsafe) {
        if (unsafe === null || unsafe === undefined) return '';
        return String(unsafe)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
}

// Setup pointer move/up listeners for voice drag (global)
document.addEventListener('pointermove', function(e) {
    // This will be handled by the controller instance
    // We need to expose this somehow - for now, attach to window
    if (window.benefitsController && e.pointerType !== 'touch') {
        const controller = window.benefitsController;
        const state = controller.voiceDragState;
        
        if (!state.isDragging || state.baselineIndex === null) return;
        
        state.lastMousePosition = { x: e.clientX, y: e.clientY };
        
        const voiceList = document.getElementById('voiceSelectionList');
        if (voiceList) {
            const rect = voiceList.getBoundingClientRect();
            if (e.clientY < rect.top + 40) {
                state.autoScrollDirection = -1;
                state.autoScrollSpeed = Math.min(40 - (e.clientY - rect.top), 40) * 1.5;
                controller.startVoiceAutoScroll();
            } else if (e.clientY > rect.bottom - 40) {
                state.autoScrollDirection = 1;
                state.autoScrollSpeed = Math.min(e.clientY - (rect.bottom - 40), 40) * 1.5;
                controller.startVoiceAutoScroll();
            } else {
                controller.stopVoiceAutoScroll();
            }
        }
        
        let currentItem = document.elementFromPoint(e.clientX, e.clientY)?.closest('.voice-select-item');
        let currentIndex = null;
        
        if (currentItem) {
            currentIndex = parseInt(currentItem.getAttribute('data-index'));
            if (!isNaN(currentIndex)) state.lastMouseIndex = currentIndex;
        }
        
        if (currentIndex !== null) {
            if (!state.originalStates) {
                state.originalStates = {};
                document.querySelectorAll('.voice-select-item').forEach(item => {
                    const index = parseInt(item.getAttribute('data-index'));
                    const checkbox = item.querySelector('input[type="checkbox"]');
                    state.originalStates[index] = checkbox.checked;
                });
            }
            controller.updateVoiceSelectionRange(state.baselineIndex, currentIndex);
        }
    }
}, { passive: false });

document.addEventListener('pointerup', function() {
    if (window.benefitsController) {
        window.benefitsController.resetVoiceDragState();
    }
});

document.addEventListener('pointercancel', function() {
    if (window.benefitsController) {
        window.benefitsController.resetVoiceDragState();
    }
});