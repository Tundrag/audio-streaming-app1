// Concise Book Request WebSocket Manager
const BookRequestWebSocketManager = {
    state: {
        socket: null,
        isConnected: false,
        userId: null,
        isAdmin: false,
        currentPage: null,
        reconnectAttempts: 0
    },
    
    init(userId, isAdmin, currentPage) {
        Object.assign(this.state, { userId, isAdmin, currentPage });
        if (userId) this.connect();
    },
    
    connect() {
        if (!window.WebSocket) return;
        
        try {
            if (this.state.socket) this.state.socket.close();
            
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            this.state.socket = new WebSocket(`${protocol}//${location.host}/api/book-requests/ws?user_id=${this.state.userId}`);
            
            this.state.socket.onopen = () => {
                this.state.isConnected = true;
                this.state.reconnectAttempts = 0;
                this.updateConnectionIndicator(true);
            };
            
            this.state.socket.onmessage = (event) => {
                if (event.data === 'ping') {
                    this.state.socket.send('pong');
                    return;
                }
                
                try {
                    const data = JSON.parse(event.data);
                    this.handleMessage(data);
                } catch (e) {
                    console.error('WebSocket message error:', e);
                }
            };
            
            this.state.socket.onclose = () => {
                this.state.isConnected = false;
                this.updateConnectionIndicator(false);
                this.scheduleReconnect();
            };
            
        } catch (e) {
            console.error('WebSocket connection failed:', e);
        }
    },
    
    handleMessage(data) {
        switch (data.type) {
            case 'book_request_update':
                this.updateBookRequest(data.book_request);
                break;
            case 'quota_update':
                if (this.state.currentPage === 'user') this.updateQuota(data.quota);
                break;
            case 'pending_count_update':
                if (this.state.isAdmin) this.updatePendingCount(data.pending_count);
                break;
        }
    },
    
    updateBookRequest(bookRequest) {
        console.log('📚 [WebSocket] updateBookRequest called:', {
            id: bookRequest.id,
            status: bookRequest.status,
            currentPage: this.state.currentPage,
            url: window.location.href
        });

        const card = document.getElementById(`request-${bookRequest.id}`);
        if (!card) {
            console.log(`⚠️ [WebSocket] Card not found for request #${bookRequest.id}`);
            return;
        }

        // Update status badge
        const statusBadge = card.querySelector('.status-badge, .request-status');
        if (statusBadge) {
            const oldStatus = statusBadge.textContent.toLowerCase().trim();
            const newStatus = bookRequest.status.toLowerCase().trim();
            const statusChanged = oldStatus !== newStatus;

            console.log('🔄 [WebSocket] Status check:', {
                requestId: bookRequest.id,
                oldStatus,
                newStatus,
                statusChanged,
                url: window.location.href
            });

            if (statusChanged) {
                // Check if we're viewing a filtered status (e.g., only "pending")
                const urlParams = new URLSearchParams(window.location.search);
                const activeFilter = urlParams.get('status');

                console.log('🎯 [WebSocket] Filter check:', {
                    requestId: bookRequest.id,
                    activeFilter: activeFilter || 'none',
                    newStatus,
                    shouldRemove: activeFilter && activeFilter !== newStatus,
                    comparisonResult: activeFilter === newStatus ? 'MATCH' : 'NO MATCH'
                });

                // If viewing filtered list and status no longer matches, remove the card
                if (activeFilter && activeFilter !== newStatus) {
                    console.log(`✂️ [WebSocket] REMOVING request #${bookRequest.id} from ${activeFilter} view (new status: ${newStatus})`);
                    card.style.transition = 'opacity 0.3s ease-out';
                    card.style.opacity = '0';
                    setTimeout(() => {
                        card.remove();
                        console.log(`✅ [WebSocket] Card successfully removed for request #${bookRequest.id}`);

                        // Check if page is now empty
                        const remainingCards = document.querySelectorAll('[id^="request-"]');
                        console.log(`📊 [WebSocket] Remaining cards: ${remainingCards.length}`);
                    }, 300);
                    return; // Don't update the card, it's being removed
                } else {
                    console.log(`✋ [WebSocket] NOT removing request #${bookRequest.id} - filter matches or no filter active`);
                }

                // Update status badge (only if staying in view)
                console.log(`🏷️ [WebSocket] Updating badge for request #${bookRequest.id}`);
                statusBadge.className = `${statusBadge.classList[0]} status-${bookRequest.status}`;
                statusBadge.textContent = bookRequest.status.charAt(0).toUpperCase() + bookRequest.status.slice(1);

                // Only update action buttons if status changed (admin page)
                if (this.state.currentPage === 'admin') {
                    this.updateActionButtons(card, bookRequest);
                }
            }
        } else {
            console.log(`⚠️ [WebSocket] Status badge not found for request #${bookRequest.id}`);
        }

        // Handle response message
        if (bookRequest.response_message) {
            this.updateResponse(card, bookRequest);
        }

        // Handle user reply
        if (bookRequest.user_reply) {
            this.updateUserReply(card, bookRequest);
        }
    },
    
    updateResponse(card, bookRequest) {
        if (this.state.currentPage === 'user') {
            // User page: Create/update response box and reply interaction
            let responseBox = card.querySelector('.response-box');
            
            if (!responseBox) {
                const insertPoint = card.querySelector('.request-description') || card.querySelector('.request-meta');
                if (insertPoint) {
                    insertPoint.insertAdjacentHTML('afterend', `
                        <div class="response-box">
                            <p class="mb-2"><strong>Response:</strong></p>
                            <p>${bookRequest.response_message}</p>
                            <p class="mt-2 text-muted">
                                <small>Responded on ${bookRequest.response_date.replace("T", " ").substring(0, 16)}</small>
                            </p>
                        </div>
                    `);
                    responseBox = card.querySelector('.response-box');
                }
            } else {
                // Update existing response
                const responseText = responseBox.querySelector('p:not(.text-muted)');
                if (responseText) responseText.textContent = bookRequest.response_message;
            }
            
            if (responseBox) this.updateReplyArea(card, bookRequest, responseBox);
            
        } else if (this.state.currentPage === 'admin') {
            // Admin page: Clean response display only
            let responseSection = card.querySelector('.admin-response-section');
            
            if (!responseSection) {
                const insertPoint = card.querySelector('.request-description') || card.querySelector('.request-meta');
                if (insertPoint) {
                    insertPoint.insertAdjacentHTML('afterend', `
                        <div class="admin-response-section">
                            <strong>Admin Response:</strong>
                            <p>${bookRequest.response_message}</p>
                            <small class="text-muted">Responded on ${bookRequest.response_date.replace("T", " ").substring(0, 16)}</small>
                        </div>
                    `);
                }
            } else {
                const responseText = responseSection.querySelector('p');
                if (responseText) responseText.textContent = bookRequest.response_message;
            }
        }
    },
    
    updateReplyArea(card, bookRequest, responseBox) {
        // Remove existing reply elements
        card.querySelectorAll('.user-reply-box, .reply-toggle-btn, .user-reply-form, .alert-info').forEach(el => {
            if (el.textContent.includes('reply') || el.textContent.includes('Reply')) {
                el.remove();
            }
        });
        
        let replyHTML = '';
        
        if (bookRequest.user_reply) {
            replyHTML = `
                <div class="user-reply-box">
                    <p class="mb-2"><strong>Your Reply:</strong></p>
                    <p>${bookRequest.user_reply}</p>
                    <p class="mt-2 text-muted">
                        <small><i class="fas fa-clock"></i> Waiting for admin response...</small>
                    </p>
                </div>
            `;
        } else if (bookRequest.status === 'rejected') {
            replyHTML = `
                <div class="alert alert-info mt-3">
                    <i class="fas fa-info-circle"></i> Replies are not allowed for rejected requests.
                </div>
            `;
        } else {
            replyHTML = `
                <button class="reply-toggle-btn" data-request-id="${bookRequest.id}">
                    <i class="fas fa-reply"></i> Reply to Response
                </button>
                <div class="user-reply-form" id="replyForm${bookRequest.id}" style="display: none;">
                    <form class="reply-form" data-request-id="${bookRequest.id}">
                        <div class="mb-3">
                            <label class="form-label">Your Reply:</label>
                            <textarea class="form-control" rows="3" placeholder="Add your reply..." required></textarea>
                        </div>
                        <div class="btn-group">
                            <button type="button" class="btn-cancel">Cancel</button>
                            <button type="submit" class="btn-reply">
                                <i class="fas fa-paper-plane"></i> Send Reply
                            </button>
                        </div>
                        <div class="alert mt-3" style="display: none;"></div>
                    </form>
                </div>
            `;
        }
        
        if (replyHTML) {
            responseBox.insertAdjacentHTML('afterend', replyHTML);
            this.attachReplyListeners(card, bookRequest.id);
        }
    },
    
    updateUserReply(card, bookRequest) {
        if (this.state.currentPage === 'admin' && bookRequest.user_reply) {
            // Admin view: Show user reply cleanly
            let userReplySection = card.querySelector('.admin-user-reply-section');
            
            if (!userReplySection) {
                const responseSection = card.querySelector('.admin-response-section');
                if (responseSection) {
                    responseSection.insertAdjacentHTML('afterend', `
                        <div class="admin-user-reply-section">
                            <strong>User Reply:</strong>
                            <p>${bookRequest.user_reply}</p>
                            <small class="text-muted">Reply from User</small>
                        </div>
                    `);
                }
            } else {
                const replyText = userReplySection.querySelector('p');
                if (replyText) replyText.textContent = bookRequest.user_reply;
            }
        }
    },
    
    updateActionButtons(card, bookRequest) {
        const actionButtons = card.querySelector('.action-buttons');
        if (!actionButtons) return;
        
        // Check if buttons are already correct
        const hasRespond = actionButtons.querySelector('.btn-respond');
        const hasApprove = actionButtons.querySelector('.btn-approve');
        const hasFulfill = actionButtons.querySelector('.btn-fulfill');
        
        const needsApprove = bookRequest.status === 'pending';
        const needsFulfill = bookRequest.status === 'approved';
        
        if (hasRespond && (needsApprove === !!hasApprove) && (needsFulfill === !!hasFulfill)) {
            return; // Buttons are already correct
        }
        
        // Rebuild buttons
        let buttonsHTML = '';
        
        if (bookRequest.status === 'pending') {
            buttonsHTML += `
                <button class="btn-action btn-approve" data-request-id="${bookRequest.id}">
                    <i class="fas fa-check"></i> Approve
                </button>
                <button class="btn-action btn-reject" data-request-id="${bookRequest.id}">
                    <i class="fas fa-times"></i> Reject
                </button>
            `;
        } else if (bookRequest.status === 'approved') {
            buttonsHTML += `
                <button class="btn-action btn-fulfill" data-request-id="${bookRequest.id}">
                    <i class="fas fa-check-circle"></i> Mark as Fulfilled
                </button>
                <button class="btn-action btn-reject" data-request-id="${bookRequest.id}">
                    <i class="fas fa-times"></i> Reject
                </button>
            `;
        }
        
        buttonsHTML += `
            <button class="btn-action btn-respond" data-request-id="${bookRequest.id}" data-title="${bookRequest.title}">
                <i class="fas fa-reply"></i> Respond
            </button>
        `;
        
        actionButtons.innerHTML = buttonsHTML;
        this.attachActionListeners(actionButtons);
    },
    
    attachReplyListeners(card, requestId) {
        const toggleBtn = card.querySelector('.reply-toggle-btn');
        const cancelBtn = card.querySelector('.btn-cancel');
        const replyForm = card.querySelector('.reply-form');
        
        if (toggleBtn) {
            toggleBtn.onclick = () => {
                const form = card.querySelector(`#replyForm${requestId}`);
                if (form) {
                    form.style.display = 'block';
                    toggleBtn.style.display = 'none';
                }
            };
        }
        
        if (cancelBtn) {
            cancelBtn.onclick = () => {
                const form = card.querySelector(`#replyForm${requestId}`);
                if (form) {
                    form.style.display = 'none';
                    if (toggleBtn) toggleBtn.style.display = 'inline-block';
                    form.querySelector('textarea').value = '';
                }
            };
        }
        
        if (replyForm) {
            replyForm.onsubmit = async (e) => {
                e.preventDefault();
                
                const submitBtn = replyForm.querySelector('.btn-reply');
                const statusDiv = replyForm.querySelector('.alert');
                const textarea = replyForm.querySelector('textarea');
                
                if (!textarea.value.trim()) {
                    statusDiv.className = 'alert alert-danger mt-3';
                    statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Please enter a reply message.';
                    statusDiv.style.display = 'block';
                    return;
                }
                
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';
                
                try {
                    const formData = new FormData();
                    formData.append('user_reply', textarea.value);
                    
                    const response = await fetch(`/api/book-requests/${requestId}/reply`, {
                        method: 'POST',
                        body: formData
                    });
                    
                    if (response.ok) {
                        statusDiv.className = 'alert alert-success mt-3';
                        statusDiv.innerHTML = '<i class="fas fa-check-circle"></i> Reply sent successfully!';
                        statusDiv.style.display = 'block';
                        setTimeout(() => statusDiv.style.display = 'none', 2000);
                    } else {
                        throw new Error('Failed to send reply');
                    }
                } catch (error) {
                    statusDiv.className = 'alert alert-danger mt-3';
                    statusDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Error sending reply. Please try again.';
                    statusDiv.style.display = 'block';
                } finally {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Send Reply';
                }
            };
        }
    },
    
    attachActionListeners(container) {
        container.querySelectorAll('.btn-action').forEach(btn => {
            btn.onclick = () => {
                const action = btn.classList.contains('btn-approve') ? 'approve' :
                             btn.classList.contains('btn-reject') ? 'reject' :
                             btn.classList.contains('btn-fulfill') ? 'fulfill' : 'respond';
                
                if (action === 'respond') {
                    const requestId = btn.dataset.requestId;
                    const title = btn.dataset.title;
                    
                    if (window.openResponseModal) {
                        window.openResponseModal(requestId, title);
                    } else {
                        document.dispatchEvent(new CustomEvent('bookRequestAction', {
                            detail: { action, requestId, title }
                        }));
                    }
                } else {
                    document.dispatchEvent(new CustomEvent('bookRequestAction', {
                        detail: { action, requestId: btn.dataset.requestId }
                    }));
                }
            };
        });
    },
    
    updateQuota(quota) {
        const remaining = document.querySelector('p span.fw-bold');
        const progressBar = document.querySelector('.progress-bar');
        const text = document.querySelector('p.small.text-muted');
        
        if (remaining) remaining.textContent = quota.requests_remaining;
        if (progressBar) {
            const percentage = quota.requests_allowed > 0 ? (quota.requests_used / quota.requests_allowed * 100) : 0;
            progressBar.style.width = `${percentage}%`;
        }
        if (text) {
            text.textContent = `${quota.requests_used} used of ${quota.requests_allowed} allowed • Quota resets at the beginning of each month`;
        }
        
        const form = document.getElementById('bookRequestForm');
        const warning = document.querySelector('.alert-warning');
        
        if (quota.requests_remaining <= 0) {
            if (form) form.style.display = 'none';
            if (warning) warning.style.display = 'block';
        } else {
            if (form) form.style.display = 'block';
            if (warning) warning.style.display = 'none';
        }
    },
    
    updatePendingCount(count) {
        document.querySelectorAll('.badge.bg-warning').forEach(badge => {
            if (badge.textContent.includes('Pending')) {
                badge.textContent = `${count} Pending`;
            }
        });

        if (window.BadgeManager) {
            window.currentPendingRequests = count;
            window.BadgeManager.state.count = count;
            BadgeManager.updateBadges();
            BadgeManager.updateMobileQuickNavBadge(); // ✅ Update mobile quick nav icon
        }

        // Update combined Admin dropdown badge
        if (window.updateAdminDropdownBadge) {
            window.updateAdminDropdownBadge();
        }
    },
    
    updateConnectionIndicator(connected) {
        const indicator = document.getElementById('ws-connection-indicator');
        if (indicator) {
            indicator.style.backgroundColor = connected ? '#10b981' : '#ef4444';
            indicator.title = connected ? 'Connected to live updates' : 'Offline mode';
        }
    },
    
    scheduleReconnect() {
        if (this.state.reconnectAttempts >= 5) return;
        
        this.state.reconnectAttempts++;
        const delay = Math.min(5000 * this.state.reconnectAttempts, 30000);
        
        setTimeout(() => {
            if (!this.state.isConnected) this.connect();
        }, delay);
    }
};