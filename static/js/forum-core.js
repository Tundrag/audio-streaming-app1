/**
 * forum-core.js - Core Forum System (WebSocket-free version)
 * Handles: App initialization, view rendering, navigation, thread management
 */

class ForumCore {
    constructor() {
        // Prevent multiple instances - reuse existing one
        if (window.forum instanceof ForumCore) {
            // ✅ CRITICAL: Update container reference to new SPA-inserted DOM
            window.forum.container = document.getElementById('forumSPA');
            if (!window.forum.container) {
                console.error('🚨 ForumCore: #forumSPA not found when reusing instance!');
            } else {
            }
            return window.forum;
        }

        // ✅ CRITICAL: Get container reference (will be verified in init)
        this.container = document.getElementById('forumSPA');
        this.currentView = 'threads';
        this.currentTab = 'main';
        this.currentThread = null;
        this.threads = [];
        this.messages = [];
        this.loadingOlderMessages = false;
        
        this.newMessagesCount = 0;
        this.isUserScrolledUp = false;
        
        this.currentUser = window.forumUserData || {
            id: null,
            username: '',
            is_creator: false,
            is_team: false,
            is_patreon: false,
            is_kofi: false
        };
        
        // 🔥 NEW: Add unique instance ID for debugging
        this._instanceId = Date.now() + Math.random().toString(36).substr(2, 9);
        
        // Initialize WebSocket manager
        this.websockets = new ForumWebSocketManager(this);
        
        // 🔥 NEW: Register this instance globally
        window.forum = this;
        
        // 🔥 NEW: Update any existing singletons to use this instance
        this.attachToExistingSingletons();

        // ✅ Initialize the forum (async, runs in background)
        this._initPromise = this.init();
    }

    // Public method to wait for initialization to complete
    async waitForInit() {
        if (this._initPromise) {
            await this._initPromise;
        } else {
        }
    }

    attachToExistingSingletons() {
        
        // Update ForumThreadSettings if it exists
        if (window.forumThreadSettings) {
            window.forumThreadSettings.attachForum(this);
        }
        
        // Update any other singletons that might need the forum reference
        if (window.forumNotificationManager) {
            window.forumNotificationManager.forum = this;
        }
        
        if (window.enhancedForumNotificationManager) {
            // If the enhanced notification manager needs forum reference
        }
    }
    async init() {
        try {
            // ✅ CRITICAL: Verify container exists before proceeding
            if (!this.container) {
                console.error('🚨 ForumCore: #forumSPA container not found on init');
                // Try to get it again (might have been inserted after constructor)
                this.container = document.getElementById('forumSPA');
                if (!this.container) {
                    throw new Error('#forumSPA container not found');
                }
            }

            await this.loadThreads(this.currentTab);

            this.renderThreadsView();

            // Connect to global WebSocket
            this.websockets.connectGlobalWebSocket();
            this.websockets.setupDebugTools();

            // 🔥 NEW: Set up notification manager integration
            this.setupNotificationManagerIntegration();

            this.handleQuickReplyTarget();


        } catch (error) {
            console.error('❌ ForumCore: Error initializing forum:', error);
            this.showError('Failed to load forum. Please refresh the page.');
            throw error; // Re-throw so waitForInit() knows it failed
        }
    }

    // 🔥 NEW: Set up integration with notification manager
    setupNotificationManagerIntegration() {
        // Wait for notification manager to be available
        const checkNotificationManager = () => {
            if (window.enhancedForumNotificationManager) {
                
                // Set up the back button update callback
                window.enhancedForumNotificationManager.backButtonUpdateCallback = (count) => {
                    this.updateBackButtonBadge(count);
                };
                
                // Force an initial update
                const initialCount = window.enhancedForumNotificationManager.getTotalUnreadCount();
                this.updateBackButtonBadge(initialCount);
                
            } else {
                // Try again in a bit
                setTimeout(checkNotificationManager, 500);
            }
        };
        
        checkNotificationManager();
    }
    // ================== TAB MANAGEMENT ==================

    async switchTab(tabName) {
        if (this.currentTab === tabName) return;
        
        this.currentTab = tabName;
        this.showLoading();
        
        try {
            await this.loadThreads(tabName);
            this.renderThreadsView();
            
            // 🔥 NEW: Update notification badges after tab switch
            this.updateNotificationBadgesAfterRender();
            
        } catch (error) {
            this.showError('Failed to load threads');
        }
    }

    // 🔥 NEW: Update notification badges after rendering
    updateNotificationBadgesAfterRender() {
        setTimeout(() => {
            if (window.enhancedForumNotificationManager) {
                window.enhancedForumNotificationManager.updateThreadBadges();
            }
        }, 100);
    }


    handleQuickReplyTarget() {
        const target = sessionStorage.getItem('forumTarget');
        if (target) {
            sessionStorage.removeItem('forumTarget');
            const { threadId, messageId } = JSON.parse(target);
            
            setTimeout(() => {
                this.viewThread(threadId).then(() => {
                    if (messageId) {
                        setTimeout(() => {
                            const messageEl = document.querySelector(`[data-message-id="${messageId}"]`);
                            if (messageEl) {
                                messageEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                messageEl.style.background = 'rgba(74, 144, 226, 0.1)';
                                setTimeout(() => messageEl.style.background = '', 2000);
                            }
                        }, 500);
                    }
                });
            }, 500);
        }
    }

    // ================== VIEW RENDERING ==================

    renderThreadsView() {
        const canCreateThreads = this.currentUser.is_creator;
        
        const mainCount = this.currentTab === 'main' ? this.threads.length : '...';
        const followingCount = this.currentTab === 'following' ? this.threads.length : '...';
        
        this.container.innerHTML = `
            <div class="forum-header">
                <div class="header-top">
                    <div class="forum-title">
                        <h1><i class="fas fa-comments"></i> Community Forum</h1>
                        <div class="forum-subtitle">Connect and discuss with the community</div>
                    </div>
                    <div class="header-actions">
                        <button class="btn-secondary" onclick="forum.navigateToSettings()" title="Forum Settings">
                            <i class="fas fa-cog"></i> Settings
                        </button>
                        ${canCreateThreads ? `<button class="new-thread-btn" onclick="forum.showModal('newThreadModal')"><i class="fas fa-plus"></i> New Discussion</button>` : ''}
                    </div>
                </div>
                <div class="connection-status">
                    <span class="live-indicator" id="globalConnectionIndicator">
                        <span class="live-dot"></span> <span class="status-text">Connecting...</span>
                    </span>
                </div>
            </div>
            
            <div class="forum-tabs">
                <button class="forum-tab ${this.currentTab === 'main' ? 'active' : ''}" onclick="forum.switchTab('main')" title="Main discussions created by the creator">
                    <i class="fas fa-list"></i> Main Discussions
                    <span class="tab-count">${mainCount}</span>
                </button>
                <button class="forum-tab ${this.currentTab === 'following' ? 'active' : ''}" onclick="forum.switchTab('following')" title="Threads you're following (includes sub-discussions)">
                    <i class="fas fa-bell"></i> Following
                    <span class="tab-count">${followingCount}</span>
                </button>
            </div>
            
            <div class="thread-list-container">
                <div class="thread-list">
                    ${this.threads.length === 0 ? this.renderEmptyState() : this.threads.map(thread => this.renderThreadItem(thread)).join('')}
                </div>
            </div>

            <!-- New Thread Modal -->
            ${canCreateThreads ? this.renderNewThreadModal() : ''}

            <!-- Thread Settings Modal (rendered by thread settings module) -->
            <div id="threadSettingsModalContainer"></div>
        `;

        this.websockets.updateGlobalLiveIndicator(this.websockets.connectionState === 'connected');

        // 🔥 ENHANCED: Better notification manager integration
        if (window.enhancedForumNotificationManager) {
            window.enhancedForumNotificationManager.onThreadListRender();
            
            // 🔥 NEW: Force update badges after a short delay to ensure DOM is ready
            setTimeout(() => {
                window.enhancedForumNotificationManager.updateThreadBadges();
                window.enhancedForumNotificationManager.forceUpdate();
            }, 100);
        }
    }
    renderNewThreadModal() {
        const tierSelector = window.forumThreadSettings ? window.forumThreadSettings.renderTierSelector() : '<option value="">Free Access</option>';
        
        return `
            <div id="newThreadModal" class="modal">
                <div class="modal-content">
                    <div class="modal-header">
                        <h3>Create New Discussion</h3>
                        <button class="modal-close" onclick="forum.hideModal('newThreadModal')">&times;</button>
                    </div>
                    <form id="newThreadForm" onsubmit="forum.handleCreateThread(event)">
                        <div class="form-group">
                            <label for="threadTitle">Discussion Title *</label>
                            <input type="text" id="threadTitle" name="title" required maxlength="200" 
                                   placeholder="Enter a clear, descriptive title...">
                        </div>
                        
                        <div class="form-group">
                            <label for="threadContent">Opening Message *</label>
                            <textarea id="threadContent" name="content" required rows="6" 
                                      placeholder="Start the discussion! Share your thoughts, ask questions, or provide context..."></textarea>
                        </div>
                        
                        <div class="form-group">
                            <label for="threadTier">Access Level</label>
                            <select id="threadTier" name="min_tier_id">
                                ${tierSelector}
                            </select>
                            <div id="newThreadTierDescription" class="tier-description">
                                Anyone can access this discussion
                            </div>
                        </div>
                        
                        <div class="modal-actions">
                            <button type="button" class="btn-secondary" onclick="forum.hideModal('newThreadModal')">Cancel</button>
                            <button type="submit" class="btn-primary">Create Discussion</button>
                        </div>
                    </form>
                </div>
            </div>
        `;
    }    
    renderEmptyState() {
        const canCreateThreads = this.currentUser.is_creator;
        
        if (this.currentTab === 'main') {
            return `
                <div class="empty-state">
                    <i class="fas fa-comments"></i>
                    <h3>No main discussions yet</h3>
                    <p>${canCreateThreads ? 'Create the first main discussion topic!' : 'Creator will add main discussion topics soon!'}</p>
                    ${canCreateThreads ? '' : '<small style="color: var(--text-secondary); margin-top: 1rem; display: block;">Sub-discussions created from messages appear in the Following tab when you follow them.</small>'}
                </div>
            `;
        } else if (this.currentTab === 'following') {
            return `
                <div class="empty-state">
                    <i class="fas fa-bell-slash"></i>
                    <h3>You're not following any threads</h3>
                    <p>Follow main discussions or sub-threads to get notifications and see them here!</p>
                    <small style="color: var(--text-secondary); margin-top: 1rem; display: block;">
                        • Follow main discussions to stay updated<br>
                        • Create or join sub-discussions from messages<br>
                        • Get notified about replies and mentions
                    </small>
                </div>
            `;
        }
        
        return `
            <div class="empty-state">
                <i class="fas fa-comments"></i>
                <h3>No discussions found</h3>
                <p>No discussions match your current filter.</p>
            </div>
        `;
    }

    renderThreadItem(thread) {
        const statusBadges = [];
        
        if (thread.is_following) {
            statusBadges.push('<span class="status-badge following"><i class="fas fa-bell"></i> Following</span>');
        }
        
        // 🔥 ENHANCED: Better unread badge rendering that can be updated live
        if (thread.unread_count > 0) {
            statusBadges.push(`<span class="status-badge unread" data-unread-count="${thread.unread_count}">${thread.unread_count} unread</span>`);
        }
        
        statusBadges.push(`<span class="status-badge ${thread.thread_type}">${thread.thread_type === 'sub' ? 'Sub' : 'Main'}</span>`);
        
        const typeClass = thread.thread_type === 'sub' ? 'sub-thread' : '';
        const followingClass = thread.is_following ? 'following' : '';
        const unreadClass = thread.unread_count > 0 ? 'has-unread' : '';
        
        const threadActions = [];
        
        if (thread.follower_count > 0) {
            threadActions.push(`
                <span class="follower-count-badge">
                    <i class="fas fa-users"></i> ${thread.follower_count}
                </span>
            `);
        }
        
        if (thread.is_pinned) {
            threadActions.push('<i class="fas fa-thumbtack thread-icon pinned"></i>');
        }
        
        if (thread.is_locked) {
            threadActions.push('<i class="fas fa-lock thread-icon locked"></i>');
        }
        
        // Tier badge using tier_info
        if (thread.tier_info && thread.tier_info.is_restricted && window.forumThreadSettings) {
            threadActions.push(window.forumThreadSettings.renderTierBadge(thread.tier_info));
        }
        
        const parentContext = thread.thread_type === 'sub' && thread.created_from_message ? `
            <div class="parent-context">
                <i class="fas fa-reply"></i> From message by @${thread.created_from_message.username}: "${thread.created_from_message.content}"
            </div>` : '';

        return `
            <div class="thread-item ${thread.is_pinned ? 'pinned' : ''} ${typeClass} ${followingClass} ${unreadClass}" 
                 onclick="forum.viewThread(${thread.id})" 
                 data-thread-id="${thread.id}">
                <div class="thread-status">
                    ${statusBadges.join('')}
                </div>
                ${threadActions.length > 0 ? `
                    <div class="thread-actions">
                        ${threadActions.join('')}
                    </div>
                ` : ''}
                ${parentContext}
                <div class="thread-header">
                    <div class="user-avatar" style="background-color: ${thread.user_badge_color}">
                        ${thread.username.substring(0, 2).toUpperCase()}
                    </div>
                    <div class="thread-content">
                        <div class="thread-title">${thread.title}</div>
                        <div class="thread-meta">
                            <div class="meta-item"><i class="fas fa-user"></i><span>${thread.username}</span><span class="user-badge" style="background-color: ${thread.user_badge_color}">${thread.user_role}</span></div>
                            <div class="meta-item"><i class="fas fa-comment"></i><span>${thread.message_count} messages</span></div>
                            <div class="meta-item"><i class="fas fa-eye"></i><span>${thread.view_count} views</span></div>
                            <div class="meta-item"><i class="fas fa-clock"></i><span>${this.formatTimeAgo(thread.last_message_at)}</span></div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    renderDiscussionView() {
        const canManage = this.currentThread.can_manage;
        const canDelete = this.currentThread.can_delete;
        
        const parentContext = this.currentThread.thread_type === 'sub' && this.currentThread.created_from_message ? `
            <div class="thread-parent-context">
                <div><i class="fas fa-reply"></i> Thread started from message by @${this.currentThread.created_from_message.username}:</div>
                <div class="preview-content">${this.currentThread.created_from_message.content}</div>
            </div>` : '';

        const followBtnText = this.currentThread.is_following ? 'Following' : 'Follow';
        const followBtnIcon = this.currentThread.is_following ? 'fa-check' : 'fa-plus';
        const followBtnClass = this.currentThread.is_following ? 'following' : '';

        const messagesSection = `
            <div class="messages-wrapper">
                <div class="discussion-messages" id="messagesContainer" style="opacity: 0;">
                    ${this.messages.length === 0 ? 
                        `<div style="text-align: center; padding: 2rem; color: var(--text-secondary);"><i class="fas fa-comment" style="font-size: 2rem; margin-bottom: 1rem; opacity: 0.5;"></i><p>Start the discussion! Be the first to share your thoughts.</p></div>` : 
                        this.messages.map(message => this.renderMessage(message)).join('')
                    }
                </div>
                <button id="inlineScrollBtn" class="inline-scroll-btn hidden" onclick="forum.scrollToBottom(true)" title="Scroll to latest messages">
                    <i class="fas fa-arrow-down"></i>
                </button>
            </div>
        `;

        // 🔥 ENHANCED: Get notification count from notification manager
        const notificationCount = this.getForumNotificationCount();
        const notificationBadge = notificationCount > 0 ? 
            `<span class="forum-notification-badge" id="forumBackBadge">${notificationCount}</span>` : 
            `<span class="forum-notification-badge" id="forumBackBadge" style="display: none;"></span>`;

        // Thread info display with tier information
        const threadInfoText = this.buildThreadInfoText();

        this.container.innerHTML = `
            <div class="breadcrumb">
                <a href="#" onclick="forum.backToThreads(); return false;" data-forum-back>
                    <i class="fas fa-arrow-left"></i> Back to Forum
                    ${notificationBadge}
                </a>
                <button class="btn-secondary" onclick="forum.navigateToSettings()" style="margin-left: auto; margin-right: 1rem;" title="Forum Settings">
                    <i class="fas fa-cog"></i> Settings
                </button>
                <span class="live-indicator" id="threadConnectionIndicator">
                    <span class="live-dot"></span> <span class="status-text">Connecting...</span>
                </span>
            </div>
            ${parentContext}
            <div class="discussion-header">
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div style="display: flex; align-items: center; gap: 0.75rem; flex: 1;">
                        <div class="user-avatar" style="background-color: ${this.currentThread.user_badge_color}">
                            ${this.currentThread.username.substring(0, 2).toUpperCase()}
                        </div>
                        <div style="flex: 1;">
                            <h2 style="margin: 0; color: var(--text-primary); display: flex; align-items: center; gap: 0.5rem;">
                                ${this.currentThread.title}
                                <span class="thread-type-badge ${this.currentThread.thread_type}">${this.currentThread.thread_type === 'sub' ? 'Sub' : 'Main'}</span>
                                ${this.currentThread.tier_info && window.forumThreadSettings ? window.forumThreadSettings.renderTierBadge(this.currentThread.tier_info) : ''}
                            </h2>
                            <div style="font-size: 0.9rem; color: var(--text-secondary); margin-top: 0.25rem;">
                                ${threadInfoText}
                            </div>
                        </div>
                    </div>
                    <div class="thread-management">
                        <button class="follow-btn ${followBtnClass}" onclick="forum.toggleFollowThread(${this.currentThread.id}, ${!this.currentThread.is_following})">
                            <i class="fas ${followBtnIcon}"></i>
                            ${followBtnText}
                        </button>
                        ${canManage ? `<button class="thread-settings-btn" style="padding: 0.5rem; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-secondary); cursor: pointer;"><i class="fas fa-cog"></i></button>` : ''}
                        ${canDelete ? `<button class="delete-thread-btn" onclick="forum.deleteThread(${this.currentThread.id})" title="Delete Thread"><i class="fas fa-trash"></i></button>` : ''}
                    </div>
                </div>
            </div>
            ${messagesSection}
            <div class="typing-indicators"></div>
            ${!this.currentThread.is_locked || this.currentUser.is_creator ? `
                <div class="message-input-area">
                    <textarea id="messageInput" class="message-input" placeholder="Join the discussion... Use @username to mention someone!"></textarea>
                    <div class="input-actions">
                        <button class="send-btn" id="sendBtn"><i class="fas fa-arrow-right"></i></button>
                    </div>
                </div>` : 
                `<div style="text-align: center; padding: 1rem; color: var(--text-secondary); background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px;"><i class="fas fa-lock"></i> This thread is locked</div>`
            }

            <!-- Thread Settings Modal Container -->
            <div id="threadSettingsModalContainer"></div>
        `;
        
        // 🔥 NEW: Add event listener for settings button (instead of inline onclick)
        const settingsBtn = this.container.querySelector('.thread-settings-btn');
        if (settingsBtn) {
            settingsBtn.addEventListener('click', () => {
                if (window.forumThreadSettings) {
                    window.forumThreadSettings.showThreadSettingsModal(this);
                }
            });
        }
        
        this.positionAtBottomInstantly();
        this.initScrollToLoadMore();
        this.initScrollDetection();
        
        this.websockets.updateLiveIndicator(this.websockets.threadWebsocket?.readyState === WebSocket.OPEN);
        
        // 🔥 ENHANCED: Register this view with notification managers for badge updates
        this.registerForNotificationUpdates();
    }
    // 🔥 ENHANCED: Get forum notification count from notification manager
    getForumNotificationCount() {
        if (window.enhancedForumNotificationManager && 
            typeof window.enhancedForumNotificationManager.getTotalUnreadCount === 'function') {
            const count = window.enhancedForumNotificationManager.getTotalUnreadCount();
            return count;
        }
        if (window.BadgeManager && 
            typeof window.BadgeManager.getForumNotificationCount === 'function') {
            const count = window.BadgeManager.getForumNotificationCount();
            return count;
        }
        if (typeof this.forumNotificationCount === 'number') {
            return this.forumNotificationCount;
        }
        return 0;
    }

    buildThreadInfoText() {
        const parts = [];
        
        parts.push(`Started by ${this.currentThread.username} • ${this.formatTimeAgo(this.currentThread.created_at)}`);
        
        if (this.currentThread.is_pinned) {
            parts.push('<i class="fas fa-thumbtack"></i> Pinned');
        }
        
        if (this.currentThread.is_locked) {
            parts.push('<i class="fas fa-lock"></i> Locked');
        }
        
        if (this.currentThread.follower_count > 0) {
            parts.push(`<i class="fas fa-users"></i> ${this.currentThread.follower_count} followers`);
        }
        
        // Add tier access info
        if (this.currentThread.tier_info && this.currentThread.tier_info.is_restricted) {
            const tierInfo = this.currentThread.tier_info;
            parts.push(`<i class="fas fa-lock"></i> ${tierInfo.tier_title} required`);
        } else {
            parts.push('<i class="fas fa-globe"></i> Free access');
        }
        
        return parts.join(' • ');
    }

    // ================== MODAL MANAGEMENT ==================

    showModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.style.display = 'flex';
            
            // Handle new thread modal initialization
            if (modalId === 'newThreadModal') {
                setTimeout(() => {
                    this.initializeNewThreadModal();
                }, 50);
            }
            
            // Pre-populate thread settings modal if viewing a thread
            if (modalId === 'threadSettingsModal' && this.currentThread && window.forumThreadSettings) {
                // Ensure thread settings is using this forum instance
                window.forumThreadSettings.attachForum(this);
                
                setTimeout(() => {
                    window.forumThreadSettings.populateThreadSettingsModal();
                }, 50);
            }
        }
    }

    async initializeNewThreadModal() {
        
        // Ensure thread settings is available and has tier data
        if (window.forumThreadSettings) {
            // Attach forum instance
            window.forumThreadSettings.attachForum(this);
            
            // Load tier data if not already loaded
            if (!window.forumThreadSettings.availableTiers?.length) {
                await window.forumThreadSettings.loadTierData();
            }
            
            // Re-populate the tier selector with fresh data
            const tierSelect = document.getElementById('threadTier');
            if (tierSelect) {
                tierSelect.innerHTML = window.forumThreadSettings.renderTierSelector();
                
                // Set initial description
                window.forumThreadSettings.updateTierDescription('', 'newThreadTierDescription');
                
                // Add change event listener
                tierSelect.onchange = (e) => {
                    window.forumThreadSettings.updateTierDescription(e.target.value, 'newThreadTierDescription');
                };
                
            }
        }
    }

    openThreadSettings() {
        if (window.forumThreadSettings) {
            // Ensure thread settings is using this forum instance
            window.forumThreadSettings.attachForum(this);
            window.forumThreadSettings.showThreadSettingsModal();
        }
    }

    // 🔥 NEW: Get thread settings reference
    getThreadSettings() {
        return window.forumThreadSettings;
    }

    hideModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.style.display = 'none';
            
            // Reset form
            const form = modal.querySelector('form');
            if (form) {
                form.reset();
            }
        }
    }

    // ================== THREAD MANAGEMENT ==================

    async handleCreateThread(event) {
        event.preventDefault();
        
        const formData = new FormData(event.target);
        const threadData = {
            title: formData.get('title'),
            content: formData.get('content'),
            min_tier_id: formData.get('min_tier_id') || null
        };
        
        try {
            const response = await fetch('/api/forum/threads', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(threadData)
            });
            
            if (!response.ok) {
                throw new Error('Failed to create thread');
            }
            
            const thread = await response.json();
            
            this.hideModal('newThreadModal');
            this.showToast('Discussion created successfully!');
            
            // Add to local threads array if on correct tab
            if (this.currentView === 'threads' && this.currentTab === 'main') {
                this.threads.unshift(thread);
                this.renderThreadsView();
            }
            
        } catch (error) {
            console.error('Error creating thread:', error);
            this.showError('Failed to create discussion');
        }
    }

    async toggleFollowThread(threadId, shouldFollow) {
        try {
            const url = `/api/forum/threads/${threadId}/follow`;
            const method = shouldFollow ? 'POST' : 'DELETE';
            
            const response = await fetch(url, { method });
            
            if (!response.ok) {
                throw new Error('Failed to update follow status');
            }
            
            const result = await response.json();
            
            // Update current thread if viewing it
            if (this.currentThread && this.currentThread.id === threadId) {
                this.currentThread.is_following = result.is_following;
                this.currentThread.follower_count = result.follower_count;
                this.renderDiscussionView();
            }
            
            // Update thread in threads array
            const threadIndex = this.threads.findIndex(t => t.id === threadId);
            if (threadIndex !== -1) {
                this.threads[threadIndex].is_following = result.is_following;
                this.threads[threadIndex].follower_count = result.follower_count;
            }
            
            this.showToast(shouldFollow ? 'Following thread' : 'Unfollowed thread');
            
        } catch (error) {
            console.error('Error toggling follow:', error);
            this.showError('Failed to update follow status');
        }
    }

    async deleteThread(threadId) {
        if (!confirm('Are you sure you want to delete this thread? This action cannot be undone.')) {
            return;
        }
        
        try {
            const response = await fetch(`/api/forum/threads/${threadId}/delete-hierarchy`, {
                method: 'DELETE'
            });
            
            if (!response.ok) {
                throw new Error('Failed to delete thread');
            }
            
            this.showToast('Thread deleted successfully');
            
            // Navigate back to threads list
            this.backToThreads();
            
        } catch (error) {
            console.error('Error deleting thread:', error);
            this.showError('Failed to delete thread');
        }
    }

    // 🔥 ENHANCED: Better back button badge updates
    updateBackButtonBadge(count) {
        const badge = document.getElementById('forumBackBadge');
        if (!badge) return;
        
        
        if (count > 0) {
            badge.textContent = count > 99 ? '99+' : count;
            badge.style.display = 'flex';
            badge.classList.add('badge-pulse');
            setTimeout(() => badge.classList.remove('badge-pulse'), 600);
        } else {
            badge.style.display = 'none';
            badge.textContent = '';
        }
    }

    // 🔥 ENHANCED: Better notification update registration
    registerForNotificationUpdates() {
        if (window.enhancedForumNotificationManager) {
            window.enhancedForumNotificationManager.backButtonUpdateCallback = (count) => {
                this.updateBackButtonBadge(count);
            };
            
            // Force an initial update
            setTimeout(() => {
                const initialCount = window.enhancedForumNotificationManager.getTotalUnreadCount();
                this.updateBackButtonBadge(initialCount);
            }, 100);
        }
        
        if (window.BadgeManager) {
            window.BadgeManager.forumBackButtonCallback = (count) => {
                this.updateBackButtonBadge(count);
            };
        }
    }

    // ================== WEBSOCKET CALLBACK HANDLERS ==================

    handleNewThreadCreated(data) {
        console.log('🆕 New main thread created via GLOBAL WebSocket:', data.thread);
        
        const existingIndex = this.threads.findIndex(t => t.id === data.thread.id);
        if (existingIndex !== -1) {
            console.log('📱 Thread already exists, skipping');
            return;
        }
        
        this.showToast(`New thread: "${data.thread.title}" by ${data.creator.username}`);
        
        if (this.currentView === 'threads') {
            if (data.thread.thread_type === 'main' && this.currentTab === 'main') {
                console.log('📱 Adding main thread to UI');
                
                this.threads.unshift(data.thread);
                this.renderThreadsView();
                this.updateTabCounts();
                
                setTimeout(() => {
                    const threadElement = document.querySelector(`[data-thread-id="${data.thread.id}"]`);
                    if (threadElement) {
                        threadElement.style.background = 'linear-gradient(135deg, var(--card-bg) 0%, rgba(16, 185, 129, 0.1) 100%)';
                        threadElement.style.borderColor = '#10b981';
                        setTimeout(() => {
                            threadElement.style.background = '';
                            threadElement.style.borderColor = '';
                        }, 3000);
                    }
                }, 100);
            }
        }
    }

    handleNewSubThreadCreated(data) {
        console.log('🆕 New sub-thread created via GLOBAL WebSocket:', data.thread);
        
        const existingIndex = this.threads.findIndex(t => t.id === data.thread.id);
        if (existingIndex !== -1) {
            console.log('📱 Sub-thread already exists, skipping');
            return;
        }
        
        this.showToast(`New discussion: "${data.thread.title}" by ${data.creator.username}`);
        
        if (this.currentView === 'threads') {
            if (this.currentTab === 'following') {
                console.log('📱 Adding sub-thread to following UI');
                
                this.threads.unshift(data.thread);
                this.renderThreadsView();
                this.updateTabCounts();
                
                setTimeout(() => {
                    const threadElement = document.querySelector(`[data-thread-id="${data.thread.id}"]`);
                    if (threadElement) {
                        threadElement.style.background = 'linear-gradient(135deg, var(--card-bg) 0%, rgba(59, 130, 246, 0.1) 100%)';
                        threadElement.style.borderColor = '#3b82f6';
                        setTimeout(() => {
                            threadElement.style.background = '';
                            threadElement.style.borderColor = '';
                        }, 3000);
                    }
                }, 100);
            }
        }
    }

    
    handleThreadDeleted(data) {
        
        this.threads = this.threads.filter(t => t.id !== data.thread_id);
        
        if (this.currentView === 'threads') {
            this.renderThreadsView();
            this.updateTabCounts();
        }
        
        this.showToast(`Thread "${data.thread_title}" was deleted`);
        
        if (this.currentView === 'discussion' && this.currentThread && this.currentThread.id === data.thread_id) {
            this.backToThreads();
        }
    }

    updateTabCounts() {
        const mainTab = document.querySelector('.forum-tab[onclick*="main"] .tab-count');
        const followingTab = document.querySelector('.forum-tab[onclick*="following"] .tab-count');
        
        if (this.currentTab === 'main' && mainTab) {
            mainTab.textContent = this.threads.length;
        }
        if (this.currentTab === 'following' && followingTab) {
            followingTab.textContent = this.threads.length;
        }
    }

    handleForumNotification(notification) {
        if (window.BadgeManager) {
            window.BadgeManager.handleNotification(notification);
        }
        
        if (window.quickReplyManager && notification.should_show_quick_reply) {
            // Let the quick reply manager handle this
        }
    }

    // 🔥 ENHANCED: Better notification count update handling
    handleNotificationCountUpdate(count) {
        this.forumNotificationCount = count;
        
        // Update back button badge if in discussion view
        if (this.currentView === 'discussion') {
            this.updateBackButtonBadge(count);
        }
        
        // Update BadgeManager if available
        if (window.BadgeManager && typeof window.BadgeManager.updateBadges === 'function') {
            window.BadgeManager.updateBadges(count);
        }
        
    }

    // 🔥 ENHANCED: Better thread entry handling
    onThreadEntered(threadId) {
        
        // Notify the notification manager immediately
        if (window.enhancedForumNotificationManager && 
            typeof window.enhancedForumNotificationManager.onThreadEntered === 'function') {
            window.enhancedForumNotificationManager.onThreadEntered(threadId);
        }
        
        // Force a badge update after a short delay
        setTimeout(() => {
            const newCount = this.getForumNotificationCount();
            this.updateBackButtonBadge(newCount);
            
            // Also force update of notification manager badges
            if (window.enhancedForumNotificationManager) {
                window.enhancedForumNotificationManager.forceUpdate();
            }
        }, 500);
    }


    // ================== API METHODS ==================

    async loadThreads(filterType = 'main') {
        const filterParams = {
            'main': 'main',
            'following': 'following',
            'all': 'all'
        };
        
        const apiFilter = filterParams[filterType] || 'main';
        const response = await fetch(`/api/forum/threads?filter_type=${apiFilter}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        this.threads = await response.json();
    }

    async loadMessages(threadId) {
        const response = await fetch(`/api/forum/threads/${threadId}/messages`);
        if (!response.ok) {
            throw new Error(response.status === 403 ? 'You do not have access to this thread' : `HTTP ${response.status}`);
        }
        this.messages = await response.json();
    }

    // ================== VIEW MANAGEMENT ==================

    async viewThread(threadId) {
        try {
            this.showLoading();
            let thread = this.threads.find(t => t.id === threadId);
            if (!thread) {
                console.log(`Thread ${threadId} not in local array, loading from API...`);
                const response = await fetch(`/api/forum/threads/${threadId}`);
                if (!response.ok) {
                    throw new Error(response.status === 403 ? 'You do not have access to this thread' : 'Thread not found');
                }
                thread = await response.json();
            }
            this.currentThread = thread;

            // 🔥 SIMPLE FIX: Set the currentThreadId IMMEDIATELY in enhanced manager
            if (window.enhancedForumNotificationManager) {
                window.enhancedForumNotificationManager.currentThreadId = threadId;
                window.enhancedForumNotificationManager.isInForum = true;
            }

            // 🔥 ENHANCED: Notify that user entered this thread with immediate UI update
            this.onThreadEntered(threadId);

            const hierarchyResponse = await fetch(`/api/forum/threads/${threadId}/hierarchy`);
            if (hierarchyResponse.ok) {
                Object.assign(this.currentThread, await hierarchyResponse.json());
            }

            await this.loadMessages(threadId);
            this.currentView = 'discussion';
            this.renderDiscussionView();
            
            // Connect to thread WebSocket
            this.websockets.connectThreadWebSocket(threadId);

            setTimeout(() => {
                this.ensureInputVisible();
                if (this.showScrollToBottomButtonIfNeeded) {
                    this.showScrollToBottomButtonIfNeeded();
                }
            }, 300);

        } catch (error) {
            console.error('Error viewing thread:', error);
            this.showError(error.message);
            this.backToThreads();
        }
    }
    // 🔥 ENHANCED: Better back to threads with notification cleanup
    backToThreads() {
        // 🔥 SIMPLE FIX: Clear the currentThreadId IMMEDIATELY
        if (window.enhancedForumNotificationManager) {
            window.enhancedForumNotificationManager.currentThreadId = null;
            console.log(`🔙 Cleared enhanced manager currentThreadId`);
        }

        if (this.currentView === 'settings') {
            this.currentView = 'threads';
            this.renderThreadsView();
            return;
        }

        this.currentView = 'threads';
        this.currentThread = null;
        this.messages = [];
        if (this.typingUsers) this.typingUsers.clear();

        // 🔥 ENHANCED: Unregister from notification updates
        this.unregisterFromNotificationUpdates();

        // 🔥 ENHANCED: Update notification manager state
        if (window.enhancedForumNotificationManager) {
            window.enhancedForumNotificationManager.currentThreadId = null;
            window.enhancedForumNotificationManager.updateForumStatus();
        }

        // Disconnect thread WebSocket
        this.websockets.disconnectThreadWebSocket();

        if (this.hideScrollToBottomButton) {
            this.hideScrollToBottomButton();
        }

        this.renderThreadsView();
    }
    // 🔥 ENHANCED: Better notification update unregistration
    unregisterFromNotificationUpdates() {
        if (window.enhancedForumNotificationManager) {
            window.enhancedForumNotificationManager.backButtonUpdateCallback = null;
        }
        if (window.BadgeManager) {
            window.BadgeManager.forumBackButtonCallback = null;
        }
    }
    // ================== SCROLL MANAGEMENT ==================

    scrollViewportToBottom() {
        const inputArea = document.querySelector('.message-input-area');
        const forumContainer = document.querySelector('.forum-spa-container');
        
        if (inputArea) {
            setTimeout(() => {
                inputArea.scrollIntoView({ 
                    behavior: 'smooth', 
                    block: 'end',
                    inline: 'nearest' 
                });
                
                if (window.innerWidth <= 768) {
                    setTimeout(() => {
                        window.scrollBy(0, 50);
                    }, 300);
                }
            }, 150);
        } else if (forumContainer) {
            setTimeout(() => {
                window.scrollTo({
                    top: document.body.scrollHeight,
                    behavior: 'smooth'
                });
            }, 150);
        }
    }

    ensureInputVisible() {
        const inputArea = document.querySelector('.message-input-area');
        if (!inputArea) return;
        
        const rect = inputArea.getBoundingClientRect();
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
        const isVisible = rect.top >= 0 && rect.bottom <= viewportHeight;
        
        if (!isVisible) {
            const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            const targetScroll = scrollTop + rect.bottom - viewportHeight + 20;
            
            window.scrollTo({
                top: targetScroll,
                behavior: 'smooth'
            });
        }
    }

    initScrollDetection() {
        const container = document.getElementById('messagesContainer');
        if (!container) return;
        
        let scrollTimeout;
        
        container.addEventListener('scroll', () => {
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {
                this.handleScrollToBottomButton(container);
                
                if (container.scrollTop < 100 && !this.loadingOlderMessages) {
                    this.loadOlderMessages();
                }
            }, 100);
        });
    }

    isDesktop() {
        return window.innerWidth >= 769;
    }

    handleScrollToBottomButton(container) {
        if (!container) return;
        
        const inlineButton = document.getElementById('inlineScrollBtn');
        
        const scrollTop = container.scrollTop;
        const scrollHeight = container.scrollHeight;
        const clientHeight = container.clientHeight;
        
        const isNearBottom = scrollHeight - scrollTop - clientHeight < 150;
        
        this.isUserScrolledUp = !isNearBottom;
        
        if (isNearBottom) {
            if (inlineButton) inlineButton.classList.add('hidden');
            this.clearNewMessagesCount();
        } else {
            if (this.messages.length > 0) {
                if (inlineButton && !this.isDesktop()) {
                    inlineButton.classList.remove('hidden');
                }
            }
        }
    }

    scrollToBottom(smooth = false) {
        const container = document.getElementById('messagesContainer');
        if (!container) return;
        
        if (smooth) {
            container.classList.add('smooth-scroll');
            container.scrollTop = container.scrollHeight;
            
            setTimeout(() => {
                container.classList.remove('smooth-scroll');
            }, 500);
        } else {
            container.scrollTop = container.scrollHeight;
        }
        
        this.hideScrollToBottomButton();
    }

    hideScrollToBottomButton() {
        const inlineButton = document.getElementById('inlineScrollBtn');
        
        if (inlineButton) inlineButton.classList.add('hidden');
        
        this.clearNewMessagesCount();
    }

    showScrollToBottomButtonIfNeeded() {
        if (this.currentView === 'discussion' && this.messages.length > 0) {
            const container = document.getElementById('messagesContainer');
            if (container && this.isUserScrolledUp && !this.isDesktop()) {
                const inlineButton = document.getElementById('inlineScrollBtn');
                
                if (inlineButton) {
                    inlineButton.classList.remove('hidden');
                }
            }
        }
    }

    incrementNewMessagesCount() {
        if (!this.isDesktop()) {
            this.newMessagesCount++;
            this.updateNewMessagesIndicator();
        }
    }

    clearNewMessagesCount() {
        this.newMessagesCount = 0;
        this.updateNewMessagesIndicator();
    }

    updateNewMessagesIndicator() {
        if (this.isDesktop()) return;
        
        const inlineButton = document.getElementById('inlineScrollBtn');
        if (!inlineButton) return;
        
        let inlineIndicator = inlineButton.querySelector('.inline-messages-indicator');
        
        if (this.newMessagesCount > 0) {
            if (!inlineIndicator) {
                inlineIndicator = document.createElement('span');
                inlineIndicator.className = 'inline-messages-indicator';
                inlineIndicator.id = 'inlineMessagesCount';
                inlineButton.appendChild(inlineIndicator);
            }
            inlineIndicator.textContent = this.newMessagesCount;
            inlineIndicator.style.display = 'flex';
        } else if (inlineIndicator) {
            inlineIndicator.remove();
        }
    }
    
    positionAtBottomInstantly() {
        const container = document.getElementById('messagesContainer');
        if (!container) return;
        
        container.classList.remove('smooth-scroll');
        container.style.opacity = '1';
        
        setTimeout(() => {
            container.scrollTop = container.scrollHeight - container.clientHeight;
            
            requestAnimationFrame(() => {
                container.scrollTop = container.scrollHeight - container.clientHeight;
                this.scrollViewportToBottom();
            });
        }, 0);
    }
    
    initScrollToLoadMore() {
        const container = document.getElementById('messagesContainer');
        if (!container) {
            console.error('❌ Messages container not found for scroll loading');
            return;
        }
        
        
        let scrollTimeout;
        
        container.addEventListener('scroll', () => {
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {
                if (container.scrollTop < 100 && !this.loadingOlderMessages) {
                    console.log('🔼 User scrolled to top, loading older messages...');
                    this.loadOlderMessages();
                }
            }, 100);
        });
    }

    async loadOlderMessages() {
        if (!this.currentThread || this.messages.length === 0 || this.loadingOlderMessages) return;
        
        this.loadingOlderMessages = true;
        
        const oldestMessageId = this.messages[0].id;
        const container = document.getElementById('messagesContainer');
        const currentScrollHeight = container.scrollHeight;
        const currentScrollTop = container.scrollTop;
        
        this.showLoadingIndicator();
        
        try {
            const response = await fetch(
                `/api/forum/threads/${this.currentThread.id}/messages?before_id=${oldestMessageId}&limit=20`
            );
            if (!response.ok) throw new Error('Failed to load older messages');
            
            const olderMessages = await response.json();
            if (olderMessages.length === 0) {
                console.log('📭 No more older messages to load');
                this.hideLoadingIndicator();
                return;
            }
            
            
            this.messages = [...olderMessages, ...this.messages];
            
            const messagesHtml = this.messages.map(message => this.renderMessage(message)).join('');
            container.innerHTML = messagesHtml;
            
            const newScrollHeight = container.scrollHeight;
            const scrollDifference = newScrollHeight - currentScrollHeight;
            container.scrollTop = currentScrollTop + scrollDifference;
            
            
        } catch (error) {
            console.error('Error loading older messages:', error);
            this.showError('Failed to load older messages');
            this.hideLoadingIndicator();
        } finally {
            this.loadingOlderMessages = false;
        }
    }

    showLoadingIndicator() {
        const container = document.getElementById('messagesContainer');
        if (!container) return;
        
        const loadingDiv = document.createElement('div');
        loadingDiv.id = 'oldMessagesLoading';
        loadingDiv.innerHTML = `
            <div style="text-align: center; padding: 1rem; color: var(--text-secondary); font-size: 0.9rem;">
                <i class="fas fa-spinner fa-spin" style="margin-right: 0.5rem;"></i>
                Loading older messages...
            </div>
        `;
        
        container.insertBefore(loadingDiv, container.firstChild);
    }

    hideLoadingIndicator() {
        const loadingDiv = document.getElementById('oldMessagesLoading');
        if (loadingDiv) {
            loadingDiv.remove();
        }
    }

    // ================== UTILITY METHODS ==================

    showLoading() {
        this.container.innerHTML = '<div class="spa-loading"><i class="fas fa-spinner"></i><span>Loading...</span></div>';
    }

    showError(message) {
        window.showToast ? window.showToast(message, 'error') : alert(message);
    }

    showToast(message) {
        window.showToast ? window.showToast(message) : console.log(message);
    }

    formatTimeAgo(dateString) {
        const diff = new Date() - new Date(dateString);
        if (diff < 60000) return 'Just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
        return new Date(dateString).toLocaleDateString();
    }

    formatTime(dateString) {
        return new Date(dateString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    // ================== CLEANUP ==================

    destroy() {
        // Destroy WebSocket manager
        this.websockets.destroy();
        
    }

    // ================== PLACEHOLDER METHODS (implemented in other modules) ==================
    
    renderMessage(message, isNew = false) {
        return `<div>Message rendering placeholder - implement in forum-messages.js</div>`;
    }

    handleNewMessage(message) {
        console.log('handleNewMessage - implement in forum-messages.js');
    }

    handleTypingIndicator(data) {
        console.log('handleTypingIndicator - implement in forum-messages.js');
    }

    handleThreadUpdate(data) {
        if (window.forumThreadSettings) {
            window.forumThreadSettings.handleThreadUpdate(data);
        } else {
            console.log('handleThreadUpdate - forum-thread-settings.js not loaded');
        }
    }

    handleMessageEdit(editedMessage) {
        console.log('handleMessageEdit - implement in forum-messages.js');
    }

    handleMessageDelete(messageId) {
        console.log('handleMessageDelete - implement in forum-messages.js');
    }

    handleMessagesDeleted(messageIds) {
        console.log('handleMessagesDeleted - implement in forum-messages.js');
    }

    navigateToSettings() {
        console.log('navigateToSettings - implement in forum-settings.js');
    }

    handleSendMessage() {
        console.log('handleSendMessage - implement in forum-messages.js');
    }

    showMentionNotification(data) {
        console.log('showMentionNotification - implement in forum-messages.js');
    }

    handleMessageLiked(data) {
        console.log('handleMessageLiked - implement in forum-messages.js');
    }

    handleMessageUnliked(data) {
        console.log('handleMessageUnliked - implement in forum-messages.js');
    }

    handleMessageThreadCountUpdated(data) {
        console.log('handleMessageThreadCountUpdated - implement in forum-messages.js');
    }

    showSpawnedThreads(messageId) {
        console.log('showSpawnedThreads - implement in forum-messages.js');
    }

    // For thread WebSocket message handling - delegate to other modules as needed
    handleWebSocketMessage(data) {
        const handlers = {
            'new_message': () => this.handleNewMessage(data.message),
            'user_typing': () => this.handleTypingIndicator(data),
            'mention': () => this.showMentionNotification(data),
            'thread_updated': () => this.handleThreadUpdate(data),
            'message_edited': () => this.handleMessageEdit(data.message),
            'message_deleted': () => this.handleMessageDelete(data.message_id),
            'messages_deleted': () => this.handleMessagesDeleted(data.message_ids),
            'forum_notification': () => this.handleForumNotification(data.notification),
            'forum_notification_count': () => this.handleNotificationCountUpdate(data.count),
            'message_liked': () => this.handleMessageLiked(data),
            'message_unliked': () => this.handleMessageUnliked(data),
            'new_thread_created': () => this.handleNewThreadCreated(data),
            'new_sub_thread_created': () => this.handleNewSubThreadCreated(data),
            'message_thread_count_updated': () => this.handleMessageThreadCountUpdated(data),
            'test_broadcast': () => {
                console.log('🧪 Test broadcast received:', data);
                this.showToast(`Test broadcast: ${data.message}`);
            },
            'test_individual_send': () => {
                console.log('🧪 Test individual send received:', data);
                this.showToast(`Individual test: ${data.message}`);
            }
        };
        handlers[data.type]?.();
    }
}

// Export for other modules to extend
window.ForumCore = ForumCore;