const BadgeManager = {
  config: {
    endpoint: '/api/notifications/pending-count',
    notificationEndpoint: '/api/notifications',
    forumNotificationEndpoint: '/api/forum/notifications',
    pollInterval: 30000,
    badgeSelectors: {
      desktopAdmin: '.nav-link.dropdown-toggle[id="adminDropdown"]',
      desktopMenuItem: '.dropdown-item[href="/admin/book-requests"]',
      mobileSidebar: '.side-nav-item[href="/admin/book-requests"]',
      mobileQuickIcon: '.mobile-nav-icon.manage-requests-icon'
    },
    notificationSound: '/static/sounds/notification.mp3',
  },

  state: {
    count: 0,
    notificationCount: 0,
    forumNotificationCount: 0,
    socket: null,
    wsSocket: null,
    isWsConnected: false,
    wsReconnectAttempts: 0,
    pollTimer: null,
    healthCheckTimer: null,
    sessionCheckTimer: null,
    notifications: [],
    forumNotifications: [],
    isPanelVisible: false,
    errorsDetected: false,
    userInForum: false,
    suppressForumBadges: false,
    notificationFilter: 'all',
    currentPage: 1,
    hasMoreNotifications: false,
    isLoadingMore: false,
    perPage: 50  // BATCH SIZE: 50 notifications per load
  },

  init: function() {
    // Clean up any duplicate buttons first
    this.cleanupDuplicateButtons();
    
    this.initNotificationPanel();
    this.initForumAwareness();
    
    if (window.currentPendingRequests && window.currentPendingRequests > 0) {
      this.state.count = window.currentPendingRequests;
      this.updateBadges();
      this.updateMobileQuickNavBadge();
    }
    
    const isCreatorOrTeam = (window.isCreator || window.isTeam);

    this.setupWebSocketNotifications();
    this.setupPolling();

    if (this.hasValidSession()) {
      this.setupWebSocket();
      this.setupWebSocketHealthCheck();
    }

    // ✅ Setup book request WebSocket for live badge updates
    if (isCreatorOrTeam) {
      this.setupBookRequestWebSocket();
    }

    this.setupSessionChecker();
    if (isCreatorOrTeam) this.setupFormListeners();
    this.setupNotificationHandlers();
    this.requestNotificationPermission();
    
    if (window.ForumNotificationManager) {
      window.ForumNotificationManager.fetchCount();
    }
  },

  // CLEANUP: Remove duplicate notification buttons
  cleanupDuplicateButtons: function() {
    // Remove any duplicate mark all read buttons
    const existingMarkAllBtns = document.querySelectorAll('#markAllReadBtn, .mark-all-read-btn, [data-action="mark-all-read"]');
    if (existingMarkAllBtns.length > 1) {
      // console.log(`🧹 Found ${existingMarkAllBtns.length} mark all read buttons, removing duplicates`);
      for (let i = 1; i < existingMarkAllBtns.length; i++) {
        existingMarkAllBtns[i].remove();
      }
    }
    
    // Remove any duplicate delete read buttons
    const existingDeleteBtns = document.querySelectorAll('#deleteReadBtn, .delete-read-btn, [data-action="delete-read"]');
    if (existingDeleteBtns.length > 1) {
      // console.log(`🧹 Found ${existingDeleteBtns.length} delete read buttons, removing duplicates`);
      for (let i = 1; i < existingDeleteBtns.length; i++) {
        existingDeleteBtns[i].remove();
      }
    }
    
    // Remove any old notification modals that might conflict
    const oldModals = document.querySelectorAll('#notificationsModal, .notification-modal-old');
    oldModals.forEach(modal => {
      // console.log('🧹 Removing old notification modal');
      modal.remove();
    });
  },

  // DEBUG: Add debugging for delete operations
  debugDeleteOperation: function() {
    // console.log('🐛 DEBUG: Current notification state before delete:');
    // console.log('📊 General notifications (read):', this.state.notifications.filter(n => n.is_read).length);
    // console.log('📊 Forum notifications (read):', this.state.forumNotifications.filter(n => n.is_read).length);
    // console.log('📊 General notifications (unread):', this.state.notifications.filter(n => !n.is_read).length);
    // console.log('📊 Forum notifications (unread):', this.state.forumNotifications.filter(n => !n.is_read).length);
    
    // Test the delete endpoint
    fetch('/api/notifications/debug/state')
      .then(response => response.json())
      .then(data => {
        // console.log('🐛 Backend notification state:', data);
      })
      .catch(error => {
        // console.error('❌ Error getting debug state:', error);
      });
  },

  initForumAwareness: function() {
    this.updateForumStatus();
    
    if (window.forumDetector) {
      window.forumDetector.onStatusChange((inForum, wasInForum) => {
        // console.log(`🔔 BadgeManager: Forum status changed - ${wasInForum ? 'was' : 'not'} in forum -> ${inForum ? 'now' : 'not'} in forum`);
        this.handleForumStatusChange(inForum, wasInForum);
      });
    }
    
    setInterval(() => {
      this.updateForumStatus();
    }, 2000);
    
    // console.log('🔔 BadgeManager: Forum awareness initialized');
  },

  updateForumStatus: function() {
    const wasInForum = this.state.userInForum;
    
    const inForum = 
      (window.forumDetector && window.forumDetector.isInForum) ||
      window.userInForum ||
      document.body.getAttribute('data-forum-active') === 'true' ||
      window.location.pathname.includes('/forum') ||
      window.location.pathname.includes('/api/forum');
    
    if (wasInForum !== inForum) {
      this.handleForumStatusChange(inForum, wasInForum);
    }
  },

  handleForumStatusChange: function(inForum, wasInForum) {
    this.state.userInForum = inForum;
    this.state.suppressForumBadges = inForum;
    
    if (inForum) {
      // console.log('🔔 BadgeManager: User ENTERED forum - suppressing forum notification badges');
      this.suppressForumNotificationBadges();
    } else {
      // console.log('🔔 BadgeManager: User LEFT forum - restoring forum notification badges');
      this.restoreForumNotificationBadges();
    }
  },

  suppressForumNotificationBadges: function() {
    const forumNotificationElements = [
      ...document.querySelectorAll('.notifications-link'),
      ...document.querySelectorAll('[href="/api/forum"]'),
      ...document.querySelectorAll('[href*="forum"]')
    ];
    
    forumNotificationElements.forEach(element => {
      const badge = element.querySelector('.notification-badge');
      if (badge && this.isForumRelatedBadge(element)) {
        badge.style.display = 'none';
        badge.setAttribute('data-suppressed', 'true');
      }
    });
    
    if (window.ForumNotificationManager) {
      window.ForumNotificationManager.suppressBadges();
    }
  },

  restoreForumNotificationBadges: function() {
    const forumNotificationElements = [
      ...document.querySelectorAll('.notifications-link'),
      ...document.querySelectorAll('[href="/api/forum"]'),
      ...document.querySelectorAll('[href*="forum"]')
    ];
    
    forumNotificationElements.forEach(element => {
      const badge = element.querySelector('.notification-badge[data-suppressed="true"]');
      if (badge && this.isForumRelatedBadge(element)) {
        if (this.state.forumNotificationCount > 0) {
          badge.style.display = 'flex';
        }
        badge.removeAttribute('data-suppressed');
      }
    });
    
    this.updateForumNotificationBadges();
    
    if (window.ForumNotificationManager) {
      window.ForumNotificationManager.restoreBadges(this.state.forumNotificationCount);
    }
  },

  isForumRelatedBadge: function(element) {
    const href = element.getAttribute('href');
    const text = element.textContent.toLowerCase();
    
    return href && (
      href.includes('forum') ||
      text.includes('forum') ||
      element.classList.contains('forum-notification') ||
      element.closest('.forum-nav')
    );
  },

  initNotificationPanel: function() {
    if (document.getElementById('notificationPanel')) return;
    
    const panel = document.createElement('div');
    panel.id = 'notificationPanel';
    panel.className = 'notification-panel';
    panel.innerHTML = `
      <div class="notification-header">
        <h3><i class="fas fa-bell"></i> Notifications</h3>
        <div class="notification-actions">
          <button id="closeNotificationsBtn"><i class="fas fa-times"></i></button>
        </div>
      </div>
      
      <div class="notification-filters">
        <div class="filter-buttons">
          <button class="filter-btn active" data-filter="all">All</button>
          <button class="filter-btn" data-filter="unread">Unread</button>
          <button class="filter-btn" data-filter="read">Read</button>
        </div>
        <div class="bulk-actions">
          <button id="deleteReadBtn">Delete read</button>
        </div>
      </div>
      
      <div id="notificationList" class="notification-list">
        <div class="notification-loading">
          <i class="fas fa-spinner fa-spin"></i>
          <p>Loading notifications...</p>
        </div>
      </div>
    `;
    document.body.appendChild(panel);
  },

  updateForumNotificationBadges: function() {
    const count = window.enhancedForumNotificationManager 
      ? window.enhancedForumNotificationManager.totalUnreadCount 
      : this.state.forumNotificationCount;
    
    // console.log(`🔔 Updating forum badges with count: ${count}`);
    
    const forumLinks = document.querySelectorAll(
      'a[href*="/forum"], a[href*="/api/forum"], .back-to-forum, [data-forum-back]'
    );
    
    forumLinks.forEach(link => {
      this.addBadgeToElement(link, count);
    });
    
    this.updateForumUIBadges(count);
  },

  addBadgeToElement: function(element, count) {
    if (!element) return;
    
    if (getComputedStyle(element).position === 'static') {
      element.style.position = 'relative';
    }
    
    let badge = element.querySelector(':scope > .forum-notification-badge, :scope > .notification-badge');
    
    if (count > 0) {
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'forum-notification-badge';
        element.appendChild(badge);
      }
      badge.textContent = count > 99 ? '99+' : count;
      badge.style.display = 'flex';
      
      badge.classList.add('badge-pulse');
      setTimeout(() => badge.classList.remove('badge-pulse'), 800);
    } else if (badge) {
      badge.style.display = 'none';
    }
  },

  updateForumUIBadges: function(count) {
    if (this.state.suppressForumBadges) {
      return;
    }
    
    if (window.forum && typeof window.forum.updateNotificationBadges === 'function') {
      window.forum.updateNotificationBadges(count);
    }
  },

  navigateToForumNotification: function(notificationId, threadId, messageId) {
    this.markForumNotificationRead(notificationId);
    
    const forumUrl = `/api/forum#thread-${threadId}`;
    if (messageId) {
      window.location.href = `${forumUrl}&message=${messageId}`;
    } else {
      window.location.href = forumUrl;
    }
  },

  markForumNotificationRead: function(notificationId) {
    fetch(`/api/forum/notifications/${notificationId}/read`, {
      method: 'POST'
    }).then(response => {
      if (response.ok) {
        return response.json();
      }
    }).then(data => {
      if (data) {
        this.state.forumNotificationCount = data.unread_count;
        this.updateForumNotificationBadges();
      }
    }).catch(error => {
      // console.error('Error marking forum notification as read:', error);
    });
    
    if (this.state.forumNotificationCount > 0) {
      this.state.forumNotificationCount--;
      this.updateForumNotificationBadges();
    }
  },

  fetchNotifications: function(loadMore = false) {
    const skip = loadMore ? (this.state.currentPage - 1) * this.state.perPage : 0;
    const endpoint = `${this.config.notificationEndpoint}/list?limit=${this.state.perPage}&skip=${skip}`;
    
    if (!this.state.isWsConnected || loadMore) {
      if (loadMore) {
        this.state.isLoadingMore = true;
      }
      
      fetch(endpoint)
        .then(response => response.json())
        .then(data => {
          const oldCount = this.state.notificationCount;
          
          if (loadMore) {
            this.state.notifications = [...this.state.notifications, ...(data.notifications || [])];
            this.state.isLoadingMore = false;
          } else {
            this.state.notifications = data.notifications || [];
          }
          
          this.state.notificationCount = data.unread_count || 0;
          this.state.hasMoreNotifications = data.has_more || false;
          this.state.currentPage = data.current_page || 1;
          
          this.updateNotificationBadges();
          if (this.state.isPanelVisible) this.renderNotifications();
          if (this.state.notificationCount > oldCount && oldCount !== 0 && !loadMore) {
            this.playNotificationSound();
          }
        })
        .catch(() => {
          this.state.notifications = [];
          this.state.notificationCount = 0;
          this.state.isLoadingMore = false;
          if (this.state.isPanelVisible) this.renderNotifications();
        });
    }
    
    fetch(`${this.config.forumNotificationEndpoint}`)
      .then(response => response.json())
      .then(data => {
        const oldForumCount = this.state.forumNotificationCount;
        this.state.forumNotifications = data.notifications || [];
        this.state.forumNotificationCount = data.unread_count || 0;
        
        this.updateForumNotificationBadges();
        
        if (this.state.forumNotificationCount > oldForumCount && oldForumCount !== 0) {
          if (!this.state.suppressForumBadges) {
            this.playNotificationSound();
          } else {
            // console.log('🔔 BadgeManager: Forum notification received but sound suppressed (user in forum)');
          }
        }
      })
      .catch(() => {
        this.state.forumNotifications = [];
        this.state.forumNotificationCount = 0;
      });
  },

  loadMoreNotifications: function() {
    if (this.state.isLoadingMore || !this.state.hasMoreNotifications) {
      return;
    }
    
    // console.log(`🔔 Loading more notifications, page ${this.state.currentPage + 1}`);
    this.state.currentPage++;
    this.fetchNotifications(true);
  },

  handleWebSocketNotificationMessage: function(data) {
    switch (data.type) {
      case 'connected':
        // console.log('🔔 Notification WebSocket authenticated');
        break;
        
      case 'initial_data':
        this.state.notifications = data.notifications || [];
        this.state.notificationCount = data.unread_count || 0;
        this.updateNotificationBadges();
        
        this.coordinateForumNotifications(data.notifications || []);
        
        if (this.state.isPanelVisible) this.renderNotifications();
        // console.log(`📊 Loaded ${this.state.notifications.length} notifications via WebSocket`);
        break;
        
      case 'new_notification':
        const notification = data.notification;
        
        const isForumNotification = this.isNotificationFromForum(notification);
        
        if (isForumNotification) {
          const threadId = this.getForumThreadId(notification);
          const isViewingThisThread = (
            window.forum?.currentThread?.id === threadId && 
            window.forum?.currentView === 'discussion'
          );
          
          if (isViewingThisThread) {
            // console.log(`🚫 Suppressing notification for current thread ${threadId}`);
            return;
          }
          
          this.state.forumNotifications.unshift(notification);
          this.state.forumNotificationCount++;
          
          this.updateEnhancedForumManager(notification, 'add');
          
          if (!this.state.suppressForumBadges) {
            this.state.notifications.unshift(notification);
            this.state.notificationCount++;
            this.updateNotificationBadges();
            this.playNotificationSound();
            // console.log('🔔 Forum notification processed (user not in forum)');
          } else {
            // console.log('🔔 Forum notification stored silently (user in forum - badges suppressed)');
          }
          
          this.updateForumNotificationBadges();
        } else {
          this.state.notifications.unshift(notification);
          this.state.notificationCount++;
          this.updateNotificationBadges();
          this.playNotificationSound();
          // console.log('🔔 General notification processed:', notification.content);
        }
        
        if (this.state.isPanelVisible) this.renderNotifications();
        break;
        
      case 'notification_count':
        this.state.notificationCount = data.count;
        this.updateNotificationBadges();
        break;
        
      case 'badge_count':
        this.state.count = data.count;
        this.updateBadges();
        this.updateMobileQuickNavBadge();
        break;

      case 'activity_log_count_update':
        // Update activity logs badge
        // console.log(`📊 Activity logs count update received: ${data.count}`);
        if (window.updateActivityLogsBadge) {
          window.updateActivityLogsBadge(data.count);
        }
        break;

      default:
        // console.log('📡 Unknown WebSocket notification message:', data.type);
    }
  },

  updateEnhancedForumManager: function(notification, action = 'add') {
    if (!window.enhancedForumNotificationManager) {
      // console.log('🔔 EnhancedForumNotificationManager not available for coordination');
      return;
    }
    
    const threadId = this.getForumThreadId(notification);
    if (!threadId) {
      // console.log('🔔 No thread ID found in notification:', notification);
      return;
    }
    
    // console.log(`🔔 Coordinating with EnhancedForumNotificationManager: ${action} notification for thread ${threadId}`);
    
    if (action === 'add') {
      const currentCount = window.enhancedForumNotificationManager.threadNotifications.get(threadId) || 0;
      window.enhancedForumNotificationManager.threadNotifications.set(threadId, currentCount + 1);
      window.enhancedForumNotificationManager.recomputeTotal();
      
      // console.log(`🔔 Updated thread ${threadId} count to ${currentCount + 1}, total: ${window.enhancedForumNotificationManager.totalUnreadCount}`);
    } else if (action === 'remove') {
      const currentCount = window.enhancedForumNotificationManager.threadNotifications.get(threadId) || 0;
      if (currentCount > 0) {
        window.enhancedForumNotificationManager.threadNotifications.set(threadId, currentCount - 1);
        if (currentCount - 1 <= 0) {
          window.enhancedForumNotificationManager.threadNotifications.delete(threadId);
        }
        window.enhancedForumNotificationManager.recomputeTotal();
      }
    }
  },

  getForumThreadId: function(notification) {
    if (!notification) return null;
    
    return +(
      notification.notification_data?.thread_id ||
      notification.thread_id ||
      (notification.content?.match(/thread[ _](\d+)/i)?.[1] ?? 0)
    );
  },

  coordinateForumNotifications: function(notifications) {
    if (!window.enhancedForumNotificationManager) {
      // console.log('🔔 EnhancedForumNotificationManager not available for initial coordination');
      return;
    }
    
    // console.log(`🔔 Coordinating ${notifications.length} initial notifications with EnhancedForumNotificationManager`);
    
    window.enhancedForumNotificationManager.processInitialData(
      notifications.filter(n => this.isNotificationFromForum(n))
    );
  },

  isNotificationFromForum: function(notification) {
    if (!notification) return false;
    
    if (notification.notification_data && notification.notification_data.source === 'forum') {
      return true;
    }
    
    if (notification.title && notification.title.startsWith('[Forum]')) {
      return true;
    }
    
    if (notification.content && notification.content.includes('thread_id')) {
      return true;
    }
    
    return false;
  },

  // FIXED: renderNotifications - removed duplicate mark all read button
  renderNotifications: function() {
    const listElement = document.getElementById('notificationList');
    if (!listElement) return;
    
    const allNotifications = [
      ...this.state.notifications.map(n => ({...n, source: 'general'})),
      ...this.state.forumNotifications.map(n => ({...n, source: 'forum'}))
    ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    
    if (!allNotifications.length) {
      listElement.innerHTML = `
        <div class="no-notifications">
          <i class="fas fa-bell-slash"></i>
          <p>No notifications yet</p>
        </div>
      `;
      return;
    }
    
    const filteredNotifications = allNotifications.filter(notification => {
      const isUnread = !notification.is_read;
      
      if (this.state.notificationFilter === 'unread' && !isUnread) {
        return false;
      } else if (this.state.notificationFilter === 'read' && isUnread) {
        return false;
      }
      return true;
    });
    
    // FIXED: Single set of action buttons (removed mark all read button)
    const filterHTML = `
      <div class="notification-filters">
        <div class="filter-buttons">
          <button class="filter-btn ${this.state.notificationFilter === 'all' ? 'active' : ''}" data-filter="all">
            All (${allNotifications.length})
          </button>
          <button class="filter-btn ${this.state.notificationFilter === 'unread' ? 'active' : ''}" data-filter="unread">
            Unread (${allNotifications.filter(n => !n.is_read).length})
          </button>
          <button class="filter-btn ${this.state.notificationFilter === 'read' ? 'active' : ''}" data-filter="read">
            Read (${allNotifications.filter(n => n.is_read).length})
          </button>
        </div>
        <div class="bulk-actions">
          <button id="deleteReadBtn" ${allNotifications.filter(n => n.is_read).length === 0 ? 'disabled' : ''}>
            <i class="fas fa-trash"></i> Delete Read
          </button>
        </div>
      </div>
    `;
    
    let notificationsHTML = filteredNotifications.map(notification => {
      const isUnread = !notification.is_read;
      const unreadClass = isUnread ? 'unread' : '';
      const icon = this.getNotificationIcon(notification.type || notification.notification_type, notification);
      const sourceIcon = notification.source === 'forum' ? '<i class="fas fa-comments forum-source-icon"></i>' : '';

      return `
        <div class="notification-item ${unreadClass}" data-id="${notification.id}" data-type="${notification.type || notification.notification_type}" data-source="${notification.source}">
          <div class="notification-icon"><i class="${icon}"></i></div>
          <div class="notification-content">
            <div class="notification-title-wrapper">
              ${sourceIcon}
              <span class="notification-title">${notification.title || this.getNotificationTitle(notification.type || notification.notification_type, notification.source)}</span>
            </div>
            <p>${notification.content}</p>
            <span class="notification-time">${notification.time_since || this.getTimeAgo(new Date(notification.created_at))}</span>
          </div>
          <button class="notification-delete" title="Delete notification">
            <i class="fas fa-times"></i>
          </button>
        </div>
      `;
    }).join('');
    
    // INFINITE SCROLL: Add "Load More" button if there are more notifications
    if (this.state.hasMoreNotifications && this.state.notificationFilter === 'all') {
      notificationsHTML += `
        <div class="load-more-container" style="padding: 1rem; text-align: center; border-top: 1px solid var(--border-color);">
          ${this.state.isLoadingMore ? 
            '<i class="fas fa-spinner fa-spin"></i> Loading more...' : 
            '<button id="loadMoreBtn" style="background: var(--accent-color); color: white; border: none; padding: 0.5rem 1rem; border-radius: 0.25rem; cursor: pointer;">Load More Notifications</button>'
          }
        </div>
      `;
    }
    
    listElement.innerHTML = filterHTML + '<div class="notification-list-inner">' + notificationsHTML + '</div>';
    
    this.attachNotificationListeners();
  },

  attachNotificationListeners: function() {
    const listElement = document.getElementById('notificationList');
    if (!listElement) return;
    
    const self = this;
    
    // INFINITE SCROLL: Attach load more button listener
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener('click', function() {
        self.loadMoreNotifications();
      });
    }
    
    listElement.querySelectorAll('.notification-item').forEach(item => {
      const deleteBtn = item.querySelector('.notification-delete');
      const contentArea = item.querySelector('.notification-content');
      
      if (deleteBtn) {
        deleteBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          const id = parseInt(item.dataset.id);
          const source = item.dataset.source;
          self.deleteNotification(id, source, item);
        });
      }
      
      if (contentArea) {
        contentArea.addEventListener('click', function(e) {
          e.preventDefault();
          
          const id = parseInt(item.dataset.id);
          const source = item.dataset.source;
          const notification = source === 'forum' ? 
            self.state.forumNotifications.find(n => n.id === id) :
            self.state.notifications.find(n => n.id === id);
            
          if (!notification) return;
          
          if (!notification.is_read) {
            item.classList.remove('unread');
            notification.is_read = true;
            
            if (source === 'forum') {
              if (self.state.forumNotificationCount > 0) {
                self.state.forumNotificationCount--;
                self.updateForumNotificationBadges();
              }
              self.markForumNotificationRead(id);
            } else {
              if (self.state.notificationCount > 0) {
                self.state.notificationCount--;
                self.updateNotificationBadges();
              }
              fetch(`${self.config.notificationEndpoint}/${id}/read`, {
                method: 'POST',
                keepalive: true
              }).catch(() => { self.state.errorsDetected = true; });
            }
          }
          
          const destination = source === 'forum' ? 
            self.getForumNotificationDestination(notification) :
            self.getDestinationForNotification(notification);
            
          self.hideNotificationPanel();
          
          if (destination) {
            setTimeout(() => window.location.href = destination, 50);
          }
        });
      }
    });
  },

  deleteNotification: function(notificationId, source, itemElement) {
    if (!confirm('Delete this notification?')) return;
    
    const endpoint = source === 'forum' 
      ? `${this.config.forumNotificationEndpoint}/${notificationId}`
      : `${this.config.notificationEndpoint}/${notificationId}`;
    
    fetch(endpoint, { method: 'DELETE' })
      .then(response => response.json())
      .then(data => {
        if (data.success) {
          if (source === 'forum') {
            this.state.forumNotifications = this.state.forumNotifications.filter(n => n.id !== notificationId);
            this.state.forumNotificationCount = Math.max(0, this.state.forumNotificationCount - 1);
            this.updateForumNotificationBadges();
          } else {
            this.state.notifications = this.state.notifications.filter(n => n.id !== notificationId);
            this.state.notificationCount = data.unread_count || 0;
            this.updateNotificationBadges();
          }
          
          itemElement.remove();
          
          if (!document.querySelectorAll('.notification-item:not(.filtered-out)').length) {
            this.renderNotifications();
          }
        }
      })
      .catch(error => {
        // console.error('Error deleting notification:', error);
        alert('Failed to delete notification');
      });
  },

  // FIXED: Enhanced deleteAllRead with debugging and single endpoint call
  deleteAllRead: function() {
    // Add debugging
    if (window.DEBUG_NOTIFICATIONS) {
      this.debugDeleteOperation();
    }
    
    // Get total read count from combined notifications
    const allNotifications = [
      ...this.state.notifications.map(n => ({...n, source: 'general'})),
      ...this.state.forumNotifications.map(n => ({...n, source: 'forum'}))
    ];
    
    const readCount = allNotifications.filter(n => n.is_read).length;
    const readGeneral = this.state.notifications.filter(n => n.is_read).length;
    const readForum = this.state.forumNotifications.filter(n => n.is_read).length;
    
    // console.log(`🗑️ Delete request: ${readCount} total (${readGeneral} general + ${readForum} forum)`);
                        
    if (!readCount) {
      alert('No read notifications to delete');
      return;
    }
    
    if (!confirm(`Delete all ${readCount} read notifications?`)) return;
    
    // Show loading state
    const deleteBtn = document.getElementById('deleteReadBtn');
    if (deleteBtn) {
      deleteBtn.disabled = true;
      deleteBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deleting...';
    }
    
    // FIXED: Only call main delete endpoint (since forum notifications are now in main table)
    fetch(`${this.config.notificationEndpoint}/delete-read`, { 
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json'
      }
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return response.json();
      })
      .then(data => {
        // console.log('🗑️ Delete response:', data);
        
        if (data.success) {
          // Update both notification arrays to remove read items
          const beforeGeneral = this.state.notifications.length;
          const beforeForum = this.state.forumNotifications.length;
          
          this.state.notifications = this.state.notifications.filter(n => !n.is_read);
          this.state.forumNotifications = this.state.forumNotifications.filter(n => !n.is_read);
          
          const afterGeneral = this.state.notifications.length;
          const afterForum = this.state.forumNotifications.length;
          
          // console.log(`✅ Deleted notifications: General ${beforeGeneral}→${afterGeneral}, Forum ${beforeForum}→${afterForum}`);
          
          // Update all badges
          this.updateNotificationBadges();
          this.updateForumNotificationBadges();
          
          // Show success message
          const deletedCount = data.deleted_count || readCount;
          // console.log(`✅ Successfully deleted ${deletedCount} read notifications`);
          
        } else {
          // console.error('❌ Server error deleting notifications:', data.error);
          alert(`Failed to delete notifications: ${data.error || 'Unknown error'}`);
        }
      })
      .catch(error => {
        // console.error('❌ Network error deleting notifications:', error);
        alert(`Failed to delete notifications: ${error.message}`);
      })
      .finally(() => {
        // Restore button state
        if (deleteBtn) {
          deleteBtn.disabled = false;
          deleteBtn.innerHTML = '<i class="fas fa-trash"></i> Delete Read';
        }
        
        // Re-render notifications
        setTimeout(() => this.renderNotifications(), 100);
      });
  },

  setNotificationFilter: function(filter) {
    // console.log(`🔔 Setting filter to: ${filter}`);
    this.state.notificationFilter = filter;
    
    document.querySelectorAll('.filter-btn').forEach(btn => {
      btn.classList.remove('active');
      if (btn.dataset.filter === filter) {
        btn.classList.add('active');
      }
    });
    
    this.renderNotifications();
  },

  getForumNotificationDestination: function(notification) {
    if (!notification.thread_id) return null;
    
    let url = `/api/forum#thread-${notification.thread_id}`;
    if (notification.message_id) {
      url += `&message=${notification.message_id}`;
    }
    return url;
  },

  getNotificationTitle: function(type, source = 'general') {
    if (source === 'forum') {
      const forumTitles = {
        'new_message': 'New Message',
        'reply': 'New Reply',
        'mention': 'You were mentioned',
        'thread_update': 'Thread Updated'
      };
      return forumTitles[type] || 'Forum Notification';
    }
    
    const titles = {
      'comment': 'New Comment',
      'reply': 'New Reply',
      'like': 'New Like',
      'share': 'New Share',
      'comment_like': 'Comment Liked',
      'mention': 'You were mentioned',
      'new_content': 'New Content',
      'tier_update': 'Tier Updated',
      'system': 'System Notification'
    };
    return titles[type] || 'Notification';
  },

  // SIMPLIFIED: markAllRead function - removed redundant forum call
  markAllRead: function() {
    // Mark local state as read first for immediate UI update
    this.state.notifications.forEach(n => n.is_read = true);
    this.state.forumNotifications.forEach(n => n.is_read = true);
    this.state.notificationCount = 0;
    this.state.forumNotificationCount = 0;
    
    // Update badges immediately
    this.updateNotificationBadges();
    this.updateForumNotificationBadges();
    
    // Re-render notifications
    this.renderNotifications();
    
    // Send WebSocket mark all read if available
    if (this.markAllAsReadWS()) {
      // console.log('✅ Mark all read sent via WebSocket');
    } else {
      // Fallback to HTTP call (only main endpoint since forum notifications are in main table)
      fetch(`${this.config.notificationEndpoint}/mark-all-read`, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
          if (data.success) {
            // console.log(`✅ Marked ${data.marked_read || 0} notifications as read`);
          } else {
            // console.error('❌ Error marking notifications as read:', data.error);
          }
        })
        .catch(() => {
          this.state.errorsDetected = true;
          // console.error('❌ Network error marking notifications as read');
          // Refresh notifications after delay to get correct state
          setTimeout(() => this.fetchNotifications(), 2000);
        });
    }
  },

  getNotificationIcon: function(type, notification = null) {
    if (notification && notification.source === 'forum') {
      const forumIcons = {
        'new_message': 'fas fa-comment',
        'reply': 'fas fa-reply',
        'mention': 'fas fa-at',
        'thread_update': 'fas fa-cog'
      };
      return forumIcons[type] || 'fas fa-comments';
    }
    
    if (type === 'system' && 
        notification?.notification_data?.book_request_status) {
      const status = notification.notification_data.book_request_status;
      
      switch (status) {
        case 'approved': return 'fas fa-check-circle';
        case 'rejected': return 'fas fa-times-circle';
        case 'fulfilled': return 'fas fa-check-double';
        case 'accepted': return 'fas fa-user-clock';
        default: return 'fas fa-book';
      }
    }
    
    const icons = {
      comment: 'fas fa-comment',
      reply: 'fas fa-reply',
      like: 'fas fa-heart',
      share: 'fas fa-share-alt',
      comment_like: 'fas fa-thumbs-up',
      mention: 'fas fa-at',
      new_content: 'fas fa-music',
      tier_update: 'fas fa-star',
      system: 'fas fa-cog'
    };
    return icons[type] || 'fas fa-bell';
  },

  hasValidSession: function() {
    const cookies = document.cookie.split(';');
    for (let cookie of cookies) {
      cookie = cookie.trim();
      if (cookie.startsWith('session=') || cookie.startsWith('session_id=')) {
        return true;
      }
    }
    return false;
  },

  setupSessionChecker: function() {
    if (this.state.sessionCheckTimer) clearInterval(this.state.sessionCheckTimer);
    this.state.sessionCheckTimer = setInterval(() => this.checkSessionAndReconnect(), 60000);
  },

  checkSessionAndReconnect: function() {
    const hasSession = this.hasValidSession();
    if (hasSession && (!this.state.socket || this.state.socket.readyState !== WebSocket.OPEN)) {
      this.setupWebSocket();
    }
    if (!hasSession) this.setupPolling();
  },

  fetchCount: function() {
    fetch(this.config.endpoint)
      .then(response => response.json())
      .then(data => {
        const newCount = data.count;
        if (newCount !== this.state.count) {
          this.state.count = newCount;
          this.updateBadges();
          this.updateMobileQuickNavBadge();
        }
      })
      .catch(error => console.error('Error fetching count:', error));
  },

  updateMobileQuickNavBadge: function() {
    const count = this.state.count;
    const mobileNavIcon = document.querySelector(this.config.badgeSelectors.mobileQuickIcon);
    if (!mobileNavIcon) return;
    
    const faLayers = mobileNavIcon.querySelector('.fa-layers');
    if (!faLayers) return;
    
    let badge = faLayers.querySelector('.notification-badge');
    if (!badge && count > 0) {
      badge = document.createElement('span');
      badge.className = 'notification-badge';
      faLayers.appendChild(badge);
    }
    
    if (badge) {
      if (count > 0) {
        badge.textContent = count;
        badge.style.display = 'flex';
      } else {
        badge.style.display = 'none';
      }
    }
  },

  updateBadges: function() {
    const count = this.state.count;
    const containers = [
      document.querySelector(this.config.badgeSelectors.desktopAdmin),
      document.querySelector(this.config.badgeSelectors.desktopMenuItem),
      document.querySelector(this.config.badgeSelectors.mobileSidebar)
    ].filter(el => el !== null);

    containers.forEach(container => {
      let badge = container.querySelector('.notification-badge');

      if (!badge && count > 0) {
        badge = document.createElement('span');
        badge.className = 'notification-badge';
        container.appendChild(badge);
      }

      if (badge) {
        if (count > 0) {
          badge.textContent = count;
          badge.style.display = 'flex';
        } else {
          badge.style.display = 'none';
        }
      }
    });

    this.updateMobileQuickNavBadge();

    // Update admin dropdown badge (sum of all admin badges)
    if (window.updateAdminDropdownBadge) {
      window.updateAdminDropdownBadge();
    }
  },

  updateNotificationBadges: function() {
    const count = this.state.notificationCount;
    const notificationLinks = document.querySelectorAll('.notifications-link');
    
    notificationLinks.forEach(link => {
      const container = link.closest('.desktop-nav-icon-wrapper') || 
                        link.closest('.mobile-nav-icon-wrapper') || link;
      
      let badge = container.querySelector('.notification-badge') || 
                  link.querySelector('.notification-badge');
      
      if (!badge && count > 0) {
        badge = document.createElement('span');
        badge.className = 'notification-badge';
        container.appendChild(badge);
      }
      
      if (badge) {
        if (count > 0) {
          badge.textContent = count;
          badge.style.display = 'flex';
          badge.classList.add('pulse');
          setTimeout(() => badge.classList.remove('pulse'), 1000);
        } else {
          badge.style.display = 'none';
          badge.textContent = '';
        }
      }
    });
  },

  setupWebSocketHealthCheck: function() {
    if (this.state.healthCheckTimer) clearInterval(this.state.healthCheckTimer);
    
    this.state.healthCheckTimer = setInterval(() => {
      if (!this.state.socket || this.state.socket.readyState !== WebSocket.OPEN) {
        if (this.hasValidSession()) this.setupWebSocket();
        this.setupPolling();
      } else {
        try {
          this.state.socket.send('ping');
        } catch (e) {
          this.setupWebSocket();
          this.setupPolling();
        }
      }
    }, 30000);
  },

  setupWebSocket: function() {
    if (!('WebSocket' in window) || !this.hasValidSession()) return;
    
    try {
      if (this.state.socket) this.state.socket.close();

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/api/notifications/ws`;
      
      this.state.socket = new WebSocket(wsUrl);
      
      this.state.socket.onmessage = (event) => {
        try {
          if (typeof event.data === 'string' && !event.data.startsWith('{')) {
            if (event.data === 'ping') this.state.socket.send('pong');
            return;
          }
          
          const data = JSON.parse(event.data);
          
          if (data.type === 'auth' && data.status === 'error') {
            this.setupPolling();
            return;
          }
          
          switch (data.type) {
            case 'book_request':
              if (data.action === 'created') this.fetchCount();
              break;
            case 'badge_count':
              this.state.count = data.count;
              this.updateBadges();
              this.updateMobileQuickNavBadge();
              break;
            case 'notification_count':
              this.state.notificationCount = data.count;
              this.updateNotificationBadges();
              break;
            case 'new_notification':
              this.state.notifications.unshift(data.notification);
              this.state.notificationCount++;
              this.updateNotificationBadges();
              this.playNotificationSound();
              if (this.state.isPanelVisible) this.renderNotifications();
              break;
            case 'new_notifications':
              if (data.notifications?.length > 0) {
                data.notifications.forEach(n => this.state.notifications.unshift(n));
                this.state.notificationCount = data.count || 
                  this.state.notificationCount + data.notifications.length;
                this.updateNotificationBadges();
                this.playNotificationSound();
                if (this.state.isPanelVisible) this.renderNotifications();
              }
              break;
            case 'recent_notifications':
              if (data.notifications?.length > 0) {
                this.state.notifications = data.notifications;
                if (this.state.isPanelVisible) this.renderNotifications();
              }
              break;
          }
        } catch (e) {
          this.setupPolling();
        }
      };
      
      this.state.socket.onclose = this.setupPolling.bind(this);
      this.state.socket.onerror = this.setupPolling.bind(this);
    } catch (e) {
      this.setupPolling();
    }
  },

  setupWebSocketNotifications: function() {
    if (!('WebSocket' in window)) {
      // console.log('WebSocket not supported, using polling');
      return;
    }

    const userId = window.currentUserId;
    if (!userId) {
      // console.log('No user ID available for WebSocket');
      return;
    }

    try {
      if (this.state.wsSocket) {
        this.state.wsSocket.close();
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/api/notifications/ws?user_id=${userId}`;
      
      // console.log('🔔 Connecting to notification WebSocket...');
      this.state.wsSocket = new WebSocket(wsUrl);
      
      this.state.wsSocket.onopen = () => {
        // console.log('✅ Connected to notification WebSocket');
        this.state.isWsConnected = true;
        this.state.wsReconnectAttempts = 0;
        this.updateConnectionIndicator(true);
        
        if (this.state.pollTimer) {
          clearInterval(this.state.pollTimer);
          this.state.pollTimer = setInterval(() => {
            if (!this.state.isWsConnected) {
              if (window.isCreator || window.isTeam) this.fetchCount();
              this.fetchNotifications();
            }
          }, this.config.pollInterval);
        }
      };
      
      this.state.wsSocket.onmessage = (event) => {
        try {
          if (typeof event.data === 'string' && !event.data.startsWith('{')) {
            if (event.data === 'ping') this.state.wsSocket.send('pong');
            return;
          }
          
          const data = JSON.parse(event.data);
          this.handleWebSocketNotificationMessage(data);
          
        } catch (e) {
          // console.error('Error handling WebSocket message:', e);
        }
      };
      
      this.state.wsSocket.onclose = () => {
        // console.log('📡 Notification WebSocket disconnected');
        this.state.isWsConnected = false;
        this.updateConnectionIndicator(false);
        this.scheduleWebSocketReconnect();
      };
      
      this.state.wsSocket.onerror = (error) => {
        // console.error('📡 Notification WebSocket error:', error);
        this.state.isWsConnected = false;
        this.updateConnectionIndicator(false);
      };
      
    } catch (e) {
      // console.error('Failed to setup notification WebSocket:', e);
      this.state.isWsConnected = false;
    }
  },

  scheduleWebSocketReconnect: function() {
    if (this.state.wsReconnectAttempts >= 5) {
      // console.log('❌ Max WebSocket reconnection attempts reached');
      return;
    }

    this.state.wsReconnectAttempts++;
    const delay = Math.min(5000 * this.state.wsReconnectAttempts, 30000);
    
    // console.log(`🔄 Reconnecting notification WebSocket in ${delay/1000}s (attempt ${this.state.wsReconnectAttempts})`);
    
    setTimeout(() => {
      if (!this.state.isWsConnected) {
        this.setupWebSocketNotifications();
      }
    }, delay);
  },

  setupBookRequestWebSocket: function() {
    // ✅ Listen to book request WebSocket for pending_count_update messages
    if (!('WebSocket' in window)) return;
    if (!(window.isCreator || window.isTeam)) return; // Only for admins

    const userId = window.currentUserId;
    if (!userId) return;

    try {
      if (this.state.bookRequestSocket) {
        this.state.bookRequestSocket.close();
      }

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/api/book-requests/ws?user_id=${userId}`;

      // console.log('📚 [BadgeManager] Connecting to book request WebSocket for live badge updates...');
      this.state.bookRequestSocket = new WebSocket(wsUrl);

      this.state.bookRequestSocket.onopen = () => {
        // console.log('✅ [BadgeManager] Connected to book request WebSocket');
      };

      this.state.bookRequestSocket.onmessage = (event) => {
        if (event.data === 'ping') {
          if (this.state.bookRequestSocket && this.state.bookRequestSocket.readyState === WebSocket.OPEN) {
            this.state.bookRequestSocket.send('pong');
          }
          return;
        }

        try {
          const data = JSON.parse(event.data);
          // console.log('📨 [BadgeManager] Book request WebSocket message:', data.type);

          // Listen for pending_count_update messages
          if (data.type === 'pending_count_update') {
            // console.log(`📊 [BadgeManager] Pending count update: ${this.state.count} → ${data.pending_count}`);
            this.state.count = data.pending_count;
            window.currentPendingRequests = data.pending_count;
            this.updateBadges();
            this.updateMobileQuickNavBadge();

            // Update combined Admin dropdown badge
            if (window.updateAdminDropdownBadge) {
              window.updateAdminDropdownBadge();
            }

            // console.log(`✅ [BadgeManager] Badge updated to ${data.pending_count}`);
          }
        } catch (e) {
          // console.error('❌ [BadgeManager] Error parsing book request WebSocket message:', e);
        }
      };

      this.state.bookRequestSocket.onclose = () => {
        // console.log('🔌 [BadgeManager] Book request WebSocket closed');
        this.state.bookRequestSocket = null;
      };

      this.state.bookRequestSocket.onerror = (error) => {
        // console.error('❌ [BadgeManager] Book request WebSocket error:', error);
      };
    } catch (e) {
      // console.error('❌ [BadgeManager] Failed to setup book request WebSocket:', e);
    }
  },

  updateConnectionIndicator: function(connected) {
    const header = document.querySelector('.notification-header h3');
    if (header) {
      const indicator = header.querySelector('.ws-indicator') || document.createElement('span');
      indicator.className = 'ws-indicator';
      indicator.className = `ws-indicator ${connected ? 'connected' : 'disconnected'}`;
      indicator.title = connected ? 'Connected to live updates' : 'Offline mode';

      if (!header.querySelector('.ws-indicator')) {
        header.appendChild(indicator);
      }
    }
  },

  markNotificationAsRead: function(notificationId, notification) {
    const notificationIndex = this.state.notifications.findIndex(n => n.id === notificationId);
    if (notificationIndex !== -1) {
      this.state.notifications[notificationIndex].is_read = true;
      if (this.state.notificationCount > 0) {
        this.state.notificationCount--;
      }
    }
    
    if (notification && this.isNotificationFromForum(notification)) {
      this.updateEnhancedForumManager(notification, 'remove');
      
      if (this.state.forumNotificationCount > 0) {
        this.state.forumNotificationCount--;
      }
    }
    
    this.updateNotificationBadges();
    
    if (this.markNotificationAsReadWS && this.markNotificationAsReadWS(notificationId)) {
      // WebSocket handled it
    } else {
      fetch(`${this.config.notificationEndpoint}/${notificationId}/read`, {
        method: 'POST',
        keepalive: true
      }).catch(() => { this.state.errorsDetected = true; });
    }
  },

  markAllAsReadWS: function() {
    if (this.state.isWsConnected && this.state.wsSocket.readyState === WebSocket.OPEN) {
      this.state.wsSocket.send(JSON.stringify({
        type: 'mark_all_read'
      }));
      return true;
    }
    return false;
  },

  setupPolling: function() {
    if (this.state.pollTimer) clearInterval(this.state.pollTimer);
    
    const interval = this.state.isWsConnected ? this.config.pollInterval : 10000;
    
    this.state.pollTimer = setInterval(() => {
      if (window.isCreator || window.isTeam) this.fetchCount();
      this.fetchNotifications();
    }, interval);
    
    this.fetchNotifications();
    if (window.isCreator || window.isTeam) this.fetchCount();
  },

  setupFormListeners: function() {
    document.addEventListener('submit', (event) => {
      const form = event.target;
      if (form.action?.includes('/api/book-requests')) {
        setTimeout(() => this.fetchCount(), 1000);
      }
    });
  },

  setupNotificationHandlers: function() {
    document.querySelectorAll('.notifications-link').forEach(link => {
      const newLink = link.cloneNode(true);
      if (link.parentNode) link.parentNode.replaceChild(newLink, link);
      
      newLink.addEventListener('click', (e) => {
        e.preventDefault();
        this.toggleNotificationPanel();
      });
    });
    
    document.addEventListener('click', (e) => {
      if (e.target.id === 'closeNotificationsBtn' || e.target.closest('#closeNotificationsBtn')) {
        this.hideNotificationPanel();
        return;
      }
      
      // REMOVED: markAllReadBtn event listener since we removed the button
      
      if (e.target.classList.contains('filter-btn')) {
        e.stopPropagation();
        const filter = e.target.dataset.filter;
        // console.log(`🔔 Filter button clicked: ${filter}`);
        this.setNotificationFilter(filter);
        return;
      }
      
      if (e.target.id === 'deleteReadBtn' || e.target.closest('#deleteReadBtn')) {
        e.stopPropagation();
        this.deleteAllRead();
        return;
      }
      
      if (this.state.isPanelVisible) {
        const panel = document.getElementById('notificationPanel');
        const notificationLinks = Array.from(document.querySelectorAll('.notifications-link'));
        
        if (panel && !panel.contains(e.target) && 
            !notificationLinks.some(link => link.contains(e.target))) {
          this.hideNotificationPanel();
        }
      }
    });
    
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.state.isPanelVisible) {
        this.hideNotificationPanel();
      }
    });
  },
  
  toggleNotificationPanel: function() {
    if (this.state.isPanelVisible) this.hideNotificationPanel();
    else this.showNotificationPanel();
  },
  
  showNotificationPanel: function() {
    const panel = document.getElementById('notificationPanel');
    if (!panel) return;
    
    panel.style.display = 'flex';
    this.state.isPanelVisible = true;
    
    if (!this.state.notificationFilter) {
      this.state.notificationFilter = 'all';
    }
    
    this.fetchNotifications();
    this.renderNotifications();
    
    setTimeout(() => {
      document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.filter === this.state.notificationFilter) {
          btn.classList.add('active');
        }
      });
    }, 100);
  },
  
  hideNotificationPanel: function() {
    const panel = document.getElementById('notificationPanel');
    if (!panel) return;
    
    panel.style.display = 'none';
    this.state.isPanelVisible = false;
  },

  getDestinationForNotification: function(notification) {
    if (!notification) return null;
    
    if (notification.type === 'system' && 
        notification.notification_data && 
        notification.notification_data.book_request_id) {
      return '/my-book-requests';
    }
      
    switch (notification.type) {
      case 'comment':
      case 'reply':
      case 'comment_like':
        if (notification.notification_data?.track_id) {
          return `/player/${notification.notification_data.track_id}#comment-${notification.notification_data.comment_id}`;
        }
        break;
      case 'like':
      case 'share':
        if (notification.notification_data?.track_id) {
          return `/player/${notification.notification_data.track_id}`;
        }
        break;
    }
    return null;
  },
  
  playNotificationSound: function() {
    try {
      const audio = new Audio(this.config.notificationSound);
      audio.volume = 0.5;
      audio.play().catch(() => {});
    } catch (error) {}
  },
  
  requestNotificationPermission: function() {
    if ('Notification' in window && 
        Notification.permission !== 'granted' && 
        Notification.permission !== 'denied') {
      Notification.requestPermission();
    }
  },
  
  getTimeAgo: function(date) {
    const now = new Date();
    const diffMs = now - date;
    const diffSec = Math.round(diffMs / 1000);
    const diffMin = Math.round(diffSec / 60);
    const diffHour = Math.round(diffMin / 60);
    const diffDay = Math.round(diffHour / 24);
    
    if (diffSec < 60) return 'just now';
    if (diffMin < 60) return `${diffMin} minute${diffMin > 1 ? 's' : ''} ago`;
    if (diffHour < 24) return `${diffHour} hour${diffHour > 1 ? 's' : ''} ago`;
    if (diffDay < 7) return `${diffDay} day${diffDay > 1 ? 's' : ''} ago`;
    return date.toLocaleDateString();
  }
};

// DEBUGGING: Add to window for manual testing
window.debugNotifications = function() {
  if (window.BadgeManager) {
    // console.log('🐛 Manual debug triggered');
    BadgeManager.debugDeleteOperation();
  } else {
    // console.error('❌ BadgeManager not found');
  }
};

// Enable debug mode with: window.DEBUG_NOTIFICATIONS = true;

document.addEventListener('DOMContentLoaded', function() {
  const notificationsModal = document.getElementById('notificationsModal');
  if (notificationsModal) notificationsModal.remove();
  
  (function fixMobileQuickNavBadge() {
    if (!(window.isCreator || window.isTeam)) return;
    
    const pendingCount = window.currentPendingRequests || 0;
    if (pendingCount <= 0) return;
    
    const mobileIcon = document.querySelector('.mobile-nav-icon.manage-requests-icon');
    if (!mobileIcon) return;
    
    const faLayers = mobileIcon.querySelector('.fa-layers');
    if (!faLayers) return;
    
    let badge = faLayers.querySelector('.notification-badge');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'notification-badge';
      faLayers.appendChild(badge);
      badge.textContent = pendingCount;
    }
  })();
  
  BadgeManager.init();
});