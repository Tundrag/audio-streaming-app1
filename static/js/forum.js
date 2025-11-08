class EnhancedForumSPA {
    constructor() {
        this.container = document.getElementById('forumSPA');
        this.currentView = 'threads';
        this.currentTab = 'main';
        this.currentThread = null;
        this.threads = [];
        this.messages = [];
        this.websocket = null;
        this.typingTimer = null;
        this.typingUsers = new Set();
        this.mentionAutocomplete = null;
        this.selectedMentionIndex = -1;
        this.currentMessageForThread = null;
        this.spawnedThreads = [];
        this.currentReplyToMessage = null;
        
        // Settings-related properties
        this.forumSettings = {};
        this.aliasCheckTimeout = null;
        
        // For follow modal
        this.followingThreadId = null;
        this.loadingOlderMessages = false;
        this.currentUser = window.forumUserData || {
            id: null,
            username: '',
            is_creator: false,
            is_team: false,
            is_patreon: false,
            is_kofi: false
        };
        
        this.init();
    }

    async init() {
        try {
            await this.loadThreads(this.currentTab);
            this.renderThreadsView();
        } catch (error) {
            console.error('Error initializing forum:', error);
            this.showError('Failed to load forum. Please refresh the page.');
        }
    }

    // ================== SETTINGS INTEGRATION ==================

    async navigateToSettings() {
        this.currentView = 'settings';
        this.showLoading();
        
        try {
            await this.loadForumSettings();
            this.renderSettingsView();
        } catch (error) {
            console.error('Error loading settings:', error);
            this.showError('Failed to load settings');
            this.backToThreads();
        }
    }

    async loadForumSettings() {
        const response = await fetch('/api/forum/settings');
        if (!response.ok) throw new Error('Failed to load settings');
        this.forumSettings = await response.json();
    }

    renderSettingsView() {
        this.container.innerHTML = `
            <div class="forum-header">
                <div>
                    <h1><i class="fas fa-cog"></i> Forum Settings</h1>
                    <div class="forum-subtitle">Customize your forum experience</div>
                </div>
                <div style="display: flex; gap: 0.5rem; align-items: center;">
                    <button class="btn-secondary" onclick="forum.backToThreads()">
                        <i class="fas fa-arrow-left"></i> Back to Forum
                    </button>
                </div>
            </div>

            <div class="settings-container">
                <div class="success-message" id="successMessage" style="display: none;">
                    <i class="fas fa-check-circle"></i> Settings saved successfully!
                </div>
                
                <!-- Display Settings -->
                <div class="settings-section">
                    <h3><i class="fas fa-user"></i> Display Settings</h3>
                    
                    <div class="setting-item">
                        <label class="setting-label">Display Name</label>
                        <div class="setting-description">
                            Choose how your name appears in the forum. You can use an alias instead of your username.
                        </div>
                        <div class="setting-toggle">
                            <div class="toggle-switch ${this.forumSettings.use_alias ? 'active' : ''}" onclick="forum.toggleSetting('useAlias')">
                                <input type="checkbox" id="useAlias" ${this.forumSettings.use_alias ? 'checked' : ''} style="display: none;">
                            </div>
                            <span class="toggle-label">Use custom alias</span>
                        </div>
                    </div>
                    
                    <div class="setting-item" id="aliasInputSection" style="display: ${this.forumSettings.use_alias ? 'block' : 'none'};">
                        <label class="setting-label" for="displayAlias">Custom Alias</label>
                        <div class="setting-description">
                            Choose a unique display name (2-50 characters). This will replace your username in the forum.
                        </div>
                        <div class="alias-input-container">
                            <input type="text" id="displayAlias" class="setting-input" placeholder="Enter your alias..." 
                                   maxlength="50" value="${this.forumSettings.display_alias || ''}"
                                   oninput="forum.handleAliasInput(this.value)">
                            <span class="alias-check" id="aliasCheck"></span>
                        </div>
                    </div>
                </div>
                
                <!-- Notification Settings -->
                <div class="settings-section">
                    <h3><i class="fas fa-bell"></i> Quick Reply Notifications</h3>
                    
                    <div class="setting-item">
                        <label class="setting-label">Enable Quick Reply Notifications</label>
                        <div class="setting-description">
                            Show popup notifications that allow you to reply without navigating to the forum.
                        </div>
                        <div class="setting-toggle">
                            <div class="toggle-switch ${this.forumSettings.enable_quick_reply_notifications ? 'active' : ''}" onclick="forum.toggleSetting('enableQuickReply')">
                                <input type="checkbox" id="enableQuickReply" ${this.forumSettings.enable_quick_reply_notifications ? 'checked' : ''} style="display: none;">
                            </div>
                            <span class="toggle-label">Enable quick reply notifications</span>
                        </div>
                    </div>
                    
                    <div id="quickReplySettings" style="display: ${this.forumSettings.enable_quick_reply_notifications ? 'block' : 'none'};">
                        <div class="setting-item">
                            <label class="setting-label">Notification Types</label>
                            <div class="setting-description">
                                Choose which types of messages trigger quick reply notifications.
                            </div>
                            
                            <div style="margin-bottom: 0.75rem;">
                                <div class="setting-toggle">
                                    <div class="toggle-switch ${this.forumSettings.quick_reply_for_mentions ? 'active' : ''}" onclick="forum.toggleSetting('quickReplyMentions')">
                                        <input type="checkbox" id="quickReplyMentions" ${this.forumSettings.quick_reply_for_mentions ? 'checked' : ''} style="display: none;">
                                    </div>
                                    <span class="toggle-label">When someone mentions me</span>
                                </div>
                            </div>
                            
                            <div>
                                <div class="setting-toggle">
                                    <div class="toggle-switch ${this.forumSettings.quick_reply_for_replies ? 'active' : ''}" onclick="forum.toggleSetting('quickReplyReplies')">
                                        <input type="checkbox" id="quickReplyReplies" ${this.forumSettings.quick_reply_for_replies ? 'checked' : ''} style="display: none;">
                                    </div>
                                    <span class="toggle-label">When someone replies to my messages</span>
                                </div>
                            </div>
                        </div>
                        
                        <div class="setting-item">
                            <label class="setting-label">Auto-Dismiss Timer</label>
                            <div class="setting-description">
                                How long notifications stay visible before automatically disappearing.
                            </div>
                            <div class="range-container">
                                <input type="range" id="autoDismissRange" class="range-input" min="5" max="60" 
                                       value="${this.forumSettings.quick_reply_auto_dismiss_seconds}" 
                                       oninput="forum.updateRangeValue('autoDismissValue', this.value)">
                                <span class="range-value"><span id="autoDismissValue">${this.forumSettings.quick_reply_auto_dismiss_seconds}</span> seconds</span>
                            </div>
                        </div>
                        
                        <div class="setting-item">
                            <label class="setting-label">Notification Position</label>
                            <div class="setting-description">
                                Where notifications appear on your screen.
                            </div>
                            <select id="notificationPosition" class="setting-select">
                                <option value="top-right" ${this.forumSettings.notification_position === 'top-right' ? 'selected' : ''}>Top Right</option>
                                <option value="top-left" ${this.forumSettings.notification_position === 'top-left' ? 'selected' : ''}>Top Left</option>
                                <option value="bottom-right" ${this.forumSettings.notification_position === 'bottom-right' ? 'selected' : ''}>Bottom Right</option>
                                <option value="bottom-left" ${this.forumSettings.notification_position === 'bottom-left' ? 'selected' : ''}>Bottom Left</option>
                            </select>
                        </div>
                        
                        <div class="setting-item">
                            <label class="setting-label">Notification Sound</label>
                            <div class="setting-description">
                                Play a sound when receiving quick reply notifications.
                            </div>
                            <div class="setting-toggle">
                                <div class="toggle-switch ${this.forumSettings.enable_notification_sound ? 'active' : ''}" onclick="forum.toggleSetting('notificationSound')">
                                    <input type="checkbox" id="notificationSound" ${this.forumSettings.enable_notification_sound ? 'checked' : ''} style="display: none;">
                                </div>
                                <span class="toggle-label">Enable notification sound</span>
                            </div>
                        </div>
                        
                        <!-- Preview Notification -->
                        <div class="setting-item">
                            <label class="setting-label">Preview</label>
                            <div class="setting-description">
                                See how your notifications will look.
                            </div>
                            <button type="button" class="btn-secondary" onclick="forum.showPreviewNotification()">
                                <i class="fas fa-eye"></i> Preview Notification
                            </button>
                            
                            <div class="preview-notification" id="previewNotification" style="display: none;">
                                <div class="notification-header">
                                    <span class="notification-sender">SampleUser mentioned you</span>
                                    <span class="notification-time">just now</span>
                                </div>
                                <div class="notification-content">
                                    "Hey <span id="previewUsername">@${this.getDisplayUsername()}</span>, what do you think about this idea?"
                                </div>
                                <textarea class="quick-reply-input" placeholder="Type your reply..." rows="2"></textarea>
                                <div class="notification-actions">
                                    <button class="notification-btn btn-primary-small">Send</button>
                                    <button class="notification-btn btn-secondary-small">View Thread</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Privacy Settings -->
                <div class="settings-section">
                    <h3><i class="fas fa-shield-alt"></i> Privacy Settings</h3>
                    
                    <div class="setting-item">
                        <label class="setting-label">Online Status</label>
                        <div class="setting-description">
                            Show when you're online in the forum.
                        </div>
                        <div class="setting-toggle">
                            <div class="toggle-switch ${this.forumSettings.show_online_status ? 'active' : ''}" onclick="forum.toggleSetting('showOnlineStatus')">
                                <input type="checkbox" id="showOnlineStatus" ${this.forumSettings.show_online_status ? 'checked' : ''} style="display: none;">
                            </div>
                            <span class="toggle-label">Show my online status</span>
                        </div>
                    </div>
                    
                    <div class="setting-item">
                        <label class="setting-label">Direct Mentions</label>
                        <div class="setting-description">
                            Allow other users to mention you in their messages.
                        </div>
                        <div class="setting-toggle">
                            <div class="toggle-switch ${this.forumSettings.allow_direct_mentions ? 'active' : ''}" onclick="forum.toggleSetting('allowDirectMentions')">
                                <input type="checkbox" id="allowDirectMentions" ${this.forumSettings.allow_direct_mentions ? 'checked' : ''} style="display: none;">
                            </div>
                            <span class="toggle-label">Allow direct mentions</span>
                        </div>
                    </div>
                </div>
                
                <!-- Save/Reset Buttons -->
                <div style="display: flex; align-items: center; padding: 1rem 0;">
                    <button type="button" class="btn-primary" id="saveButton" onclick="forum.saveForumSettings()">
                        <i class="fas fa-save"></i> Save Settings
                    </button>
                    <button type="button" class="btn-secondary" onclick="forum.resetForumSettings()" style="margin-left: 1rem;">
                        <i class="fas fa-undo"></i> Reset to Defaults
                    </button>
                </div>
            </div>
        `;
    }

    // Settings interaction methods
    toggleSetting(settingName) {
        const mappings = {
            'useAlias': 'use_alias',
            'enableQuickReply': 'enable_quick_reply_notifications',
            'quickReplyMentions': 'quick_reply_for_mentions',
            'quickReplyReplies': 'quick_reply_for_replies',
            'notificationSound': 'enable_notification_sound',
            'showOnlineStatus': 'show_online_status',
            'allowDirectMentions': 'allow_direct_mentions'
        };

        const settingKey = mappings[settingName];
        if (!settingKey) return;

        this.forumSettings[settingKey] = !this.forumSettings[settingKey];
        
        // Update UI
        const checkbox = document.getElementById(settingName);
        const toggle = checkbox.parentElement;
        
        checkbox.checked = this.forumSettings[settingKey];
        toggle.classList.toggle('active', this.forumSettings[settingKey]);

        // Handle special cases
        if (settingName === 'useAlias') {
            this.toggleAliasSection(this.forumSettings[settingKey]);
        } else if (settingName === 'enableQuickReply') {
            this.toggleQuickReplySettings(this.forumSettings[settingKey]);
        }
    }

    toggleAliasSection(show) {
        const section = document.getElementById('aliasInputSection');
        if (section) {
            section.style.display = show ? 'block' : 'none';
        }
    }

    toggleQuickReplySettings(show) {
        const section = document.getElementById('quickReplySettings');
        if (section) {
            section.style.display = show ? 'block' : 'none';
        }
    }

    handleAliasInput(value) {
        clearTimeout(this.aliasCheckTimeout);
        const trimmedValue = value.trim();
        
        if (trimmedValue.length >= 2) {
            this.aliasCheckTimeout = setTimeout(() => this.checkAliasAvailability(trimmedValue), 500);
        } else {
            document.getElementById('aliasCheck').textContent = '';
        }
    }

    async checkAliasAvailability(alias) {
        const checkElement = document.getElementById('aliasCheck');
        if (!checkElement) return;
        
        checkElement.textContent = 'Checking...';
        checkElement.className = 'alias-check checking';
        
        try {
            const response = await fetch(`/api/forum/settings/alias/check?alias=${encodeURIComponent(alias)}`);
            const result = await response.json();
            
            if (result.available) {
                checkElement.innerHTML = '<i class="fas fa-check"></i> Available';
                checkElement.className = 'alias-check available';
            } else {
                checkElement.innerHTML = '<i class="fas fa-times"></i> ' + result.message;
                checkElement.className = 'alias-check unavailable';
            }
        } catch (error) {
            checkElement.textContent = '';
            console.error('Error checking alias:', error);
        }
    }

    updateRangeValue(elementId, value) {
        const element = document.getElementById(elementId);
        if (element) {
            element.textContent = value;
        }
    }

    showPreviewNotification() {
        const preview = document.getElementById('previewNotification');
        if (preview) {
            preview.style.display = 'block';
            setTimeout(() => {
                preview.style.display = 'none';
            }, 5000);
        }
    }

    async saveForumSettings() {
        const saveButton = document.getElementById('saveButton');
        if (!saveButton) return;
        
        saveButton.disabled = true;
        saveButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
        
        try {
            const formData = {
                display_alias: document.getElementById('displayAlias')?.value.trim() || null,
                use_alias: this.forumSettings.use_alias,
                enable_quick_reply_notifications: this.forumSettings.enable_quick_reply_notifications,
                quick_reply_for_mentions: this.forumSettings.quick_reply_for_mentions,
                quick_reply_for_replies: this.forumSettings.quick_reply_for_replies,
                quick_reply_auto_dismiss_seconds: parseInt(document.getElementById('autoDismissRange')?.value || '10'),
                notification_position: document.getElementById('notificationPosition')?.value || 'top-right',
                enable_notification_sound: this.forumSettings.enable_notification_sound,
                show_online_status: this.forumSettings.show_online_status,
                allow_direct_mentions: this.forumSettings.allow_direct_mentions
            };
            
            const response = await fetch('/api/forum/settings', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(formData)
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to save settings');
            }
            
            this.forumSettings = await response.json();
            this.showSuccessMessage();
            this.showToast('Settings saved successfully!');
            
        } catch (error) {
            console.error('Error saving settings:', error);
            this.showError(error.message);
        } finally {
            saveButton.disabled = false;
            saveButton.innerHTML = '<i class="fas fa-save"></i> Save Settings';
        }
    }

    async resetForumSettings() {
        if (!confirm('Are you sure you want to reset all forum settings to defaults?')) return;
        
        try {
            const response = await fetch('/api/forum/settings/reset', { method: 'POST' });
            if (!response.ok) throw new Error('Failed to reset settings');
            
            await this.loadForumSettings();
            this.renderSettingsView();
            this.showToast('Settings reset to defaults');
        } catch (error) {
            console.error('Error resetting settings:', error);
            this.showError('Failed to reset settings');
        }
    }

    showSuccessMessage() {
        const message = document.getElementById('successMessage');
        if (message) {
            message.style.display = 'block';
            setTimeout(() => {
                message.style.display = 'none';
            }, 3000);
        }
    }

    getDisplayUsername() {
        return (this.forumSettings.use_alias && this.forumSettings.display_alias) 
            ? this.forumSettings.display_alias 
            : this.currentUser.username;
    }

    // ================== WEBSOCKET MANAGEMENT ==================

    connectWebSocket(threadId) {
        if (this.websocket) this.websocket.close();

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/forum/ws/thread/${threadId}?user_id=${this.currentUser.id}`;
    
        this.websocket = new WebSocket(wsUrl);
        this.websocket.onopen = () => this.updateLiveIndicator(true);
        this.websocket.onmessage = (event) => this.handleWebSocketMessage(JSON.parse(event.data));
        this.websocket.onclose = () => this.updateLiveIndicator(false);
        this.websocket.onerror = () => this.updateLiveIndicator(false);
    }

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
            'forum_notification_count': () => this.handleNotificationCountUpdate(data.count)
        };
        handlers[data.type]?.();
    }

    handleForumNotification(notification) {
        if (window.BadgeManager) {
            window.BadgeManager.handleNotification(notification);
        }
        
        if (window.quickReplyManager && notification.should_show_quick_reply) {
            // Let the quick reply manager handle this
        }
    }

    handleNotificationCountUpdate(count) {
        if (window.BadgeManager) {
            window.BadgeManager.updateBadges(count);
        }
    }

    // ================== TAB MANAGEMENT ==================

    async switchTab(tabName) {
        if (this.currentTab === tabName) return;
        
        this.currentTab = tabName;
        this.showLoading();
        
        try {
            await this.loadThreads(tabName);
            this.renderThreadsView();
        } catch (error) {
            this.showError('Failed to load threads');
        }
    }

    // ================== VIEW RENDERING ==================

    renderThreadsView() {
        const canCreateThreads = this.currentUser.is_creator;
        
        const mainCount = this.currentTab === 'main' ? this.threads.length : '...';
        const followingCount = this.currentTab === 'following' ? this.threads.length : '...';
        
        this.container.innerHTML = `
            <div class="forum-header">
                <div>
                    <h1><i class="fas fa-comments"></i> Community Forum</h1>
                    <div class="forum-subtitle">Connect and discuss with the community</div>
                </div>
                <div style="display: flex; gap: 0.5rem; align-items: center;">
                    <button class="btn-secondary" onclick="forum.navigateToSettings()" title="Forum Settings">
                        <i class="fas fa-cog"></i> Settings
                    </button>
                    ${canCreateThreads ? `<button class="new-thread-btn" onclick="forum.showModal('newThreadModal')"><i class="fas fa-plus"></i> New Discussion</button>` : ''}
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
            
            <div class="thread-list">
                ${this.threads.length === 0 ? this.renderEmptyState() : this.threads.map(thread => this.renderThreadItem(thread)).join('')}
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
        
        if (thread.unread_count > 0) {
            statusBadges.push(`<span class="status-badge unread">${thread.unread_count} unread</span>`);
        }
        
        statusBadges.push(`<span class="status-badge ${thread.thread_type}">${thread.thread_type === 'sub' ? 'Sub' : 'Main'}</span>`);
        
        const typeClass = thread.thread_type === 'sub' ? 'sub-thread' : '';
        const followingClass = thread.is_following ? 'following' : '';
        const unreadClass = thread.unread_count > 0 ? 'has-unread' : '';
        
        const followBadge = thread.follower_count > 0 ? `<span style="font-size: 0.7rem; background: #10b981; color: white; padding: 0.2rem 0.5rem; border-radius: 4px;"><i class="fas fa-users"></i> ${thread.follower_count}</span>` : '';
        const parentContext = thread.thread_type === 'sub' && thread.created_from_message ? `
            <div style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                <i class="fas fa-reply"></i> From message by @${thread.created_from_message.username}: "${thread.created_from_message.content}"
            </div>` : '';

        return `
            <div class="thread-item ${thread.is_pinned ? 'pinned' : ''} ${typeClass} ${followingClass} ${unreadClass}" onclick="forum.viewThread(${thread.id})">
                <div class="thread-status">
                    ${statusBadges.join('')}
                </div>
                <div class="thread-icons">
                    ${followBadge}
                    ${thread.is_pinned ? '<i class="fas fa-thumbtack" style="color: var(--button-primary);"></i>' : ''}
                    ${thread.is_locked ? '<i class="fas fa-lock" style="color: #f59e0b;"></i>' : ''}
                    ${thread.min_tier_cents > 0 ? `<span style="font-size: 0.7rem; background: #7c3aed; color: white; padding: 0.2rem 0.5rem; border-radius: 4px;">$${thread.min_tier_cents / 100}+</span>` : ''}
                </div>
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

        this.container.innerHTML = `
            <div class="breadcrumb">
                <a href="#" onclick="forum.backToThreads()"><i class="fas fa-arrow-left"></i> Back to Forum</a>
                <button class="btn-secondary" onclick="forum.navigateToSettings()" style="margin-left: auto; margin-right: 1rem;" title="Forum Settings">
                    <i class="fas fa-cog"></i> Settings
                </button>
                <span class="live-indicator"><span class="live-dot"></span> Connecting...</span>
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
                            </h2>
                            <div style="font-size: 0.9rem; color: var(--text-secondary); margin-top: 0.25rem;">
                                Started by ${this.currentThread.username} • ${this.formatTimeAgo(this.currentThread.created_at)}
                                ${this.currentThread.is_pinned ? ' • <i class="fas fa-thumbtack"></i> Pinned' : ''}
                                ${this.currentThread.is_locked ? ' • <i class="fas fa-lock"></i> Locked' : ''}
                                ${this.currentThread.follower_count > 0 ? ` • <i class="fas fa-users"></i> ${this.currentThread.follower_count} followers` : ''}
                            </div>
                        </div>
                    </div>
                    <div class="thread-management">
                        <button class="follow-btn ${followBtnClass}" onclick="forum.toggleFollowThread(${this.currentThread.id}, ${!this.currentThread.is_following})">
                            <i class="fas ${followBtnIcon}"></i>
                            ${followBtnText}
                        </button>
                        ${canManage ? `<button onclick="forum.showModal('threadSettingsModal')" style="padding: 0.5rem; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-secondary); cursor: pointer;"><i class="fas fa-cog"></i></button>` : ''}
                        ${canDelete ? `<button class="delete-thread-btn" onclick="forum.deleteThread(${this.currentThread.id})" title="Delete Thread"><i class="fas fa-trash"></i></button>` : ''}
                    </div>
                </div>
            </div>
            <div class="discussion-messages" id="messagesContainer" style="opacity: 0;">
                ${this.messages.length === 0 ? 
                    `<div style="text-align: center; padding: 2rem; color: var(--text-secondary);"><i class="fas fa-comment" style="font-size: 2rem; margin-bottom: 1rem; opacity: 0.5;"></i><p>Start the discussion! Be the first to share your thoughts.</p></div>` : 
                    this.messages.map(message => this.renderMessage(message)).join('')
                }
            </div>
            <div class="typing-indicators"></div>
            ${!this.currentThread.is_locked || this.currentUser.is_creator ? `
                <div class="message-input-area">
                    <textarea id="messageInput" class="message-input" placeholder="Join the discussion... Use @username to mention someone!" onkeydown="if(event.key==='Enter' && !event.shiftKey && !forum.mentionAutocomplete?.style.display === 'block') { event.preventDefault(); forum.handleSendMessage(); }"></textarea>
                    <div style="display: flex; justify-content: flex-end;">
                        <button class="send-btn" onclick="forum.handleSendMessage()"><i class="fas fa-paper-plane"></i> Send Message</button>
                    </div>
                </div>` : 
                `<div style="text-align: center; padding: 1rem; color: var(--text-secondary); background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px;"><i class="fas fa-lock"></i> This thread is locked</div>`
            }
        `;
        
        // 🔥 UPDATED: Position at bottom BEFORE making visible
        this.positionAtBottomInstantly();
        this.initScrollToLoadMore();
        
        const messageInput = document.getElementById('messageInput');
        if (messageInput) this.initMentionAutocomplete(messageInput);
    }
    positionAtBottomInstantly() {
        const container = document.getElementById('messagesContainer');
        if (container && this.messages.length > 0) {
            // Set scroll position to bottom instantly WITHOUT requestAnimationFrame
            container.scrollTop = container.scrollHeight;
            
            // Now make it visible
            container.style.opacity = '1';
            
            console.log('✅ Positioned at bottom instantly - newest messages visible');
        } else if (container) {
            // No messages, just make visible
            container.style.opacity = '1';
        }
    }



    initScrollToLoadMore() {
        const container = document.getElementById('messagesContainer');
        if (!container) {
            console.error('❌ Messages container not found for scroll loading');
            return;
        }
        
        console.log('✅ Scroll-to-load initialized for container:', container);
        
        let scrollTimeout;
        
        container.addEventListener('scroll', () => {
            // Debounce scroll events to avoid too many API calls
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {
                // Load more when user scrolls near the top (within 100px)
                if (container.scrollTop < 100 && !this.loadingOlderMessages) {
                    console.log('🔼 User scrolled to top, loading older messages...');
                    this.loadOlderMessages();
                }
            }, 100); // 100ms debounce
        });
    }

    async loadOlderMessages() {
        if (!this.currentThread || this.messages.length === 0) return;
        
        // Prevent multiple simultaneous requests
        if (this.loadingOlderMessages) return;
        this.loadingOlderMessages = true;
        
        const oldestMessageId = this.messages[0].id;
        const container = document.getElementById('messagesContainer');
        const currentScrollHeight = container.scrollHeight;
        const currentScrollTop = container.scrollTop;
        
        // Show loading indicator
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
            
            console.log(`🔄 Loading ${olderMessages.length} older messages before ID ${oldestMessageId}`);
            
            // Prepend older messages to the array
            this.messages = [...olderMessages, ...this.messages];
            
            // Re-render all messages (this will remove the loading indicator)
            const messagesHtml = this.messages.map(message => this.renderMessage(message)).join('');
            container.innerHTML = messagesHtml;
            
            // 🔥 KEY: Maintain scroll position - user stays where they were
            const newScrollHeight = container.scrollHeight;
            const scrollDifference = newScrollHeight - currentScrollHeight;
            container.scrollTop = currentScrollTop + scrollDifference;
            
            console.log(`✅ Loaded ${olderMessages.length} older messages, maintained scroll position`);
            
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

   
    renderMessage(message, isNew = false) {
        const threadIndicator = message.spawned_thread_count > 0 ? `
            <div class="message-threads-indicator" onclick="forum.showSpawnedThreads(${message.id})">
                <i class="fas fa-comments"></i> ${message.spawned_thread_count} thread${message.spawned_thread_count !== 1 ? 's' : ''}
            </div>` : '';

        const replyIndicator = message.reply_count > 0 ? `
            <div class="reply-indicator" onclick="forum.toggleReplies(${message.id})">
                <i class="fas fa-reply"></i> ${message.reply_count} ${message.reply_count === 1 ? 'reply' : 'replies'}
            </div>` : '';

        const replyContext = message.reply_to_message ? `
            <div class="reply-context">
                <div class="reply-author">Replying to @${message.reply_to_message.username}</div>
                <div class="reply-content">${message.reply_to_message.content}</div>
            </div>` : '';

        const isReply = message.reply_to_id ? 'reply' : '';

        const canEdit = message.user_id === this.currentUser.id;
        const canDelete = message.user_id === this.currentUser.id || this.currentUser.is_creator;

        const editButton = canEdit ? `
            <button class="message-action-btn" onclick="forum.showEditInput(${message.id})" title="Edit">
                <i class="fas fa-edit"></i> Edit
            </button>` : '';

        const deleteButton = canDelete ? `
            <button class="message-action-btn delete-btn" onclick="forum.deleteMessage(${message.id})" title="Delete">
                <i class="fas fa-trash"></i> Delete
            </button>` : '';

        const editedIndicator = message.is_edited ? `
            <span class="edited-indicator" style="font-size: 0.7rem; color: var(--text-secondary); font-style: italic;">
                <i class="fas fa-edit"></i> edited
            </span>` : '';

        return `
            <div class="message-item ${isNew ? 'new-message' : ''} ${isReply}" data-message-id="${message.id}">
                <div class="message-actions">
                    <button class="message-action-btn" onclick="forum.showReplyInput(${message.id})" title="Reply">
                        <i class="fas fa-reply"></i> Reply
                    </button>
                    <button class="message-action-btn" onclick="forum.createThreadFromMessage(${message.id})" title="Create Thread">
                        <i class="fas fa-comments"></i> Thread
                    </button>
                    ${editButton}
                    ${deleteButton}
                </div>
                ${replyContext}
                <div class="message-header">
                    <div class="user-avatar" style="background-color: ${message.user_badge_color}; width: 32px; height: 32px; font-size: 0.8rem;">
                        ${message.username.substring(0, 2).toUpperCase()}
                    </div>
                    <span style="font-weight: 600; color: var(--text-primary);">${message.username}</span>
                    <span class="user-badge" style="background-color: ${message.user_badge_color}; font-size: 0.7rem;">${message.user_role}</span>
                    <span style="font-size: 0.8rem; color: var(--text-secondary);">${this.formatTime(message.created_at)}</span>
                    ${editedIndicator}
                    ${message.mentions.length > 0 ? `<span style="font-size: 0.7rem; background: rgba(59, 130, 246, 0.1); color: #3b82f6; padding: 0.1rem 0.3rem; border-radius: 4px;"><i class="fas fa-at"></i> ${message.mentions.length}</span>` : ''}
                </div>
                <div class="message-content">${message.content_html || message.content}</div>
                ${threadIndicator}
                ${replyIndicator}
                <div class="reply-input-container">
                    <div class="replying-to">Replying to @${message.username}</div>
                    <textarea class="reply-input" placeholder="Write your reply... Use @username to mention someone!"></textarea>
                    <div class="reply-actions">
                        <button class="btn-secondary" onclick="forum.hideReplyInput(${message.id})">Cancel</button>
                        <button class="btn-primary" onclick="forum.sendReply(${message.id})">Send Reply</button>
                    </div>
                </div>
            </div>
        `;
    }

    // ================== FOLLOW SYSTEM ==================

    async toggleFollowThread(threadId, showWarning = false) {
        const thread = this.threads.find(t => t.id === threadId) || this.currentThread;
        if (!thread) return;

        try {
            if (thread.is_following) {
                const response = await fetch(`/api/forum/threads/${threadId}/follow`, {
                    method: 'DELETE'
                });
                if (!response.ok) throw new Error('Failed to unfollow thread');

                const result = await response.json();
                thread.is_following = result.is_following;
                thread.follower_count = result.follower_count;
                
                if (window.showToast) {
                    window.showToast('Unfollowed thread');
                }
            } else {
                if (showWarning || (thread.thread_type === 'main' && thread.message_count > 10)) {
                    this.followingThreadId = threadId;
                    
                    const warningDiv = document.getElementById('followWarning');
                    if (thread.thread_type === 'main' && thread.message_count > 10) {
                        warningDiv.style.display = 'block';
                    } else {
                        warningDiv.style.display = 'none';
                    }
                    
                    this.showModal('followThreadModal');
                    return;
                }
                
                await this.followThreadWithSettings(threadId, {
                    notify_on_new_message: true,
                    notify_on_mention: true,
                    notify_on_reply: true
                });
            }
            
            if (this.currentView === 'discussion' && this.currentThread?.id === threadId) {
                this.renderDiscussionView();
            }
            
        } catch (error) {
            this.showError('Failed to update follow status');
        }
    }

    async followThreadWithSettings(threadId, settings) {
        try {
            const response = await fetch(`/api/forum/threads/${threadId}/follow`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            
            if (!response.ok) throw new Error('Failed to follow thread');

            const result = await response.json();
            const thread = this.threads.find(t => t.id === threadId) || this.currentThread;
            
            if (thread) {
                thread.is_following = result.is_following;
                thread.follower_count = result.follower_count;
            }
            
            if (window.showToast) {
                if (result.requires_warning && result.warning_message) {
                    window.showToast(result.warning_message);
                } else {
                    window.showToast('Following thread');
                }
            }
            
            return result;
            
        } catch (error) {
            this.showError('Failed to follow thread');
            throw error;
        }
    }

    async handleFollowThreadForm(event) {
        event.preventDefault();
        
        if (!this.followingThreadId) return;
        
        const settings = {
            notify_on_new_message: document.getElementById('notifyNewMessage').checked,
            notify_on_mention: document.getElementById('notifyMention').checked,
            notify_on_reply: document.getElementById('notifyReply').checked
        };
        
        try {
            await this.followThreadWithSettings(this.followingThreadId, settings);
            this.hideModal('followThreadModal');
            this.followingThreadId = null;
            
            if (this.currentView === 'discussion') {
                this.renderDiscussionView();
            }
        } catch (error) {
            // Error already handled in followThreadWithSettings
        }
    }

    // ================== MESSAGE HANDLERS ==================

    handleNewMessage(message) {
        if (this.currentView === 'discussion' && this.currentThread) {
            this.messages.push(message);
            this.renderNewMessage(message);
                        
            const thread = this.threads.find(t => t.id === this.currentThread.id);
            if (thread) thread.message_count++;

            if (message.reply_to_id) {
                const parentMessage = this.messages.find(m => m.id === message.reply_to_id);
                if (parentMessage) {
                    parentMessage.reply_count = (parentMessage.reply_count || 0) + 1;
                    const replyIndicator = document.querySelector(`[data-message-id="${message.reply_to_id}"] .reply-indicator`);
                    if (replyIndicator) {
                        replyIndicator.innerHTML = `<i class="fas fa-reply"></i> ${parentMessage.reply_count} ${parentMessage.reply_count === 1 ? 'reply' : 'replies'}`;
                    } else {
                        const messageElement = document.querySelector(`[data-message-id="${message.reply_to_id}"]`);
                        if (messageElement) {
                            const indicator = document.createElement('div');
                            indicator.className = 'reply-indicator';
                            indicator.onclick = () => this.toggleReplies(message.reply_to_id);
                            indicator.innerHTML = `<i class="fas fa-reply"></i> ${parentMessage.reply_count} ${parentMessage.reply_count === 1 ? 'reply' : 'replies'}`;
                            messageElement.querySelector('.message-content').appendChild(indicator);
                        }
                    }
                }
            }
        }
    }


    handleTypingIndicator(data) {
        if (data.user_id === this.currentUser.id) return;
        data.is_typing ? this.typingUsers.add(data.username) : this.typingUsers.delete(data.username);
        this.updateTypingIndicators();
    }

    handleThreadUpdate(data) {
        if (this.currentThread && this.currentThread.id === data.thread_id) {
            Object.assign(this.currentThread, data.updates);
            this.renderDiscussionView();
        }
    }

    handleMessageEdit(editedMessage) {
        if (this.currentView === 'discussion' && this.currentThread) {
            const messageIndex = this.messages.findIndex(m => m.id === editedMessage.id);
            if (messageIndex !== -1) {
                this.messages[messageIndex] = { ...this.messages[messageIndex], ...editedMessage };
                
                const messageElement = document.querySelector(`[data-message-id="${editedMessage.id}"]`);
                if (messageElement) {
                    const contentElement = messageElement.querySelector('.message-content');
                    if (contentElement) {
                        contentElement.innerHTML = editedMessage.content_html || editedMessage.content;
                    }
                    
                    const headerElement = messageElement.querySelector('.message-header');
                    let editedIndicator = headerElement.querySelector('.edited-indicator');
                    if (!editedIndicator) {
                        editedIndicator = document.createElement('span');
                        editedIndicator.className = 'edited-indicator';
                        editedIndicator.style.cssText = 'font-size: 0.7rem; color: var(--text-secondary); font-style: italic;';
                        headerElement.appendChild(editedIndicator);
                    }
                    editedIndicator.innerHTML = '<i class="fas fa-edit"></i> edited';
                    
                    messageElement.style.background = 'rgba(255, 193, 7, 0.1)';
                    setTimeout(() => { messageElement.style.background = ''; }, 2000);
                }
            }
        }
    }

    handleMessageDelete(messageId) {
        if (this.currentView === 'discussion' && this.currentThread) {
            this.messages = this.messages.filter(m => m.id !== messageId);
            
            const messageElement = document.querySelector(`[data-message-id="${messageId}"]`);
            if (messageElement) {
                messageElement.classList.add('deleting');
                setTimeout(() => messageElement.remove(), 200);
            }
            
            if (this.currentThread) {
                this.currentThread.message_count = Math.max(0, this.currentThread.message_count - 1);
            }
        }
    }

    handleMessagesDeleted(messageIds) {
        if (this.currentView === 'discussion' && this.currentThread) {
            this.messages = this.messages.filter(m => !messageIds.includes(m.id));
            
            messageIds.forEach(messageId => {
                const messageElement = document.querySelector(`[data-message-id="${messageId}"]`);
                if (messageElement) {
                    messageElement.classList.add('deleting');
                    setTimeout(() => messageElement.remove(), 200);
                }
            });
            
            if (this.currentThread) {
                this.currentThread.message_count = Math.max(0, this.currentThread.message_count - messageIds.length);
            }
        }
    }

    updateLiveIndicator(connected) {
        document.querySelectorAll('.live-indicator').forEach(indicator => {
            indicator.className = `live-indicator ${connected ? '' : 'disconnected'}`;
            indicator.innerHTML = connected ? 
                '<span class="live-dot"></span> Live' : 
                '<i class="fas fa-exclamation-triangle"></i> Disconnected';
        });
    }

    renderNewMessage(message) {
        const container = document.getElementById('messagesContainer');
        if (container) {
            container.insertAdjacentHTML('beforeend', this.renderMessage(message, true));
            setTimeout(() => {
                const messageElement = container.querySelector(`[data-message-id="${message.id}"]`);
                if (messageElement) messageElement.classList.remove('new-message');
            }, 3000);
        }
    }

    // ================== EDIT/DELETE SYSTEM ==================

    async showEditInput(messageId) {
        const message = this.messages.find(m => m.id === messageId);
        if (!message) return;

        if (message.user_id !== this.currentUser.id) {
            this.showError("You can only edit your own messages");
            return;
        }

        document.querySelectorAll('.edit-input-container').forEach(container => {
            container.classList.remove('active');
        });

        const messageElement = document.querySelector(`[data-message-id="${messageId}"]`);
        const contentElement = messageElement.querySelector('.message-content');
        
        let editContainer = messageElement.querySelector('.edit-input-container');
        if (!editContainer) {
            editContainer = document.createElement('div');
            editContainer.className = 'edit-input-container';
            editContainer.innerHTML = `
                <div class="editing-message">Editing message...</div>
                <textarea class="edit-input">${message.content}</textarea>
                <div class="edit-actions">
                    <button class="btn-secondary" onclick="forum.hideEditInput(${messageId})">Cancel</button>
                    <button class="btn-primary" onclick="forum.saveEdit(${messageId})">Save Changes</button>
                </div>
            `;
            contentElement.appendChild(editContainer);
        }
        
        editContainer.classList.add('active');
        const textarea = editContainer.querySelector('.edit-input');
        textarea.focus();
        this.initMentionAutocomplete(textarea);
    }

    hideEditInput(messageId) {
        const container = document.querySelector(`[data-message-id="${messageId}"] .edit-input-container`);
        if (container) {
            container.classList.remove('active');
            const textarea = container.querySelector('.edit-input');
            if (textarea) {
                const message = this.messages.find(m => m.id === messageId);
                textarea.value = message ? message.content : '';
            }
        }
        this.hideMentionAutocomplete();
    }

    async saveEdit(messageId) {
        const container = document.querySelector(`[data-message-id="${messageId}"] .edit-input-container`);
        if (!container) return;

        const textarea = container.querySelector('.edit-input');
        const content = textarea.value.trim();
        if (!content) return;

        try {
            const response = await fetch(`/api/forum/messages/${messageId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });

            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to edit message');

            const message = this.messages.find(m => m.id === messageId);
            if (message) {
                message.content = content;
                message.is_edited = true;
            }

            this.hideEditInput(messageId);
            this.hideMentionAutocomplete();

            if (window.showToast) window.showToast('Message updated successfully!');
            return true;
        } catch (error) {
            this.showError(error.message);
            return false;
        }
    }

    async deleteMessage(messageId) {
        const message = this.messages.find(m => m.id === messageId);
        if (!message) return;

        const canDelete = message.user_id === this.currentUser.id || this.currentUser.is_creator;
        if (!canDelete) {
            this.showError("You don't have permission to delete this message");
            return;
        }

        if (!confirm('Are you sure you want to delete this message? This action cannot be undone.')) return;

        try {
            const response = await fetch(`/api/forum/messages/${messageId}`, {
                method: 'DELETE'
            });

            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to delete message');

            this.messages = this.messages.filter(m => m.id !== messageId);

            const messageElement = document.querySelector(`[data-message-id="${messageId}"]`);
            if (messageElement) {
                messageElement.classList.add('deleting');
                setTimeout(() => messageElement.remove(), 200);
            }

            if (window.showToast) window.showToast('Message deleted successfully!');
            return true;
        } catch (error) {
            this.showError(error.message);
            return false;
        }
    }

    // ================== REPLY SYSTEM ==================

    showReplyInput(messageId) {
        const message = this.messages.find(m => m.id === messageId);
        if (!message) return;

        document.querySelectorAll('.reply-input-container').forEach(container => {
            container.classList.remove('active');
        });

        this.currentReplyToMessage = message;
        const container = document.querySelector(`[data-message-id="${messageId}"] .reply-input-container`);
        if (container) {
            container.classList.add('active');
            const textarea = container.querySelector('.reply-input');
            if (textarea) {
                textarea.focus();
                this.initMentionAutocomplete(textarea);
            }
        }
    }

    hideReplyInput(messageId = null) {
        if (messageId) {
            const container = document.querySelector(`[data-message-id="${messageId}"] .reply-input-container`);
            if (container) {
                container.classList.remove('active');
                const textarea = container.querySelector('.reply-input');
                if (textarea) textarea.value = '';
            }
        } else {
            document.querySelectorAll('.reply-input-container').forEach(container => {
                container.classList.remove('active');
                const textarea = container.querySelector('.reply-input');
                if (textarea) textarea.value = '';
            });
        }
        this.currentReplyToMessage = null;
        this.hideMentionAutocomplete();
    }

    async sendReply(messageId) {
        const container = document.querySelector(`[data-message-id="${messageId}"] .reply-input-container`);
        if (!container) return;

        const textarea = container.querySelector('.reply-input');
        const content = textarea.value.trim();
        if (!content) return;

        try {
            const response = await fetch(`/api/forum/threads/${this.currentThread.id}/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    content: content,
                    reply_to_id: messageId 
                })
            });

            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to send reply');

            textarea.value = '';
            this.hideReplyInput(messageId);
            this.hideMentionAutocomplete();

            const replyIndicator = document.querySelector(`[data-message-id="${messageId}"] .reply-indicator`);
            if (replyIndicator) {
                const message = this.messages.find(m => m.id === messageId);
                if (message) {
                    message.reply_count = (message.reply_count || 0) + 1;
                    replyIndicator.innerHTML = `<i class="fas fa-reply"></i> ${message.reply_count} ${message.reply_count === 1 ? 'reply' : 'replies'}`;
                }
            }

            return true;
        } catch (error) {
            this.showError(error.message);
            return false;
        }
    }

    async toggleReplies(messageId) {
        console.log(`Toggle replies for message ${messageId}`);
    }

    // ================== THREAD FROM MESSAGE ==================

    async createThreadFromMessage(messageId) {
        const message = this.messages.find(m => m.id === messageId);
        if (!message) return this.showError('Message not found');
        
        this.currentMessageForThread = message;
        document.getElementById('originalMessagePreview').innerHTML = `
            <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                <i class="fas fa-user"></i> ${message.username} • ${this.formatTimeAgo(message.created_at)}
            </div>
            <div>${message.content}</div>
        `;
        this.showModal('threadFromMessageModal');
    }

    async handleCreateThreadFromMessage(event) {
        event.preventDefault();
        if (!this.currentMessageForThread) return;

        const title = document.getElementById('threadFromMessageTitle').value.trim();
        const content = document.getElementById('threadFromMessageContent').value.trim();
        if (!title || !content) return;
        
        try {
            const response = await fetch(`/api/forum/messages/${this.currentMessageForThread.id}/create-thread`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, content })
            });

            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to create thread');

            const newThread = await response.json();
            this.currentMessageForThread.spawned_thread_count = (this.currentMessageForThread.spawned_thread_count || 0) + 1;
            this.updateMessageWithThreadCount(this.currentMessageForThread);
            this.hideModal('threadFromMessageModal');
            
            if (window.showToast) window.showToast('Thread created successfully!');
            setTimeout(() => this.viewThread(newThread.id), 500);
            
        } catch (error) {
            this.showError(error.message);
        }
    }

    async showSpawnedThreads(messageId) {
        try {
            const response = await fetch(`/api/forum/messages/${messageId}/threads`);
            if (!response.ok) throw new Error('Failed to load spawned threads');

            this.spawnedThreads = await response.json();
            const list = document.getElementById('spawnedThreadsList');

            list.innerHTML = this.spawnedThreads.length === 0 ? 
                `<div style="text-align: center; padding: 2rem; color: var(--text-secondary);">
                    <i class="fas fa-comments" style="font-size: 2rem; margin-bottom: 1rem; opacity: 0.5;"></i>
                    <p>No threads created from this message yet.</p>
                </div>` : 
                this.spawnedThreads.map(thread => `
                    <div class="spawned-thread-item" onclick="forum.viewThread(${thread.id}); forum.hideModal('spawnedThreadsModal');">
                        <div>
                            <div style="font-weight: 600; color: var(--text-primary);">${thread.title}</div>
                            <div style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.25rem;">
                                by ${thread.username} • ${thread.message_count} messages • ${this.formatTimeAgo(thread.created_at)}
                            </div>
                        </div>
                        <i class="fas fa-arrow-right" style="color: var(--text-secondary);"></i>
                    </div>
                `).join('');

            this.showModal('spawnedThreadsModal');
        } catch (error) {
            this.showError('Failed to load spawned threads');
        }
    }

    updateMessageWithThreadCount(message) {
        const messageElement = document.querySelector(`[data-message-id="${message.id}"]`);
        if (messageElement && message.spawned_thread_count > 0) {
            const existing = messageElement.querySelector('.message-threads-indicator');
            const indicator = existing || document.createElement('div');
            indicator.className = 'message-threads-indicator';
            indicator.onclick = () => this.showSpawnedThreads(message.id);
            indicator.innerHTML = `<i class="fas fa-comments"></i> ${message.spawned_thread_count} thread${message.spawned_thread_count !== 1 ? 's' : ''}`;
            if (!existing) messageElement.querySelector('.message-content').appendChild(indicator);
        }
    }

    async deleteThread(threadId) {
        if (!confirm('Are you sure you want to delete this thread? This action cannot be undone.')) return;

        try {
            const response = await fetch(`/api/forum/threads/${threadId}/delete-hierarchy`, { method: 'DELETE' });
            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to delete thread');

            this.threads = this.threads.filter(t => t.id !== threadId);
            if (window.showToast) window.showToast('Thread deleted successfully');
            
            if (this.currentView === 'discussion' && this.currentThread?.id === threadId) {
                this.backToThreads();
            } else {
                this.renderThreadsView();
            }
        } catch (error) {
            this.showError(error.message);
        }
    }

    // ================== MENTION SYSTEM ==================

    initMentionAutocomplete(textArea) {
        textArea.addEventListener('input', (e) => this.handleMentionInput(e));
        textArea.addEventListener('keydown', (e) => this.handleMentionKeydown(e));
    }

    async handleMentionInput(event) {
        const textArea = event.target;
        const text = textArea.value;
        const beforeCursor = text.substring(0, textArea.selectionStart);
        const mentionMatch = beforeCursor.match(/@(\w*)$/);
        
        if (mentionMatch && mentionMatch[1].length >= 1) {
            await this.showMentionAutocomplete(textArea, mentionMatch[1]);
        } else {
            this.hideMentionAutocomplete();
        }

        this.sendTypingIndicator(true);
        clearTimeout(this.typingTimer);
        this.typingTimer = setTimeout(() => this.sendTypingIndicator(false), 2000);
    }

    async showMentionAutocomplete(textArea, query) {
        try {
            const response = await fetch(`/api/forum/users/search?q=${encodeURIComponent(query)}&limit=5`);
            if (!response.ok) return;
            
            const users = await response.json();
            if (users.length === 0) return this.hideMentionAutocomplete();

            if (!this.mentionAutocomplete) {
                this.mentionAutocomplete = document.createElement('div');
                this.mentionAutocomplete.className = 'mention-autocomplete';
                textArea.parentElement.appendChild(this.mentionAutocomplete);
            }

            this.mentionAutocomplete.innerHTML = users.map((user, index) => `
                <div class="mention-item ${index === 0 ? 'selected' : ''}" data-username="${user.username}">
                    <div class="user-avatar" style="background-color: ${user.badge_color}">
                        ${user.username.substring(0, 2).toUpperCase()}
                    </div>
                    <div>
                        <div style="font-weight: 500;">${user.username}</div>
                        <div style="font-size: 0.8rem; color: var(--text-secondary);">${user.role}</div>
                    </div>
                </div>
            `).join('');

            this.mentionAutocomplete.style.display = 'block';
            this.selectedMentionIndex = 0;

            this.mentionAutocomplete.querySelectorAll('.mention-item').forEach((item) => {
                item.addEventListener('click', () => this.insertMention(textArea, item.dataset.username));
            });
        } catch (error) {
            console.error('Error loading user suggestions:', error);
        }
    }

    handleMentionKeydown(event) {
        if (!this.mentionAutocomplete || this.mentionAutocomplete.style.display === 'none') return;

        const items = this.mentionAutocomplete.querySelectorAll('.mention-item');
        const keyActions = {
            'ArrowDown': () => {
                this.selectedMentionIndex = Math.min(this.selectedMentionIndex + 1, items.length - 1);
                this.updateMentionSelection(items);
            },
            'ArrowUp': () => {
                this.selectedMentionIndex = Math.max(this.selectedMentionIndex - 1, 0);
                this.updateMentionSelection(items);
            },
            'Enter': () => this.insertMention(event.target, items[this.selectedMentionIndex]?.dataset.username),
            'Tab': () => this.insertMention(event.target, items[this.selectedMentionIndex]?.dataset.username),
            'Escape': () => this.hideMentionAutocomplete()
        };

        if (keyActions[event.key]) {
            event.preventDefault();
            keyActions[event.key]();
        }
    }

    updateMentionSelection(items) {
        items.forEach((item, index) => {
            item.classList.toggle('selected', index === this.selectedMentionIndex);
        });
    }

    insertMention(textArea, username) {
        if (!username) return;
        const cursorPos = textArea.selectionStart;
        const text = textArea.value;
        const beforeCursor = text.substring(0, cursorPos);
        const afterCursor = text.substring(cursorPos);
        
        const newBefore = beforeCursor.replace(/@\w*$/, `@${username} `);
        textArea.value = newBefore + afterCursor;
        textArea.selectionStart = textArea.selectionEnd = newBefore.length;
        
        this.hideMentionAutocomplete();
        textArea.focus();
    }

    hideMentionAutocomplete() {
        if (this.mentionAutocomplete) this.mentionAutocomplete.style.display = 'none';
        this.selectedMentionIndex = -1;
    }

    // ================== TYPING & NOTIFICATIONS ==================

    sendTypingIndicator(isTyping) {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            this.websocket.send(JSON.stringify({ type: 'typing', is_typing: isTyping }));
        }
    }

    updateTypingIndicators() {
        const container = document.querySelector('.typing-indicators');
        if (!container) return;

        if (this.typingUsers.size === 0) {
            container.innerHTML = '';
            return;
        }

        const userList = Array.from(this.typingUsers);
        let text = userList.length === 1 ? `${userList[0]} is typing` : 
                   userList.length === 2 ? `${userList[0]} and ${userList[1]} are typing` :
                   `${userList[0]} and ${userList.length - 1} others are typing`;

        container.innerHTML = `${text} <span class="typing-dots">
            <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
        </span>`;
    }

    showMentionNotification(data) {
        const notification = document.createElement('div');
        notification.style.cssText = `position: fixed; top: 1rem; right: 1rem; background: var(--card-bg); border: 1px solid var(--button-primary); border-radius: 8px; padding: 1rem; max-width: 300px; z-index: 2000; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);`;
        
        notification.innerHTML = `
            <div style="font-weight: 600; margin-bottom: 0.5rem;">
                <i class="fas fa-at"></i> ${data.from_user} mentioned you
            </div>
            <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                in "${data.thread_title}"
            </div>
            <div style="background: var(--bg-secondary); padding: 0.5rem; border-radius: 4px; font-size: 0.9rem;">
                ${data.message.content.substring(0, 100)}${data.message.content.length > 100 ? '...' : ''}
            </div>
            <button onclick="forum.viewThread(${data.thread_id}); this.parentElement.remove();" 
                    style="margin-top: 0.5rem; padding: 0.25rem 0.5rem; background: var(--button-primary); color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.8rem;">
                View Thread
            </button>
        `;

        document.body.appendChild(notification);
        setTimeout(() => notification.remove(), 10000);
        notification.addEventListener('click', (e) => { if (e.target === notification) notification.remove(); });
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

    async createThread(title, content, minTier, access) {
        try {
            const rolesMap = {
                'public': ['creator', 'team', 'patron', 'kofi', 'member'],
                'patrons': ['creator', 'team', 'patron'],
                'team': ['creator', 'team']
            };

            const response = await fetch('/api/forum/threads', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title, content,
                    min_tier_cents: minTier,
                    roles_allowed: rolesMap[access]
                })
            });

            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to create thread');

            const newThread = await response.json();
            this.threads.unshift(newThread);
            this.renderThreadsView();
            
            if (window.showToast) window.showToast('Thread created successfully!');
            return true;
        } catch (error) {
            this.showError(error.message);
            return false;
        }
    }

    // ================== VIEW MANAGEMENT ==================

    async viewThread(threadId) {
        try {
            this.showLoading();
            
            const thread = this.threads.find(t => t.id === threadId);
            if (!thread) throw new Error('Thread not found');
            
            this.currentThread = thread;
            
            const hierarchyResponse = await fetch(`/api/forum/threads/${threadId}/hierarchy`);
            if (hierarchyResponse.ok) {
                Object.assign(this.currentThread, await hierarchyResponse.json());
            }
            
            await this.loadMessages(threadId);
            this.currentView = 'discussion';
            this.renderDiscussionView();
            this.connectWebSocket(threadId);
            
        } catch (error) {
            this.showError(error.message);
            this.backToThreads();
        }
    }

    backToThreads() {
        if (this.currentView === 'settings') {
            this.currentView = 'threads';
            this.renderThreadsView();
            return;
        }
        
        this.currentView = 'threads';
        this.currentThread = null;
        this.messages = [];
        this.typingUsers.clear();
        
        if (this.websocket) {
            this.websocket.close();
            this.websocket = null;
        }
        
        this.renderThreadsView();
    }

    // ================== MODAL MANAGEMENT ==================

    showModal(modalId) {
        document.getElementById(modalId).classList.add('active');
        const firstInput = document.querySelector(`#${modalId} input, #${modalId} textarea`);
        if (firstInput) {
            firstInput.focus();
            if (firstInput.type === 'textarea' || firstInput.type === 'text') {
                this.initMentionAutocomplete(firstInput);
            }
        }
    }

    hideModal(modalId) {
        document.getElementById(modalId).classList.remove('active');
        const form = document.querySelector(`#${modalId} form`);
        if (form) form.reset();
        this.hideMentionAutocomplete();
        if (modalId === 'followThreadModal') this.followingThreadId = null;
        if (modalId === 'threadFromMessageModal') this.currentMessageForThread = null;
        if (modalId === 'spawnedThreadsModal') this.spawnedThreads = [];
    }

    // ================== EVENT HANDLERS ==================

    async handleCreateThread(event) {
        event.preventDefault();
        const title = document.getElementById('threadTitle').value.trim();
        const content = document.getElementById('threadContent').value.trim();
        const minTier = parseInt(document.getElementById('threadMinTier').value) || 0;
        const access = document.getElementById('threadAccess').value;
        
        if (!title || !content) return;
        
        const success = await this.createThread(title, content, minTier, access);
        if (success) this.hideModal('newThreadModal');
    }

    async handleSendMessage() {
        const input = document.getElementById('messageInput');
        const content = input.value.trim();
        if (!content) return;
        
        this.sendTypingIndicator(false);
        
        try {
            const response = await fetch(`/api/forum/threads/${this.currentThread.id}/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });

            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to send message');

            input.value = '';
            this.hideMentionAutocomplete();
            return true;
        } catch (error) {
            this.showError(error.message);
            return false;
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
}

// ================== INITIALIZATION ==================

let forum;
document.addEventListener('DOMContentLoaded', () => {
    forum = new EnhancedForumSPA();
    
    // Form event listeners
    document.getElementById('newThreadForm').addEventListener('submit', (e) => forum.handleCreateThread(e));
    document.getElementById('followThreadForm').addEventListener('submit', (e) => forum.handleFollowThreadForm(e));
    document.getElementById('threadFromMessageForm').addEventListener('submit', (e) => forum.handleCreateThreadFromMessage(e));
    
    // Modal close handlers
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('modal')) {
            e.target.classList.remove('active');
            forum.hideMentionAutocomplete();
        }
    });
    
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            forum.hideMentionAutocomplete();
            document.querySelectorAll('.modal.active').forEach(modal => modal.classList.remove('active'));
        }
    });
});

// Cleanup
window.addEventListener('beforeunload', () => {
    if (forum?.websocket) forum.websocket.close();
});