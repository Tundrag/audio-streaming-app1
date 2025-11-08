// my-downloads-shared-spa.js - Universal controller for My Downloads page (SSR and SPA modes)

export class MyDownloadsController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.currentTab = 'active';
        this.currentMonth = null;
        this.currentFilter = 'all';
        this.monthsData = new Map();
        this.activeDownloads = [];
        this.currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }

        try {
            return await this.generateHTML();
        } catch (error) {
            console.error('Error rendering My Downloads:', error);
            return this.errorHTML(error.message);
        }
    }

    // ✅ Generate HTML for SPA mode (CSS loaded separately)
    async generateHTML() {
        return `
            
            <div class="main-container">
                <!-- Tabs -->
                <div class="downloads-tabs">
                    <div class="downloads-tab active" data-tab="active">
                        <i class="fas fa-download"></i>
                        Active Downloads
                        <span class="tab-badge" id="activeTabBadge">0</span>
                    </div>
                    <div class="downloads-tab" data-tab="history">
                        <i class="fas fa-history"></i>
                        Download History
                    </div>
                    <div class="downloads-tab" data-tab="search" id="searchTab" style="display: none;">
                        <i class="fas fa-search"></i>
                        User Search
                    </div>
                </div>
                
                <!-- Search Section -->
                <div class="search-section" id="searchSection" style="display: none;">
                    <div class="search-input-group">
                        <input type="text" id="userSearchInput" class="search-input" placeholder="Search by name or email..." />
                        <button id="searchUsersBtn" class="search-btn">
                            <i class="fas fa-search"></i> Search
                        </button>
                    </div>
                    <div id="searchResults" class="search-results"></div>
                </div>
                
                <!-- Active Downloads -->
                <div class="active-downloads-section" id="activeDownloadsSection">
                    <div class="section-header">
                        <p>Your downloads are available for 24 hours. You can re-download any item during this period without using additional download credits. You are limited to 10 active downloads.</p>
                    </div>
                    
                    <div id="limitWarning" class="limit-warning" style="display: none;">
                        <i class="fas fa-exclamation-triangle"></i>
                        <div>
                            <h4>Download Limit Warning</h4>
                            <div id="warningMessage"></div>
                        </div>
                    </div>
                    
                    <div class="download-count">
                        <i class="fas fa-download"></i>
                        <span id="downloadsCount">0</span> / 10 downloads available
                    </div>
                    
                    <div id="downloadsContainer" class="downloads-container">
                        <div class="loading-spinner">
                            <i class="fas fa-spinner fa-spin"></i>
                            <span>Loading your downloads...</span>
                        </div>
                    </div>
                </div>
                
                <!-- History Section -->
                <div class="history-section" id="historySection" style="display: none;">
                    <div class="section-header">
                        <h3><i class="fas fa-history"></i> Download History</h3>
                        <p>Browse your download history by month. Only the last 6 months are shown.</p>
                    </div>
                    
                    <div id="monthsGrid" class="months-grid">
                        <div class="loading-spinner">
                            <i class="fas fa-spinner fa-spin"></i>
                            <span>Loading history...</span>
                        </div>
                    </div>
                    
                    <div id="monthDetailView" class="month-detail-view">
                        <div class="month-detail-header">
                            <button id="backToMonthsBtn" class="back-btn">
                                <i class="fas fa-arrow-left"></i>
                                Back to Months
                            </button>
                            <div>
                                <h3 id="monthDetailTitle" class="month-detail-title">January 2025</h3>
                                <div id="monthDetailStats" class="month-detail-stats">
                                    <span><i class="fas fa-download"></i> <span id="detailTotalCount">0</span> downloads</span>
                                    <span><i class="fas fa-compact-disc"></i> <span id="detailAlbumCount">0</span> albums</span>
                                    <span><i class="fas fa-music"></i> <span id="detailTrackCount">0</span> tracks</span>
                                </div>
                            </div>
                        </div>
                        <div class="history-filters">
                            <button class="filter-btn active" data-filter="all">All</button>
                            <button class="filter-btn" data-filter="success">Successful</button>
                            <button class="filter-btn" data-filter="error">Failed</button>
                            <button class="filter-btn" data-filter="album">Albums</button>
                            <button class="filter-btn" data-filter="track">Tracks</button>
                        </div>
                        <div id="monthDetailContent" class="month-detail-content">
                            <div class="loading-spinner">
                                <i class="fas fa-spinner fa-spin"></i>
                                <span>Loading month details...</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- User Modal -->
            <div id="userDownloadsModal" class="modal" style="display: none;">
                <div class="modal-content">
                    <div class="modal-header">
                        <h3 id="userDownloadsTitle">User Downloads</h3>
                        <button id="closeUserModal">&times;</button>
                    </div>
                    <div class="modal-body">
                        <div class="user-modal-tabs">
                            <div class="user-modal-tab active" data-user-tab="downloads">
                                <i class="fas fa-download"></i> Active Downloads
                            </div>
                            <div class="user-modal-tab" data-user-tab="history">
                                <i class="fas fa-history"></i> Download History
                            </div>
                        </div>
                        <div id="userModalContent" class="user-modal-content">
                            <div class="loading-spinner">
                                <i class="fas fa-spinner fa-spin"></i>
                                <span>Loading...</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`📥 MyDownloads: Mounting in ${this.mode} mode...`);

        // Check if user is creator
        const isCreator = window.isCreator || false;
        const searchTab = document.getElementById('searchTab');
        if (searchTab && isCreator) {
            searchTab.style.display = 'flex';
        }

        // Setup event listeners
        this.setupTabSwitching();
        this.setupSearch();
        this.setupHistoryNavigation();
        this.setupFilters();
        this.setupTimerUpdates();

        // Load initial data
        await this.fetchActiveDownloads();
        await this.fetchHistoryMonths();

        // ✅ CRITICAL: Expose globally for onclick handlers
        window.downloadsManager = this;

        console.log('✅ MyDownloads: Mounted successfully');

        this.updateActiveLinks('/my-downloads');
    }

    setupTabSwitching() {
        document.querySelectorAll('.downloads-tab').forEach(tab => {
            tab.addEventListener('click', () => this.switchTab(tab.dataset.tab));
        });
    }

    switchTab(tabName) {
        document.querySelectorAll('.downloads-tab').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.tab === tabName);
        });

        document.getElementById('activeDownloadsSection').style.display = tabName === 'active' ? 'block' : 'none';
        document.getElementById('historySection').style.display = tabName === 'history' ? 'block' : 'none';

        const searchSection = document.getElementById('searchSection');
        if (searchSection) {
            searchSection.style.display = tabName === 'search' ? 'block' : 'none';
        }

        this.currentTab = tabName;

        if (tabName === 'active') {
            this.fetchActiveDownloads();
        } else if (tabName === 'history') {
            this.fetchHistoryMonths();
        }
    }

    setupSearch() {
        const searchInput = document.getElementById('userSearchInput');
        const searchBtn = document.getElementById('searchUsersBtn');
        
        if (!searchInput || !searchBtn) return;

        const performSearch = () => this.searchUsers();
        searchBtn.addEventListener('click', performSearch);
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') performSearch();
        });
    }

    async searchUsers() {
        const query = document.getElementById('userSearchInput').value.trim();
        if (!query) return;

        const btn = document.getElementById('searchUsersBtn');
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Searching...';
        btn.disabled = true;

        try {
            const response = await fetch(`/api/creator/downloads/users/search?q=${encodeURIComponent(query)}`);
            const users = await response.json();
            this.renderSearchResults(users);
        } catch (error) {
            this.showMessage('Error searching users', 'error');
        } finally {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    }

    renderSearchResults(users) {
        const container = document.getElementById('searchResults');
        
        if (users.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-user-slash"></i>
                    <h3>No Users Found</h3>
                    <p>No users found matching your search criteria.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = users.map(user => `
            <div class="user-search-result">
                <div class="user-info">
                    <div class="user-name">${user.name || 'N/A'}</div>
                    <div class="user-email">${user.email}</div>
                </div>
                <div class="user-actions">
                    <button class="btn-small btn-view" onclick="window.downloadsManager.viewUserDownloads(${user.id}, '${(user.name || user.email).replace(/'/g, "\\'")}')">
                        <i class="fas fa-download"></i> Downloads
                    </button>
                    <button class="btn-small btn-view" onclick="window.downloadsManager.viewUserHistory(${user.id}, '${(user.name || user.email).replace(/'/g, "\\'")}')">
                        <i class="fas fa-history"></i> History
                    </button>
                </div>
            </div>
        `).join('');
    }

    async viewUserDownloads(userId, userName) {
        await this.showUserModal(userId, userName, 'downloads');
    }

    async viewUserHistory(userId, userName) {
        await this.showUserModal(userId, userName, 'history');
    }

    async showUserModal(userId, userName, type) {
        const modal = document.getElementById('userDownloadsModal');
        const title = document.getElementById('userDownloadsTitle');
        const content = document.getElementById('userModalContent');
        
        title.textContent = userName;
        content.innerHTML = '<div class="loading-spinner"><i class="fas fa-spinner fa-spin"></i><span>Loading...</span></div>';
        
        this.setupUserModalTabs(userId, userName);
        
        const tabs = modal.querySelectorAll('.user-modal-tab');
        tabs.forEach(tab => {
            tab.classList.toggle('active', tab.dataset.userTab === type);
        });
        
        modal.style.display = 'flex';

        const closeBtn = document.getElementById('closeUserModal');
        closeBtn.onclick = () => {
            modal.style.display = 'none';
            content.innerHTML = '';
        };

        modal.onclick = (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
                content.innerHTML = '';
            }
        };

        await this.loadUserModalContent(userId, type);
    }

    setupUserModalTabs(userId, userName) {
        const modal = document.getElementById('userDownloadsModal');
        const tabs = modal.querySelectorAll('.user-modal-tab');
        
        tabs.forEach(tab => {
            tab.onclick = async () => {
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                
                const tabType = tab.dataset.userTab;
                await this.loadUserModalContent(userId, tabType);
            };
        });
    }

    async loadUserModalContent(userId, type) {
        const content = document.getElementById('userModalContent');
        content.innerHTML = '<div class="loading-spinner"><i class="fas fa-spinner fa-spin"></i><span>Loading...</span></div>';

        try {
            const endpoint = type === 'downloads' 
                ? `/api/creator/users/${userId}/downloads`
                : `/api/creator/users/${userId}/history`;
            
            const response = await fetch(endpoint);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            
            if (type === 'downloads') {
                this.renderUserDownloads(data, content);
            } else {
                this.renderUserHistoryWithMonths(data, content);
            }
            
        } catch (error) {
            content.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-exclamation-circle"></i>
                    <h3>Error</h3>
                    <p>${error.message}</p>
                </div>
            `;
        }
    }

    renderUserDownloads(downloads, container) {
        if (!downloads || downloads.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-download"></i>
                    <h3>No Active Downloads</h3>
                    <p>This user has no active downloads.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = downloads.map(download => {
            const filename = download.original_filename || 'Unknown File';
            const type = download.type || 'unknown';
            const downloadedAt = new Date(download.downloaded_at).toLocaleString();
            const expiresAt = new Date(download.expires_at).toLocaleString();
            
            return `
                <div class="download-card">
                    <div class="download-content">
                        <div class="download-header">
                            <div>
                                <div class="download-title">${filename}</div>
                                <div class="download-subtitle">${type === 'album' ? 'Album' : 'Track'}</div>
                            </div>
                        </div>
                        <div class="download-footer">
                            <div class="download-meta">
                                Downloaded: ${downloadedAt}<br>
                                Expires: ${expiresAt}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    renderUserHistoryWithMonths(history, container) {
        if (!history || history.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-history"></i>
                    <h3>No Download History</h3>
                    <p>This user has no download history.</p>
                </div>
            `;
            return;
        }

        const monthsData = this.groupUserHistoryByMonth(history);
        
        if (monthsData.size === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-history"></i>
                    <h3>No Recent History</h3>
                    <p>This user has no download history in the last 6 months.</p>
                </div>
            `;
            return;
        }

        const monthsGrid = Array.from(monthsData.entries()).map(([key, data]) => {
            const dataJson = JSON.stringify(data).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
            return `
                <div class="user-month-card" onclick="window.downloadsManager.viewUserMonth('${key}', JSON.parse('${dataJson.replace(/'/g, "\\'")}'))">
                    <div class="user-month-name">${data.displayName}</div>
                    <div class="user-month-count">${data.stats.total}</div>
                    <div style="font-size: 0.7rem; color: var(--text-muted); margin-top: 4px;">
                        ${data.stats.success}✓ ${data.stats.errors}✗
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = `
            <div class="user-months-grid" id="userMonthsGrid">
                ${monthsGrid}
            </div>
            <div id="userMonthDetail" style="display: none;">
                <div style="margin-bottom: 16px;">
                    <button onclick="document.getElementById('userMonthDetail').style.display='none'; document.getElementById('userMonthsGrid').style.display='grid';" 
                            class="back-btn">
                        <i class="fas fa-arrow-left"></i> Back to Months
                    </button>
                </div>
                <div id="userMonthDetailContent"></div>
            </div>
        `;
    }

    groupUserHistoryByMonth(history) {
        const months = new Map();
        const now = new Date();
        
        for (let i = 0; i < 6; i++) {
            const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
            const key = `${(date.getMonth() + 1).toString().padStart(2, '0')}/${date.getFullYear().toString().slice(-2)}`;
            
            months.set(key, {
                displayName: date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' }),
                downloads: [],
                stats: { total: 0, albums: 0, tracks: 0, success: 0, errors: 0 }
            });
        }

        history.forEach(download => {
            const date = new Date(download.downloaded_at);
            const key = `${(date.getMonth() + 1).toString().padStart(2, '0')}/${date.getFullYear().toString().slice(-2)}`;
            
            if (months.has(key)) {
                const monthData = months.get(key);
                monthData.downloads.push(download);
                monthData.stats.total++;
                monthData.stats[download.download_type + 's']++;
                monthData.stats[download.status === 'success' ? 'success' : 'errors']++;
            }
        });

        for (const [key, data] of months.entries()) {
            if (data.stats.total === 0) {
                months.delete(key);
            }
        }

        return months;
    }

    viewUserMonth(monthKey, monthData) {
        const monthsGrid = document.getElementById('userMonthsGrid');
        const monthDetail = document.getElementById('userMonthDetail');
        const monthDetailContent = document.getElementById('userMonthDetailContent');

        if (monthsGrid) monthsGrid.style.display = 'none';
        if (monthDetail) monthDetail.style.display = 'block';

        if (monthData.downloads.length === 0) {
            monthDetailContent.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-download"></i>
                    <h3>No Downloads This Month</h3>
                    <p>No downloads in ${monthData.displayName}.</p>
                </div>
            `;
            return;
        }

        const historyItems = monthData.downloads.map(item => {
            const title = item.title || 'Unknown';
            const type = item.download_type || 'unknown';
            const status = item.status || 'unknown';
            const downloadedAt = new Date(item.downloaded_at).toLocaleString();
            const errorMessage = item.error_message || '';
            
            return `
                <div class="history-item ${status}">
                    <div class="history-icon ${type} ${status}">
                        <i class="fas fa-${type === 'album' ? 'compact-disc' : 'music'}"></i>
                    </div>
                    <div class="history-content">
                        <div class="history-title">
                            ${title}
                            <span class="history-type-badge">${type}</span>
                            ${item.voice_id ? `<span class="history-type-badge">${item.voice_id}</span>` : ''}
                        </div>
                        <div class="history-meta">
                            <span><i class="fas fa-calendar"></i> ${downloadedAt}</span>
                            <span class="history-status ${status}">
                                <i class="fas fa-${status === 'success' ? 'check' : 'times'}"></i>
                                ${status}
                            </span>
                        </div>
                        ${errorMessage ? `<div class="error-message">${errorMessage}</div>` : ''}
                    </div>
                </div>
            `;
        }).join('');

        monthDetailContent.innerHTML = `
            <div style="margin-bottom: 16px; padding: 12px; background: var(--card-bg); border-radius: 8px;">
                <h4 style="margin: 0 0 8px 0;">${monthData.displayName}</h4>
                <div style="font-size: 0.9rem; color: var(--text-muted);">
                    ${monthData.stats.total} downloads • ${monthData.stats.success} successful • ${monthData.stats.errors} failed
                </div>
            </div>
            ${historyItems}
        `;
    }

    async fetchActiveDownloads() {
        try {
            const response = await fetch('/api/my-downloads');
            if (!response.ok) throw new Error('Failed to fetch downloads');
            
            const downloads = await response.json();
            this.activeDownloads = downloads;
            this.renderActiveDownloads(downloads);
            this.updateLimitWarning(downloads.length);
            document.getElementById('activeTabBadge').textContent = downloads.length;
        } catch (error) {
            document.getElementById('downloadsContainer').innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-exclamation-circle"></i>
                    <h3>Error Loading Downloads</h3>
                    <p>Please try refreshing the page.</p>
                </div>
            `;
        }
    }

    async fetchHistoryMonths() {
        try {
            const response = await fetch('/api/my-downloads/history?limit=1000');
            if (!response.ok) throw new Error('Failed to fetch history');
            
            const history = await response.json();
            const monthsData = this.groupHistoryByMonth(history);
            this.monthsData = monthsData;
            this.renderHistoryMonths(monthsData);
        } catch (error) {
            document.getElementById('monthsGrid').innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-exclamation-circle"></i>
                    <h3>Error Loading History</h3>
                    <p>Please try refreshing the page.</p>
                </div>
            `;
        }
    }

    groupHistoryByMonth(history) {
        const months = new Map();
        const now = new Date();
        
        for (let i = 0; i < 6; i++) {
            const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
            const key = `${(date.getMonth() + 1).toString().padStart(2, '0')}/${date.getFullYear().toString().slice(-2)}`;
            const fullKey = `${date.getFullYear()}-${(date.getMonth() + 1).toString().padStart(2, '0')}`;
            
            months.set(key, {
                fullKey,
                displayName: date.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }),
                downloads: [],
                stats: { total: 0, albums: 0, tracks: 0, success: 0, errors: 0 }
            });
        }

        history.forEach(download => {
            const date = new Date(download.downloaded_at);
            const key = `${(date.getMonth() + 1).toString().padStart(2, '0')}/${date.getFullYear().toString().slice(-2)}`;
            
            if (months.has(key)) {
                const monthData = months.get(key);
                monthData.downloads.push(download);
                monthData.stats.total++;
                monthData.stats[download.download_type + 's']++;
                monthData.stats[download.status === 'success' ? 'success' : 'errors']++;
            }
        });

        for (const [key, data] of months.entries()) {
            if (data.stats.total === 0) {
                months.delete(key);
            }
        }

        return months;
    }

    renderHistoryMonths(monthsData) {
        const container = document.getElementById('monthsGrid');
        
        if (monthsData.size === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-history"></i>
                    <h3>No Download History</h3>
                    <p>You haven't downloaded anything in the last 6 months.</p>
                </div>
            `;
            return;
        }

        const monthCards = Array.from(monthsData.entries()).map(([key, data]) => `
            <div class="month-card" data-month="${key}" onclick="window.downloadsManager.viewMonth('${key}')">
                <div class="month-header">
                    <div class="month-name">${data.displayName}</div>
                    <div class="month-count">${data.stats.total}</div>
                </div>
                <div class="month-stats">
                    <div class="month-breakdown">
                        <div class="stat-item stat-albums">
                            <i class="fas fa-compact-disc"></i>
                            ${data.stats.albums} albums
                        </div>
                        <div class="stat-item stat-tracks">
                            <i class="fas fa-music"></i>
                            ${data.stats.tracks} tracks
                        </div>
                    </div>
                    <div style="margin-top: 8px; font-size: 0.75rem;">
                        ${data.stats.success} successful, ${data.stats.errors} failed
                    </div>
                </div>
            </div>
        `).join('');

        container.innerHTML = monthCards;
    }

    viewMonth(monthKey) {
        const monthData = this.monthsData.get(monthKey);
        if (!monthData) return;

        this.currentMonth = monthKey;
        
        document.getElementById('monthsGrid').style.display = 'none';
        document.getElementById('monthDetailView').classList.add('active');
        
        document.getElementById('monthDetailTitle').textContent = monthData.displayName;
        document.getElementById('detailTotalCount').textContent = monthData.stats.total;
        document.getElementById('detailAlbumCount').textContent = monthData.stats.albums;
        document.getElementById('detailTrackCount').textContent = monthData.stats.tracks;
        
        this.renderMonthDetails(monthData.downloads);
    }

    renderMonthDetails(downloads) {
        const container = document.getElementById('monthDetailContent');
        
        if (downloads.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-download"></i>
                    <h3>No Downloads This Month</h3>
                    <p>You didn't download anything this month.</p>
                </div>
            `;
            return;
        }

        const filteredDownloads = this.applyHistoryFilter(downloads);
        
        if (filteredDownloads.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-filter"></i>
                    <h3>No Results</h3>
                    <p>No downloads match the current filter.</p>
                </div>
            `;
            return;
        }

        const historyItems = filteredDownloads.map(item => `
            <div class="history-item ${item.status}">
                <div class="history-icon ${item.download_type} ${item.status}">
                    <i class="fas fa-${item.download_type === 'album' ? 'compact-disc' : 'music'}"></i>
                </div>
                <div class="history-content">
                    <div class="history-title">
                        ${item.title}
                        <span class="history-type-badge">${item.download_type}</span>
                        ${item.voice_id ? `<span class="history-type-badge">${item.voice_id}</span>` : ''}
                    </div>
                    <div class="history-meta">
                        <span><i class="fas fa-calendar"></i> ${new Date(item.downloaded_at).toLocaleString()}</span>
                        <span class="history-status ${item.status}">
                            <i class="fas fa-${item.status === 'success' ? 'check' : 'times'}"></i>
                            ${item.status}
                        </span>
                    </div>
                    ${item.error_message ? `<div class="error-message">${item.error_message}</div>` : ''}
                </div>
            </div>
        `).join('');

        container.innerHTML = historyItems;
    }

    setupHistoryNavigation() {
        document.getElementById('backToMonthsBtn').addEventListener('click', () => {
            document.getElementById('monthDetailView').classList.remove('active');
            document.getElementById('monthsGrid').style.display = 'grid';
            this.currentMonth = null;
        });
    }

    setupFilters() {
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.currentFilter = btn.dataset.filter;
                
                if (this.currentMonth) {
                    const monthData = this.monthsData.get(this.currentMonth);
                    if (monthData) {
                        this.renderMonthDetails(monthData.downloads);
                    }
                }
            });
        });
    }

    applyHistoryFilter(downloads) {
        switch (this.currentFilter) {
            case 'success':
                return downloads.filter(d => d.status === 'success');
            case 'error':
                return downloads.filter(d => d.status !== 'success');
            case 'album':
                return downloads.filter(d => d.download_type === 'album');
            case 'track':
                return downloads.filter(d => d.download_type === 'track');
            default:
                return downloads;
        }
    }

    renderActiveDownloads(downloads) {
        const container = document.getElementById('downloadsContainer');
        
        if (!downloads || downloads.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-download"></i>
                    <h3>No downloads available</h3>
                    <p>You don't have any active downloads at the moment.</p>
                </div>
            `;
            return;
        }
        
        downloads.sort((a, b) => new Date(a.downloaded_at) - new Date(b.downloaded_at));
        
        if (downloads.length === 10) {
            downloads[0].isOldest = true;
        }
        
        const downloadsHtml = downloads.map((download, index) => {
            const isAlbum = download.type === 'album';
            const itemData = isAlbum ? download.album : download.track;
            
            if (!itemData) return '';
            
            const coverPath = isAlbum ? 
                (itemData.cover_path || '/static/images/default-album.jpg') : 
                (itemData.album?.cover_path || '/static/images/default-track.jpg');
            
            const typeLabel = isAlbum ? 'Album' : 'Track';
            const oldestClass = download.isOldest ? 'oldest-download' : '';
            const oldestLabel = download.isOldest ? '<span style="color: #FF9800; font-weight: bold;">(Oldest - will be removed first)</span>' : '';
            
            return `
                <div class="download-card ${oldestClass}" data-id="${download.id}" data-expires="${download.expires_at}" data-date="${download.downloaded_at}">
                    <img src="${coverPath}" alt="${itemData.title}" class="download-image">
                    <div class="download-content">
                        <div class="download-header">
                            <div>
                                <div class="download-title">${itemData.title}</div>
                                <div class="download-subtitle">${typeLabel}</div>
                            </div>
                            <div class="download-timer">
                                <i class="fas fa-clock"></i>
                                <span class="timer-value">${download.time_remaining}</span>
                            </div>
                        </div>
                        <div class="download-footer">
                            <div class="download-meta">
                                Downloaded on ${new Date(download.downloaded_at).toLocaleString()} ${oldestLabel}
                            </div>
                            <div class="download-actions">
                                <button class="btn-download" onclick="window.downloadsManager.downloadFile(${download.id})">
                                    <i class="fas fa-download"></i> Download
                                </button>
                                <button class="btn-delete" onclick="window.downloadsManager.deleteDownload(${download.id})">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
        
        container.innerHTML = downloadsHtml;
    }

    updateLimitWarning(downloadCount) {
        const warningElement = document.getElementById('limitWarning');
        const countElement = document.getElementById('downloadsCount');
        const warningMessageElement = document.getElementById('warningMessage');
        
        countElement.textContent = downloadCount;
        
        if (downloadCount >= 8) {
            document.querySelector('.download-count').classList.add('warning');
        } else {
            document.querySelector('.download-count').classList.remove('warning');
        }
        
        if (downloadCount === 9) {
            warningElement.style.display = 'flex';
            warningMessageElement.innerHTML = `
                <p>You have 9 out of 10 possible downloads. 
                Your next download will remove your oldest download automatically.</p>
            `;
        } else if (downloadCount === 10) {
            warningElement.style.display = 'flex';
            warningMessageElement.innerHTML = `
                <p>You have reached the maximum of 10 downloads. 
                Any new download will automatically remove your oldest download highlighted below.</p>
            `;
        } else {
            warningElement.style.display = 'none';
        }
    }

    setupTimerUpdates() {
        setInterval(() => this.updateTimers(), 1000);
    }

    updateTimers() {
        const downloadCards = document.querySelectorAll('.download-card');
        
        downloadCards.forEach(card => {
            const expiryTime = new Date(card.dataset.expires);
            const now = new Date();
            const diff = expiryTime - now;
            
            if (diff <= 0) {
                card.remove();
                return;
            }
            
            const hours = Math.floor(diff / (1000 * 60 * 60));
            const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
            const seconds = Math.floor((diff % (1000 * 60)) / 1000);
            
            const timerValue = card.querySelector('.timer-value');
            if (timerValue) {
                timerValue.textContent = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
                
                const timerElement = card.querySelector('.download-timer');
                if (hours === 0 && minutes < 30) {
                    timerElement.style.setProperty('--timer-bg', 'rgba(255, 59, 48, 0.1)');
                    timerElement.style.setProperty('--timer-color', '#FF3B30');
                }
            }
        });
        
        const currentCount = document.querySelectorAll('.download-card').length;
        this.updateLimitWarning(currentCount);
        document.getElementById('activeTabBadge').textContent = currentCount;
        
        if (currentCount === 0) {
            document.getElementById('downloadsContainer').innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-download"></i>
                    <h3>No downloads available</h3>
                    <p>You don't have any active downloads at the moment.</p>
                </div>
            `;
        }
    }

    async downloadFile(downloadId) {
        window.location.href = `/api/my-downloads/${downloadId}/file`;
    }

    async deleteDownload(downloadId) {
        if (!confirm('Are you sure you want to delete this download?')) {
            return;
        }
        
        try {
            const response = await fetch(`/api/my-downloads/${downloadId}`, {
                method: 'DELETE'
            });
            
            if (!response.ok) {
                throw new Error('Failed to delete download');
            }
            
            const card = document.querySelector(`.download-card[data-id="${downloadId}"]`);
            if (card) {
                card.remove();
            }
            
            this.fetchActiveDownloads();
            this.showMessage('Download deleted successfully', 'success');
        } catch (error) {
            this.showMessage('Error deleting download. Please try again.', 'error');
        }
    }

    showMessage(message, type = 'info') {
        if (window.showToast) {
            window.showToast(message, type);
        } else {
            alert(message);
        }
    }

    updateActiveLinks(path) {
        document.querySelectorAll('.nav-link, .side-nav-item, .dropdown-item').forEach(link => {
            const href = link.getAttribute('href');
            if (href === path) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
    }

    errorHTML(message) {
        return `
            <div style="max-width: 1200px; margin: 0 auto; padding: 32px 24px;">
                <div style="background-color: var(--card-bg); border-radius: 0.75rem; padding: 40px; text-align: center;">
                    <i class="fas fa-exclamation-circle" style="font-size: 2rem; color: #ef4444; margin-bottom: 16px; display: block;"></i>
                    <h2 style="color: var(--text-color); margin: 0 0 8px 0;">Error Loading Downloads</h2>
                    <p style="color: var(--text-muted); margin: 0;">${message}</p>
                    <button onclick="location.reload()" style="margin-top: 20px; padding: 10px 20px; background-color: #2196F3; color: white; border: none; border-radius: 0.375rem; cursor: pointer; font-weight: 500;">
                        Reload Page
                    </button>
                </div>
            </div>
        `;
    }

    // ✅ Cleanup
    async destroy() {
        console.log('🧹 MyDownloads: Destroying...');
        this.monthsData.clear();
        this.activeDownloads = [];
        // ✅ Clean up global reference
        if (window.downloadsManager === this) {
            delete window.downloadsManager;
        }
    }
}
