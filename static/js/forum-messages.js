/**
 * forum-messages.js - FIXED & CLEANED
 * Handles: Message rendering, editing, deleting, replies, mentions, typing indicators, likes
 * Dependencies: forum-core.js (extends ForumCore)
 */

class ForumMessages extends ForumCore {
    constructor() {
        super();
        
        // Initialize typing users set
        this.typingUsers = new Set();
        
        // Prevent duplicate sends
        this.isSendingMessage = false;
        this.lastSentMessage = '';
        this.lastSentTime = 0;
        this.sendDebounceTimeout = null;
        
        // Event handlers
        this.enterKeyHandler = null;
        this.keyboardHandlers = null;
        this.viewportHandler = null;
        this.typingTimer = null;
        this.mentionAutocomplete = null;
        this.selectedMentionIndex = -1;
        
        // Current states
        this.currentReplyToMessage = null;
        this.currentMessageForThread = null;
        this.spawnedThreads = [];
        
        this.init();
    }
    
    initEnterKeyHandling() {
        if (this.enterKeyHandler) {
            document.removeEventListener('keydown', this.enterKeyHandler);
        }
        
        this.enterKeyHandler = (e) => {
            if (e.key !== 'Enter') return;
            
            const target = e.target;
            
            if (target.classList.contains('reply-input')) {
                if (e.shiftKey) return;
                e.preventDefault();
                
                const sendBtn = target.closest('.reply-input-container')?.querySelector('.btn-primary');
                if (sendBtn && target.value.trim() && !sendBtn.disabled) {
                    sendBtn.click();
                }
                return;
            }
            
            if (target.classList.contains('message-input')) {
                if (e.shiftKey) return;
                e.preventDefault();
                
                if (target.value.trim() && !this.isSendingMessage) {
                    this.handleSendMessage();
                }
                return;
            }
            
            if (target.classList.contains('edit-input')) {
                if (e.shiftKey) return;
                e.preventDefault();
                
                const saveBtn = target.closest('.edit-input-container')?.querySelector('.btn-primary');
                if (saveBtn && target.value.trim() && !saveBtn.disabled) {
                    saveBtn.click();
                }
                return;
            }
        };
        
        document.addEventListener('keydown', this.enterKeyHandler);
    }

    // ================== MESSAGE RENDERING ==================

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

        const likeButton = `
            <button class="message-action-btn like-button ${message.user_has_liked ? 'liked' : ''}" 
                    onclick="forum.toggleMessageLike(${message.id})" 
                    title="${message.user_has_liked ? 'Unlike this message' : 'Like this message'}"
                    ${message.user_id === this.currentUser.id ? 'disabled style="opacity: 0.5;"' : ''}>
                <i class="${message.user_has_liked ? 'fas fa-heart' : 'far fa-heart'}"></i> Like
            </button>`;

        const likeCount = message.like_count > 0 ? `
            <div class="message-like-info" onclick="forum.showMessageLikes(${message.id})" 
                 style="cursor: pointer; display: flex; align-items: center; gap: 0.25rem; font-size: 0.75rem; color: var(--text-secondary);">
                <i class="fas fa-heart" style="color: #ef4444;"></i>
                <span class="like-count">${message.like_count}</span>
            </div>` : `<span class="like-count" style="display: none;"></span>`;

        return `
            <div class="message-item ${isNew ? 'new-message' : ''} ${isReply}" data-message-id="${message.id}">
                <div class="message-actions">
                    <button class="message-action-btn" onclick="forum.showReplyInput(${message.id})" title="Reply">
                        <i class="fas fa-reply"></i> Reply
                    </button>
                    ${this.currentThread && this.currentThread.thread_type === 'main' ? `
                        <button class="message-action-btn" onclick="forum.createThreadFromMessage(${message.id})" title="Create Thread">
                            <i class="fas fa-comments"></i> Thread
                        </button>` : ''}
                    ${likeButton}
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
                    ${message.mentions && message.mentions.length > 0 ? `<span style="font-size: 0.7rem; background: rgba(59, 130, 246, 0.1); color: #3b82f6; padding: 0.1rem 0.3rem; border-radius: 4px;"><i class="fas fa-at"></i> ${message.mentions.length}</span>` : ''}
                </div>
                <div class="message-content">${message.content_html || message.content}</div>
                <div class="message-footer">
                    ${likeCount}
                    ${threadIndicator}
                    ${replyIndicator}
                </div>
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

    // ================== MESSAGE LIKE SYSTEM ==================

    async likeMessage(messageId) {
        try {
            const response = await fetch(`/api/forum/messages/${messageId}/like`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to like message');
            }

            const result = await response.json();
            this.updateMessageLikeUI(messageId, result.like_count, result.user_has_liked);
            
            if (window.showToast && result.message !== "Already liked") {
                window.showToast('Message liked!');
            }
            
            return result;
        } catch (error) {
            console.error('Error liking message:', error);
            this.showError(error.message);
            return null;
        }
    }

    async unlikeMessage(messageId) {
        try {
            const response = await fetch(`/api/forum/messages/${messageId}/like`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to unlike message');
            }

            const result = await response.json();
            this.updateMessageLikeUI(messageId, result.like_count, result.user_has_liked);
            
            if (window.showToast && result.message !== "Not liked") {
                window.showToast('Message unliked');
            }
            
            return result;
        } catch (error) {
            console.error('Error unliking message:', error);
            this.showError(error.message);
            return null;
        }
    }

    async toggleMessageLike(messageId) {
        const likeButton = document.querySelector(`[data-message-id="${messageId}"] .like-button`);
        if (!likeButton) return;
        
        const isLiked = likeButton.classList.contains('liked');
        likeButton.disabled = true;
        
        if (isLiked) {
            await this.unlikeMessage(messageId);
        } else {
            await this.likeMessage(messageId);
        }
        
        likeButton.disabled = false;
    }

    updateMessageLikeUI(messageId, likeCount, userHasLiked) {
        const messageElement = document.querySelector(`[data-message-id="${messageId}"]`);
        if (!messageElement) return;
        
        const likeButton = messageElement.querySelector('.like-button');
        const likeCountSpan = messageElement.querySelector('.like-count');
        const likeInfo = messageElement.querySelector('.message-like-info');
        
        if (likeButton) {
            likeButton.classList.toggle('liked', userHasLiked);
            likeButton.title = userHasLiked ? 'Unlike this message' : 'Like this message';
            
            const icon = likeButton.querySelector('i');
            if (icon) {
                icon.className = userHasLiked ? 'fas fa-heart' : 'far fa-heart';
            }
        }
        
        if (likeCountSpan) {
            likeCountSpan.textContent = likeCount;
        }
        
        if (likeInfo) {
            if (likeCount > 0) {
                likeInfo.style.display = 'flex';
                const countSpan = likeInfo.querySelector('.like-count');
                if (countSpan) {
                    countSpan.textContent = likeCount;
                }
            } else {
                likeInfo.style.display = 'none';
            }
        }
        
        if (likeButton) {
            likeButton.style.transform = 'scale(1.2)';
            setTimeout(() => {
                likeButton.style.transform = 'scale(1)';
            }, 150);
        }
    }

    async showMessageLikes(messageId) {
        try {
            const response = await fetch(`/api/forum/messages/${messageId}/likes?limit=50`);
            if (!response.ok) throw new Error('Failed to load likes');
            
            const result = await response.json();
            this.showLikesModal(messageId, result.likes, result.total_likes);
            
        } catch (error) {
            console.error('Error loading message likes:', error);
            this.showError('Failed to load likes');
        }
    }

    showLikesModal(messageId, likes, totalLikes) {
        const modal = document.createElement('div');
        modal.className = 'modal likes-modal active';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3>Message Likes (${totalLikes})</h3>
                    <button class="modal-close" onclick="this.closest('.modal').remove()">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="likes-list">
                    ${likes.length === 0 ? 
                        '<p style="text-align: center; color: var(--text-secondary); padding: 2rem;">No likes yet</p>' :
                        likes.map(like => `
                            <div class="like-item">
                                <div class="user-avatar" style="background-color: ${like.user.badge_color}">
                                    ${like.user.username.substring(0, 2).toUpperCase()}
                                </div>
                                <div class="like-user-info">
                                    <div class="like-username">${like.user.username}</div>
                                    <div class="like-role">${like.user.role}</div>
                                </div>
                                <div class="like-time">${this.formatTimeAgo(like.created_at)}</div>
                            </div>
                        `).join('')
                    }
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });
    }

    // ================== MESSAGE HANDLERS ==================

    handleNewMessage(message) {
        if (this.currentView === 'discussion' && this.currentThread) {
            if (this.messages.find(m => m.id === message.id)) {
                return;
            }
            
            this.messages.push(message);
            this.renderNewMessage(message);
            
            const thread = this.threads.find(t => t.id === this.currentThread.id);
            if (thread) thread.message_count++;
            
            const container = document.getElementById('messagesContainer');
            if (container) {
                const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 150;
                
                if (!isNearBottom) {
                    this.incrementNewMessagesCount();
                    const button = document.getElementById('inlineScrollBtn');
                    if (button) {
                        button.classList.remove('hidden');
                    }
                } else {
                    setTimeout(() => this.scrollToBottom(false), 100);
                }
            }
            
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
                            messageElement.querySelector('.message-footer').appendChild(indicator);
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

    handleMessageLiked(data) {
        if (this.currentView === 'discussion' && this.currentThread) {
            const message = this.messages.find(m => m.id === data.message_id);
            if (message) {
                message.like_count = data.like_count;
            }
            
            const messageElement = document.querySelector(`[data-message-id="${data.message_id}"]`);
            if (messageElement) {
                const likeInfo = messageElement.querySelector('.message-like-info');
                const likeCountSpan = messageElement.querySelector('.like-count');
                
                if (likeInfo && data.like_count > 0) {
                    likeInfo.style.display = 'flex';
                    const countSpan = likeInfo.querySelector('.like-count');
                    if (countSpan) {
                        countSpan.textContent = data.like_count;
                    }
                }
                
                if (likeCountSpan) {
                    likeCountSpan.textContent = data.like_count;
                }
                
                if (likeInfo) {
                    likeInfo.classList.add('updated');
                    setTimeout(() => likeInfo.classList.remove('updated'), 300);
                }
            }
            
            if (data.liked_by.id !== this.currentUser.id) {
                this.showLikeNotification(data.liked_by.username, 'liked');
            }
        }
    }

    handleMessageUnliked(data) {
        if (this.currentView === 'discussion' && this.currentThread) {
            const message = this.messages.find(m => m.id === data.message_id);
            if (message) {
                message.like_count = data.like_count;
            }
            
            const messageElement = document.querySelector(`[data-message-id="${data.message_id}"]`);
            if (messageElement) {
                const likeInfo = messageElement.querySelector('.message-like-info');
                const likeCountSpan = messageElement.querySelector('.like-count');
                
                if (likeInfo) {
                    if (data.like_count > 0) {
                        likeInfo.style.display = 'flex';
                        const countSpan = likeInfo.querySelector('.like-count');
                        if (countSpan) {
                            countSpan.textContent = data.like_count;
                        }
                    } else {
                        likeInfo.style.display = 'none';
                    }
                }
                
                if (likeCountSpan) {
                    if (data.like_count > 0) {
                        likeCountSpan.textContent = data.like_count;
                        likeCountSpan.style.display = 'inline';
                    } else {
                        likeCountSpan.style.display = 'none';
                    }
                }
            }
            
            if (data.unliked_by.id !== this.currentUser.id) {
                this.showLikeNotification(data.unliked_by.username, 'unliked');
            }
        }
    }

    handleMessageThreadCountUpdate(data) {
        const message = this.messages.find(m => m.id === data.message_id);
        if (message) {
            message.spawned_thread_count = data.spawned_thread_count;
        }
        
        const messageElement = document.querySelector(`[data-message-id="${data.message_id}"]`);
        if (!messageElement) return;
        
        const footer = messageElement.querySelector('.message-footer');
        if (!footer) return;
        
        let threadIndicator = messageElement.querySelector('.message-threads-indicator');
        
        if (data.spawned_thread_count > 0) {
            if (!threadIndicator) {
                threadIndicator = document.createElement('div');
                threadIndicator.className = 'message-threads-indicator';
                threadIndicator.onclick = () => this.showSpawnedThreads(data.message_id);
                footer.appendChild(threadIndicator);
            }
            
            threadIndicator.innerHTML = `
                <i class="fas fa-comments"></i> 
                ${data.spawned_thread_count} thread${data.spawned_thread_count !== 1 ? 's' : ''}
            `;
            
            threadIndicator.style.animation = 'pulse 0.6s ease-out';
            setTimeout(() => {
                threadIndicator.style.animation = '';
            }, 600);
            
        } else if (threadIndicator) {
            threadIndicator.remove();
        }
        
        if (data.creator_username && data.creator_username !== this.currentUser.username) {
            console.log(`💬 ${data.creator_username} created a new thread: "${data.sub_thread_title}"`);
        }
    }

    showLikeNotification(username, action) {
        const notification = document.createElement('div');
        notification.className = 'like-notification';
        notification.innerHTML = `
            <i class="fas fa-heart"></i>
            <span>${username} ${action} a message</span>
        `;
        
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            padding: 0.75rem 1rem;
            box-shadow: var(--shadow-md);
            z-index: 1000;
            opacity: 0;
            transform: translateX(100%);
            transition: all 0.3s ease;
            font-size: 0.875rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        `;
        
        const heart = notification.querySelector('i');
        heart.style.color = action === 'liked' ? '#ef4444' : '#6b7280';
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.style.opacity = '1';
            notification.style.transform = 'translateX(0)';
        }, 100);
        
        setTimeout(() => {
            notification.style.opacity = '0';
            notification.style.transform = 'translateX(100%)';
            setTimeout(() => notification.remove(), 300);
        }, 3000);
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

    incrementNewMessagesCount() {
        this.newMessagesCount++;
        this.updateNewMessagesIndicator();
    }

    clearNewMessagesCount() {
        this.newMessagesCount = 0;
        this.updateNewMessagesIndicator();
    }

    updateNewMessagesIndicator() {
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
            inlineIndicator.style.animation = 'pulse 2s infinite';
        } else if (inlineIndicator) {
            inlineIndicator.remove();
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
            
            setTimeout(() => this.navigateToNewThread(newThread.id), 500);
            
        } catch (error) {
            this.showError(error.message);
        }
    }

    async navigateToNewThread(threadId) {
        try {
            this.showLoading();
            
            const threadResponse = await fetch(`/api/forum/threads/${threadId}`);
            if (!threadResponse.ok) {
                throw new Error('Failed to load thread');
            }
            
            this.currentThread = await threadResponse.json();
            
            if (window.enhancedForumNotificationManager) {
                window.enhancedForumNotificationManager.onThreadEntered(threadId);
            }
            
            const hierarchyResponse = await fetch(`/api/forum/threads/${threadId}/hierarchy`);
            if (hierarchyResponse.ok) {
                Object.assign(this.currentThread, await hierarchyResponse.json());
            }
            
            await this.loadMessages(threadId);
            this.currentView = 'discussion';
            this.renderDiscussionView();
            this.websockets.connectThreadWebSocket(threadId);
            
            setTimeout(() => {
                if (this.showScrollToBottomButtonIfNeeded) {
                    this.showScrollToBottomButtonIfNeeded();
                }
            }, 100);
            
        } catch (error) {
            console.error('Error navigating to new thread:', error);
            this.showError('Thread created but failed to open. You can find it in the Following tab.');
            
            this.currentTab = 'following';
            this.backToThreads();
            setTimeout(() => this.loadThreads('following').then(() => this.renderThreadsView()), 500);
        }
    }

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

            if (window.enhancedForumNotificationManager) {
                window.enhancedForumNotificationManager.onThreadEntered(threadId);
            }
            
            const hierarchyResponse = await fetch(`/api/forum/threads/${threadId}/hierarchy`);
            if (hierarchyResponse.ok) {
                Object.assign(this.currentThread, await hierarchyResponse.json());
            }
            
            await this.loadMessages(threadId);
            this.currentView = 'discussion';
            this.renderDiscussionView();
            this.websockets.connectThreadWebSocket(threadId);
            
            setTimeout(() => {
                if (this.showScrollToBottomButtonIfNeeded) {
                    this.showScrollToBottomButtonIfNeeded();
                }
            }, 100);
            
        } catch (error) {
            console.error('Error viewing thread:', error);
            this.showError(error.message);
            this.backToThreads();
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
            if (!existing) messageElement.querySelector('.message-footer').appendChild(indicator);
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
        if (this.currentThread) {
            this.websockets.sendTypingIndicator(this.currentThread.id, isTyping);
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

    // ================== MESSAGE SENDING ==================

    async handleSendMessage() {
        const input = document.getElementById('messageInput');
        const sendBtn = document.getElementById('sendBtn') || document.querySelector('.send-btn');
        
        if (!input) {
            console.error('Message input not found');
            return false;
        }
        
        const content = input.value.trim();
        if (!content) return false;
        
        if (this.isSendingMessage) {
            return false;
        }
        
        const now = Date.now();
        if (content === this.lastSentMessage && now - this.lastSentTime < 1000) {
            return false;
        }
        
        if (this.sendDebounceTimeout) {
            clearTimeout(this.sendDebounceTimeout);
            this.sendDebounceTimeout = null;
        }
        
        this.isSendingMessage = true;
        this.lastSentMessage = content;
        this.lastSentTime = now;
        
        if (sendBtn) {
            sendBtn.disabled = true;
            sendBtn.style.opacity = '0.5';
        }
        
        input.value = '';
        this.sendTypingIndicator(false);
        
        try {
            
            const response = await fetch(`/api/forum/threads/${this.currentThread.id}/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to send message');
            }

            
            this.hideMentionAutocomplete();
            this.clearNewMessagesCount();
            setTimeout(() => this.scrollToBottom(true), 100);
            
            return true;
            
        } catch (error) {
            console.error('❌ Error sending message:', error);
            input.value = content;
            this.showError(error.message);
            return false;
            
        } finally {
            setTimeout(() => {
                this.isSendingMessage = false;
                
                if (sendBtn) {
                    sendBtn.disabled = false;
                    sendBtn.style.opacity = '1';
                }
            }, 500);
        }
    }

    initSendButton() {
        const sendBtn = document.getElementById('sendBtn') || document.querySelector('.send-btn');
        if (!sendBtn) return;
        
        const newSendBtn = sendBtn.cloneNode(true);
        sendBtn.parentNode.replaceChild(newSendBtn, sendBtn);
        
        newSendBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            if (!this.isSendingMessage && !newSendBtn.disabled) {
                this.handleSendMessage();
            }
        });
        
    }

    // ================== WEBSOCKET HANDLERS ==================

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
            'message_thread_count_updated': () => this.handleMessageThreadCountUpdate(data)
        };
        handlers[data.type]?.();
    }

    // ================== VIEW RENDERING OVERRIDE ==================

    renderDiscussionView() {
        super.renderDiscussionView();
        
        setTimeout(() => {
            this.initEnterKeyHandling();
            this.initSendButton();
            
            const messageInput = document.getElementById('messageInput');
            if (messageInput) {
                this.initMentionAutocomplete(messageInput);
            }
        }, 200);
    }

    // ================== CLEANUP ==================

    backToThreads() {
        if (this.enterKeyHandler) {
            document.removeEventListener('keydown', this.enterKeyHandler);
            this.enterKeyHandler = null;
        }
        
        this.isSendingMessage = false;
        if (this.sendDebounceTimeout) {
            clearTimeout(this.sendDebounceTimeout);
            this.sendDebounceTimeout = null;
        }
        
        super.backToThreads();
    }
}

window.ForumMessages = ForumMessages;