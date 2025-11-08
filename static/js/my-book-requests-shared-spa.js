// my-book-requests-shared-spa.js - Universal controller for my book requests (SSR and SPA modes)

export class MyBookRequestsController {
    constructor(mode = 'spa') {
        this.mode = mode; // 'ssr' or 'spa'
        this.bookRequests = [];
        this.quota = null;
        this.requestsByMonth = {};
        this.statusCounts = {};
        this.ws = null;
    }

    // ✅ For SPA mode: generate HTML
    async render() {
        if (this.mode === 'ssr') {
            throw new Error('render() should not be called in SSR mode');
        }

        // Just generate HTML with loading state for SPA mode
        return this.generateHTML();
    }

    // ✅ For both modes: attach event listeners and initialize
    async mount() {
        console.log(`📚 MyBookRequests: Mounting in ${this.mode} mode...`);

        if (this.mode === 'ssr') {
            // SSR: Read bootstrap data from DOM if available
            this.hydrateFromDOM();
            // Render the hydrated data immediately
            this.renderSubmitForm();
            this.renderRequests();
            // Set up event listeners for SSR rendered content
            this.setupEventListeners();
        }

        // Load/refresh data (now DOM exists)
        await this.loadData().catch(err => {
            console.error('Failed to load book requests:', err);
            this.renderError();
        });

        // Set up event listeners after data loads (for SPA mode)
        if (this.mode === 'spa') {
            this.setupEventListeners();
        }

        this.initWebSocket();

        console.log('✅ MyBookRequests: Mounted successfully');
    }

    // ✅ Read data from DOM (SSR mode)
    hydrateFromDOM() {
        const bootstrapScript = document.getElementById('book-requests-bootstrap-data');
        if (bootstrapScript) {
            try {
                const data = JSON.parse(bootstrapScript.textContent);
                this.quota = data.quota || null;
                this.bookRequests = data.requests || [];
                this.organizeRequests();
                console.log('📦 Hydrated book requests data from DOM');
            } catch (error) {
                console.error('Error parsing bootstrap data:', error);
            }
        }
    }

    // ✅ Generate HTML for SPA mode
    generateHTML() {
        return `
            <div class="container">
                <div class="row">
                    <div class="col-lg-8">
                        <div class="request-section">
                            <div class="card">
                                <div class="card-header">
                                    <h2>Submit a New Book Request</h2>
                                </div>
                                <div class="card-body" id="submitFormContainer">
                                    <div class="loading">
                                        <i class="fas fa-spinner fa-spin"></i>
                                        <p>Loading...</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="request-section">
                            <h2>My Book Requests <span id="totalRequestsCount"></span></h2>
                            <div id="requestsContainer">
                                <div class="loading">
                                    <i class="fas fa-spinner fa-spin"></i>
                                    <p>Loading your requests...</p>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="col-lg-4">
                        <div class="card">
                            <div class="card-header">
                                <h3>About Book Requests</h3>
                            </div>
                            <div class="card-body">
                                <p>As a valued patron, you can request books you'd like to see added to the catalog.</p>
                                <ul>
                                    <li>You get <strong id="quotaAllowed"></strong> requests per month based on your patron tier</li>
                                    <li>Your requests will be reviewed by the creator</li>
                                    <li>Approved requests will be added to the acquisition list</li>
                                    <li>Unused requests don't carry over to the next month</li>
                                    <li>Request quota resets at the beginning of each month</li>
                                    <li><strong>You can reply to admin responses to provide additional information</strong></li>
                                </ul>
                                <p class="mb-0">Have more books you'd like to request? Consider upgrading your patron tier for additional benefits.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    async destroy() {
        console.log('📚 MyBookRequests: Destroying...');
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        return Promise.resolve();
    }

    async loadData() {
        try {
            const [quotaResponse, requestsResponse] = await Promise.all([
                fetch('/api/book-requests/quota'),
                fetch('/api/book-requests/')
            ]);

            if (!quotaResponse.ok || !requestsResponse.ok) {
                throw new Error('Failed to load data');
            }

            this.quota = await quotaResponse.json();
            const requestsData = await requestsResponse.json();
            this.bookRequests = requestsData.requests || [];

            this.organizeRequests();
            this.renderSubmitForm();
            this.renderRequests();

        } catch (error) {
            console.error('❌ Error loading book requests data:', error);
            this.renderError();
        }
    }

    organizeRequests() {
        this.requestsByMonth = {};
        this.statusCounts = {
            total: this.bookRequests.length,
            pending: 0,
            approved: 0,
            rejected: 0,
            fulfilled: 0
        };

        this.bookRequests.forEach(request => {
            const month = request.month_year;
            if (!this.requestsByMonth[month]) {
                this.requestsByMonth[month] = [];
            }
            this.requestsByMonth[month].push(request);

            const status = request.status?.toLowerCase();
            if (this.statusCounts.hasOwnProperty(status)) {
                this.statusCounts[status]++;
            }
        });
    }

    renderSubmitForm() {
        const container = document.getElementById('submitFormContainer');
        if (!container) return;

        // Update the quota in sidebar
        const quotaAllowed = document.getElementById('quotaAllowed');
        if (quotaAllowed) {
            quotaAllowed.textContent = this.quota.requests_allowed;
        }

        if (this.quota.requests_remaining > 0) {
            const progressPercent = this.quota.requests_allowed > 0
                ? (this.quota.requests_used / this.quota.requests_allowed * 100)
                : 0;

            container.innerHTML = `
                <div class="mb-3">
                    <p>You have <span class="fw-bold">${this.quota.requests_remaining}</span> book requests remaining for this month (${this.quota.current_month}).</p>
                    <div class="progress quota-progress">
                        <div class="progress-bar" role="progressbar"
                             style="width: ${progressPercent}%"
                             aria-valuenow="${this.quota.requests_used}"
                             aria-valuemin="0"
                             aria-valuemax="${this.quota.requests_allowed}"></div>
                    </div>
                    <p class="small text-muted">
                        ${this.quota.requests_used} used of ${this.quota.requests_allowed} allowed
                        • Quota resets at the beginning of each month
                    </p>
                </div>

                <form id="bookRequestForm" method="POST" action="/api/book-requests/">
                    <div class="mb-3">
                        <label for="bookTitle" class="form-label">Book Title *</label>
                        <input type="text" class="form-control" id="bookTitle" name="title" required>
                    </div>
                    <div class="mb-3">
                        <label for="bookAuthor" class="form-label">Author *</label>
                        <input type="text" class="form-control" id="bookAuthor" name="author" required>
                    </div>
                    <div class="mb-3">
                        <label for="bookLink" class="form-label">Link (optional)</label>
                        <input type="url" class="form-control" id="bookLink" name="link" placeholder="https://...">
                        <div class="form-text">Link is optional</div>
                    </div>
                    <div class="mb-3">
                        <label for="bookDescription" class="form-label">Description/Notes (optional)</label>
                        <textarea class="form-control" id="bookDescription" name="description" rows="3" placeholder="Why you'd like this book, additional details, etc."></textarea>
                    </div>
                    <button type="submit" class="btn btn-primary" id="submitBtn">Submit Request</button>
                    <div id="requestStatus" class="alert mt-3" style="display: none;"></div>
                </form>
            `;
        } else {
            container.innerHTML = `
                <div class="alert alert-warning">
                    <div class="d-flex flex-column">
                        <div class="mb-3">
                            <i class="fas fa-exclamation-triangle"></i> You've used all your book requests for this month. Quota will reset at the beginning of next month.
                        </div>
                        <div class="d-flex justify-content-center">
                            <a href="/support" class="btn btn-primary">
                                <i class="fas fa-arrow-circle-up"></i> Upgrade Your Membership
                            </a>
                        </div>
                    </div>
                </div>
            `;
        }
    }

    renderRequests() {
        const container = document.getElementById('requestsContainer');
        if (!container) return;

        // Update total count
        const totalCount = document.getElementById('totalRequestsCount');
        if (totalCount) {
            totalCount.textContent = `(${this.statusCounts.total})`;
        }

        const months = Object.keys(this.requestsByMonth).sort().reverse();

        if (months.length === 0) {
            container.innerHTML = `
                <div class="alert alert-info">
                    <i class="fas fa-info-circle"></i> You haven't made any book requests yet.
                </div>
            `;
            return;
        }

        let html = `
            <div class="card mb-3">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <div>
                            <span class="badge bg-warning text-dark me-2">${this.statusCounts.pending} Pending</span>
                            <span class="badge bg-success me-2">${this.statusCounts.approved} Approved</span>
                            <span class="badge bg-danger me-2">${this.statusCounts.rejected} Rejected</span>
                            <span class="badge bg-primary">${this.statusCounts.fulfilled} Fulfilled</span>
                        </div>
                    </div>
                </div>
            </div>
        `;

        months.forEach((month, index) => {
            const requests = this.requestsByMonth[month];
            html += `
                <div class="month-container">
                    <div class="month-header ${index === 0 ? 'active' : ''}" data-month="${month}">
                        <h3>${month}</h3>
                        <span class="badge bg-secondary">${requests.length}</span>
                    </div>
                    <div class="month-requests">
                        ${requests.map(req => this.createRequestCard(req)).join('')}
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    }

    createRequestCard(request) {
        const createdAt = request.created_at.replace('T', ' ').substring(0, 16);
        const responseDate = request.response_date ? request.response_date.replace('T', ' ').substring(0, 16) : '';

        return `
            <div class="request-card" id="request-${request.id}">
                <div class="d-flex justify-content-between align-items-start">
                    <h3>${this.escapeHtml(request.title)}</h3>
                    <span class="request-status status-${request.status}">${this.capitalizeStatus(request.status)}</span>
                </div>
                <div class="request-meta">
                    <span>Author: ${this.escapeHtml(request.author)}</span>
                    <span>Requested: ${createdAt}</span>
                </div>
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

                ${request.response_message ? `
                    <div class="response-box">
                        <p class="mb-2"><strong>Response:</strong></p>
                        <p>${this.escapeHtml(request.response_message)}</p>
                        <p class="mt-2 text-muted">
                            <small>Responded on ${responseDate}</small>
                        </p>
                    </div>

                    ${request.user_reply ? `
                        <!-- Show existing user reply -->
                        <div class="user-reply-box">
                            <p class="mb-2"><strong>Your Reply:</strong></p>
                            <p>${this.escapeHtml(request.user_reply)}</p>
                            <p class="mt-2 text-muted">
                                <small><i class="fas fa-clock"></i> Waiting for admin response...</small>
                            </p>
                        </div>
                    ` : request.status === 'rejected' ? `
                        <!-- No reply allowed for rejected requests -->
                        <div class="alert alert-info mt-3">
                            <i class="fas fa-info-circle"></i> Replies are not allowed for rejected requests.
                        </div>
                    ` : `
                        <!-- Show reply form if no user reply exists and not rejected -->
                        <button class="reply-toggle-btn" data-request-id="${request.id}">
                            <i class="fas fa-reply"></i> Reply to Response
                        </button>

                        <div class="user-reply-form" id="replyForm${request.id}" style="display: none;">
                            <form class="reply-form" data-request-id="${request.id}">
                                <div class="mb-3">
                                    <label for="userReply${request.id}" class="form-label">Your Reply:</label>
                                    <textarea
                                        class="form-control"
                                        id="userReply${request.id}"
                                        name="user_reply"
                                        rows="3"
                                        placeholder="Add your reply to the admin response..."
                                        required
                                    ></textarea>
                                    <div class="form-text">
                                        <i class="fas fa-info-circle"></i> You can reply to continue the conversation.
                                        After you reply, wait for the admin to respond before you can reply again.
                                    </div>
                                </div>
                                <div class="btn-group">
                                    <button type="button" class="btn-cancel" data-request-id="${request.id}">Cancel</button>
                                    <button type="submit" class="btn-reply">
                                        <i class="fas fa-paper-plane"></i> Send Reply
                                    </button>
                                </div>
                                <div class="alert mt-3" id="replyStatus${request.id}" style="display: none;"></div>
                            </form>
                        </div>
                    `}
                ` : ''}
            </div>
        `;
    }

    capitalizeStatus(status) {
        if (!status) return '';
        return status.charAt(0).toUpperCase() + status.slice(1);
    }

    setupEventListeners() {
        // Month header toggle
        document.querySelectorAll('.month-header').forEach(header => {
            header.addEventListener('click', () => {
                header.classList.toggle('active');
            });
        });

        // Form submission
        const form = document.getElementById('bookRequestForm');
        if (form) {
            form.addEventListener('submit', (e) => this.handleSubmit(e));
        }

        // Reply toggle buttons
        document.querySelectorAll('.reply-toggle-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const requestId = btn.getAttribute('data-request-id');
                const replyForm = document.getElementById(`replyForm${requestId}`);

                if (replyForm.style.display === 'none' || replyForm.style.display === '') {
                    replyForm.style.display = 'block';
                    btn.style.display = 'none';
                }
            });
        });

        // Reply cancel buttons
        document.querySelectorAll('.btn-cancel').forEach(btn => {
            btn.addEventListener('click', () => {
                const requestId = btn.getAttribute('data-request-id');
                const replyForm = document.getElementById(`replyForm${requestId}`);
                const toggleBtn = document.querySelector(`.reply-toggle-btn[data-request-id="${requestId}"]`);

                replyForm.style.display = 'none';
                if (toggleBtn) {
                    toggleBtn.style.display = 'inline-block';
                }

                // Clear the textarea
                const textarea = replyForm.querySelector('textarea');
                if (textarea) {
                    textarea.value = '';
                }
            });
        });

        // Reply form submission
        document.querySelectorAll('.reply-form').forEach(form => {
            form.addEventListener('submit', (e) => this.handleReplySubmit(e));
        });
    }

    async handleSubmit(e) {
        e.preventDefault();

        const form = e.target;
        const submitBtn = document.getElementById('submitBtn');
        const statusDiv = document.getElementById('requestStatus');

        // Disable button and show loading state
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Submitting...';

        try {
            const formData = new FormData(form);
            const response = await fetch('/api/book-requests/', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (response.ok) {
                // Success
                statusDiv.className = 'alert alert-success mt-3';
                statusDiv.innerHTML = '<i class="fas fa-check-circle"></i> Book request submitted successfully!';
                statusDiv.style.display = 'block';
                form.reset();

                // WebSocket will handle UI updates - no page reload needed
                setTimeout(() => {
                    statusDiv.style.display = 'none';
                }, 3000);
            } else {
                // Error
                statusDiv.className = 'alert alert-danger mt-3';
                if (result.detail && result.detail.message) {
                    statusDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${result.detail.message}`;
                } else {
                    statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> There was an error submitting your request. Please try again.';
                }
                statusDiv.style.display = 'block';
            }
        } catch (error) {
            // Network or other error
            statusDiv.className = 'alert alert-danger mt-3';
            statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> There was an error submitting your request. Please try again.';
            statusDiv.style.display = 'block';
            console.error('Error:', error);
        } finally {
            // Reset button state
            submitBtn.disabled = false;
            submitBtn.innerHTML = 'Submit Request';
        }
    }

    async handleReplySubmit(e) {
        e.preventDefault();

        const form = e.target;
        const requestId = form.getAttribute('data-request-id');
        const submitBtn = form.querySelector('.btn-reply');
        const statusDiv = document.getElementById(`replyStatus${requestId}`);
        const textarea = form.querySelector('textarea');

        if (!textarea.value.trim()) {
            statusDiv.className = 'alert alert-danger mt-3';
            statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Please enter a reply message.';
            statusDiv.style.display = 'block';
            return;
        }

        // Disable button and show loading state
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';

        try {
            const formData = new FormData();
            formData.append('user_reply', textarea.value);

            const response = await fetch(`/api/book-requests/${requestId}/reply`, {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (response.ok) {
                // Success
                statusDiv.className = 'alert alert-success mt-3';
                statusDiv.innerHTML = '<i class="fas fa-check-circle"></i> Reply sent successfully!';
                statusDiv.style.display = 'block';

                // WebSocket will handle UI updates - no page reload needed
                setTimeout(() => {
                    statusDiv.style.display = 'none';
                }, 2000);
            } else {
                // Error
                statusDiv.className = 'alert alert-danger mt-3';
                if (result.detail) {
                    statusDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${result.detail}`;
                } else {
                    statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> There was an error sending your reply. Please try again.';
                }
                statusDiv.style.display = 'block';
            }
        } catch (error) {
            // Network or other error
            statusDiv.className = 'alert alert-danger mt-3';
            statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> There was an error sending your reply. Please try again.';
            statusDiv.style.display = 'block';
            console.error('Error:', error);
        } finally {
            // Reset button state
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Send Reply';
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
                console.log('📚 Book Requests WebSocket connected');
            };

            this.ws.onmessage = (event) => {
                if (event.data === 'ping') {
                    this.ws.send('pong');
                    return;
                }

                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
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
        console.log('📚 [SPA] WebSocket message received:', data.type);

        switch (data.type) {
            case 'book_request_update':
                console.log('📚 [SPA] book_request_update - delegating to global WebSocket handler');
                // ✅ FIX: Don't reload all data - let book-request-websocket.js handle card updates
                // Only update if this is a NEW book request (created action)
                if (data.action === 'created' && data.book_request) {
                    console.log('📚 [SPA] New book request created, adding to list');
                    // Add the new request to our data
                    this.bookRequests.unshift(data.book_request);
                    // Re-organize and re-render
                    this.organizeRequests();
                    this.renderRequests();
                    this.setupEventListeners();
                } else {
                    console.log('📚 [SPA] Status update - book-request-websocket.js will handle card update');
                    // For status updates, just update our local data without re-rendering
                    // (book-request-websocket.js already updated the DOM)
                    const index = this.bookRequests.findIndex(r => r.id === data.book_request?.id);
                    if (index !== -1) {
                        this.bookRequests[index] = { ...this.bookRequests[index], ...data.book_request };
                        // Update counts without re-rendering
                        this.organizeRequests();
                        // Update just the status badges
                        this.updateStatusBadges();
                    }
                }
                break;
            case 'quota_update':
                console.log('📚 [SPA] quota_update:', data.quota);
                this.quota = data.quota;
                this.renderSubmitForm();
                break;
            case 'connected':
                console.log('📚 WebSocket connected:', data.message);
                break;
        }
    }

    updateStatusBadges() {
        // Update the status count badges without re-rendering all cards
        const container = document.querySelector('.card-body');
        if (container) {
            const badgeContainer = container.querySelector('.d-flex');
            if (badgeContainer) {
                badgeContainer.innerHTML = `
                    <div>
                        <span class="badge bg-warning text-dark me-2">${this.statusCounts.pending} Pending</span>
                        <span class="badge bg-success me-2">${this.statusCounts.approved} Approved</span>
                        <span class="badge bg-danger me-2">${this.statusCounts.rejected} Rejected</span>
                        <span class="badge bg-primary">${this.statusCounts.fulfilled} Fulfilled</span>
                    </div>
                `;
            }
        }

        // Update total count
        const totalCount = document.getElementById('totalRequestsCount');
        if (totalCount) {
            totalCount.textContent = `(${this.statusCounts.total})`;
        }
    }

    renderError() {
        const container = document.getElementById('requestsContainer');
        if (container) {
            container.innerHTML = `
                <div class="alert alert-danger">
                    <i class="fas fa-exclamation-triangle"></i> Error loading book requests. Please try refreshing the page.
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
}
