// statistics-spa.js - Universal Statistics Controller (SSR + SPA)

export class StatisticsSPA {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.refreshInterval = null;
        this.autoRefreshTimeout = null;
        this.container = null;
        this.data = null;
        
        console.log(`📊 Statistics Controller initialized in ${mode.toUpperCase()} mode`);
    }

    /**
     * Returns required stylesheets for this page
     */
    getRequiredStyles() {
        return ['/static/css/statistics.css'];
    }

    /**
     * Returns page title for SPA navigation
     */
    getPageTitle() {
        return 'Statistics';
    }

    /**
     * SSR Mode: Returns empty string (HTML already rendered by server)
     * SPA Mode: Fetches data and renders HTML string
     */
    async render() {
        if (this.mode === 'ssr') {
            // SSR: HTML already exists, just return empty string
            console.log('📊 SSR Mode: Skipping render, using server-generated HTML');
            return '';
        }

        // SPA: Fetch data and render HTML
        console.log('📊 SPA Mode: Fetching data and rendering HTML');
        await this.fetchData();
        return this.renderHTML();
    }

    /**
     * Fetch statistics data from API (SPA mode only)
     */
    async fetchData() {
        try {
            const response = await fetch('/api/statistics/data');
            if (!response.ok) {
                throw new Error(`Failed to fetch statistics: ${response.status}`);
            }
            this.data = await response.json();
            console.log('📊 Statistics data fetched:', this.data);
        } catch (error) {
            console.error('Error fetching statistics:', error);
            throw error;
        }
    }

    /**
     * Render HTML string (SPA mode only)
     */
    renderHTML() {
        const { monthly_stats = [], total_stats = {} } = this.data;

        return `
            <div class="container mx-auto px-4 sm:px-6 lg:px-8 py-8">
                <!-- Active Users Section -->
                <div class="stats-card p-6 mb-8">
                    <div class="flex items-center justify-between border-b pb-4 mb-6" style="border-color: var(--custom-border);">
                        <h2 class="text-2xl font-bold stats-title">Active Users</h2>
                        <button id="refreshStats" class="pin-button pin-button-secondary">
                            <i class="fas fa-sync"></i> Refresh Stats
                        </button>
                    </div>

                    <div class="active-users-stats">
                        <div class="user-stat-row">
                            <span class="stat-label"><i class="fas fa-crown"></i> Creators:</span>
                            <span id="creatorCount" class="stat-value">0</span>
                        </div>
                        <div class="user-stat-row">
                            <span class="stat-label"><i class="fas fa-users"></i> Team:</span>
                            <span id="teamCount" class="stat-value">0</span>
                        </div>
                        <div class="user-stat-row">
                            <span class="stat-label"><i class="fas fa-user"></i> Patrons:</span>
                            <span id="patronCount" class="stat-value">0</span>
                        </div>
                        <div class="user-stat-row">
                            <span class="stat-label"><i class="fas fa-mug-hot"></i> Ko-fi Supporters:</span>
                            <span id="kofiCount" class="stat-value">0</span>
                        </div>
                        <div class="user-stat-row">
                            <span class="stat-label">
                                <i class="fas fa-user-clock"></i> 
                                Guest Trial Users:
                                <div class="guest-trial-info">
                                    <span class="trial-badge" id="activeTrialBadge">0 Active</span>
                                    <span class="expired-badge" id="expiredTrialBadge">0 Expired</span>
                                </div>
                            </span>
                            <span id="guestCount" class="stat-value">0</span>
                        </div>
                        <div class="user-stat-row total">
                            <span class="stat-label"><i class="fas fa-chart-line"></i> Total Active:</span>
                            <span id="totalCount" class="stat-value">0</span>
                        </div>
                    </div>
                </div>

                <!-- Overall Statistics Section -->
                <div class="stats-card p-6 mb-8">
                    <div class="flex items-center justify-between border-b pb-4 mb-6" style="border-color: var(--custom-border);">
                        <div>
                            <h2 class="text-2xl font-bold stats-title">Overall Statistics</h2>
                            <p class="text-sm stats-subtitle mt-1">Last 6 months (excluding creator downloads)</p>
                        </div>
                    </div>
                    
                    <div class="grid grid-cols-2 md:grid-cols-3 gap-6">
                        <div class="stats-section p-4">
                            <div class="text-3xl font-bold stat-value-primary">
                                ${total_stats.albums || 0}
                            </div>
                            <div class="text-sm stats-subtitle">Total Albums Downloaded</div>
                        </div>
                        
                        <div class="stats-section p-4">
                            <div class="text-3xl font-bold stat-value-success">
                                ${total_stats.tracks || 0}
                            </div>
                            <div class="text-sm stats-subtitle">Total Tracks Downloaded</div>
                        </div>
                        
                        <div class="stats-section p-4">
                            <div class="text-3xl font-bold stat-value-primary">
                                ${total_stats.total_users || 0}
                            </div>
                            <div class="text-sm stats-subtitle">Total Users</div>
                        </div>
                    </div>
                </div>

                <!-- Monthly Download Statistics -->
                <div class="stats-card p-6">
                    <div class="border-b pb-4 mb-6" style="border-color: var(--custom-border);">
                        <h2 class="text-2xl font-bold stats-title">Monthly Download History</h2>
                        <p class="text-sm stats-subtitle mt-1">Download activity by month (last 6 months)</p>
                    </div>

                    ${monthly_stats.length > 0 ? this.renderMonthsGrid(monthly_stats) : this.renderEmptyState()}
                </div>
            </div>
        `;
    }

    /**
     * Render months grid HTML
     */
    renderMonthsGrid(monthly_stats) {
        return `
            <div class="months-grid">
                ${monthly_stats.map((month, index) => this.renderMonthCard(month, index)).join('')}
            </div>
        `;
    }

    /**
     * Render individual month card
     */
    renderMonthCard(month, index) {
        const monthId = `month-${index + 1}`;
        return `
            <div class="month-card" data-month-id="${monthId}">
                <div class="month-header">
                    <div class="month-name">${month.month_name}</div>
                    <div class="month-total">${month.total_downloads}</div>
                </div>
                
                <div class="month-breakdown">
                    <div class="breakdown-item breakdown-albums">
                        <i class="fas fa-compact-disc"></i>
                        <span>${month.album_downloads.success} albums</span>
                    </div>
                    <div class="breakdown-item breakdown-tracks">
                        <i class="fas fa-music"></i>
                        <span>${month.track_downloads.success} tracks</span>
                    </div>
                </div>
                
                <div class="month-status">
                    <span class="status-success">${month.successful_downloads} successful</span>
                    <span class="status-failed">${month.failed_downloads} failed</span>
                </div>

                <!-- Tier Breakdown (Initially Hidden) -->
                <div id="${monthId}" class="tier-breakdown hidden">
                    <div class="tier-breakdown-header">
                        <h4>Downloads by Tier:</h4>
                    </div>
                    ${this.renderTierBreakdown(month.tier_breakdown)}
                </div>
            </div>
        `;
    }

    /**
     * Render tier breakdown for a month
     */
    renderTierBreakdown(tier_breakdown) {
        if (!tier_breakdown || Object.keys(tier_breakdown).length === 0) {
            return '<p class="text-sm text-gray-500">No tier data available</p>';
        }

        return Object.entries(tier_breakdown).map(([tier_name, tier_data]) => `
            <div class="tier-item">
                <div class="tier-item-header">
                    <span class="tier-name">${tier_name}</span>
                    ${tier_data.amount_cents > 0 ? 
                        `<span class="tier-price">$${(tier_data.amount_cents / 100).toFixed(2)}/mo</span>` : 
                        ''}
                    <span class="tier-total">${tier_data.total}</span>
                </div>
                <div class="tier-item-details">
                    ${tier_data.albums.success > 0 ? 
                        `<span class="tier-detail-albums">${tier_data.albums.success} albums</span>` : 
                        ''}
                    ${tier_data.tracks.success > 0 ? 
                        `<span class="tier-detail-tracks">${tier_data.tracks.success} tracks</span>` : 
                        ''}
                    ${(tier_data.albums.failed + tier_data.tracks.failed) > 0 ? 
                        `<span class="tier-detail-failed">${tier_data.albums.failed + tier_data.tracks.failed} failed</span>` : 
                        ''}
                </div>
            </div>
        `).join('');
    }

    /**
     * Render empty state
     */
    renderEmptyState() {
        return `
            <div class="empty-months">
                <i class="fas fa-chart-line"></i>
                <h3>No Download Activity</h3>
                <p>No downloads found in the last 6 months.</p>
            </div>
        `;
    }

    /**
     * Mount/Hydrate - Works the same for both SSR and SPA modes
     * This is where we attach event listeners and start dynamic behavior
     */
    async mount() {
        console.log(`📊 Mounting Statistics (${this.mode} mode)`);
        
        // In SSR mode, we might have bootstrapped data in a script tag
        if (this.mode === 'ssr') {
            this.loadBootstrappedData();
        }

        // Attach event listeners
        this.attachEventListeners();

        // Fetch and display user stats
        await this.updateUserStats();

        // Start auto-refresh timer (5 minutes)
        this.startAutoRefresh();
    }

    /**
     * Load bootstrapped data from SSR (if available)
     */
    loadBootstrappedData() {
        const bootstrapScript = document.getElementById('statistics-bootstrap-data');
        if (bootstrapScript) {
            try {
                this.data = JSON.parse(bootstrapScript.textContent);
                console.log('📊 Loaded bootstrapped data:', this.data);
            } catch (error) {
                console.error('Error parsing bootstrapped data:', error);
            }
        }
    }

    /**
     * Attach all event listeners
     */
    attachEventListeners() {
        // Refresh button
        const refreshBtn = document.getElementById('refreshStats');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => this.handleRefresh());
        }

        // Month card click handlers (for tier breakdown toggle)
        const monthCards = document.querySelectorAll('.month-card');
        monthCards.forEach(card => {
            card.addEventListener('click', (e) => {
                const monthId = card.dataset.monthId;
                if (monthId) {
                    this.toggleMonthDetails(monthId);
                }
            });
        });

        console.log('📊 Event listeners attached');
    }

    /**
     * Handle refresh button click
     */
    async handleRefresh() {
        const btnIcon = document.querySelector('#refreshStats i');
        if (btnIcon) {
            btnIcon.classList.add('fa-spin');
        }

        try {
            await this.updateUserStats();
        } finally {
            if (btnIcon) {
                btnIcon.classList.remove('fa-spin');
            }
        }
    }

    /**
     * Toggle month details (tier breakdown)
     */
    toggleMonthDetails(monthId) {
        const tierBreakdown = document.getElementById(monthId);
        if (tierBreakdown) {
            tierBreakdown.classList.toggle('hidden');
        }
    }

    /**
     * Update user statistics from API
     */
    async updateUserStats() {
        try {
            const response = await fetch('/api/sessions/stats');
            if (!response.ok) {
                throw new Error('Failed to fetch user stats');
            }

            const data = await response.json();
            this.updateStatsUI(data);
        } catch (error) {
            console.error('Error updating user stats:', error);
        }
    }

    /**
     * Update stats UI with fetched data
     */
    updateStatsUI(data) {
        if (!data?.stats) return;

        // Update counts
        this.updateElement('creatorCount', data.stats.creator?.total || 0);
        this.updateElement('teamCount', data.stats.team?.total || 0);
        this.updateElement('patronCount', data.stats.patron?.total || 0);
        this.updateElement('kofiCount', data.stats.kofi?.total || 0);
        
        // Update guest trial users
        const guestStats = data.stats.guest || {};
        this.updateElement('guestCount', guestStats.total || 0);
        this.updateElement('activeTrialBadge', `${guestStats.active || 0} Active`);
        this.updateElement('expiredTrialBadge', `${guestStats.expired || 0} Expired`);
        
        this.updateElement('totalCount', data.total_active || 0);

        console.log('📊 User stats updated');
    }

    /**
     * Helper to update element text content
     */
    updateElement(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    }

    /**
     * Start auto-refresh timer (5 minutes)
     */
    startAutoRefresh() {
        // Clear existing timer
        if (this.autoRefreshTimeout) {
            clearTimeout(this.autoRefreshTimeout);
        }

        // Set new timer for 5 minutes
        this.autoRefreshTimeout = setTimeout(() => {
            console.log('📊 Auto-refreshing page...');
            location.reload();
        }, 300000); // 5 minutes

        console.log('📊 Auto-refresh timer started (5 minutes)');
    }

    /**
     * Cleanup method called when navigating away
     */
    async destroy() {
        console.log('📊 Destroying Statistics controller');

        // Clear timers
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
        }
        if (this.autoRefreshTimeout) {
            clearTimeout(this.autoRefreshTimeout);
        }

        // Remove event listeners (if we stored references)
        // In this simple case, they'll be cleaned up with the DOM

        this.container = null;
        this.data = null;
    }
}

// Export for both module and global use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { StatisticsSPA };
}