// activity-logs-shared-spa.js - Universal controller for Activity Logs page (SSR and SPA modes)

export class ActivityLogsController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.currentPage = 1;
        this.totalPages = 1;
        this.filters = { year: null, month: null, action_type: null, user_id: null };
        this.teamMembers = [];
        this.dropdownClickHandler = null;
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }

        await this.fetchTeamMembers();

        return `
            <div class="activity-logs-container">
                ${this.renderFilters()}
                ${this.renderLogsTable()}
                ${this.renderPagination()}
            </div>
            ${this.renderModal()}
        `;
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`📊 ActivityLogs: Mounting in ${this.mode} mode...`);

        // If in SSR mode and data isn't loaded yet, fetch it
        if (this.mode === 'ssr' && this.teamMembers.length === 0) {
            await this.fetchTeamMembers();
        }

        this.setupEventListeners();
        this.populateYearFilter();

        // Mark activity logs as read when user views this page
        await this.markLogsAsRead();

        await this.loadLogs();

        console.log('✅ ActivityLogs: Mounted successfully');
    }

    async markLogsAsRead() {
        try {
            const response = await fetch('/api/activity-logs/mark-read', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            if (response.ok) {
                const data = await response.json();
                console.log('📊 Activity logs marked as read, unread count:', data.unread_count);

                // Reset the badge to 0 in the UI
                if (window.updateActivityLogsBadge) {
                    window.updateActivityLogsBadge(0);
                }
            }
        } catch (error) {
            console.error('Error marking activity logs as read:', error);
            // Non-fatal - don't show error to user
        }
    }

    async fetchTeamMembers() {
        try {
            const response = await fetch('/api/activity-logs/team-members');
            if (response.ok) {
                const data = await response.json();
                this.teamMembers = data.team_members || [];
            }
        } catch (error) {
            console.error('Error fetching team members:', error);
            this.teamMembers = [];
        }
    }

    renderFilters() {
        const teamMemberOptions = this.teamMembers.map(member =>
            `<option value="${member.id}">${member.username} (${member.role})</option>`
        ).join('');

        return `
            <div class="filters-container">
                <h3><i class="fas fa-filter"></i> Filters</h3>

                <div class="filters-grid">
                    <div class="filter-group">
                        <label>Year</label>
                        <div class="custom-select" data-filter="year">
                            <div class="select-trigger">All Years</div>
                            <div class="select-options">
                                <div class="select-option" data-value="">All Years</div>
                            </div>
                        </div>
                    </div>

                    <div class="filter-group">
                        <label>Month</label>
                        <div class="custom-select" data-filter="month">
                            <div class="select-trigger">All Months</div>
                            <div class="select-options">
                                <div class="select-option" data-value="">All Months</div>
                                <div class="select-option" data-value="1">January</div>
                                <div class="select-option" data-value="2">February</div>
                                <div class="select-option" data-value="3">March</div>
                                <div class="select-option" data-value="4">April</div>
                                <div class="select-option" data-value="5">May</div>
                                <div class="select-option" data-value="6">June</div>
                                <div class="select-option" data-value="7">July</div>
                                <div class="select-option" data-value="8">August</div>
                                <div class="select-option" data-value="9">September</div>
                                <div class="select-option" data-value="10">October</div>
                                <div class="select-option" data-value="11">November</div>
                                <div class="select-option" data-value="12">December</div>
                            </div>
                        </div>
                    </div>

                    <div class="filter-group">
                        <label>Action Type</label>
                        <div class="custom-select" data-filter="action">
                            <div class="select-trigger">All Actions</div>
                            <div class="select-options">
                                <div class="select-option" data-value="">All Actions</div>
                                <div class="select-option" data-value="create">Create</div>
                                <div class="select-option" data-value="update">Update</div>
                                <div class="select-option" data-value="delete">Delete</div>
                            </div>
                        </div>
                    </div>

                    <div class="filter-group">
                        <label>Team Member</label>
                        <div class="custom-select" data-filter="user">
                            <div class="select-trigger">All Members</div>
                            <div class="select-options">
                                <div class="select-option" data-value="">All Members</div>
                                ${teamMemberOptions.replace(/<option/g, '<div class="select-option"').replace(/value="/g, 'data-value="').replace(/<\/option>/g, '</div>')}
                            </div>
                        </div>
                    </div>
                </div>

                <div class="filter-actions">
                    <button id="applyFilters" class="btn btn-primary">
                        <i class="fas fa-search"></i> Apply Filters
                    </button>
                    <button id="clearFilters" class="btn btn-secondary">
                        <i class="fas fa-times"></i> Clear
                    </button>
                    <button id="exportLogs" class="btn btn-secondary">
                        <i class="fas fa-download"></i> Export CSV
                    </button>
                </div>
            </div>
        `;
    }

    renderLogsTable() {
        return `
            <div class="logs-table-container">
                <div id="logsLoading" class="loading-state">
                    <i class="fas fa-spinner fa-spin"></i>
                    <p>Loading activity logs...</p>
                </div>

                <table id="logsTable" class="logs-table" style="display: none;">
                    <thead>
                        <tr>
                            <th>Timestamp</th>
                            <th>User</th>
                            <th>Action</th>
                            <th>Description</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody id="logsTableBody"></tbody>
                </table>

                <div id="noLogs" class="empty-state" style="display: none;">
                    <i class="fas fa-inbox"></i>
                    <p>No activity logs found</p>
                </div>
            </div>
        `;
    }

    renderPagination() {
        return `
            <div id="pagination" class="pagination-container" style="display: none;">
                <button id="prevPage" class="btn btn-secondary" disabled>
                    <i class="fas fa-chevron-left"></i> Previous
                </button>
                <span id="pageInfo">Page 1 of 1</span>
                <button id="nextPage" class="btn btn-secondary" disabled>
                    Next <i class="fas fa-chevron-right"></i>
                </button>
            </div>
        `;
    }

    renderModal() {
        return `
            <div id="logDetailsModal" class="modal">
                <div class="modal-content log-details-modal">
                    <div class="modal-header">
                        <h2><i class="fas fa-info-circle"></i> Activity Details</h2>
                        <button class="close-modal" onclick="document.getElementById('logDetailsModal').classList.remove('active')">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="modal-body" id="logDetailsBody"></div>
                </div>
            </div>
        `;
    }

    setupEventListeners() {
        document.getElementById('applyFilters')?.addEventListener('click', () => this.applyFilters());
        document.getElementById('clearFilters')?.addEventListener('click', () => this.clearFilters());
        document.getElementById('exportLogs')?.addEventListener('click', () => this.exportLogs());
        document.getElementById('prevPage')?.addEventListener('click', () => {
            if (this.currentPage > 1) {
                this.currentPage--;
                this.loadLogs();
            }
        });
        document.getElementById('nextPage')?.addEventListener('click', () => {
            if (this.currentPage < this.totalPages) {
                this.currentPage++;
                this.loadLogs();
            }
        });

        // Setup custom dropdown interactions
        this.setupCustomDropdowns();
    }

    setupCustomDropdowns() {
        // Remove old handlers if they exist
        if (this.dropdownClickHandler) {
            document.removeEventListener('click', this.dropdownClickHandler);
        }

        // Use event delegation for better performance and to handle dynamically added options
        this.dropdownClickHandler = (e) => {
            const trigger = e.target.closest('.select-trigger');
            const option = e.target.closest('.select-option');

            // Handle trigger click
            if (trigger) {
                e.stopPropagation();
                const select = trigger.closest('.custom-select');

                // Close other dropdowns
                document.querySelectorAll('.custom-select.open').forEach(s => {
                    if (s !== select) s.classList.remove('open');
                });

                // Toggle current dropdown
                select.classList.toggle('open');
                return;
            }

            // Handle option click
            if (option) {
                e.stopPropagation();
                const select = option.closest('.custom-select');
                const trigger = select.querySelector('.select-trigger');
                const value = option.dataset.value;
                const text = option.textContent;

                // Update trigger text
                trigger.textContent = text;

                // Update selected state
                select.querySelectorAll('.select-option').forEach(opt => {
                    opt.classList.remove('selected');
                });
                option.classList.add('selected');

                // Store value
                select.dataset.value = value;

                // Close dropdown
                select.classList.remove('open');
                return;
            }

            // Close all dropdowns if clicking outside
            document.querySelectorAll('.custom-select.open').forEach(select => {
                select.classList.remove('open');
            });
        };

        document.addEventListener('click', this.dropdownClickHandler);
    }

    populateYearFilter() {
        const yearSelect = document.querySelector('[data-filter="year"]');
        if (!yearSelect) return;

        const optionsContainer = yearSelect.querySelector('.select-options');
        const currentYear = new Date().getFullYear();

        for (let year = currentYear; year >= currentYear - 5; year--) {
            const option = document.createElement('div');
            option.className = 'select-option';
            option.dataset.value = year;
            option.textContent = year;
            optionsContainer.appendChild(option);
        }

        // Event delegation handles new options automatically, no need to re-setup
    }

    applyFilters() {
        this.filters.year = document.querySelector('[data-filter="year"]')?.dataset.value || null;
        this.filters.month = document.querySelector('[data-filter="month"]')?.dataset.value || null;
        this.filters.action_type = document.querySelector('[data-filter="action"]')?.dataset.value || null;
        this.filters.user_id = document.querySelector('[data-filter="user"]')?.dataset.value || null;
        this.currentPage = 1;
        this.loadLogs();
    }

    clearFilters() {
        // Reset all custom dropdowns
        document.querySelectorAll('.custom-select').forEach(select => {
            const trigger = select.querySelector('.select-trigger');
            const firstOption = select.querySelector('.select-option[data-value=""]');

            if (firstOption) {
                trigger.textContent = firstOption.textContent;
                select.dataset.value = '';

                // Clear selected state
                select.querySelectorAll('.select-option').forEach(opt => {
                    opt.classList.remove('selected');
                });
                firstOption.classList.add('selected');
            }
        });

        this.filters = { year: null, month: null, action_type: null, user_id: null };
        this.currentPage = 1;
        this.loadLogs();
    }

    async loadLogs() {
        const logsLoading = document.getElementById('logsLoading');
        const logsTable = document.getElementById('logsTable');
        const noLogs = document.getElementById('noLogs');

        logsLoading.style.display = 'block';
        logsTable.style.display = 'none';
        noLogs.style.display = 'none';

        try {
            const params = new URLSearchParams({ page: this.currentPage, per_page: 50 });
            if (this.filters.year) params.append('year', this.filters.year);
            if (this.filters.month) params.append('month', this.filters.month);
            if (this.filters.action_type) params.append('action_type', this.filters.action_type);
            if (this.filters.user_id) params.append('user_id', this.filters.user_id);

            const response = await fetch(`/api/activity-logs?${params}`);
            if (!response.ok) throw new Error('Failed to load logs');

            const data = await response.json();
            logsLoading.style.display = 'none';

            if (data.logs.length === 0) {
                noLogs.style.display = 'block';
            } else {
                logsTable.style.display = 'table';
                this.renderLogsData(data.logs);
                this.updatePagination(data.pagination);
            }
        } catch (error) {
            console.error('Error loading logs:', error);
            logsLoading.style.display = 'none';
            noLogs.style.display = 'block';
            this.showToast('Error loading activity logs', 'error');
        }
    }

    renderLogsData(logs) {
        const tbody = document.getElementById('logsTableBody');
        tbody.innerHTML = '';

        logs.forEach(log => {
            const row = document.createElement('tr');
            const timestamp = new Date(log.created_at);
            const formattedDate = timestamp.toLocaleString();

            row.innerHTML = `
                <td>
                    <div>${formattedDate}</div>
                    <small style="color: var(--text-muted)">${this.getRelativeTime(timestamp)}</small>
                </td>
                <td>
                    <div>${this.escapeHtml(log.user.username)}</div>
                    <div style="font-size: 0.85rem; color: var(--text-muted)">${log.user.role}</div>
                </td>
                <td>
                    <span class="action-badge ${log.action_type}">${log.action_type}</span>
                    <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 4px;">
                        ${this.escapeHtml(log.table_name)}
                    </div>
                </td>
                <td>${this.escapeHtml(log.description || 'No description')}</td>
                <td>
                    <button class="view-details-btn" data-log-id="${log.id}">
                        <i class="fas fa-eye"></i> View
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });

        // Attach event listeners to view buttons
        document.querySelectorAll('.view-details-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const logId = parseInt(e.currentTarget.dataset.logId);
                this.viewDetails(logId, logs);
            });
        });
    }

    updatePagination(pagination) {
        this.totalPages = pagination.total_pages;
        document.getElementById('pageInfo').textContent = `Page ${pagination.page} of ${pagination.total_pages}`;
        document.getElementById('prevPage').disabled = pagination.page <= 1;
        document.getElementById('nextPage').disabled = pagination.page >= pagination.total_pages;
        document.getElementById('pagination').style.display = pagination.total_pages > 1 ? 'flex' : 'none';
    }

    viewDetails(logId, logs) {
        const log = logs.find(l => l.id === logId);
        if (!log) {
            this.showToast('Log not found', 'error');
            return;
        }

        const modal = document.getElementById('logDetailsModal');
        const body = document.getElementById('logDetailsBody');
        const timestamp = new Date(log.created_at);

        body.innerHTML = `
            <div class="log-detail-grid">
                <div><strong>User:</strong></div>
                <div>${this.escapeHtml(log.user.username)} (${log.user.role})</div>

                <div><strong>Action:</strong></div>
                <div><span class="action-badge ${log.action_type}">${log.action_type}</span></div>

                <div><strong>Table:</strong></div>
                <div>${this.escapeHtml(log.table_name)}</div>

                <div><strong>Record ID:</strong></div>
                <div>${this.escapeHtml(log.record_id || 'N/A')}</div>

                <div><strong>Timestamp:</strong></div>
                <div>${timestamp.toLocaleString()}</div>

                <div><strong>IP Address:</strong></div>
                <div>${this.escapeHtml(log.ip_address || 'N/A')}</div>
            </div>

            ${log.description ? `<div style="margin-top: 20px;"><h4>Description</h4><p>${this.escapeHtml(log.description)}</p></div>` : ''}

            ${log.changes_summary && Object.keys(log.changes_summary).length > 0 ? `
                <div style="margin-top: 20px;">
                    <h4>Changes</h4>
                    <div class="changes-diff">${this.renderChanges(log.changes_summary)}</div>
                </div>
            ` : ''}
        `;

        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    renderChanges(changes) {
        let html = '<table style="width: 100%;">';
        for (const [key, change] of Object.entries(changes)) {
            html += `
                <tr>
                    <td style="padding: 5px; font-weight: 600;">${this.escapeHtml(key)}:</td>
                    <td style="padding: 5px;">
                        <span style="color: #ef4444; text-decoration: line-through;">
                            ${this.escapeHtml(JSON.stringify(change.old))}
                        </span>
                        →
                        <span style="color: #10b981;">
                            ${this.escapeHtml(JSON.stringify(change.new))}
                        </span>
                    </td>
                </tr>
            `;
        }
        html += '</table>';
        return html;
    }

    async exportLogs() {
        try {
            const params = new URLSearchParams({ per_page: 10000 });
            if (this.filters.year) params.append('year', this.filters.year);
            if (this.filters.month) params.append('month', this.filters.month);
            if (this.filters.action_type) params.append('action_type', this.filters.action_type);
            if (this.filters.user_id) params.append('user_id', this.filters.user_id);

            const response = await fetch(`/api/activity-logs?${params}`);
            if (!response.ok) throw new Error('Failed to export logs');
            const data = await response.json();

            const csv = this.convertToCSV(data.logs);
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `activity-logs-${new Date().toISOString().split('T')[0]}.csv`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);

            this.showToast('Logs exported successfully', 'success');
        } catch (error) {
            console.error('Error exporting logs:', error);
            this.showToast('Error exporting logs', 'error');
        }
    }

    convertToCSV(logs) {
        const headers = ['Timestamp', 'User', 'Role', 'Action', 'Table', 'Record ID', 'Description', 'IP Address'];
        const rows = logs.map(log => [
            log.created_at, log.user.username, log.user.role, log.action_type,
            log.table_name, log.record_id || '', log.description || '', log.ip_address || ''
        ]);
        return [headers.join(','), ...rows.map(row => row.map(cell => `"${cell}"`).join(','))].join('\n');
    }

    getRelativeTime(date) {
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`;
        if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
        if (diffDays < 30) return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
        return date.toLocaleDateString();
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

    showToast(message, type = 'info') {
        if (window.showToast) {
            window.showToast(message, type);
        } else {
            alert(message);
        }
    }

    // ✅ Cleanup
    async destroy() {
        console.log('🧹 ActivityLogs: Destroying...');

        // Remove dropdown event handler
        if (this.dropdownClickHandler) {
            document.removeEventListener('click', this.dropdownClickHandler);
            this.dropdownClickHandler = null;
        }

        // Close modal if open
        const modal = document.getElementById('logDetailsModal');
        if (modal) {
            modal.classList.remove('active');
            document.body.style.overflow = '';
        }
    }
}
