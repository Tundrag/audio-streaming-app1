// manage-book-requests-shared-spa.js - Universal controller for Manage Book Requests page (SSR and SPA modes)
// Version: 2025-10-27-card-removal-fix

console.log('📦 [Admin SPA] Loading manage-book-requests-shared-spa.js v2025-10-27-card-removal-fix');

export class ManageBookRequestsController {
    constructor(mode = 'spa') {
        console.log('🏗️ [Admin SPA] Constructor called in', mode, 'mode');
        this.mode = mode; // 'ssr' or 'spa'
        this.bookRequests = [];
        this.availableMonths = [];
        this.requestsByMonth = {};
        this.statusCounts = {};
        this.activeMonth = '';
        this.activeStatus = '';
        this.ws = null;
        this.confirmAction = null;
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
        console.log(`📚 ManageBookRequests: Mounting in ${this.mode} mode...`);

        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM if available
            this.hydrateFromDOM();
        }

        // Initialize WebSocket manager FIRST - matching template
        const userId = window.currentUserId;
        const isAdmin = true;
        const currentPage = 'admin';

        // Initialize the WebSocket connection (using global manager from base.html)
        if (window.BookRequestWebSocketManager) {
            window.BookRequestWebSocketManager.init(userId, isAdmin, currentPage);

            // Add connection indicator
            const header = document.querySelector('.card-body h2');
            if (header && !document.getElementById('ws-connection-indicator')) {
                header.insertAdjacentHTML('afterend', `
                    <span id="ws-connection-indicator" style="
                        display: inline-block;
                        width: 8px;
                        height: 8px;
                        border-radius: 50%;
                        margin-left: 8px;
                        background-color: #ef4444;
                        vertical-align: middle;
                    " title="Connecting to live updates..."></span>
                `);
            }
        }

        await this.loadData();
        this.setupEventListeners();
        this.initWebSocket();

        // ✅ Clear badge when page is opened (mark as "viewed")
        this.clearBookRequestBadge();

        console.log('✅ ManageBookRequests: Mounted successfully');
    }

    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        const bootstrapScript = document.getElementById('manage-book-requests-bootstrap-data');
        if (bootstrapScript) {
            try {
                this.bootstrapData = JSON.parse(bootstrapScript.textContent);
                if (this.bootstrapData.requests) {
                    this.bookRequests = this.bootstrapData.requests;
                    this.availableMonths = this.bootstrapData.months || [];
                    this.statusCounts = this.bootstrapData.status_counts || {};
                    this.requestsByMonth = this.bootstrapData.requests_by_month || {};
                    this.activeMonth = this.bootstrapData.active_month || '';
                    this.activeStatus = this.bootstrapData.active_status || '';
                    console.log('📦 Hydrated manage-book-requests data from DOM');

                    // Render immediately with hydrated data
                    this.renderAll();
                }
            } catch (error) {
                console.error('Error parsing bootstrap data:', error);
            }
        }
    }

    // ✅ Generate HTML for SPA mode
    generateHTML() {
        return `
            <div class="container">
                <!-- Notification Area -->
                <div id="notification-area"></div>

                <div class="row">
                    <div class="col-lg-8">
                        <div class="card mb-4">
                            <div class="card-body">
                                <div class="d-flex justify-content-between align-items-center mb-3">
                                    <h2 class="mb-0">Book Requests</h2>
                                    <div class="badge-group" id="statusBadges">
                                        <div style="text-align: center; padding: 1rem;">
                                            <i class="fas fa-spinner fa-spin"></i>
                                        </div>
                                    </div>
                                </div>

                                <!-- Month stats -->
                                <div class="month-stats" id="monthStats">
                                    <div style="text-align: center; padding: 1rem;">
                                        <i class="fas fa-spinner fa-spin"></i>
                                    </div>
                                </div>

                                <!-- Status filters -->
                                <div class="status-filters mb-3" id="statusFilters">
                                    <div style="text-align: center; padding: 1rem;">
                                        <i class="fas fa-spinner fa-spin"></i>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Book requests list -->
                        <div class="book-requests-list" id="requestsList">
                            <div style="text-align: center; padding: 3rem;">
                                <i class="fas fa-spinner fa-spin" style="font-size: 2rem;"></i>
                                <p>Loading requests...</p>
                            </div>
                        </div>
                    </div>

                    <div class="col-lg-4">
                        <div class="card mb-4">
                            <div class="card-header">
                                <h3>Monthly Request Stats</h3>
                            </div>
                            <div class="card-body">
                                <p>Book requests are allocated to users based on their tier level. Each user gets a monthly quota.</p>
                                <div class="table-responsive">
                                    <table class="table table-sm">
                                        <thead>
                                            <tr>
                                                <th>Month</th>
                                                <th>Total</th>
                                                <th>Pending</th>
                                            </tr>
                                        </thead>
                                        <tbody id="monthlyStatsTable">
                                            <tr>
                                                <td colspan="3" style="text-align: center;">
                                                    <i class="fas fa-spinner fa-spin"></i>
                                                </td>
                                            </tr>
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Response Modal -->
            <div id="responseModal" class="response-modal">
                <div class="modal-content">
                    <span class="close-modal" id="closeResponseModal">&times;</span>
                    <h2 id="modalTitle">Respond to Request</h2>
                    <form id="responseForm">
                        <input type="hidden" id="requestId" name="request_id">
                        <div class="mb-3">
                            <label for="statusSelect" class="form-label">Status</label>
                            <select class="form-select" id="statusSelect" name="status" required>
                                <option value="pending">Pending</option>
                                <option value="approved">Approved</option>
                                <option value="rejected">Rejected</option>
                                <option value="fulfilled">Fulfilled</option>
                            </select>
                        </div>
                        <div class="mb-3">
                            <label for="responseMessage" class="form-label">Response Message</label>
                            <textarea class="form-control" id="responseMessage" name="response_message" rows="5" placeholder="Enter your response to this request..."></textarea>
                        </div>
                        <div class="d-flex justify-content-end">
                            <button type="button" class="btn btn-secondary me-2" id="cancelResponse">Cancel</button>
                            <button type="submit" class="btn btn-primary" id="submitResponse">
                                <i class="fas fa-paper-plane"></i> Submit Response
                            </button>
                        </div>
                    </form>
                </div>
            </div>

            <!-- Confirm Modal -->
            <div id="confirmModal" class="response-modal">
                <div class="modal-content">
                    <span class="close-modal" id="closeConfirmModal">&times;</span>
                    <h2 id="confirmModalTitle">Confirm Action</h2>
                    <div class="mb-4" id="confirmModalMessage">
                        Are you sure you want to take this action?
                    </div>
                    <div class="mb-3" id="confirmResponseContainer">
                        <label for="confirmResponseMessage" class="form-label">Response Message</label>
                        <textarea class="form-control" id="confirmResponseMessage" rows="3" placeholder="Enter a response message..."></textarea>
                        <small class="form-text text-muted">
                            This message will be sent to the user explaining the status change.
                        </small>
                    </div>
                    <div class="d-flex justify-content-end">
                        <button type="button" class="btn btn-secondary me-2" id="cancelConfirm">Cancel</button>
                        <button type="button" class="btn btn-primary" id="confirmAction">
                            <i class="fas fa-check"></i> Confirm
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    async loadData(forceReload = false) {
        // Skip if we already have hydrated data unless a reload was requested
        if (!forceReload && this.bookRequests.length > 0 && this.mode === 'ssr') {
            console.log('✅ Using hydrated book requests data');
            return;
        }

        try {
            // Parse URL parameters
            const urlParams = new URLSearchParams(window.location.search);
            this.activeMonth = urlParams.get('month_year') || '';
            this.activeStatus = urlParams.get('status') || '';

            const params = new URLSearchParams();
            if (this.activeStatus) params.append('status', this.activeStatus);
            if (this.activeMonth) params.append('month_year', this.activeMonth);

            const response = await fetch(`/api/book-requests/admin?${params.toString()}`, {
                cache: 'no-store'
            });

            if (!response.ok) {
                throw new Error('Failed to load book requests');
            }

            const data = await response.json();
            this.bookRequests = data.requests || [];

            const activeStatusFilter = (this.activeStatus || '').toLowerCase();
            if (activeStatusFilter) {
                this.bookRequests = this.bookRequests.filter(req => (req.status || '').toLowerCase() === activeStatusFilter);
            }
            this.availableMonths = data.months || [];

            this.recalculateAggregates();
            this.renderAll();

        } catch (error) {
            console.error('❌ Error loading book requests:', error);
            this.renderError();
        }
    }

    recalculateAggregates() {
        // Reset counts
        this.statusCounts = {
            total: 0,
            pending: 0,
            approved: 0,
            rejected: 0,
            fulfilled: 0
        };

        const monthSet = new Set(this.availableMonths || []);

        this.bookRequests.forEach(req => {
            const statusKey = (req.status || '').toLowerCase();
            if (this.statusCounts.hasOwnProperty(statusKey)) {
                this.statusCounts[statusKey]++;
            }

            if (req.month_year) {
                monthSet.add(req.month_year);
            }
        });

        this.statusCounts.total = this.bookRequests.length;

        this.availableMonths = Array.from(monthSet).sort((a, b) => b.localeCompare(a));

        this.requestsByMonth = {};
        this.availableMonths.forEach(month => {
            const monthRequests = this.bookRequests.filter(req => req.month_year === month);
            this.requestsByMonth[month] = {
                total: monthRequests.length,
                pending: monthRequests.filter(req => (req.status || '').toLowerCase() === 'pending').length
            };
        });
    }

    renderAll() {
        this.renderStatusBadges();
        this.renderMonthStats();
        this.renderStatusFilters();
        this.renderRequests();
        this.renderMonthlyStatsTable();
    }

    renderStatusBadges() {
        const container = document.getElementById('statusBadges');
        if (!container) return;

        container.innerHTML = `
            <span class="badge bg-warning text-dark me-2">${this.statusCounts.pending} Pending</span>
            <span class="badge bg-success me-2">${this.statusCounts.approved} Approved</span>
            <span class="badge bg-danger me-2">${this.statusCounts.rejected} Rejected</span>
            <span class="badge bg-primary">${this.statusCounts.fulfilled} Fulfilled</span>
        `;
    }

    renderMonthStats() {
        const container = document.getElementById('monthStats');
        if (!container) return;

        let html = `
            <div class="month-stat-card ${!this.activeMonth ? 'active' : ''}" data-month="">
                <div class="month-name">All Time</div>
                <div class="month-counts">
                    <span>${this.statusCounts.total} requests</span><br>
                    <span>${this.statusCounts.pending} pending</span>
                </div>
            </div>
        `;

        this.availableMonths.forEach(month => {
            const monthStats = this.requestsByMonth[month] || { total: 0, pending: 0 };
            html += `
                <div class="month-stat-card ${month === this.activeMonth ? 'active' : ''}" data-month="${month}">
                    <div class="month-name">${month}</div>
                    <div class="month-counts">
                        <span>${monthStats.total} requests</span><br>
                        <span>${monthStats.pending} pending</span>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    }

    renderStatusFilters() {
        const container = document.getElementById('statusFilters');
        if (!container) return;

        const filters = [
            { value: '', label: 'All' },
            { value: 'pending', label: 'Pending' },
            { value: 'approved', label: 'Approved' },
            { value: 'rejected', label: 'Rejected' },
            { value: 'fulfilled', label: 'Fulfilled' }
        ];

        container.innerHTML = filters.map(filter => `
            <div class="status-filter ${filter.value === this.activeStatus ? 'active' : ''}" data-status="${filter.value}">
                ${filter.label}
            </div>
        `).join('');
    }

    renderRequests() {
        const container = document.getElementById('requestsList');
        if (!container) return;

        if (this.bookRequests.length === 0) {
            container.innerHTML = `
                <div class="alert alert-info">
                    <i class="fas fa-info-circle"></i> No book requests found matching the current filters.
                </div>
            `;
            return;
        }

        container.innerHTML = this.bookRequests.map(req => this.createRequestCard(req)).join('');
    }

    createRequestCard(request) {
        const createdAt = request.created_at.replace('T', ' ').substring(0, 16);
        const responseDate = request.response_date ? request.response_date.replace('T', ' ').substring(0, 16) : '';

        // User info with chapters - exact template match
        let userInfoHtml = '';
        if (request.user) {
            let chaptersHtml = '';
            if (request.user.chapters_allowed !== undefined && request.user.chapters_allowed !== null) {
                if (request.user.role_type === 'creator' && request.user.chapters_allowed >= 1000000) {
                    chaptersHtml = `
                        <span class="chapters-info">
                            <i class="fas fa-infinity"></i> ∞ chapters
                        </span>
                    `;
                } else if (request.user.chapters_allowed > 0) {
                    chaptersHtml = `
                        <span class="chapters-info">
                            <i class="fas fa-file-alt"></i> ${request.user.chapters_allowed} chapters
                        </span>
                    `;
                } else {
                    chaptersHtml = `
                        <span class="chapters-info">
                            <i class="fas fa-question-circle"></i> chapters not set
                        </span>
                    `;
                }
            }

            userInfoHtml = `
                <div class="user-info">
                    <i class="fas fa-user"></i>
                    ${this.escapeHtml(request.user.display_name || request.user.username)}
                    ${chaptersHtml}
                </div>
            `;
        }

        // Response box - exact template match
        let responseBoxHtml = '';
        if (request.response_message) {
            responseBoxHtml = `
                <div class="response-box">
                    <div class="response-title">Admin Response:</div>
                    <div class="response-content">${this.escapeHtml(request.response_message)}</div>
                    ${request.responder ? `
                        <div class="response-footer">
                            Responded by ${this.escapeHtml(request.responder.username)} on ${responseDate}
                        </div>
                    ` : ''}
                </div>
            `;
        }

        // User reply box - exact template match
        let userReplyBoxHtml = '';
        if (request.user_reply) {
            userReplyBoxHtml = `
                <div class="user-reply-box">
                    <div class="response-title">User Reply:</div>
                    <div class="response-content">${this.escapeHtml(request.user_reply)}</div>
                    <div class="response-footer">
                        ${request.user ? `Reply from ${this.escapeHtml(request.user.username || request.user.email)}` : ''}
                    </div>
                </div>
            `;
        }

        // Action buttons based on status - exact template match
        let actionButtonsHtml = '';
        if (request.status === 'pending') {
            actionButtonsHtml = `
                <div class="action-buttons">
                    <button class="btn-action btn-approve" data-request-id="${request.id}">
                        <i class="fas fa-check"></i> Approve
                    </button>
                    <button class="btn-action btn-reject" data-request-id="${request.id}">
                        <i class="fas fa-times"></i> Reject
                    </button>
                    <button class="btn-action btn-respond" data-request-id="${request.id}" data-title="${this.escapeHtml(request.title)}">
                        <i class="fas fa-reply"></i> Respond
                    </button>
                </div>
            `;
        } else if (request.status === 'approved') {
            actionButtonsHtml = `
                <div class="action-buttons">
                    <button class="btn-action btn-fulfill" data-request-id="${request.id}">
                        <i class="fas fa-check-circle"></i> Mark as Fulfilled
                    </button>
                    <button class="btn-action btn-reject" data-request-id="${request.id}">
                        <i class="fas fa-times"></i> Reject
                    </button>
                </div>
            `;
        }

        return `
            <div class="request-card" id="request-${request.id}">
                <div class="d-flex justify-content-between align-items-start">
                    <h2 class="request-title">${this.escapeHtml(request.title)}</h2>
                    <div class="d-flex align-items-center">
                        <div class="status-badge status-${request.status}">${this.capitalizeStatus(request.status)}</div>
                        ${request.user_reply ? `
                            <span class="user-reply-indicator">
                                <i class="fas fa-reply"></i> User replied
                            </span>
                        ` : ''}
                    </div>
                </div>

                <div class="request-meta">
                    <div><strong>Author:</strong> ${this.escapeHtml(request.author)}</div>
                    <div><strong>Requested:</strong> ${createdAt}</div>
                </div>

                ${userInfoHtml}

                ${request.link ? `
                    <div>
                        <a href="${this.escapeHtml(request.link)}" target="_blank" class="request-link" rel="noopener noreferrer">
                            <i class="fas fa-external-link-alt"></i> ${this.escapeHtml(request.link)}
                        </a>
                    </div>
                ` : ''}

                ${request.description ? `
                    <div class="request-description">${this.escapeHtml(request.description)}</div>
                ` : ''}

                ${responseBoxHtml}

                ${userReplyBoxHtml}

                ${actionButtonsHtml}
            </div>
        `;
    }

    renderMonthlyStatsTable() {
        const tbody = document.getElementById('monthlyStatsTable');
        if (!tbody) return;

        const topMonths = this.availableMonths.slice(0, 5);

        if (topMonths.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" style="text-align: center;">No data available</td></tr>';
            return;
        }

        tbody.innerHTML = topMonths.map(month => {
            const monthStats = this.requestsByMonth[month] || { total: 0, pending: 0 };
            return `
                <tr>
                    <td>${month}</td>
                    <td>${monthStats.total}</td>
                    <td>${monthStats.pending}</td>
                </tr>
            `;
        }).join('');
    }

    setupEventListeners() {
        // Month filter clicks
        document.addEventListener('click', (e) => {
            const monthCard = e.target.closest('.month-stat-card');
            if (monthCard) {
                const month = monthCard.getAttribute('data-month');
                this.filterByMonth(month);
            }
        });

        // Status filter clicks
        document.addEventListener('click', (e) => {
            const statusFilter = e.target.closest('.status-filter');
            if (statusFilter) {
                const status = statusFilter.getAttribute('data-status');
                this.filterByStatus(status);
            }
        });

        // Action buttons
        document.addEventListener('click', (e) => {
            if (e.target.closest('.btn-approve')) {
                const btn = e.target.closest('.btn-approve');
                this.handleApprove(btn.getAttribute('data-request-id'));
            } else if (e.target.closest('.btn-reject')) {
                const btn = e.target.closest('.btn-reject');
                this.handleReject(btn.getAttribute('data-request-id'));
            } else if (e.target.closest('.btn-respond')) {
                const btn = e.target.closest('.btn-respond');
                this.openResponseModal(btn.getAttribute('data-request-id'), btn.getAttribute('data-title'));
            } else if (e.target.closest('.btn-fulfill')) {
                const btn = e.target.closest('.btn-fulfill');
                this.handleFulfill(btn.getAttribute('data-request-id'));
            }
        });

        // Response modal
        this.setupResponseModal();

        // Confirm modal
        this.setupConfirmModal();
    }

    setupResponseModal() {
        const modal = document.getElementById('responseModal');
        const closeBtn = document.getElementById('closeResponseModal');
        const cancelBtn = document.getElementById('cancelResponse');
        const form = document.getElementById('responseForm');

        if (closeBtn) {
            closeBtn.onclick = () => { modal.style.display = 'none'; };
        }

        if (cancelBtn) {
            cancelBtn.onclick = () => { modal.style.display = 'none'; };
        }

        window.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        });

        if (form) {
            form.onsubmit = (e) => this.handleResponseSubmit(e);
        }
    }

    setupConfirmModal() {
        const modal = document.getElementById('confirmModal');
        const closeBtn = document.getElementById('closeConfirmModal');
        const cancelBtn = document.getElementById('cancelConfirm');
        const confirmBtn = document.getElementById('confirmAction');

        if (closeBtn) {
            closeBtn.onclick = () => { modal.style.display = 'none'; };
        }

        if (cancelBtn) {
            cancelBtn.onclick = () => { modal.style.display = 'none'; };
        }

        if (confirmBtn) {
            confirmBtn.onclick = () => {
                modal.style.display = 'none';
                if (this.confirmAction) {
                    this.confirmAction();
                }
            };
        }

        window.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        });
    }

    filterByMonth(month) {
        const params = new URLSearchParams();
        if (month) params.append('month_year', month);
        if (this.activeStatus) params.append('status', this.activeStatus);

        const newUrl = `/admin/book-requests${params.toString() ? '?' + params.toString() : ''}`;
        window.history.pushState({}, '', newUrl);
        this.activeMonth = month;
        this.loadData(true);
    }

    filterByStatus(status) {
        const params = new URLSearchParams();
        if (status) params.append('status', status);
        if (this.activeMonth) params.append('month_year', this.activeMonth);

        const newUrl = `/admin/book-requests${params.toString() ? '?' + params.toString() : ''}`;
        window.history.pushState({}, '', newUrl);
        this.activeStatus = status;
        this.loadData(true);
    }

    openResponseModal(requestId, title) {
        const modal = document.getElementById('responseModal');
        document.getElementById('requestId').value = requestId;
        document.getElementById('modalTitle').textContent = `Respond to: ${title}`;

        // Set current status as default
        const request = this.bookRequests.find(r => r.id === parseInt(requestId));
        if (request) {
            const statusSelect = document.getElementById('statusSelect');
            statusSelect.value = request.status;
        }

        modal.style.display = 'flex';
    }

    async handleResponseSubmit(e) {
        e.preventDefault();

        const requestId = document.getElementById('requestId').value;
        const submitBtn = document.getElementById('submitResponse');

        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting...';

        try {
            const formData = new FormData(e.target);
            const response = await fetch(`/api/book-requests/${requestId}/respond`, {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (response.ok) {
                this.showNotification('success', 'Response submitted successfully');
                document.getElementById('responseModal').style.display = 'none';
                // WebSocket will handle UI update
            } else {
                this.showNotification('error', result.detail || 'Error submitting response');
            }
        } catch (error) {
            console.error('Error:', error);
            this.showNotification('error', 'An error occurred while submitting your response');
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Submit Response';
        }
    }

    handleApprove(requestId) {
        this.showConfirmModal(
            'Approve Book Request',
            'Are you sure you want to approve this book request?',
            () => {
                const message = document.getElementById('confirmResponseMessage').value ||
                    'Request approved. We will try to add this book to our catalog.';
                this.respondToRequest(requestId, 'approved', message);
            },
            true,
            'Request approved. We will try to add this book to our catalog.'
        );
    }

    handleReject(requestId) {
        const request = this.bookRequests.find(r => r.id === parseInt(requestId));
        const isApproved = request && request.status === 'approved';

        let message = 'Are you sure you want to reject this book request?';
        let defaultMessage = 'We are unable to fulfill this request at this time.';

        if (isApproved) {
            message = 'Are you sure you want to change this approved request to rejected?';
            defaultMessage = 'After further review, we are unable to fulfill this book request.';
        }

        this.showConfirmModal(
            'Reject Book Request',
            message,
            () => {
                const responseMessage = document.getElementById('confirmResponseMessage').value || defaultMessage;
                this.respondToRequest(requestId, 'rejected', responseMessage);
            },
            true,
            defaultMessage
        );
    }

    handleFulfill(requestId) {
        this.showConfirmModal(
            'Mark as Fulfilled',
            'Are you sure you want to mark this book request as fulfilled? This indicates the book has been added to the catalog.',
            () => {
                const message = document.getElementById('confirmResponseMessage').value ||
                    'Good news! Your book request has been fulfilled and the book is now available in our catalog.';
                this.respondToRequest(requestId, 'fulfilled', message);
            },
            true,
            'Good news! Your book request has been fulfilled and the book is now available in our catalog.'
        );
    }

    showConfirmModal(title, message, action, showResponseField, defaultResponse) {
        document.getElementById('confirmModalTitle').textContent = title;
        document.getElementById('confirmModalMessage').textContent = message;

        const responseContainer = document.getElementById('confirmResponseContainer');
        if (showResponseField) {
            responseContainer.style.display = 'block';
            document.getElementById('confirmResponseMessage').value = defaultResponse || '';
        } else {
            responseContainer.style.display = 'none';
        }

        this.confirmAction = action;
        document.getElementById('confirmModal').style.display = 'flex';
    }

    async respondToRequest(requestId, status, message) {
        try {
            const formData = new FormData();
            formData.append('status', status);
            formData.append('response_message', message || '');

            const response = await fetch(`/api/book-requests/${requestId}/respond`, {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (response.ok) {
                this.showNotification('success', result.message || 'Response recorded successfully');
                // WebSocket will handle UI update
            } else {
                this.showNotification('error', result.detail || 'Error updating request');
            }
        } catch (error) {
            console.error('Error:', error);
            this.showNotification('error', 'An error occurred while processing your request');
        }
    }

    initWebSocket() {
        if (this.ws) return;

        const userId = window.currentUserId;
        if (!userId) return;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/book-requests/ws?user_id=${userId}`;

        try {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('📚 Book Requests WebSocket connected (admin)');
            };

            this.ws.onmessage = (event) => {
                if (event.data === 'ping') {
                    this.ws.send('pong');
                    return;
                }

                console.log('🔌 [Admin SPA] Raw WebSocket message received:', event.data.substring(0, 200));

                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    console.error('❌ [Admin SPA] Error parsing WebSocket message:', error, event.data);
                }
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };

            this.ws.onclose = () => {
                console.log('📚 Book Requests WebSocket disconnected');
                this.ws = null;
            };
        } catch (error) {
            console.error('Error creating WebSocket:', error);
        }
    }

    async handleWebSocketMessage(data) {
        console.log('📨 [Admin SPA] WebSocket message received:', {
            type: data.type,
            action: data.action,
            timestamp: new Date().toISOString()
        });

        switch (data.type) {
            case 'book_request_update': {
                const updatedRequest = data.book_request;
                if (!updatedRequest) {
                    console.warn('⚠️ [Admin SPA] No book_request in update, reloading data');
                    await this.loadData(true);
                    return;
                }

                const updatedStatus = (updatedRequest.status || '').toLowerCase();
                const updatedId = Number(updatedRequest.id);
                const activeStatus = (this.activeStatus || '').toLowerCase();
                const requestIndex = this.bookRequests.findIndex(req => Number(req.id) === updatedId);

                console.log('📚 [Admin SPA] book_request_update received', {
                    action: data.action,
                    updatedId,
                    updatedStatus,
                    activeStatus: activeStatus || 'NONE (showing all)',
                    requestIndex,
                    requestInArray: requestIndex !== -1 ? 'YES' : 'NO',
                    listLength: this.bookRequests.length,
                    url: window.location.href,
                    queryParams: window.location.search
                });

                if (data.action === 'created') {
                    // Insert new request at the top if it matches current filter
                    if (!activeStatus || updatedStatus === activeStatus) {
                        if (requestIndex !== -1) {
                            this.bookRequests[requestIndex] = {
                                ...this.bookRequests[requestIndex],
                                ...updatedRequest,
                                id: updatedId
                            };
                        } else {
                            this.bookRequests.unshift({
                                ...updatedRequest,
                                id: updatedId
                            });
                        }
                        this.recalculateAggregates();
                        this.renderAll();
                    } else {
                        // Doesn't match current filter, but still refresh counts
                        await this.loadData(true);
                    }
                    return;
                }

                if (requestIndex !== -1) {
                    console.log(`🔍 [Admin SPA] Request #${updatedId} found in array at index ${requestIndex}`);

                    // Merge updated fields into existing record
                    this.bookRequests[requestIndex] = {
                        ...this.bookRequests[requestIndex],
                        ...updatedRequest,
                        id: updatedId
                    };

                    console.log(`🔍 [Admin SPA] Checking removal condition:`, {
                        hasActiveFilter: !!activeStatus,
                        activeStatus,
                        updatedStatus,
                        statusMismatch: updatedStatus !== activeStatus,
                        shouldRemove: activeStatus && updatedStatus !== activeStatus
                    });

                    // If the updated request no longer matches the active status, remove it
                    if (activeStatus && updatedStatus !== activeStatus) {
                        console.log(`✂️ [Admin SPA] REMOVING: Status filter is "${activeStatus}", new status is "${updatedStatus}" - request #${updatedId} no longer matches`);

                        // Remove from array
                        this.bookRequests.splice(requestIndex, 1);
                        console.log(`✅ [Admin SPA] Removed from array. New array length: ${this.bookRequests.length}`);

                        // Find and remove card element
                        const cardEl = document.getElementById(`request-${updatedId}`);
                        console.log(`🔍 [Admin SPA] Looking for card element #request-${updatedId}:`, cardEl ? 'FOUND' : 'NOT FOUND');

                        if (cardEl) {
                            console.log(`✂️ [Admin SPA] Fading out and removing card element #${updatedId}`);
                            cardEl.style.transition = 'opacity 0.3s ease-out';
                            cardEl.style.opacity = '0';
                            setTimeout(() => {
                                cardEl.remove();
                                console.log(`✅ [Admin SPA] Card #${updatedId} removed from DOM`);
                            }, 300);
                        } else {
                            console.warn(`⚠️ [Admin SPA] Card element #request-${updatedId} not found in DOM!`);
                        }

                        // Recalculate and render after removal
                        this.recalculateAggregates();
                        this.renderStatusBadges();
                        console.log(`✅ [Admin SPA] Recalculated aggregates and updated badges. Returning early (no renderAll).`);
                        return; // Don't call renderAll(), we've handled the removal
                    } else {
                        console.log(`✅ [Admin SPA] Request #${updatedId} still matches filter or no filter active - updating in place`);
                    }
                } else {
                    console.log(`🔍 [Admin SPA] Request #${updatedId} NOT in current array`);

                    // Not currently in view (likely different filter) – add it only if it now matches
                    if (!activeStatus || updatedStatus === activeStatus) {
                        console.log(`➕ [Admin SPA] Adding request #${updatedId} to view (matches filter or no filter)`);
                        this.bookRequests.unshift({
                            ...updatedRequest,
                            id: updatedId
                        });
                    } else {
                        console.log(`🚫 [Admin SPA] Request #${updatedId} doesn't match filter "${activeStatus}" - ignoring`);
                        return;
                    }
                }

                console.log(`🔄 [Admin SPA] Calling recalculateAggregates() and renderAll()`);
                this.recalculateAggregates();
                this.renderAll();
                break;
            }
            case 'pending_count_update':
                console.log(`📊 [Admin SPA] pending_count_update received:`, {
                    oldCount: this.statusCounts.pending,
                    newCount: data.pending_count,
                    delta: data.pending_count - (this.statusCounts.pending || 0)
                });
                // Update pending badge
                this.statusCounts.pending = data.pending_count;
                this.renderStatusBadges();

                // ✅ Update global navigation badges (desktop, mobile sidebar, mobile quick nav)
                if (window.BadgeManager) {
                    console.log(`🔄 [Admin SPA] Updating BadgeManager with count: ${data.pending_count}`);
                    window.BadgeManager.state.count = data.pending_count;
                    window.BadgeManager.updateBadges();
                    window.BadgeManager.updateMobileQuickNavBadge();
                }

                // ✅ Update combined Admin dropdown badge
                if (window.updateAdminDropdownBadge) {
                    window.updateAdminDropdownBadge();
                }

                console.log(`✅ [Admin SPA] Pending badge updated to ${data.pending_count} (page + global nav)`);
                break;
            case 'connected':
                console.log('📚 WebSocket connected:', data.message);
                break;
            case 'initial_data':
                console.log('📚 [Admin SPA] Initial data received, ignoring (already loaded from API)');
                break;
            default:
                console.warn('⚠️ [Admin SPA] Unknown WebSocket message type:', {
                    type: data.type,
                    fullData: data
                });
                break;
        }
    }

    showNotification(type, message) {
        const notificationArea = document.getElementById('notification-area');
        if (!notificationArea) return;

        const notification = document.createElement('div');
        notification.className = `alert alert-${type === 'success' ? 'success' : 'danger'} alert-dismissible fade show`;
        notification.innerHTML = `
            <strong>${type === 'success' ? 'Success!' : 'Error!'}</strong> ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        `;

        notificationArea.appendChild(notification);

        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => notification.remove(), 300);
        }, 5000);
    }

    renderError() {
        const container = document.getElementById('requestsList');
        if (container) {
            container.innerHTML = `
                <div class="alert alert-danger">
                    <i class="fas fa-exclamation-triangle"></i> Error loading book requests. Please try refreshing the page.
                </div>
            `;
        }
    }

    capitalizeStatus(status) {
        if (!status) return '';
        return status.charAt(0).toUpperCase() + status.slice(1);
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

    clearBookRequestBadge() {
        console.log('🧹 [Admin SPA] Clearing book request badge (page viewed)');

        // Clear BadgeManager state
        if (window.BadgeManager) {
            window.BadgeManager.state.count = 0;
            window.BadgeManager.updateBadges();
            window.BadgeManager.updateMobileQuickNavBadge();
        }

        // Clear global variable
        window.currentPendingRequests = 0;

        // Update combined Admin dropdown badge
        if (window.updateAdminDropdownBadge) {
            window.updateAdminDropdownBadge();
        }

        console.log('✅ [Admin SPA] Badge cleared');
    }

    async destroy() {
        console.log('🧹 ManageBookRequests: Destroying...');
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
