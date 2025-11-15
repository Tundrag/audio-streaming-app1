/**forum-settings.js
 * FORUM SETTINGS MODULE  
 * Handles: Settings integration, follow system, thread management, modal management, thread creation/deletion
 * Dependencies: forum-messages.js (extends ForumMessages)
 * Used by: Main forum app (final complete class)
 */

class EnhancedForumSPA extends ForumMessages {
    constructor() {
        super();
        
        // Settings-related properties (existing)
        this.forumSettings = {};
        this.aliasCheckTimeout = null;
        this.followingThreadId = null;
        
        // 🆕 NEW: Browser notification properties
        this.browserNotificationSettings = {
            enabled: false,
            forumNotifications: true,
            mentionNotifications: true,
            replyNotifications: true,
            generalNotifications: true,
            soundEnabled: true,
            autoClose: 5000,
            maxConcurrent: 3
        };
        this.notificationPermission = 'default';
        this.activeNotifications = new Set();
        
        // 🆕 NEW: @everyone moderation properties
        this.moderationSettings = {};
        this.canUseEveryone = false;
        this.allowEveryoneMentions = true;

        // Initialize browser notifications
        this.initBrowserNotifications();
    }





    // ================== BROWSER NOTIFICATIONS INITIALIZATION ==================

    initBrowserNotifications() {
        this.updateNotificationPermission();
        this.loadBrowserNotificationSettings();
        
        // Listen for permission changes
        if ('permissions' in navigator) {
            navigator.permissions.query({name: 'notifications'})
                .then(permission => {
                    permission.addEventListener('change', () => {
                        this.updateNotificationPermission();
                        this.updatePermissionStatusUI();
                    });
                })
                .catch(() => {
                    // Fallback for browsers that don't support permissions API
                });
        }

    }

    updateNotificationPermission() {
        if ('Notification' in window) {
            this.notificationPermission = Notification.permission;
        }
    }

    loadBrowserNotificationSettings() {
        try {
            const saved = localStorage.getItem('forumBrowserNotificationSettings');
            if (saved) {
                this.browserNotificationSettings = {
                    ...this.browserNotificationSettings,
                    ...JSON.parse(saved)
                };
            }
        } catch (error) {
            console.error('Error loading browser notification settings:', error);
        }
    }

    saveBrowserNotificationSettings() {
        try {
            localStorage.setItem('forumBrowserNotificationSettings', 
                JSON.stringify(this.browserNotificationSettings));
        } catch (error) {
            console.error('Error saving browser notification settings:', error);
        }
    }



    // ================== PERMISSION MANAGEMENT ==================

    async requestNotificationPermission() {
        if (!('Notification' in window)) {
            this.showError('Browser notifications are not supported');
            return 'unsupported';
        }

        if (Notification.permission === 'granted') {
            this.updateNotificationPermission();
            return 'granted';
        }

        if (Notification.permission === 'denied') {
            this.showError('Notifications are blocked. Please enable them in your browser settings.');
            return 'denied';
        }

        // Show user-friendly prompt before asking for permission
        const userWants = await this.showNotificationPermissionPrompt();
        if (!userWants) return 'declined';

        try {
            const permission = await Notification.requestPermission();
            this.updateNotificationPermission();
            
            if (permission === 'granted') {
                this.browserNotificationSettings.enabled = true;
                this.saveBrowserNotificationSettings();
                this.showWelcomeNotification();
                this.updatePermissionStatusUI();
                this.showToast('✅ Browser notifications enabled!');
            } else {
                this.showError('❌ Permission denied for notifications');
            }
            
            return permission;
        } catch (error) {
            console.error('Error requesting notification permission:', error);
            return 'error';
        }
    }

    showNotificationPermissionPrompt() {
        return new Promise(resolve => {
            // Check if user has been asked recently
            const lastPrompt = localStorage.getItem('notificationPromptTime');
            const now = Date.now();
            
            if (lastPrompt && (now - parseInt(lastPrompt)) < 24 * 60 * 60 * 1000) {
                resolve(false);
                return;
            }

            // Create permission prompt modal
            const modal = document.createElement('div');
            modal.className = 'modal active';
            modal.style.zIndex = '10000';
            modal.innerHTML = `
                <div class="modal-content" style="max-width: 500px;">
                    <div class="modal-header">
                        <h3><i class="fas fa-bell"></i> Enable Notifications</h3>
                    </div>
                    <div style="padding: 1rem 0;">
                        <p style="margin-bottom: 1rem; color: var(--text-primary);">
                            Get instant notifications for forum activity even when you're not on the page:
                        </p>
                        <ul style="margin: 1rem 0; padding-left: 1.5rem; color: var(--text-secondary);">
                            <li style="margin-bottom: 0.5rem;">
                                <i class="fas fa-at" style="color: #3b82f6; margin-right: 0.5rem;"></i>
                                When someone mentions you
                            </li>
                            <li style="margin-bottom: 0.5rem;">
                                <i class="fas fa-reply" style="color: #10b981; margin-right: 0.5rem;"></i>
                                Replies to your messages
                            </li>
                            <li style="margin-bottom: 0.5rem;">
                                <i class="fas fa-comments" style="color: #f59e0b; margin-right: 0.5rem;"></i>
                                New messages in followed threads
                            </li>
                            <li>
                                <i class="fas fa-bullhorn" style="color: #ef4444; margin-right: 0.5rem;"></i>
                                @everyone announcements
                            </li>
                        </ul>
                        <p style="font-size: 0.9rem; color: var(--text-secondary); font-style: italic;">
                            You can customize or disable these notifications anytime in forum settings.
                        </p>
                    </div>
                    <div class="modal-actions">
                        <button class="btn-secondary" id="notificationDecline">Not Now</button>
                        <button class="btn-primary" id="notificationAllow">
                            <i class="fas fa-bell"></i> Enable Notifications
                        </button>
                    </div>
                </div>
            `;

            document.body.appendChild(modal);

            document.getElementById('notificationAllow').onclick = () => {
                localStorage.setItem('notificationPromptTime', now.toString());
                document.body.removeChild(modal);
                resolve(true);
            };

            document.getElementById('notificationDecline').onclick = () => {
                localStorage.setItem('notificationPromptTime', now.toString());
                document.body.removeChild(modal);
                resolve(false);
            };

            // Close on backdrop click
            modal.onclick = (e) => {
                if (e.target === modal) {
                    document.body.removeChild(modal);
                    resolve(false);
                }
            };
        });
    }

    showWelcomeNotification() {
        this.showBrowserNotification({
            title: '🔔 Forum Notifications Enabled',
            body: "You'll now receive notifications for mentions, replies, and updates!",
            icon: '/static/images/forum-icon.png',
            tag: 'welcome-notification',
            silent: false
        });
    }



 // ================== BROWSER NOTIFICATION CREATION ==================

    canShowBrowserNotifications() {
        return (
            'Notification' in window &&
            Notification.permission === 'granted' &&
            this.browserNotificationSettings.enabled &&
            document.hidden // Only show when page is not visible
        );
    }

    showBrowserNotification(options) {
        if (!this.canShowBrowserNotifications()) {
            return null;
        }

        // Limit concurrent notifications
        if (this.activeNotifications.size >= this.browserNotificationSettings.maxConcurrent) {
            return null;
        }

        const defaultOptions = {
            icon: '/static/images/forum-icon.png',
            badge: '/static/images/forum-badge.png',
            tag: `forum-notification-${Date.now()}`,
            requireInteraction: false,
            silent: !this.browserNotificationSettings.soundEnabled,
            vibrate: [200, 100, 200]
        };

        const notificationOptions = { ...defaultOptions, ...options };

        try {
            const notification = new Notification(options.title, notificationOptions);
            
            // Track notification
            this.activeNotifications.add(notification);
            
            // Auto-close
            if (this.browserNotificationSettings.autoClose > 0) {
                setTimeout(() => {
                    notification.close();
                }, this.browserNotificationSettings.autoClose);
            }

            // Handle events
            notification.onclick = () => {
                window.focus();
                notification.close();
                
                if (options.clickHandler) {
                    options.clickHandler();
                } else if (options.threadId) {
                    this.navigateToThread(options.threadId, options.messageId);
                }
            };

            notification.onclose = () => {
                this.activeNotifications.delete(notification);
            };

            notification.onerror = () => {
                this.activeNotifications.delete(notification);
                console.error('Browser notification error');
            };

            return notification;

        } catch (error) {
            console.error('Error showing browser notification:', error);
            return null;
        }
    }




    navigateToThread(threadId, messageId) {
        if (!threadId) return;
        
        let url = `/api/forum#thread-${threadId}`;
        if (messageId) {
            url += `&message=${messageId}`;
        }
        
        // If already on forum page, navigate internally
        if (window.location.pathname.includes('/forum')) {
            this.viewThread(threadId);
            if (messageId) {
                setTimeout(() => {
                    const messageEl = document.querySelector(`[data-message-id="${messageId}"]`);
                    if (messageEl) {
                        messageEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        messageEl.style.background = 'rgba(59, 130, 246, 0.1)';
                        setTimeout(() => messageEl.style.background = '', 2000);
                    }
                }, 500);
            }
        } else {
            window.location.href = url;
        }
    }

    truncateText(text, maxLength) {
        if (!text) return '';
        if (text.length <= maxLength) return text;
        return text.substring(0, maxLength - 3) + '...';
    }


    // ================== NOTIFICATION HANDLERS ==================

    handleForumNotification(notification) {
        // Call parent handler first
        if (super.handleForumNotification) {
            super.handleForumNotification(notification);
        }

        // Show browser notification if enabled
        if (!this.browserNotificationSettings.forumNotifications) return;

        const notificationType = notification.notification_data?.forum_type || notification.type;
        
        // Check if this type is enabled
        if (notificationType === 'mention' && !this.browserNotificationSettings.mentionNotifications) return;
        if (notificationType === 'reply' && !this.browserNotificationSettings.replyNotifications) return;
        if (!['mention', 'reply'].includes(notificationType) && !this.browserNotificationSettings.generalNotifications) return;

        this.showForumBrowserNotification(notification);
    }

    showForumBrowserNotification(notification) {
        const threadTitle = notification.notification_data?.thread_title || 'Forum Thread';
        const senderUsername = notification.notification_data?.sender_username || 'Someone';
        const messagePreview = this.truncateText(notification.content, 100);
        
        let title, body, icon;
        
        switch (notification.notification_data?.forum_type) {
            case 'mention':
                title = `💬 ${senderUsername} mentioned you`;
                body = `In "${threadTitle}": ${messagePreview}`;
                break;
            case 'reply':
                title = `↩️ ${senderUsername} replied`;
                body = `In "${threadTitle}": ${messagePreview}`;
                break;
            case 'new_message':
                title = `📝 New message in ${threadTitle}`;
                body = `${senderUsername}: ${messagePreview}`;
                break;
            default:
                title = `🔔 Forum Notification`;
                body = messagePreview;
        }

        this.showBrowserNotification({
            title,
            body,
            icon: '/static/images/forum-icon.png',
            tag: `forum-${notification.notification_data?.thread_id || 'general'}`,
            threadId: notification.notification_data?.thread_id,
            messageId: notification.notification_data?.message_id,
            clickHandler: () => {
                this.navigateToThread(
                    notification.notification_data?.thread_id,
                    notification.notification_data?.message_id
                );
            }
        });
    }




    // ================== SETTINGS INTEGRATION ==================

    async navigateToSettings() {
        this.currentView = 'settings';
        this.showLoading();
        
        try {
            await this.loadForumSettings(); // This now loads everything
            this.renderSettingsView();
            
            // Load analytics summary for creators
            if (this.currentUser.is_creator) {
                await this.loadAnalyticsSummary();
            }
        } catch (error) {
            console.error('Error loading settings:', error);
            this.showError('Failed to load settings');
            this.backToThreads();
        }
    }

    async loadForumSettings() {
        // Load regular settings (existing)
        const response = await fetch('/api/forum/settings');
        if (!response.ok) throw new Error('Failed to load settings');
        this.forumSettings = await response.json();
        
        // 🆕 NEW: Load @everyone permissions and settings
        await this.loadEveryoneSettings();
        
        // 🆕 NEW: Load moderation settings if user is creator
        if (this.currentUser.is_creator) {
            await this.loadModerationSettings();
        }
    }
    async loadEveryoneSettings() {
        try {
            const response = await fetch('/api/forum/settings/everyone-mentions');
            if (response.ok) {
                const data = await response.json();
                this.canUseEveryone = data.can_use_everyone;
                this.allowEveryoneMentions = data.allow_everyone_mentions;
            }
        } catch (error) {
            console.error('Error loading @everyone settings:', error);
        }
    }

    async loadModerationSettings() {
        try {
            const response = await fetch('/api/forum/moderation/everyone/settings');
            if (response.ok) {
                this.moderationSettings = await response.json();
            }
        } catch (error) {
            console.error('Error loading moderation settings:', error);
        }
    }
    renderSettingsView() {
        const canCreateThreads = this.currentUser.is_creator;
        
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
                
                <!-- 🆕 NEW: @everyone Settings Section -->
                ${this.renderEveryoneSettingsSection()}
                
                <!-- 🆕 NEW: Creator Moderation Panel (only for creators) -->
                ${this.currentUser.is_creator ? this.renderCreatorModerationPanel() : ''}
                
                <!-- Existing Display Settings -->
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


    renderBrowserNotificationSettings() {
        const permissionStatus = this.getPermissionStatusInfo();
        
        return `
            <div class="settings-section" id="browserNotificationSection">
                <h3><i class="fas fa-desktop"></i> Browser Notifications</h3>
                <p style="color: var(--text-secondary); margin-bottom: 1rem;">
                    Get notifications on your device even when the forum page is not open.
                </p>
                
                <!-- Permission Status -->
                <div class="permission-status ${permissionStatus.class}" id="permissionStatus">
                    <i class="fas fa-${permissionStatus.icon}"></i>
                    <span>${permissionStatus.text}</span>
                    ${permissionStatus.action ? `<button class="btn-primary-small" onclick="forum.requestNotificationPermission()" style="margin-left: auto;">${permissionStatus.action}</button>` : ''}
                </div>
                
                <!-- Browser Notification Controls -->
                <div id="browserNotificationControls" style="display: ${this.notificationPermission === 'granted' ? 'block' : 'none'};">
                    <div class="setting-item">
                        <label class="setting-label">Enable Browser Notifications</label>
                        <div class="setting-description">
                            Show notifications outside the browser when you receive forum updates.
                        </div>
                        <div class="setting-toggle">
                            <div class="toggle-switch ${this.browserNotificationSettings.enabled ? 'active' : ''}" 
                                 onclick="forum.toggleBrowserNotifications(this)">
                            </div>
                            <span class="toggle-label">
                                ${this.browserNotificationSettings.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                        </div>
                    </div>
                    
                    <div id="browserNotificationTypes" style="display: ${this.browserNotificationSettings.enabled ? 'block' : 'none'};">
                        <div class="setting-item">
                            <label class="setting-label">Notification Types</label>
                            <div class="setting-description">Choose which types of forum activity trigger browser notifications.</div>
                            
                            <div style="margin-bottom: 0.75rem;">
                                <div class="setting-toggle">
                                    <div class="toggle-switch ${this.browserNotificationSettings.mentionNotifications ? 'active' : ''}" 
                                         onclick="forum.toggleNotificationType('mentionNotifications', this)">
                                    </div>
                                    <span class="toggle-label">When someone mentions me (@username)</span>
                                </div>
                            </div>
                            
                            <div style="margin-bottom: 0.75rem;">
                                <div class="setting-toggle">
                                    <div class="toggle-switch ${this.browserNotificationSettings.replyNotifications ? 'active' : ''}" 
                                         onclick="forum.toggleNotificationType('replyNotifications', this)">
                                    </div>
                                    <span class="toggle-label">When someone replies to my messages</span>
                                </div>
                            </div>
                            
                            <div style="margin-bottom: 0.75rem;">
                                <div class="setting-toggle">
                                    <div class="toggle-switch ${this.browserNotificationSettings.forumNotifications ? 'active' : ''}" 
                                         onclick="forum.toggleNotificationType('forumNotifications', this)">
                                    </div>
                                    <span class="toggle-label">New messages in followed threads</span>
                                </div>
                            </div>
                            
                            <div>
                                <div class="setting-toggle">
                                    <div class="toggle-switch ${this.browserNotificationSettings.generalNotifications ? 'active' : ''}" 
                                         onclick="forum.toggleNotificationType('generalNotifications', this)">
                                    </div>
                                    <span class="toggle-label">General forum notifications</span>
                                </div>
                            </div>
                        </div>
                        
                        <div class="setting-item">
                            <label class="setting-label">Notification Sound</label>
                            <div class="setting-description">
                                Play a sound with browser notifications.
                            </div>
                            <div class="setting-toggle">
                                <div class="toggle-switch ${this.browserNotificationSettings.soundEnabled ? 'active' : ''}" 
                                     onclick="forum.toggleNotificationType('soundEnabled', this)">
                                </div>
                                <span class="toggle-label">Enable notification sound</span>
                            </div>
                        </div>
                        
                        <div class="setting-item">
                            <label class="setting-label">Auto-Close Timer</label>
                            <div class="setting-description">
                                How long notifications stay visible before automatically closing.
                            </div>
                            <div class="range-container">
                                <input type="range" id="autoCloseRange" class="range-input" min="3" max="30" 
                                       value="${this.browserNotificationSettings.autoClose / 1000}" 
                                       oninput="forum.updateBrowserNotificationTimer(this.value)">
                                <span class="range-value">
                                    <span id="autoCloseValue">${this.browserNotificationSettings.autoClose / 1000}</span> seconds
                                </span>
                            </div>
                        </div>
                        
                        <!-- Test Notification -->
                        <div class="setting-item">
                            <label class="setting-label">Test Notification</label>
                            <div class="setting-description">
                                See how your browser notifications will look.
                            </div>
                            <button type="button" class="btn-secondary" onclick="forum.showTestBrowserNotification()">
                                <i class="fas fa-test-tube"></i> Send Test Notification
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }




    renderEveryoneSettingsSection() {
        return `
            <div class="everyone-settings-section">
                <h3>
                    <i class="fas fa-bullhorn"></i>
                    @everyone Mentions
                </h3>
                
                <div class="everyone-permission-indicator ${this.canUseEveryone ? 'allowed' : 'denied'}">
                    <i class="fas fa-${this.canUseEveryone ? 'check-circle' : 'times-circle'}"></i>
                    ${this.canUseEveryone ? 
                        'You can use @everyone to notify all forum users' : 
                        'Only creators and team members can use @everyone'
                    }
                </div>
                
                <div class="setting-item">
                    <label class="setting-label">Receive @everyone Notifications</label>
                    <div class="setting-description">
                        Get notified when someone uses @everyone in any thread. You can disable this if you find these notifications too frequent.
                    </div>
                    <div class="setting-toggle">
                        <div class="toggle-switch ${this.allowEveryoneMentions ? 'active' : ''}" 
                             onclick="forum.toggleEveryoneNotifications(this)">
                        </div>
                        <span class="toggle-label">
                            ${this.allowEveryoneMentions ? 'Enabled' : 'Disabled'}
                        </span>
                    </div>
                </div>
                
                ${this.canUseEveryone ? `
                    <div class="setting-item">
                        <label class="setting-label">@everyone Usage Guidelines</label>
                        <div class="setting-description">
                            <ul style="margin: 0; padding-left: 1.5rem; color: var(--text-secondary); font-size: 0.9rem;">
                                <li>Use @everyone sparingly for important announcements</li>
                                <li>Consider the time zone of your audience</li>
                                <li>Limit usage: Team members have rate limits</li>
                                <li>Creators have unlimited usage but should use responsibly</li>
                            </ul>
                        </div>
                    </div>
                ` : ''}
            </div>
        `;
    }

    // 🆕 NEW: Render creator moderation panel
    renderCreatorModerationPanel() {
        return `
            <div class="settings-section creator-moderation-panel">
                <h3>
                    <i class="fas fa-shield-alt"></i>
                    @everyone Moderation Controls
                    <span class="creator-only-badge">Creator Only</span>
                </h3>
                
                <!-- Emergency Controls -->
                <div class="moderation-emergency-controls">
                    <div class="emergency-status" id="emergencyStatus">
                        <div class="status-indicator" id="everyoneStatusIndicator">
                            <span class="status-dot ${this.moderationSettings.everyone_globally_disabled ? 'disabled' : 'active'}"></span>
                            <span class="status-text">
                                ${this.moderationSettings.everyone_globally_disabled 
                                    ? `@everyone is disabled${this.moderationSettings.emergency_disable_reason ? ': ' + this.moderationSettings.emergency_disable_reason : ''}` 
                                    : '@everyone is enabled'}
                            </span>
                        </div>
                        <button class="emergency-button ${this.moderationSettings.everyone_globally_disabled ? 'enable' : ''}" 
                                id="emergencyToggleBtn" onclick="forum.toggleEmergencyDisable()">
                            <i class="fas fa-${this.moderationSettings.everyone_globally_disabled ? 'check' : 'ban'}"></i>
                            ${this.moderationSettings.everyone_globally_disabled ? 'Re-enable' : 'Emergency Disable'}
                        </button>
                    </div>
                </div>
                
                <!-- Rate Limiting Controls -->
                <div class="moderation-section">
                    <h4><i class="fas fa-clock"></i> Rate Limiting</h4>
                    
                    <div class="control-group">
                        <label class="control-label">Team Member Limit</label>
                        <div class="control-input-group">
                            <input type="number" id="teamRateLimit" min="0" max="50" 
                                   value="${this.moderationSettings.team_rate_limit || 3}" class="control-input">
                            <span class="control-unit">uses per</span>
                            <input type="number" id="rateLimitWindow" min="1" max="168" 
                                   value="${this.moderationSettings.rate_limit_window_hours || 24}" class="control-input">
                            <span class="control-unit">hours</span>
                        </div>
                        <div class="control-description">How many times team members can use @everyone</div>
                    </div>
                    
                    <div class="control-group">
                        <label class="control-label">Global Cooldown</label>
                        <div class="control-input-group">
                            <input type="number" id="globalCooldown" min="0" max="1440" 
                                   value="${this.moderationSettings.global_cooldown_minutes || 0}" class="control-input">
                            <span class="control-unit">minutes between any @everyone use</span>
                        </div>
                        <div class="control-description">Minimum time between ANY @everyone mentions (0 = no cooldown)</div>
                    </div>
                </div>
                
                <!-- Content Controls -->
                <div class="moderation-section">
                    <h4><i class="fas fa-edit"></i> Content Controls</h4>
                    
                    <div class="control-group">
                        <label class="control-label">Maximum Message Length</label>
                        <div class="control-input-group">
                            <input type="number" id="maxMessageLength" min="0" max="5000" 
                                   value="${this.moderationSettings.max_message_length || ''}" 
                                   placeholder="No limit" class="control-input">
                            <span class="control-unit">characters</span>
                        </div>
                        <div class="control-description">Maximum length for messages containing @everyone</div>
                    </div>
                    
                    <div class="control-group">
                        <label class="control-label">Notification Limit</label>
                        <div class="control-input-group">
                            <input type="number" id="notificationLimit" min="0" max="10000" 
                                   value="${this.moderationSettings.notification_limit || ''}" 
                                   placeholder="No limit" class="control-input">
                            <span class="control-unit">users maximum</span>
                        </div>
                        <div class="control-description">Maximum number of users to notify per @everyone</div>
                    </div>
                </div>
                
                <!-- Analytics Summary -->
                <div class="moderation-section">
                    <h4><i class="fas fa-chart-bar"></i> Quick Analytics</h4>
                    <div class="analytics-summary" id="analyticsSummary">
                        <div class="stat-card">
                            <div class="stat-number" id="totalUses">-</div>
                            <div class="stat-label">@everyone Uses (7d)</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-number" id="totalNotifications">-</div>
                            <div class="stat-label">Notifications Sent</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-number" id="uniqueUsers">-</div>
                            <div class="stat-label">Unique Users</div>
                        </div>
                    </div>
                    <button onclick="forum.showFullAnalytics()" class="btn-secondary" style="margin-top: 1rem;">
                        <i class="fas fa-chart-line"></i> View Full Analytics
                    </button>
                </div>
            </div>
        `;
    }

    getPermissionStatusInfo() {
        if (!('Notification' in window)) {
            return {
                class: 'denied',
                icon: 'times-circle',
                text: 'Browser notifications are not supported in this browser',
                action: null
            };
        }

        switch (this.notificationPermission) {
            case 'granted':
                return {
                    class: 'granted',
                    icon: 'check-circle',
                    text: 'Browser notifications are enabled',
                    action: null
                };
            case 'denied':
                return {
                    class: 'denied',
                    icon: 'times-circle',
                    text: 'Browser notifications are blocked. Please enable them in your browser settings.',
                    action: null
                };
            default:
                return {
                    class: 'default',
                    icon: 'exclamation-triangle',
                    text: 'Click "Enable Notifications" to receive browser notifications',
                    action: 'Enable Notifications'
                };
        }
    }

    toggleBrowserNotifications(toggleElement) {
        const isCurrentlyEnabled = toggleElement.classList.contains('active');
        const newState = !isCurrentlyEnabled;
        
        // Update UI
        toggleElement.classList.toggle('active', newState);
        const label = toggleElement.nextElementSibling;
        label.textContent = newState ? 'Enabled' : 'Disabled';
        
        // Update settings
        this.browserNotificationSettings.enabled = newState;
        this.saveBrowserNotificationSettings();
        
        // Show/hide notification types
        const typesSection = document.getElementById('browserNotificationTypes');
        if (typesSection) {
            typesSection.style.display = newState ? 'block' : 'none';
        }
        
        if (newState) {
            this.showToast('✅ Browser notifications enabled');
        } else {
            this.showToast('❌ Browser notifications disabled');
        }
    }

    toggleNotificationType(settingName, toggleElement) {
        const isCurrentlyEnabled = toggleElement.classList.contains('active');
        const newState = !isCurrentlyEnabled;
        
        // Update UI
        toggleElement.classList.toggle('active', newState);
        
        // Update settings
        this.browserNotificationSettings[settingName] = newState;
        this.saveBrowserNotificationSettings();
        
    }

    updateBrowserNotificationTimer(seconds) {
        const valueElement = document.getElementById('autoCloseValue');
        if (valueElement) {
            valueElement.textContent = seconds;
        }
        
        this.browserNotificationSettings.autoClose = parseInt(seconds) * 1000;
        this.saveBrowserNotificationSettings();
    }

    updatePermissionStatusUI() {
        const statusElement = document.getElementById('permissionStatus');
        const controlsElement = document.getElementById('browserNotificationControls');
        
        if (!statusElement || !controlsElement) return;
        
        const permissionInfo = this.getPermissionStatusInfo();
        
        statusElement.className = `permission-status ${permissionInfo.class}`;
        statusElement.innerHTML = `
            <i class="fas fa-${permissionInfo.icon}"></i>
            <span>${permissionInfo.text}</span>
            ${permissionInfo.action ? `<button class="btn-primary-small" onclick="forum.requestNotificationPermission()" style="margin-left: auto;">${permissionInfo.action}</button>` : ''}
        `;
        
        controlsElement.style.display = this.notificationPermission === 'granted' ? 'block' : 'none';
    }

    showTestBrowserNotification() {
        if (!this.canShowBrowserNotifications()) {
            if (this.notificationPermission !== 'granted') {
                this.showError('Please enable browser notifications first');
                return;
            }
            if (this.browserNotificationSettings.enabled === false) {
                this.showError('Please enable browser notifications in the settings above');
                return;
            }
            if (!document.hidden) {
                this.showToast('Browser notifications only show when the page is not visible. Try switching tabs and then clicking this button.');
                return;
            }
        }

        this.showBrowserNotification({
            title: '🧪 Test Forum Notification',
            body: `This is how your forum notifications will look! Sent at ${new Date().toLocaleTimeString()}`,
            icon: '/static/images/forum-icon.png',
            tag: 'test-notification',
            clickHandler: () => {
                this.showToast('✅ Test notification clicked!');
            }
        });

        this.showToast('📱 Test notification sent! (Only visible when page is hidden)');
    }

    // 🆕 NEW: Enhanced save method that handles all settings
    async saveAllSettings() {
        const saveButton = document.getElementById('saveButton');
        if (!saveButton) return;
        
        saveButton.disabled = true;
        saveButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
        
        try {
            // Save regular forum settings (existing logic)
            await this.saveForumSettings();
            
            // 🆕 NEW: Save @everyone settings
            await this.saveEveryoneSettings();
            
            // 🆕 NEW: Save moderation settings if creator
            if (this.currentUser.is_creator) {
                await this.saveModerationSettings();
            }
            
            this.showSuccessMessage();
            this.showToast('All settings saved successfully!');
            
        } catch (error) {
            console.error('Error saving settings:', error);
            this.showError(error.message);
        } finally {
            saveButton.disabled = false;
            saveButton.innerHTML = '<i class="fas fa-save"></i> Save All Settings';
        }
    }

    // 🆕 NEW: Save @everyone settings
    async saveEveryoneSettings() {
        const response = await fetch('/api/forum/settings/everyone-mentions', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                allow_everyone_mentions: this.allowEveryoneMentions 
            })
        });
        
        if (!response.ok) {
            throw new Error('Failed to save @everyone settings');
        }
    }

    // 🆕 NEW: Save moderation settings
    async saveModerationSettings() {
        const settings = {
            team_rate_limit: parseInt(document.getElementById('teamRateLimit')?.value) || 3,
            rate_limit_window_hours: parseInt(document.getElementById('rateLimitWindow')?.value) || 24,
            global_cooldown_minutes: parseInt(document.getElementById('globalCooldown')?.value) || 0,
            max_message_length: parseInt(document.getElementById('maxMessageLength')?.value) || null,
            notification_limit: parseInt(document.getElementById('notificationLimit')?.value) || null
        };
        
        const response = await fetch('/api/forum/moderation/everyone/settings', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        if (!response.ok) {
            throw new Error('Failed to save moderation settings');
        }
        
        this.moderationSettings = { ...this.moderationSettings, ...settings };
    }

    // 🆕 NEW: Toggle @everyone notifications
    async toggleEveryoneNotifications(toggleElement) {
        const isCurrentlyEnabled = toggleElement.classList.contains('active');
        const newState = !isCurrentlyEnabled;
        
        // Update UI optimistically
        toggleElement.classList.toggle('active', newState);
        const label = toggleElement.nextElementSibling;
        label.textContent = newState ? 'Enabled' : 'Disabled';
        
        // Update local state
        this.allowEveryoneMentions = newState;
        
        try {
            await this.saveEveryoneSettings();
        } catch (error) {
            // Revert UI on failure
            toggleElement.classList.toggle('active', isCurrentlyEnabled);
            label.textContent = isCurrentlyEnabled ? 'Enabled' : 'Disabled';
            this.allowEveryoneMentions = isCurrentlyEnabled;
            this.showError('Failed to update @everyone settings');
        }
    }

    // 🆕 NEW: Emergency disable/enable @everyone
    async toggleEmergencyDisable() {
        const isCurrentlyDisabled = this.moderationSettings.everyone_globally_disabled;
        
        if (isCurrentlyDisabled) {
            // Re-enable
            if (!confirm('Are you sure you want to re-enable @everyone mentions?')) return;
            
            try {
                const response = await fetch('/api/forum/moderation/everyone/emergency-enable', {
                    method: 'POST'
                });
                
                if (response.ok) {
                    this.moderationSettings.everyone_globally_disabled = false;
                    this.moderationSettings.emergency_disable_reason = null;
                    this.updateEmergencyStatusUI();
                    this.showToast('✅ @everyone has been re-enabled');
                } else {
                    throw new Error('Failed to re-enable @everyone');
                }
            } catch (error) {
                this.showError('❌ Failed to re-enable @everyone');
            }
        } else {
            // Disable
            const reason = prompt('Enter reason for emergency disable (optional):');
            if (reason === null) return; // User cancelled
            
            try {
                const response = await fetch('/api/forum/moderation/everyone/emergency-disable', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason: reason || 'Emergency disable by creator' })
                });
                
                if (response.ok) {
                    this.moderationSettings.everyone_globally_disabled = true;
                    this.moderationSettings.emergency_disable_reason = reason;
                    this.updateEmergencyStatusUI();
                    this.showToast('⚠️ @everyone has been disabled');
                } else {
                    throw new Error('Failed to disable @everyone');
                }
            } catch (error) {
                this.showError('❌ Failed to disable @everyone');
            }
        }
    }

    // 🆕 NEW: Update emergency status UI
    updateEmergencyStatusUI() {
        const indicator = document.getElementById('everyoneStatusIndicator');
        const button = document.getElementById('emergencyToggleBtn');
        
        if (!indicator || !button) return;
        
        const statusDot = indicator.querySelector('.status-dot');
        const statusText = indicator.querySelector('.status-text');
        const isDisabled = this.moderationSettings.everyone_globally_disabled;
        const reason = this.moderationSettings.emergency_disable_reason;
        
        if (isDisabled) {
            statusDot.classList.add('disabled');
            statusDot.classList.remove('active');
            statusText.textContent = `@everyone is disabled${reason ? ': ' + reason : ''}`;
            button.innerHTML = '<i class="fas fa-check"></i> Re-enable';
            button.classList.add('enable');
        } else {
            statusDot.classList.remove('disabled');
            statusDot.classList.add('active');
            statusText.textContent = '@everyone is enabled';
            button.innerHTML = '<i class="fas fa-ban"></i> Emergency Disable';
            button.classList.remove('enable');
        }
    }

    // 🆕 NEW: Load and show analytics summary
    async loadAnalyticsSummary() {
        if (!this.currentUser.is_creator) return;
        
        try {
            const response = await fetch('/api/forum/moderation/everyone/analytics?days=7');
            if (response.ok) {
                const data = await response.json();
                
                document.getElementById('totalUses').textContent = data.summary.total_uses;
                document.getElementById('totalNotifications').textContent = data.summary.total_notifications.toLocaleString();
                document.getElementById('uniqueUsers').textContent = data.summary.unique_users;
            }
        } catch (error) {
            console.error('Error loading analytics summary:', error);
        }
    }

    // 🆕 NEW: Show full analytics (placeholder for future modal)
    showFullAnalytics() {
        this.showToast('Full analytics dashboard coming soon!');
        // TODO: Implement full analytics modal
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

    // ================== THREAD MANAGEMENT ==================

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
            
            // 🚀 IMPORTANT: Don't add manually since it will come via WebSocket
            // this.threads.unshift(newThread);
            
            // Show success message
            if (window.showToast) window.showToast('Thread created successfully!');
            
            // The thread will be added via WebSocket broadcast automatically
            // Just make sure we're ready to receive it
            
            return true;
        } catch (error) {
            this.showError(error.message);
            return false;
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

    // Handle websocket thread updates
    handleThreadUpdate(data) {
        if (this.currentThread && this.currentThread.id === data.thread_id) {
            Object.assign(this.currentThread, data.updates);
            this.renderDiscussionView();
        }
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
    // ❌ REMOVED: handleCreateThread method - now handled by forum-core.js
}

// Final export - this is the complete forum class
window.EnhancedForumSPA = EnhancedForumSPA;

// ================== INITIALIZATION ==================

let forum;
document.addEventListener('DOMContentLoaded', () => {
    forum = new EnhancedForumSPA();
    
    // ✅ UPDATED: Form event listeners - removed newThreadForm listener
    // The handleCreateThread will be handled by forum-core.js
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