// comment-system.js - WebSocket Version

// ============================================================================
// COMMENT SYSTEM INITIALIZATION
// ============================================================================
window.initCommentSystem = function() {
    // Get DOM elements
    const elements = getCommentElements();
    
    // State variables
    let isTrackLiked = false;
    let commentToDelete = null;
    let shareMetrics = { count: 0 };
    
    // Initialize system
    loadTrackMetrics();
    loadComments();
    setupEventListeners();
    setupShareOptions();
    setupTimestampInputs();
    
    // ============================================================================
    // CORE FUNCTIONS
    // ============================================================================
    
    function getCommentElements() {
        return {
            // Social elements
            likeTrackBtn: document.getElementById('likeTrackBtn'),
            commentBtn: document.getElementById('commentBtn'),
            shareBtn: document.getElementById('shareBtn'),
            likeCount: document.getElementById('likeCount'),
            commentCount: document.getElementById('commentCount'),
            totalCommentCount: document.getElementById('totalCommentCount'),
            shareCount: document.getElementById('shareCount'),
            
            // Comment section
            commentSection: document.getElementById('commentSection'),
            commentForm: document.getElementById('commentForm'),
            commentInput: document.getElementById('commentInput'),
            commentList: document.getElementById('commentList'),
            commentTimestamp: document.getElementById('commentTimestamp'),
            
            // Timestamp controls
            addTimestampBtn: document.getElementById('addTimestampBtn'),
            timestampDisplay: document.getElementById('timestampDisplay'),
            timestampValue: document.getElementById('timestampValue'),
            clearTimestamp: document.getElementById('clearTimestamp'),
            
            // Modals
            shareModal: document.getElementById('shareModal'),
            replyModal: document.getElementById('replyModal'),
            editCommentModal: document.getElementById('editCommentModal'),
            deleteConfirmModal: document.getElementById('deleteConfirmModal'),
            
            // Reply modal elements
            replyForm: document.getElementById('replyForm'),
            replyInput: document.getElementById('replyInput'),
            replyParentId: document.getElementById('replyParentId'),
            parentCommentContent: document.getElementById('parentCommentContent'),
            replyTimestamp: document.getElementById('replyTimestamp'),
            replyTimestampDisplay: document.getElementById('replyTimestampDisplay'),
            replyTimestampValue: document.getElementById('replyTimestampValue'),
            addReplyTimestampBtn: document.getElementById('addReplyTimestampBtn'),
            clearReplyTimestamp: document.getElementById('clearReplyTimestamp'),
            
            // Edit modal elements
            editCommentForm: document.getElementById('editCommentForm'),
            editCommentInput: document.getElementById('editCommentInput'),
            editCommentId: document.getElementById('editCommentId'),
            editTimestamp: document.getElementById('editTimestamp'),
            editTimestampDisplay: document.getElementById('editTimestampDisplay'),
            editTimestampValue: document.getElementById('editTimestampValue'),
            addEditTimestampBtn: document.getElementById('addEditTimestampBtn'),
            clearEditTimestamp: document.getElementById('clearEditTimestamp'),
            
            // Share elements
            shareUrl: document.getElementById('shareUrl'),
            copyShareUrl: document.getElementById('copyShareUrl')
        };
    }
    
    // Make getCommentElements globally accessible for WebSocket
    window.getCommentElements = getCommentElements;
    
    function loadTrackMetrics() {
        fetch(`/api/tracks/${window.currentTrackId}/metrics`)
            .then(response => response.ok ? response.json() : { likes: 0, comments: 0, shares: 0, is_liked: false })
            .then(updateMetrics)
            .catch(error => {
                // console.error('Error loading track metrics:', error);
                updateMetrics({ likes: 0, comments: 0, shares: 0, is_liked: false });
            });
    }
    
    function updateMetrics(data) {
        // Update like state
        if (elements.likeCount) elements.likeCount.textContent = data.likes || 0;
        isTrackLiked = data.is_liked || false;
        
        if (elements.likeTrackBtn) {
            elements.likeTrackBtn.classList.toggle('active', isTrackLiked);
            const icon = elements.likeTrackBtn.querySelector('i');
            if (icon) {
                icon.className = isTrackLiked ? 'fas fa-heart' : 'far fa-heart';
            }
        }
        
        // Update comment counts
        const commentTotal = data.comments || 0;
        if (elements.commentCount) elements.commentCount.textContent = commentTotal;
        if (elements.totalCommentCount) elements.totalCommentCount.textContent = `(${commentTotal})`;
        
        // Update share count
        if (elements.shareCount) {
            elements.shareCount.textContent = data.shares || 0;
            shareMetrics.count = data.shares || 0;
        }
    }
    
    function loadComments() {
        if (!elements.commentList) return;
        
        elements.commentList.innerHTML = '<div class="comments-loading"><i class="fas fa-spinner fa-spin"></i> Loading comments...</div>';
        
        fetch(`/api/tracks/${window.currentTrackId}/comments`)
            .then(response => {
                if (!response.ok) throw new Error('Failed to load comments');
                return response.json();
            })
            .then(data => {
                renderComments(data);
                
                // Update comment counts
                const commentTotal = data.length || 0;
                if (elements.commentCount) elements.commentCount.textContent = commentTotal;
                if (elements.totalCommentCount) elements.totalCommentCount.textContent = `(${commentTotal})`;
            })
            .catch(error => {
                // console.error('Error loading comments:', error);
                if (elements.commentList) {
                    elements.commentList.innerHTML = '<div class="comment-empty">Failed to load comments. Please try again later.</div>';
                }
            });
    }
    
    // Make loadComments globally accessible
    window.loadComments = loadComments;
    
    function renderComments(comments) {
        if (!elements.commentList) return;
        
        if (!comments || comments.length === 0) {
            elements.commentList.innerHTML = '<div class="comment-empty">No comments yet. Be the first to comment!</div>';
            return;
        }
        
        // Group comments by parent
        const commentsByParent = {};
        comments.forEach(comment => {
            const parentId = comment.parent_id || 'root';
            if (!commentsByParent[parentId]) commentsByParent[parentId] = [];
            commentsByParent[parentId].push(comment);
        });
        
        // Render top-level comments
        const rootComments = commentsByParent['root'] || [];
        elements.commentList.innerHTML = rootComments.map(comment => {
            const replies = commentsByParent[comment.id] || [];
            return renderCommentItem(comment, replies, commentsByParent);
        }).join('');
    }
    
    function renderCommentItem(comment, replies, commentsByParent) {
        const permissions = getCommentPermissions(comment);
        const timeAgo = getTimeAgo(new Date(comment.created_at));
        const isLiked = comment.user_has_liked || false;
        
        // Format timestamp
        let timestampHtml = '';
        if (comment.timestamp && comment.timestamp > 0) {
            const formattedTime = formatTimestamp(comment.timestamp);
            timestampHtml = `
                <span class="comment-time-link" data-seconds="${comment.timestamp}">
                    <i class="fas fa-clock"></i> ${formattedTime}
                </span>
            `;
        }
        
        // Render replies
        let repliesHtml = '';
        if (replies && replies.length > 0) {
            const repliesText = replies.length === 1 ? '1 reply' : `${replies.length} replies`;
            repliesHtml = `
                <div class="comment-replies-container">
                    <button class="replies-toggle" data-comment-id="${comment.id}">
                        <i class="fas fa-chevron-down"></i> ${repliesText}
                    </button>
                    <div class="comment-replies">
                        ${replies.map(reply => {
                            const nestedReplies = commentsByParent[reply.id] || [];
                            return renderCommentItem(reply, nestedReplies, commentsByParent);
                        }).join('')}
                    </div>
                </div>
            `;
        }
        
        return `
            <div class="comment-item" data-comment-id="${comment.id}">
                <div class="comment-header-row">
                    <div class="comment-user-info">
                        <div class="user-avatar">
                            <div class="avatar-placeholder">${(comment.username || 'U').charAt(0).toUpperCase()}</div>
                        </div>
                        <div>
                            <div class="comment-username">${comment.username || 'Anonymous'}</div>
                            <div class="comment-timestamp">${timeAgo}${comment.is_edited ? ' (edited)' : ''}</div>
                        </div>
                    </div>
                    ${permissions.showMenu ? `
                        <div class="comment-menu">
                            <button class="comment-menu-btn" data-comment-id="${comment.id}">
                                <i class="fas fa-ellipsis-vertical"></i>
                            </button>
                            <div class="comment-menu-dropdown" id="commentMenu${comment.id}">
                                ${permissions.canEdit ? `
                                <div class="comment-menu-option edit-option" data-comment-id="${comment.id}">
                                    <i class="fas fa-edit"></i> Edit
                                </div>
                                ` : ''}
                                <div class="comment-menu-option delete-option" data-comment-id="${comment.id}">
                                    <i class="fas fa-trash"></i> Delete
                                </div>
                            </div>
                        </div>
                    ` : ''}
                </div>
                
                <div class="comment-content" data-comment-id="${comment.id}">
                    ${timestampHtml}
                    ${comment.content}
                </div>

                <!-- ✅ Inline edit UI (hidden by default) -->
                <div class="comment-edit-inline" data-comment-id="${comment.id}" style="display: none;">
                    <textarea class="comment-edit-textarea" placeholder="Edit your comment...">${comment.content}</textarea>
                    <div class="comment-edit-actions">
                        <button class="cancel-edit-btn" data-comment-id="${comment.id}">
                            <i class="fas fa-times"></i> Cancel
                        </button>
                        <button class="save-edit-btn" data-comment-id="${comment.id}">
                            <i class="fas fa-check"></i> Save
                        </button>
                    </div>
                </div>

                <div class="comment-actions-row">
                    <button class="comment-action-btn like-btn ${isLiked ? 'liked' : ''}" data-comment-id="${comment.id}">
                        <i class="fas fa-thumbs-up"></i> ${comment.like_count || 0}
                    </button>
                    <button class="comment-action-btn reply-btn" data-comment-id="${comment.id}">
                        <i class="fas fa-reply"></i> Reply
                    </button>
                </div>
                
                ${repliesHtml}
            </div>
        `;
    }
    
    // Make renderCommentItem globally accessible for WebSocket
    window.renderCommentItem = renderCommentItem;
    
    function getCommentPermissions(comment) {
        const isCreator = currentUserData.is_creator === true;
        const isTeam = currentUserData.is_team === true;
        const isAuthor = parseInt(currentUserData.id) === comment.user_id;
        
        let canDelete = false;
        if (isAuthor || isCreator || (isTeam && !comment.author_is_creator && !comment.author_is_team)) {
            canDelete = true;
        }
        
        return {
            canEdit: isAuthor,
            canDelete: canDelete,
            showMenu: canDelete || isAuthor
        };
    }
    
    // ============================================================================
    // EVENT HANDLERS
    // ============================================================================
    
    function setupEventListeners() {
        // Social buttons
        if (elements.commentBtn && elements.commentSection) {
            elements.commentBtn.addEventListener('click', () => {
                elements.commentSection.scrollIntoView({ behavior: 'smooth' });
                if (elements.commentInput) elements.commentInput.focus();
            });
        }
        
        if (elements.likeTrackBtn) {
            elements.likeTrackBtn.addEventListener('click', toggleTrackLike);
        }
        
        if (elements.shareBtn) {
            elements.shareBtn.addEventListener('click', openShareModal);
        }
        
        // Comment form
        if (elements.commentForm) {
            elements.commentForm.addEventListener('submit', submitComment);
        }
        
        if (elements.replyForm) {
            elements.replyForm.addEventListener('submit', submitReply);
        }
        
        if (elements.editCommentForm) {
            elements.editCommentForm.addEventListener('submit', submitEditedComment);
        }
        
        // Timestamp controls
        if (elements.addTimestampBtn) {
            elements.addTimestampBtn.addEventListener('click', () => addCurrentTimestamp('comment'));
        }
        
        if (elements.clearTimestamp) {
            elements.clearTimestamp.addEventListener('click', () => clearTimestamp('comment'));
        }
        
        if (elements.addReplyTimestampBtn) {
            elements.addReplyTimestampBtn.addEventListener('click', () => addCurrentTimestamp('reply'));
        }
        
        if (elements.clearReplyTimestamp) {
            elements.clearReplyTimestamp.addEventListener('click', () => clearTimestamp('reply'));
        }
        
        if (elements.addEditTimestampBtn) {
            elements.addEditTimestampBtn.addEventListener('click', () => addCurrentTimestamp('edit'));
        }
        
        if (elements.clearEditTimestamp) {
            elements.clearEditTimestamp.addEventListener('click', () => clearTimestamp('edit'));
        }
        
        // Modal controls
        setupModalEventListeners();
        
        // Share controls
        if (elements.copyShareUrl) {
            elements.copyShareUrl.addEventListener('click', copyToClipboard);
        }
        
        // Global event delegation
        document.addEventListener('click', handleGlobalClicks);
    }
    
    function setupModalEventListeners() {
        const modalControls = [
            { modal: elements.shareModal, close: document.getElementById('closeShareModal') },
            { modal: elements.replyModal, close: document.getElementById('closeReplyModal'), cancel: document.getElementById('cancelReply') },
            { modal: elements.editCommentModal, close: document.getElementById('closeEditModal'), cancel: document.getElementById('cancelEdit') },
            { modal: elements.deleteConfirmModal, close: document.getElementById('closeDeleteModal'), cancel: document.getElementById('cancelDelete') }
        ];
        
        modalControls.forEach(({ modal, close, cancel }) => {
            if (close && modal) {
                close.addEventListener('click', () => closeModal(modal));
            }
            if (cancel && modal) {
                cancel.addEventListener('click', () => closeModal(modal));
            }
        });
        
        // Delete confirmation
        const confirmDelete = document.getElementById('confirmDelete');
        if (confirmDelete) {
            confirmDelete.addEventListener('click', deleteComment);
        }
        
        // Close modals on outside click
        window.addEventListener('click', (e) => {
            [elements.shareModal, elements.replyModal, elements.editCommentModal, elements.deleteConfirmModal].forEach(modal => {
                if (modal && e.target === modal) {
                    closeModal(modal);
                }
            });
        });
    }
    
    function handleGlobalClicks(e) {
        // Handle like button clicks
        if (e.target.closest('.like-btn')) {
            const likeBtn = e.target.closest('.like-btn');
            const commentId = likeBtn.dataset.commentId;
            toggleLike(commentId, likeBtn);
            return;
        }
        
        // Handle reply button clicks
        if (e.target.closest('.reply-btn')) {
            const replyBtn = e.target.closest('.reply-btn');
            const commentId = replyBtn.dataset.commentId;
            const commentElement = document.querySelector(`.comment-content[data-comment-id="${commentId}"]`);
            if (commentElement) {
                showReplyModal(commentId, commentElement.textContent);
            }
            return;
        }
        
        // Handle edit button clicks
        if (e.target.closest('.edit-option')) {
            const editOption = e.target.closest('.edit-option');
            const commentId = editOption.dataset.commentId;
            showEditCommentModal(commentId);
            return;
        }
        
        // Handle delete button clicks
        if (e.target.closest('.delete-option')) {
            const deleteOption = e.target.closest('.delete-option');
            const commentId = deleteOption.dataset.commentId;
            showDeleteConfirmation(commentId);
            return;
        }

        // ✅ Handle inline edit cancel button
        if (e.target.closest('.cancel-edit-btn')) {
            const cancelBtn = e.target.closest('.cancel-edit-btn');
            const commentId = cancelBtn.dataset.commentId;
            cancelInlineEdit(commentId);
            return;
        }

        // ✅ Handle inline edit save button
        if (e.target.closest('.save-edit-btn')) {
            const saveBtn = e.target.closest('.save-edit-btn');
            const commentId = saveBtn.dataset.commentId;
            saveInlineEdit(commentId);
            return;
        }

        // Handle comment menu toggles
        if (e.target.closest('.comment-menu-btn')) {
            const menuBtn = e.target.closest('.comment-menu-btn');
            const commentId = menuBtn.dataset.commentId;
            toggleCommentMenu(commentId);
            return;
        }
        
        // Handle timestamp clicks
        if (e.target.closest('.comment-time-link')) {
            const timeLink = e.target.closest('.comment-time-link');
            const seconds = parseFloat(timeLink.dataset.seconds);
            seekToTime(seconds);
            return;
        }
        
        // Handle reply toggles
        if (e.target.closest('.replies-toggle')) {
            const repliesToggle = e.target.closest('.replies-toggle');
            const commentId = repliesToggle.dataset.commentId;
            toggleReplies(commentId);
            return;
        }
        
        // Close open comment menus
        const openMenus = document.querySelectorAll('.comment-menu-dropdown.visible');
        openMenus.forEach(menu => menu.classList.remove('visible'));
    }
    
    // ============================================================================
    // FORM SUBMISSIONS
    // ============================================================================
    
    function submitComment(e) {
        e.preventDefault();
        
        // Validate timestamp if visible
        if (elements.timestampDisplay && elements.timestampDisplay.style.display !== 'none' && elements.timestampValue) {
            const timeValue = parseTimestampInput(elements.timestampValue.value);
            if (timeValue === null) {
                showToast('Invalid timestamp format. Please use mm:ss or hh:mm:ss', 'error');
                elements.timestampValue.focus();
                return;
            }
            if (elements.commentTimestamp) elements.commentTimestamp.value = timeValue;
        }
        
        const form = e.target;
        const formData = new FormData(form);
        const submitBtn = form.querySelector('button[type="submit"]');
        
        setSubmitState(submitBtn, true, 'Submitting...');
        
        fetch(`/api/tracks/${window.currentTrackId}/comments`, {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) throw new Error('Failed to submit comment');
            return response.json();
        })
        .then(data => {
            form.reset();
            clearTimestamp('comment');
            // Don't call loadComments() - WebSocket will handle the update
            showToast('Comment posted successfully');
        })
        .catch(error => {
            // console.error('Error submitting comment:', error);
            showToast('Failed to post comment. Please try again.', 'error');
        })
        .finally(() => {
            setSubmitState(submitBtn, false, 'Comment');
        });
    }
    
    function submitReply(e) {
        e.preventDefault();
        
        const form = e.target;
        const formData = new FormData(form);
        
        // Handle reply timestamp
        if (elements.replyTimestampDisplay && elements.replyTimestampDisplay.style.display !== 'none' && elements.replyTimestampValue) {
            const timeValue = parseTimestampInput(elements.replyTimestampValue.value);
            if (timeValue === null) {
                showToast('Invalid timestamp format. Please use mm:ss or hh:mm:ss', 'error');
                elements.replyTimestampValue.focus();
                return;
            }
            formData.set('timestamp', timeValue);
        } else {
            formData.set('timestamp', elements.replyTimestamp ? elements.replyTimestamp.value || 0 : 0);
        }
        
        const submitBtn = form.querySelector('button[type="submit"]');
        setSubmitState(submitBtn, true, 'Submitting...');
        
        fetch(`/api/tracks/${window.currentTrackId}/comments`, {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) throw new Error('Failed to submit reply');
            return response.json();
        })
        .then(data => {
            form.reset();
            closeModal(elements.replyModal);
            clearTimestamp('reply');
            // Don't call loadComments() - WebSocket will handle the update
            showToast('Reply posted successfully');
        })
        .catch(error => {
            // console.error('Error submitting reply:', error);
            showToast('Failed to post reply. Please try again.', 'error');
        })
        .finally(() => {
            setSubmitState(submitBtn, false, 'Reply');
        });
    }
    
    function submitEditedComment(e) {
        e.preventDefault();
        
        const commentId = elements.editCommentId.value;
        const content = elements.editCommentInput.value.trim();
        
        if (!commentId || !content) {
            showToast('Invalid comment data', 'error');
            return;
        }
        
        let timestampValue = 0;
        if (elements.editTimestampDisplay && elements.editTimestampDisplay.style.display !== 'none' && elements.editTimestampValue) {
            const timeValue = parseTimestampInput(elements.editTimestampValue.value);
            if (timeValue === null) {
                showToast('Invalid timestamp format. Please use mm:ss or hh:mm:ss', 'error');
                elements.editTimestampValue.focus();
                return;
            }
            timestampValue = timeValue;
        } else if (elements.editTimestamp) {
            timestampValue = parseFloat(elements.editTimestamp.value) || 0;
        }
        
        const submitBtn = elements.editCommentForm.querySelector('button[type="submit"]');
        setSubmitState(submitBtn, true, 'Saving...');
        
        const formData = new FormData();
        formData.append('content', content);
        formData.append('timestamp', timestampValue);
        
        fetch(`/api/comments/${commentId}`, {
            method: 'PUT',
            body: formData
        })
        .then(response => {
            if (!response.ok) throw new Error('Failed to update comment');
            return response.json();
        })
        .then(data => {
            closeModal(elements.editCommentModal);
            // WebSocket will handle the DOM update
            showToast('Comment updated successfully');
        })
        .catch(error => {
            // console.error('Error updating comment:', error);
            showToast('Failed to update comment. Please try again.', 'error');
        })
        .finally(() => {
            setSubmitState(submitBtn, false, 'Save Changes');
        });
    }
    
    // ============================================================================
    // SOCIAL ACTIONS
    // ============================================================================
    
    function toggleTrackLike() {
        // Optimistic update
        isTrackLiked = !isTrackLiked;
        updateLikeButton();
        
        const method = isTrackLiked ? 'POST' : 'DELETE';
        fetch(`/api/tracks/${window.currentTrackId}/like`, { method })
            .then(response => {
                if (!response.ok) throw new Error('Failed to update like status');
                return response.json();
            })
            .then(data => {
                if (elements.likeCount) elements.likeCount.textContent = data.likes || 0;
            })
            .catch(error => {
                // console.error('Error toggling like:', error);
                // Revert on error
                isTrackLiked = !isTrackLiked;
                updateLikeButton();
                showToast('Failed to update like. Please try again.', 'error');
            });
    }
    
    function updateLikeButton() {
        if (!elements.likeTrackBtn) return;
        
        elements.likeTrackBtn.classList.toggle('active', isTrackLiked);
        const icon = elements.likeTrackBtn.querySelector('i');
        if (icon) {
            icon.className = isTrackLiked ? 'fas fa-heart' : 'far fa-heart';
        }
        
        if (elements.likeCount) {
            const currentCount = parseInt(elements.likeCount.textContent || 0);
            elements.likeCount.textContent = isTrackLiked ? currentCount + 1 : Math.max(0, currentCount - 1);
        }
    }
    
    function toggleLike(commentId, likeBtn) {
        if (!likeBtn) return;
        
        const isLiked = likeBtn.classList.contains('liked');
        const likeCountEl = likeBtn.querySelector('span') || likeBtn;
        const currentLikes = parseInt(likeCountEl.textContent) || 0;
        
        // Optimistic update
        likeBtn.classList.toggle('liked');
        likeCountEl.textContent = isLiked ? Math.max(0, currentLikes - 1) : currentLikes + 1;
        
        fetch(`/api/comments/${commentId}/like`, {
            method: isLiked ? 'DELETE' : 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
        .then(response => {
            if (!response.ok) {
                // Revert on error
                likeBtn.classList.toggle('liked');
                likeCountEl.textContent = currentLikes;
                throw new Error('Failed to toggle like');
            }
            return response.json();
        })
        .then(data => {
            likeCountEl.textContent = data.like_count || 0;
        })
        .catch(error => {
            // console.error('Error toggling like:', error);
            showToast('Failed to update like. Please try again.', 'error');
        });
    }
    
    function openShareModal() {
        if (!elements.shareModal) return;

        elements.shareModal.classList.add('visible');

        // ✅ Scroll modal into view
        setTimeout(() => {
            elements.shareModal.scrollIntoView({
                behavior: 'smooth',
                block: 'center'
            });
        }, 100);

        if (elements.shareUrl) {
            elements.shareUrl.value = window.location.href;
            elements.shareUrl.select();
        }
    }
    
    function copyToClipboard() {
        if (!elements.shareUrl) return;
        
        elements.shareUrl.select();
        document.execCommand('copy');
        showToast('Link copied to clipboard!');
        incrementShareCount('copy');
    }
    
    function setupShareOptions() {
        const trackUrl = encodeURIComponent(window.location.href);
        const trackDescription = encodeURIComponent(`Listen to ${currentTrackTitle}`);
        
        const shareLinks = [
            { id: 'shareTwitter', url: `https://twitter.com/intent/tweet?text=${trackDescription}&url=${trackUrl}` },
            { id: 'shareFacebook', url: `https://www.facebook.com/sharer/sharer.php?u=${trackUrl}` },
            { id: 'shareWhatsapp', url: `https://wa.me/?text=${trackDescription}%20${trackUrl}` },
            { id: 'shareTelegram', url: `https://t.me/share/url?url=${trackUrl}&text=${trackDescription}` }
        ];
        
        shareLinks.forEach(({ id, url }) => {
            const element = document.getElementById(id);
            if (element) {
                element.href = url;
                element.addEventListener('click', () => {
                    const platform = id.replace('share', '').toLowerCase();
                    incrementShareCount(platform);
                });
            }
        });
    }
    
    function incrementShareCount(platform) {
        shareMetrics.count++;
        if (elements.shareCount) elements.shareCount.textContent = shareMetrics.count;
        
        fetch(`/api/tracks/${window.currentTrackId}/share`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ platform })
        })
        .then(response => {
            if (!response.ok) throw new Error('Failed to record share');
            return response.json();
        })
        .then(data => {
            if (elements.shareCount) elements.shareCount.textContent = data.shares || shareMetrics.count;
        })
        .catch(error => console.error('Error recording share:', error));
    }
    
    // ============================================================================
    // MODAL FUNCTIONS
    // ============================================================================
    
    function showReplyModal(commentId, commentContent) {
        if (!elements.replyModal || !elements.replyParentId || !elements.parentCommentContent) return;
        
        elements.replyParentId.value = commentId;
        elements.parentCommentContent.textContent = commentContent;
        clearTimestamp('reply');
        elements.replyModal.classList.add('visible');
        if (elements.replyInput) {
            setTimeout(() => elements.replyInput.focus(), 100);
        }
    }
    
    function showEditCommentModal(commentId) {
        // ✅ NEW: Inline editing instead of modal
        const commentContent = document.querySelector(`.comment-content[data-comment-id="${commentId}"]`);
        const commentEditInline = document.querySelector(`.comment-edit-inline[data-comment-id="${commentId}"]`);

        if (!commentContent || !commentEditInline) return;

        // Hide the original comment content
        commentContent.style.display = 'none';

        // Show the inline edit UI
        commentEditInline.style.display = 'block';

        // Focus the textarea and auto-resize it
        const textarea = commentEditInline.querySelector('.comment-edit-textarea');
        if (textarea) {
            // Auto-resize function
            const autoResize = () => {
                textarea.style.height = 'auto';
                textarea.style.height = textarea.scrollHeight + 'px';
            };

            // Initial resize
            autoResize();

            // Add input listener for dynamic resizing
            textarea.addEventListener('input', autoResize);

            setTimeout(() => {
                textarea.focus();
                // Move cursor to end
                textarea.setSelectionRange(textarea.value.length, textarea.value.length);
            }, 100);
        }
    }

    function cancelInlineEdit(commentId) {
        const commentContent = document.querySelector(`.comment-content[data-comment-id="${commentId}"]`);
        const commentEditInline = document.querySelector(`.comment-edit-inline[data-comment-id="${commentId}"]`);

        if (!commentContent || !commentEditInline) return;

        // Show the original comment content
        commentContent.style.display = 'block';

        // Hide the inline edit UI
        commentEditInline.style.display = 'none';

        // Reset textarea to original value (in case user made changes)
        const textarea = commentEditInline.querySelector('.comment-edit-textarea');
        if (textarea) {
            // Get original content from the comment-content div
            const timeLink = commentContent.querySelector('.comment-time-link');
            if (timeLink) {
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = commentContent.innerHTML;
                const tempTimeLink = tempDiv.querySelector('.comment-time-link');
                if (tempTimeLink) tempTimeLink.remove();
                textarea.value = tempDiv.innerHTML.trim();
            } else {
                textarea.value = commentContent.textContent.trim();
            }
        }
    }

    function saveInlineEdit(commentId) {
        const commentEditInline = document.querySelector(`.comment-edit-inline[data-comment-id="${commentId}"]`);
        if (!commentEditInline) return;

        const textarea = commentEditInline.querySelector('.comment-edit-textarea');
        const saveBtn = commentEditInline.querySelector('.save-edit-btn');

        if (!textarea || !saveBtn) return;

        const content = textarea.value.trim();
        if (!content) {
            showToast('Comment cannot be empty', 'error');
            return;
        }

        // Disable button and show loading state
        setSubmitState(saveBtn, true, 'Saving...');

        const formData = new FormData();
        formData.append('content', content);
        formData.append('timestamp', 0); // TODO: Handle timestamp if needed

        fetch(`/api/comments/${commentId}`, {
            method: 'PUT',
            body: formData
        })
        .then(response => {
            if (!response.ok) throw new Error('Failed to update comment');
            return response.json();
        })
        .then(data => {
            // Hide inline edit UI
            commentEditInline.style.display = 'none';

            // Show original comment content
            const commentContent = document.querySelector(`.comment-content[data-comment-id="${commentId}"]`);
            if (commentContent) {
                commentContent.style.display = 'block';
            }

            // WebSocket will handle the DOM update
            showToast('Comment updated successfully');
        })
        .catch(error => {
            // console.error('Error updating comment:', error);
            showToast('Failed to update comment. Please try again.', 'error');
        })
        .finally(() => {
            setSubmitState(saveBtn, false, '<i class="fas fa-check"></i> Save');
        });
    }
    
    function showDeleteConfirmation(commentId) {
        if (!elements.deleteConfirmModal) return;
        
        commentToDelete = commentId;
        elements.deleteConfirmModal.classList.add('visible');
    }
    
    function deleteComment() {
        if (!commentToDelete) return;
        
        const commentId = commentToDelete;
        const deleteBtn = document.getElementById('confirmDelete');
        
        setSubmitState(deleteBtn, true, 'Deleting...');
        
        fetch(`/api/comments/${commentId}`, { method: 'DELETE' })
            .then(response => {
                if (!response.ok) throw new Error('Failed to delete comment');
                return response.json();
            })
            .then(data => {
                closeModal(elements.deleteConfirmModal);
                // WebSocket will handle the DOM update
                showToast('Comment deleted successfully');
            })
            .catch(error => {
                // console.error('Error deleting comment:', error);
                showToast('Failed to delete comment. Please try again.', 'error');
            })
            .finally(() => {
                setSubmitState(deleteBtn, false, 'Delete');
                commentToDelete = null;
            });
    }
    
    function closeModal(modal) {
        if (!modal) return;
        modal.classList.remove('visible');
        
        // Reset specific modal states
        if (modal === elements.replyModal) clearTimestamp('reply');
        if (modal === elements.editCommentModal) clearTimestamp('edit');
        if (modal === elements.shareModal && elements.shareModal) document.body.style.overflow = '';
    }
    
    // ============================================================================
    // TIMESTAMP FUNCTIONS
    // ============================================================================
    
    function setupTimestampInputs() {
        const timestampInputs = [
            { input: elements.timestampValue, hidden: elements.commentTimestamp },
            { input: elements.replyTimestampValue, hidden: elements.replyTimestamp },
            { input: elements.editTimestampValue, hidden: elements.editTimestamp }
        ];
        
        timestampInputs.forEach(({ input, hidden }) => {
            if (!input) return;
            
            input.addEventListener('blur', function() {
                const timeValue = parseTimestampInput(this.value);
                if (timeValue !== null && hidden) {
                    hidden.value = timeValue;
                    this.value = formatTimestamp(timeValue);
                } else {
                    const currentTime = hidden ? (parseFloat(hidden.value) || 0) : 0;
                    this.value = formatTimestamp(currentTime);
                    showToast('Invalid time format. Use mm:ss or hh:mm:ss', 'error');
                }
            });
            
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this.blur();
                }
            });
        });
    }
    
    function addCurrentTimestamp(type) {
        if (!window.persistentPlayer || !window.persistentPlayer.audio) {
            showToast('Player not ready', 'error');
            return;
        }
        
        const currentTime = window.persistentPlayer.audio.currentTime;
        const formattedTime = formatTimestamp(currentTime);
        
        const configs = {
            comment: { hidden: elements.commentTimestamp, value: elements.timestampValue, display: elements.timestampDisplay },
            reply: { hidden: elements.replyTimestamp, value: elements.replyTimestampValue, display: elements.replyTimestampDisplay },
            edit: { hidden: elements.editTimestamp, value: elements.editTimestampValue, display: elements.editTimestampDisplay }
        };
        
        const config = configs[type];
        if (!config) return;
        
        if (config.hidden) config.hidden.value = currentTime;
        if (config.value && config.display) {
            config.value.value = formattedTime;
            config.display.style.display = 'flex';
            setTimeout(() => {
                config.value.focus();
                config.value.select();
            }, 50);
        }
    }
    
    function clearTimestamp(type) {
        const configs = {
            comment: { hidden: elements.commentTimestamp, display: elements.timestampDisplay },
            reply: { hidden: elements.replyTimestamp, display: elements.replyTimestampDisplay },
            edit: { hidden: elements.editTimestamp, display: elements.editTimestampDisplay }
        };
        
        const config = configs[type];
        if (!config) return;
        
        if (config.hidden) config.hidden.value = 0;
        if (config.display) config.display.style.display = 'none';
    }
    
    function seekToTime(seconds) {
        if (!window.persistentPlayer || !window.persistentPlayer.audio) {
            showToast('Player not ready', 'error');
            return;
        }
        
        window.persistentPlayer.audio.currentTime = seconds;
        if (window.persistentPlayer.audio.paused) {
            window.persistentPlayer.audio.play();
        }
        showToast(`Jumped to ${formatTimestamp(seconds)}`);
    }
    
    // ============================================================================
    // UTILITY FUNCTIONS
    // ============================================================================
    
    function toggleCommentMenu(commentId) {
        const menuId = `commentMenu${commentId}`;
        const menu = document.getElementById(menuId);
        if (!menu) return;
        
        // Close other menus
        document.querySelectorAll('.comment-menu-dropdown.visible').forEach(m => {
            if (m.id !== menuId) m.classList.remove('visible');
        });
        
        menu.classList.toggle('visible');
    }
    
    function toggleReplies(commentId) {
        const toggle = document.querySelector(`.replies-toggle[data-comment-id="${commentId}"]`);
        if (!toggle) return;
        
        const repliesContainer = toggle.closest('.comment-replies-container').querySelector('.comment-replies');
        if (!repliesContainer) return;
        
        toggle.classList.toggle('collapsed');
        repliesContainer.style.display = toggle.classList.contains('collapsed') ? 'none' : 'block';
    }
    
    function updateCommentInDOM(commentId, content, timestamp, isEdited) {
        const commentElement = document.querySelector(`.comment-content[data-comment-id="${commentId}"]`);
        if (!commentElement) return;
        
        let timestampHtml = '';
        if (timestamp && timestamp > 0) {
            const formattedTime = formatTimestamp(timestamp);
            timestampHtml = `
                <span class="comment-time-link" data-seconds="${timestamp}">
                    <i class="fas fa-clock"></i> ${formattedTime}
                </span>
            `;
        }
        
        commentElement.innerHTML = timestampHtml + content;
        
        if (isEdited) {
            const commentItem = commentElement.closest('.comment-item');
            if (commentItem) {
                const timestampElement = commentItem.querySelector('.comment-timestamp');
                if (timestampElement && !timestampElement.textContent.includes('(edited)')) {
                    timestampElement.textContent += ' (edited)';
                }
            }
        }
        
        // Highlight effect
        commentElement.classList.add('highlight-edited');
        setTimeout(() => commentElement.classList.remove('highlight-edited'), 2000);
    }
    
    function updateCommentCount(delta) {
        if (elements.commentCount && elements.totalCommentCount) {
            const currentCount = parseInt(elements.commentCount.textContent || '0');
            const newCount = Math.max(0, currentCount + delta);
            elements.commentCount.textContent = newCount;
            elements.totalCommentCount.textContent = `(${newCount})`;
        }
    }
    
    function setSubmitState(button, isLoading, text) {
        if (!button) return;
        button.disabled = isLoading;
        button.innerHTML = isLoading ? `<i class="fas fa-spinner fa-spin"></i> ${text}` : text;
    }
    
    function pauseLiveUpdates() {
        if (window.liveCommentUpdater) window.liveCommentUpdater.pause();
    }
    
    function resumeLiveUpdates(delay = 0) {
        if (window.liveCommentUpdater) {
            if (delay > 0) {
                setTimeout(() => window.liveCommentUpdater.resume(), delay);
            } else {
                window.liveCommentUpdater.resume();
            }
        }
    }
};

// ============================================================================
// LIVE COMMENT UPDATES (WebSocket Version)
// ============================================================================
window.setupLiveCommentUpdates = function() {
    let websocket = null;
    let reconnectAttempts = 0;
    let maxReconnectAttempts = 5;
    let reconnectDelay = 1000;
    let typingTimer = null;
    let typingUsers = new Map();
    
    function connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/ws/track/${window.currentTrackId}?user_id=${window.currentUserData.id}`;
        
        websocket = new WebSocket(wsUrl);
        
        websocket.onopen = () => {
            // console.log('Connected to comment updates');
            reconnectAttempts = 0;
            showConnectionStatus(true);
        };
        
        websocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        };
        
        websocket.onclose = () => {
            // console.log('Disconnected from comment updates');
            showConnectionStatus(false);
            
            // Attempt to reconnect
            if (reconnectAttempts < maxReconnectAttempts) {
                reconnectAttempts++;
                setTimeout(() => {
                    // console.log(`Reconnection attempt ${reconnectAttempts}/${maxReconnectAttempts}`);
                    connectWebSocket();
                }, reconnectDelay * reconnectAttempts);
            }
        };
        
        websocket.onerror = (error) => {
            // console.error('WebSocket error:', error);
        };
    }
    
    function handleWebSocketMessage(data) {
        switch (data.type) {
            case 'new_comment':
                handleNewComment(data.comment);
                break;
            case 'comment_edited':
                handleCommentEdit(data);
                break;
            case 'comment_deleted':
                handleCommentDelete(data.comment_id);
                break;
            case 'user_typing':
                handleTypingIndicator(data);
                break;
            case 'mention':
                handleMentionNotification(data);
                break;
        }
    }
    
    function handleNewComment(comment) {
        // Check if comment already exists to prevent duplicates
        if (document.querySelector(`.comment-item[data-comment-id="${comment.id}"]`)) {
            return;
        }
        
        const commentList = document.getElementById('commentList');
        if (!commentList) return;
        
        // Remove empty message if exists
        const emptyMessage = commentList.querySelector('.comment-empty');
        if (emptyMessage) emptyMessage.remove();
        
        // Determine where to insert the comment
        if (comment.parent_id) {
            // This is a reply
            handleNewReply(comment);
        } else {
            // This is a top-level comment
            const commentHTML = window.renderCommentItem(comment, [], {});
            commentList.insertAdjacentHTML('afterbegin', commentHTML);
            
            // Add highlight animation
            const newComment = commentList.firstElementChild;
            newComment.classList.add('highlight-new-comment');
            setTimeout(() => {
                newComment.classList.remove('highlight-new-comment');
            }, 2000);
        }
        
        // Update comment count
        window.updateCommentCount(1);
    }
    
    function handleNewReply(reply) {
        const parentComment = document.querySelector(`.comment-item[data-comment-id="${reply.parent_id}"]`);
        if (!parentComment) return;
        
        let repliesContainer = parentComment.querySelector('.comment-replies');
        let repliesToggle = parentComment.querySelector('.replies-toggle');
        
        if (!repliesContainer) {
            // Create replies container if it doesn't exist
            const repliesHTML = `
                <div class="comment-replies-container">
                    <button class="replies-toggle" data-comment-id="${reply.parent_id}">
                        <i class="fas fa-chevron-down"></i> 1 reply
                    </button>
                    <div class="comment-replies"></div>
                </div>
            `;
            parentComment.insertAdjacentHTML('beforeend', repliesHTML);
            repliesContainer = parentComment.querySelector('.comment-replies');
            repliesToggle = parentComment.querySelector('.replies-toggle');
        } else {
            // Update reply count
            const currentReplies = repliesContainer.querySelectorAll('.comment-item').length + 1;
            if (repliesToggle) {
                repliesToggle.innerHTML = `
                    <i class="fas fa-chevron-down"></i> ${currentReplies} ${currentReplies === 1 ? 'reply' : 'replies'}
                `;
            }
        }
        
        // Add the reply
        const replyHTML = window.renderCommentItem(reply, [], {});
        repliesContainer.insertAdjacentHTML('beforeend', replyHTML);
        
        // Show replies container
        repliesContainer.style.display = 'block';
        if (repliesToggle && repliesToggle.classList.contains('collapsed')) {
            repliesToggle.classList.remove('collapsed');
        }
        
        // Highlight new reply
        const newReply = repliesContainer.lastElementChild;
        newReply.classList.add('highlight-new-comment');
        setTimeout(() => {
            newReply.classList.remove('highlight-new-comment');
        }, 2000);
    }
    
    function handleCommentEdit(data) {
        const commentContent = document.querySelector(`.comment-content[data-comment-id="${data.comment_id}"]`);
        if (!commentContent) return;
        
        // Update content with timestamp if present
        let timestampHtml = '';
        if (data.timestamp && data.timestamp > 0) {
            const formattedTime = window.formatTimestamp(data.timestamp);
            timestampHtml = `
                <span class="comment-time-link" data-seconds="${data.timestamp}">
                    <i class="fas fa-clock"></i> ${formattedTime}
                </span>
            `;
        }
        
        commentContent.innerHTML = timestampHtml + data.content;
        
        // Update edited indicator
        const commentItem = commentContent.closest('.comment-item');
        if (commentItem && data.is_edited) {
            const timestampElement = commentItem.querySelector('.comment-timestamp');
            if (timestampElement && !timestampElement.textContent.includes('(edited)')) {
                timestampElement.textContent += ' (edited)';
            }
        }
        
        // Highlight effect
        commentContent.classList.add('highlight-edited');
        setTimeout(() => commentContent.classList.remove('highlight-edited'), 2000);
    }
    
    function handleCommentDelete(commentId) {
        const commentElement = document.querySelector(`.comment-item[data-comment-id="${commentId}"]`);
        if (commentElement) {
            // Animate removal
            commentElement.style.transition = 'opacity 0.3s, transform 0.3s';
            commentElement.style.opacity = '0';
            commentElement.style.transform = 'translateX(-10px)';
            
            setTimeout(() => {
                commentElement.remove();
                window.updateCommentCount(-1);
                
                // Check if no comments left
                const commentList = document.getElementById('commentList');
                if (commentList && commentList.children.length === 0) {
                    commentList.innerHTML = '<div class="comment-empty">No comments yet. Be the first to comment!</div>';
                }
            }, 300);
        }
    }
    
    function handleTypingIndicator(data) {
        if (data.user_id === parseInt(window.currentUserData.id)) return;
        
        if (data.is_typing) {
            typingUsers.set(data.user_id, {
                username: data.username,
                timestamp: Date.now()
            });
        } else {
            typingUsers.delete(data.user_id);
        }
        
        updateTypingDisplay();
    }
    
    function updateTypingDisplay() {
        // Create or get typing indicator element
        let typingIndicator = document.getElementById('commentTypingIndicator');
        if (!typingIndicator) {
            const commentSection = document.getElementById('commentSection');
            if (!commentSection) return;
            
            typingIndicator = document.createElement('div');
            typingIndicator.id = 'commentTypingIndicator';
            typingIndicator.className = 'typing-indicator';
            
            // Insert before comment list
            const commentList = document.getElementById('commentList');
            commentSection.insertBefore(typingIndicator, commentList);
        }
        
        // Clean up old typing indicators (older than 3 seconds)
        const now = Date.now();
        for (const [userId, data] of typingUsers.entries()) {
            if (now - data.timestamp > 3000) {
                typingUsers.delete(userId);
            }
        }
        
        if (typingUsers.size === 0) {
            typingIndicator.style.display = 'none';
            return;
        }
        
        const usernames = Array.from(typingUsers.values()).map(u => u.username);
        let text = '';
        
        if (usernames.length === 1) {
            text = `${usernames[0]} is typing`;
        } else if (usernames.length === 2) {
            text = `${usernames[0]} and ${usernames[1]} are typing`;
        } else {
            text = `${usernames[0]} and ${usernames.length - 1} others are typing`;
        }
        
        typingIndicator.innerHTML = `
            ${text} <span class="typing-dots">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
            </span>
        `;
        typingIndicator.style.display = 'block';
    }
    
    function handleMentionNotification(data) {
        showToast(`${data.from_user} mentioned you in a comment`, 'info', 5000);
        
        // Optionally play a sound
        if ('Audio' in window) {
            const audio = new Audio('/static/sounds/notification.mp3');
            audio.volume = 0.3;
            audio.play().catch(() => {});
        }
    }
    
    function sendTypingIndicator(isTyping) {
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify({
                type: 'typing',
                is_typing: isTyping
            }));
        }
    }
    
    function showConnectionStatus(connected) {
        // You can add a visual indicator for connection status
        const indicator = document.getElementById('connectionIndicator');
        if (indicator) {
            indicator.className = connected ? 'connected' : 'disconnected';
            indicator.title = connected ? 'Connected to live updates' : 'Disconnected from live updates';
        }
    }
    
    // Add typing detection to comment input
    const commentInput = document.getElementById('commentInput');
    if (commentInput) {
        commentInput.addEventListener('input', () => {
            sendTypingIndicator(true);
            clearTimeout(typingTimer);
            typingTimer = setTimeout(() => {
                sendTypingIndicator(false);
            }, 1000);
        });
        
        commentInput.addEventListener('blur', () => {
            clearTimeout(typingTimer);
            sendTypingIndicator(false);
        });
    }
    
    // Connect to WebSocket
    connectWebSocket();
    
    // Clean up on page unload
    window.addEventListener('beforeunload', () => {
        if (websocket) {
            websocket.close();
        }
    });
    
    return {
        pause: () => {
            if (websocket) {
                websocket.close();
            }
        },
        resume: () => {
            if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                connectWebSocket();
            }
        }
    };
};

// Make updateCommentCount globally accessible
window.updateCommentCount = function(delta) {
    const elements = window.getCommentElements();
    if (elements.commentCount && elements.totalCommentCount) {
        const currentCount = parseInt(elements.commentCount.textContent || '0');
        const newCount = Math.max(0, currentCount + delta);
        elements.commentCount.textContent = newCount;
        elements.totalCommentCount.textContent = `(${newCount})`;
    }
};

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================
function showToast(message, type = 'success', duration = 3000) {
    let toast = document.getElementById('toast');
    
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast';
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    
    toast.textContent = message;
    toast.className = `toast visible ${type}`;
    
    if (toast.timeoutId) clearTimeout(toast.timeoutId);
    
    toast.timeoutId = setTimeout(() => {
        toast.classList.remove('visible');
    }, duration);
}

function getTimeAgo(date) {
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);
    
    if (seconds < 60) return 'just now';
    
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} minute${minutes !== 1 ? 's' : ''} ago`;
    
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hour${hours !== 1 ? 's' : ''} ago`;
    
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days} day${days !== 1 ? 's' : ''} ago`;
    
    const months = Math.floor(days / 30);
    if (months < 12) return `${months} month${months !== 1 ? 's' : ''} ago`;
    
    const years = Math.floor(months / 12);
    return `${years} year${years !== 1 ? 's' : ''} ago`;
}

window.formatTimestamp = function(seconds) {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    if (hrs > 0) {
        return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    } else {
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }
};

function parseTimestampInput(input) {
    if (!input) return null;
    
    input = input.trim();
    
    // mm:ss format
    let mmssMatch = input.match(/^(\d+):(\d{1,2})$/);
    if (mmssMatch) {
        const minutes = parseInt(mmssMatch[1], 10);
        const seconds = parseInt(mmssMatch[2], 10);
        if (seconds < 60) return minutes * 60 + seconds;
    }
    
    // hh:mm:ss format
    let hhmmssMatch = input.match(/^(\d+):(\d{1,2}):(\d{1,2})$/);
    if (hhmmssMatch) {
        const hours = parseInt(hhmmssMatch[1], 10);
        const minutes = parseInt(hhmmssMatch[2], 10);
        const seconds = parseInt(hhmmssMatch[3], 10);
        if (minutes < 60 && seconds < 60) {
            return hours * 3600 + minutes * 60 + seconds;
        }
    }
    
    return null;
}

// Add CSS for highlighting and typing indicator
const style = document.createElement('style');
style.textContent = `
    @keyframes highlightNew {
        0% { background-color: rgba(74, 144, 226, 0.3); }
        100% { background-color: transparent; }
    }
    
    @keyframes highlightEdited {
        0% { background-color: rgba(255, 193, 7, 0.3); }
        100% { background-color: transparent; }
    }
    
    .highlight-new-comment {
        animation: highlightNew 2s ease-out;
    }
    
    .highlight-edited {
        animation: highlightEdited 2s ease-out;
    }
    
    /* Typing indicator styles */
    .typing-indicator {
        padding: 0.5rem 1rem;
        font-size: 0.85rem;
        color: var(--text-secondary);
        font-style: italic;
        min-height: 1.5rem;
        margin-bottom: 0.5rem;
    }
    
    .typing-dots {
        display: inline-flex;
        gap: 0.2rem;
    }
    
    .typing-dot {
        width: 4px;
        height: 4px;
        border-radius: 50%;
        background: var(--text-secondary);
        animation: typingBounce 1.4s infinite ease-in-out;
    }
    
    .typing-dot:nth-child(2) { animation-delay: 0.2s; }
    .typing-dot:nth-child(3) { animation-delay: 0.4s; }
    
    @keyframes typingBounce {
        0%, 60%, 100% { transform: translateY(0); }
        30% { transform: translateY(-8px); }
    }
    
    /* Connection indicator */
    .connection-indicator {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-left: 0.5rem;
        display: inline-block;
    }
    
    .connection-indicator.connected {
        background: #10b981;
    }
    
    .connection-indicator.disconnected {
        background: #ef4444;
    }
`;
document.head.appendChild(style);