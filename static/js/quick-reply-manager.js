class QuickReplyManager {
    constructor() {
        this.notification = null;
        this.currentNotification = null;
        this.settings = null;
        this.dismissTimer = null;
        this.progressTimer = null;
        this.wsSocket = null;
        this.isWsConnected = false;
        this.wsReconnectAttempts = 0;
        this.enterKeyHandler = null;         
        this.init();
    }    
    
    async init() {
        this.notification = document.getElementById('quickReplyNotification');
        if (!this.notification) {
            // console.error('QuickReply: Notification element not found');
            return;
        }
        
        await this.loadSettings();
        this.setupDirectWebSocketConnection();
        this.setupKeyboardShortcuts();
        this.setupInputAutoResize();
        this.initEnterKeyHandling();
    }

    // 🔥 NEW: Check if user is currently in forum
    isUserInForum() {
        // Check multiple indicators that user is in forum
        const inForumPath = window.location.pathname.includes('/forum') || 
                           window.location.pathname.includes('/api/forum');
        
        const forumSPAActive = document.querySelector('#forumSPA, .forum-spa-container') !== null;
        
        const forumInstanceActive = window.forum && 
                                   window.forum.currentView && 
                                   window.forum.currentView !== 'settings';
        
        const forumBodyMarker = document.body.dataset.forumActive === 'true';
        
        // Check if enhanced forum notification manager indicates we're in forum
        const notificationManagerInForum = window.enhancedForumNotificationManager?.isInForum;
        
        const isInForum = inForumPath || forumSPAActive || forumInstanceActive || forumBodyMarker || notificationManagerInForum;

        // console.log('🔔 QuickReply: Forum state check:', {
        //     inForumPath,
        //     forumSPAActive,
        //     forumInstanceActive,
        //     forumBodyMarker,
        //     notificationManagerInForum,
        //     finalResult: isInForum
        // });

        return isInForum;
    }

    // 🔥 NEW: Check if user is viewing the specific thread the notification is about
    isViewingNotificationThread(notification) {
        if (!notification.thread_id) return false;
        
        // Check if forum instance has current thread
        if (window.forum?.currentThread?.id) {
            return window.forum.currentThread.id === notification.thread_id;
        }
        
        // Check if notification manager knows current thread
        if (window.enhancedForumNotificationManager?.currentThreadId) {
            return window.enhancedForumNotificationManager.currentThreadId === notification.thread_id;
        }
        
        // Check URL parameters
        const urlParams = new URLSearchParams(window.location.search);
        const urlThreadId = urlParams.get('thread');
        if (urlThreadId) {
            return parseInt(urlThreadId) === notification.thread_id;
        }
        
        // Check hash
        const hashMatch = window.location.hash.match(/thread-(\d+)/);
        if (hashMatch) {
            return parseInt(hashMatch[1]) === notification.thread_id;
        }
        
        return false;
    }
    
    processForumNotification(data) {
        if (!data.notification || !this.settings?.enable_quick_reply_notifications) {
            return;
        }

        // 🔥 ENHANCED: Check if user is in forum before showing quick reply
        if (this.isUserInForum()) {
            // console.log('🔔 QuickReply: User is in forum, skipping quick reply notification');
            return;
        }
        
        const notification = data.notification;
        const forumData = notification.notification_data || {};
        
        // console.log('🔔 QuickReply: Processing notification:', notification);
        // console.log('🔔 QuickReply: Forum data:', forumData);
        
        // Determine forum notification type
        let forumType = 'new_message';
        if (notification.content.includes('replied to')) {
            forumType = 'reply';
        } else if (notification.content.includes('mentioned you')) {
            forumType = 'mention';
        }
        
        // Check if this notification type is enabled
        const shouldShow = (
            (forumType === 'mention' && this.settings.quick_reply_for_mentions) ||
            (forumType === 'reply' && this.settings.quick_reply_for_replies) ||
            (forumType === 'new_message' && this.settings.quick_reply_for_mentions)
        );
        
        if (!shouldShow) return;
        
        // Extract info directly - backend provides correct names
        let threadTitle = 'Forum Thread';
        let senderUsername = 'Unknown User';
        let senderId = null;
        
        if (notification.sender && notification.sender.username) {
            senderUsername = notification.sender.username;
            senderId = notification.sender.id;
        } else {
            const contentMatch = notification.content.match(/^(\w+)\s+(posted in|replied to|mentioned you)/);
            if (contentMatch) {
                senderUsername = contentMatch[1];
            }
            senderId = notification.sender_id;
        }
        
        // Get thread title from notification_data or parse from content
        if (forumData.thread_title) {
            threadTitle = forumData.thread_title;
        } else {
            const threadMatch = notification.content.match(/in\s+([^(]+)(?:\s+\([^)]+\))?$/);
            if (threadMatch) {
                threadTitle = threadMatch[1].trim();
            }
        }
        
        // Create enhanced forum notification object
        const forumNotification = {
            id: notification.id,
            thread_id: forumData.thread_id || null,
            thread_title: threadTitle,
            message_id: forumData.message_id || null,
            notification_type: forumType,
            title: notification.title ? notification.title.replace('[Forum] ', '') : 'Forum Notification',
            content: notification.content,
            sender_id: senderId,
            sender: {
                id: senderId,
                username: senderUsername,
                role: notification.sender?.role || 'member'
            },
            created_at: notification.created_at,
            is_read: notification.is_read || false,
            notification_data: forumData
        };
        
        // 🔥 ADDITIONAL CHECK: Don't show if user is viewing the specific thread
        if (this.isViewingNotificationThread(forumNotification)) {
            // console.log('🔔 QuickReply: User is viewing the thread this notification is about, skipping');
            return;
        }
        
        // console.log('🔔 QuickReply: Enhanced notification object:', forumNotification);
        
        this.showNotification(forumNotification);
        
        if (this.settings.enable_notification_sound) {
            this.playNotificationSound();
        }
    }

    initEnterKeyHandling() {
        // Remove any existing listener to avoid duplicates
        if (this.enterKeyHandler) {
            document.removeEventListener('keydown', this.enterKeyHandler);
        }
        
        this.enterKeyHandler = (e) => {
            if (e.key !== 'Enter') return;
            
            const target = e.target;
            
            // Quick reply input
            if (target.id === 'quickReplyInput') {
                if (e.shiftKey) return; // Shift+Enter = new line
                e.preventDefault(); // Enter = send reply
                
                if (target.value.trim()) {
                    this.sendReply();
                }
                return;
            }
        };
        
        document.addEventListener('keydown', this.enterKeyHandler);
        // console.log('✅ QuickReply Enter key handling initialized');
    }

    async loadSettings() {
        try {
            const response = await fetch('/api/forum/settings');
            if (response.ok) {
                this.settings = await response.json();
                this.applySettings();
            } else {
                this.useDefaultSettings();
            }
        } catch (error) {
            // console.warn('QuickReply: Failed to load settings, using defaults');
            this.useDefaultSettings();
        }
    }
    
    useDefaultSettings() {
        this.settings = {
            enable_quick_reply_notifications: true,
            quick_reply_for_mentions: true,
            quick_reply_for_replies: true,
            quick_reply_auto_dismiss_seconds: 8,
            notification_position: 'top-right',
            enable_notification_sound: false
        };
    }
    
    applySettings() {
        if (!this.notification || !this.settings) return;
        
        this.notification.classList.remove(
            'position-top-right', 'position-top-left', 
            'position-bottom-right', 'position-bottom-left'
        );
        
        this.notification.classList.add(`position-${this.settings.notification_position}`);
    }
    
    setupDirectWebSocketConnection() {
        if (!window.currentUserId || !('WebSocket' in window)) {
            return;
        }
        
        this.connectWebSocket();
    }
    
    connectWebSocket() {
        try {
            if (this.wsSocket) {
                this.wsSocket.close();
            }
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/api/notifications/ws?user_id=${window.currentUserId}`;
            
            this.wsSocket = new WebSocket(wsUrl);
            
            this.wsSocket.onopen = () => {
                this.isWsConnected = true;
                this.wsReconnectAttempts = 0;
                // console.log('QuickReply: WebSocket connected');
            };
            
            this.wsSocket.onmessage = (event) => {
                try {
                    if (typeof event.data === 'string' && !event.data.startsWith('{')) {
                        if (event.data === 'ping') this.wsSocket.send('pong');
                        return;
                    }
                    
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                    
                } catch (e) {
                    // console.error('QuickReply: WebSocket message error:', e);
                }
            };
            
            this.wsSocket.onclose = () => {
                this.isWsConnected = false;
                this.scheduleReconnect();
            };
            
            this.wsSocket.onerror = () => {
                this.isWsConnected = false;
                // console.error('QuickReply: WebSocket error');
            };
            
        } catch (e) {
            // console.error('QuickReply: WebSocket setup failed:', e);
            this.isWsConnected = false;
        }
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'new_notification':
                if (this.isForumNotification(data)) {
                    this.processForumNotification(data);
                }
                break;
        }
    }
    
    isForumNotification(data) {
        if (data.type === 'new_notification' && data.notification) {
            const notif = data.notification;
            
            const hasForumTitle = notif.title && notif.title.startsWith('[Forum]');
            const hasForumSource = notif.notification_data && 
                                  notif.notification_data.source === 'forum';
            const hasForumContent = notif.content && (
                notif.content.includes('posted in') || 
                notif.content.includes('replied to') ||
                notif.content.includes('mentioned you')
            );
            
            return hasForumTitle || hasForumSource || hasForumContent;
        }
        
        return false;
    }
    
    scheduleReconnect() {
        if (this.wsReconnectAttempts >= 5) return;
        
        this.wsReconnectAttempts++;
        const delay = Math.min(5000 * this.wsReconnectAttempts, 30000);
        
        setTimeout(() => {
            if (!this.isWsConnected) {
                this.connectWebSocket();
            }
        }, delay);
    }
    
    showNotification(notification) {
        if (!this.notification) return;
        
        this.dismissNotification();
        this.currentNotification = notification;
        this.populateNotification(notification);
        
        this.notification.classList.add('animating-in', 'show');
        
        setTimeout(() => {
            this.notification.classList.remove('animating-in');
        }, 350);
        
        this.setupAutoDismiss();
        
        // Focus input after animation
        setTimeout(() => {
            const input = document.getElementById('quickReplyInput');
            if (input) {
                input.focus();
                input.style.height = 'auto'; // Reset height
            }
        }, 400);
    }
    
    populateNotification(notification) {
        const avatar = this.notification.querySelector('.notification-avatar');
        const sender = this.notification.querySelector('.notification-sender');
        const type = this.notification.querySelector('.notification-type');
        const threadTitle = this.notification.querySelector('.notification-thread-title');
        const message = this.notification.querySelector('.notification-message');
        
        const senderData = notification.sender;
        
        // Set pulsing dot gradient (no text content for compact design)
        if (senderData && senderData.username) {
            avatar.style.background = this.getUserBadgeGradient(senderData);
            sender.textContent = senderData.username;
        } else {
            // Fallback for unknown user
            const contentMatch = notification.content.match(/^(\w+)\s+(posted in|replied to|mentioned you)/);
            if (contentMatch) {
                sender.textContent = contentMatch[1];
            } else {
                sender.textContent = 'Unknown User';
            }
            avatar.style.background = 'linear-gradient(135deg, #6b7280, #4b5563)';
        }
        
        // Set action type (compact text)
        const notificationType = notification.notification_type || notification.type;
        const typeText = notificationType === 'mention' ? 'mentioned you' : 
                        notificationType === 'reply' ? 'replied' : 
                        'posted';
        type.textContent = typeText;
        
        // Update thread title with new structure
        if (notification.thread_title) {
            const threadSpan = threadTitle.querySelector('span');
            if (threadSpan) {
                threadSpan.textContent = notification.thread_title;
            } else {
                threadTitle.innerHTML = `<i class="fas fa-comments"></i><span>${notification.thread_title}</span>`;
            }
        } else {
            threadTitle.innerHTML = `<i class="fas fa-comments"></i><span>Forum Discussion</span>`;
        }
        
        // ENHANCED: Show actual message content instead of just action
        let displayContent = '';
        
        // Try to get actual message content from notification_data
        if (notification.notification_data && notification.notification_data.message_content) {
            displayContent = notification.notification_data.message_content;
            // console.log('🔔 QuickReply: Using message content from notification_data:', displayContent.substring(0, 50) + '...');
        }
        // Try to get preview from notification_data
        else if (notification.notification_data && notification.notification_data.message_preview) {
            displayContent = notification.notification_data.message_preview;
            // console.log('🔔 QuickReply: Using message preview from notification_data:', displayContent.substring(0, 50) + '...');
        }
        // Try to extract from content if it includes quoted text
        else if (notification.content && notification.content.includes('"')) {
            const quotedMatch = notification.content.match(/"([^"]+)"/);
            if (quotedMatch) {
                displayContent = quotedMatch[1];
                // console.log('🔔 QuickReply: Extracted quoted content from notification:', displayContent.substring(0, 50) + '...');
            } else {
                displayContent = notification.content;
                // console.log('🔔 QuickReply: Using full notification content (no quotes found)');
            }
        }
        // Fallback to notification content
        else {
            displayContent = notification.content || 'New forum activity';
            // console.log('🔔 QuickReply: Using fallback notification content');
        }
        
        // Clean up the content - remove thread info suffix
        displayContent = displayContent.replace(/\s*\(in [^)]+\)\s*$/, '');
        
        // Truncate for compact display but preserve more text for actual messages
        const isActualMessage = notification.notification_data && 
                               (notification.notification_data.message_content || notification.notification_data.message_preview);
        const maxLength = isActualMessage ? 120 : 60;
        
        if (displayContent.length > maxLength) {
            displayContent = displayContent.substring(0, maxLength - 3) + '...';
        }
        
        message.textContent = displayContent;
        // console.log('🔔 QuickReply: Final display content:', displayContent);
        
        // Clear input and reset send button
        const input = document.getElementById('quickReplyInput');
        if (input) {
            input.value = '';
            input.style.height = 'auto';
        }
        
        // Ensure input wrapper structure exists
        this.ensureInputWrapperStructure();
        
        // Reset send button with new compact design
        const sendBtn = this.notification.querySelector('.quick-reply-send');
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '➤';
        }
    }
    
    // NEW: Helper method to ensure proper input wrapper structure
    ensureInputWrapperStructure() {
        const replyArea = this.notification.querySelector('.quick-reply-area');
        const input = document.getElementById('quickReplyInput');
        const sendBtn = this.notification.querySelector('.quick-reply-send');
        
        if (input && sendBtn && !input.parentElement.classList.contains('input-wrapper')) {
            // Create wrapper if it doesn't exist
            const wrapper = document.createElement('div');
            wrapper.className = 'input-wrapper';
            
            // Insert wrapper before input
            input.parentNode.insertBefore(wrapper, input);
            
            // Move input and send button into wrapper
            wrapper.appendChild(input);
            wrapper.appendChild(sendBtn);
            
            // console.log('QuickReply: Created input wrapper structure');
        }
    }
    
    // NEW: Get gradient for user badge based on role
    getUserBadgeGradient(user) {
        const role = user.role?.toLowerCase();
        
        switch (role) {
            case 'creator': 
                return 'linear-gradient(135deg, #f59e0b, #d97706)';
            case 'team': 
                return 'linear-gradient(135deg, #3b82f6, #2563eb)';
            case 'patron': 
                return 'linear-gradient(135deg, #f97316, #ea580c)';
            case 'supporter': 
                return 'linear-gradient(135deg, #10b981, #059669)';
            default: 
                return 'linear-gradient(135deg, #4a90e2, #357abd)';
        }
    }
    
    setupAutoDismiss() {
        if (!this.settings?.quick_reply_auto_dismiss_seconds) return;
        
        const dismissTime = this.settings.quick_reply_auto_dismiss_seconds * 1000;
        
        let progressBar = this.notification.querySelector('.notification-progress');
        if (!progressBar) {
            progressBar = document.createElement('div');
            progressBar.className = 'notification-progress';
            this.notification.appendChild(progressBar);
        }
        
        progressBar.style.width = '100%';
        progressBar.style.transition = `width ${dismissTime}ms linear`;
        
        setTimeout(() => {
            progressBar.style.width = '0%';
        }, 100);
        
        this.dismissTimer = setTimeout(() => {
            this.dismissNotification();
        }, dismissTime);
        
        // Pause on hover
        this.notification.addEventListener('mouseenter', () => {
            if (this.dismissTimer) {
                clearTimeout(this.dismissTimer);
                progressBar.style.animationPlayState = 'paused';
            }
        });
        
        this.notification.addEventListener('mouseleave', () => {
            // Resume with reduced time
            this.dismissTimer = setTimeout(() => {
                this.dismissNotification();
            }, 2000);
        });
    }
    
    dismissNotification() {
        if (!this.notification || !this.notification.classList.contains('show')) {
            return;
        }
        
        // Clean up Enter key handler when dismissing
        if (this.enterKeyHandler) {
            document.removeEventListener('keydown', this.enterKeyHandler);
            this.enterKeyHandler = null;
        }
        
        if (this.dismissTimer) {
            clearTimeout(this.dismissTimer);
            this.dismissTimer = null;
        }
        
        if (this.progressTimer) {
            clearTimeout(this.progressTimer);
            this.progressTimer = null;
        }
        
        this.notification.classList.add('animating-out');
        
        setTimeout(() => {
            this.notification.classList.remove('show', 'animating-out');
            
            const progressBar = this.notification.querySelector('.notification-progress');
            if (progressBar) {
                progressBar.remove();
            }
            
            this.currentNotification = null;
        }, 250);
    }    
    
    async sendReply() {
        if (!this.currentNotification) return;
        
        const input = document.getElementById('quickReplyInput');
        const sendBtn = this.notification.querySelector('.quick-reply-send');
        
        if (!input || !input.value.trim()) {
            input?.focus();
            return;
        }
        
        sendBtn.disabled = true;
        sendBtn.innerHTML = '⏳';
        
        try {
            const response = await fetch(`/api/forum/threads/${this.currentNotification.thread_id}/messages`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    content: input.value.trim(),
                    reply_to_id: this.currentNotification.message_id
                })
            });
            
            if (response.ok) {
                if (window.showToast) {
                    window.showToast('Reply sent! 🚀');
                }
                this.dismissNotification();
            } else {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to send reply');
            }
            
        } catch (error) {
            // console.error('QuickReply: Send error:', error);
            
            if (window.showToast) {
                window.showToast(error.message, 'error');
            } else {
                alert(error.message);
            }
            
            sendBtn.disabled = false;
            sendBtn.innerHTML = '➤';
        }
    }
    
    viewThread() {
        if (!this.currentNotification) return;
        
        const threadId = this.currentNotification.thread_id;
        const messageId = this.currentNotification.message_id;
        
        // Check if already in forum
        if (window.forum && window.location.pathname.includes('/forum')) {
            // Direct SPA navigation
            this.dismissNotification();
            window.forum.viewThread(threadId);
        } else {
            // Store target and navigate to forum
            sessionStorage.setItem('forumTarget', JSON.stringify({
                threadId: threadId,
                messageId: messageId
            }));
            this.dismissNotification();
            window.location.href = '/api/forum';
        }
    }    
    
    setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            if (!this.notification || !this.notification.classList.contains('show')) return;
            
            if (e.key === 'Escape') {
                e.preventDefault();
                this.dismissNotification();
            }
            
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                this.sendReply();
            }
        });
    }
    
    // NEW: Setup auto-resize for compact textarea
    setupInputAutoResize() {
        setTimeout(() => {
            const input = document.getElementById('quickReplyInput');
            if (input) {
                // Auto-resize textarea
                input.addEventListener('input', function() {
                    this.style.height = 'auto';
                    this.style.height = Math.min(this.scrollHeight, 44) + 'px';
                });
                
                // console.log('QuickReply: Input auto-resize setup complete');
            }
        }, 1000);
    }
    
    playNotificationSound() {
        try {
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            
            oscillator.frequency.setValueAtTime(800, audioContext.currentTime);
            oscillator.frequency.setValueAtTime(600, audioContext.currentTime + 0.1);
            
            gainNode.gain.setValueAtTime(0.05, audioContext.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.15);
            
            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.15);
            
        } catch (error) {
            // Silently fail if audio not supported
            // console.warn('QuickReply: Audio notification failed');
        }
    }
    
    // LEGACY: Keep for backward compatibility but not used in new design
    getUserBadgeColor(user) {
        const role = user.role?.toLowerCase();
        
        switch (role) {
            case 'creator': return '#f59e0b';
            case 'team': return '#3b82f6';
            case 'patron': return '#f97316';
            case 'supporter': return '#10b981';
            default: return '#6b7280';
        }
    }
    
    // Static methods for external use
    static dismissNotification() {
        if (window.quickReplyManager) {
            window.quickReplyManager.dismissNotification();
        }
    }
    
    static sendReply() {
        if (window.quickReplyManager) {
            window.quickReplyManager.sendReply();
        }
    }
    
    static viewThread() {
        if (window.quickReplyManager) {
            window.quickReplyManager.viewThread();
        }
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    if (window.currentUserId) {
        window.quickReplyManager = new QuickReplyManager();
        window.QuickReplyManager = QuickReplyManager;
        // console.log('QuickReply: Manager initialized for user', window.currentUserId);
    } else {
        // console.warn('QuickReply: No user ID found, manager not initialized');
    }
});