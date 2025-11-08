/*
 * forum-notification-manager.js — v3.1 (FIXED - Live Thread Badge Updates)
 * Syncs with server-side notification state, clears badges when threads are viewed
 * FIXED: Now properly updates thread badges live via WebSocket
 */

JSON.parseSafe = (d) => { try { return JSON.parse(d); } catch { return {}; } };

class EnhancedForumNotificationManager {
  threadNotifications = new Map();
  totalUnreadCount = 0;
  currentThreadId = null;
  isInForum = false;
  ws = null;
  isWsConnected = false;
  wsReconnectTries = 0;
  
  // 🔥 NEW: Callback for updating back button badge
  backButtonUpdateCallback = null;

  constructor() {
    if (!window.currentUserId) return;

    this.attachForumObservers();
    this.openWebSocket();
    this.injectBadgeStyles();

    window.enhancedForumNotificationManager = this;
    // console.log("🔔 Forum notification manager initialized");
  }

  attachForumObservers() {
    this.refreshLocationState();

    new MutationObserver(() => this.refreshLocationState())
      .observe(document, { childList:true, subtree:true, attributes:true });

    ["pushState","replaceState"].forEach(fn => {
      const orig = history[fn];
      history[fn] = (...args) => { orig.apply(history,args); this.refreshLocationState(); };
    });

    window.addEventListener("popstate", () => this.refreshLocationState());
    setInterval(() => this.refreshLocationState(), 2000);
  }

  refreshLocationState() {
    const prevInForum = this.isInForum;
    const prevThreadId = this.currentThreadId;

    this.isInForum = !!(
      location.pathname.includes("/forum") ||
      location.pathname.includes("/api/forum") ||
      document.body.dataset.forumActive === "true" ||
      (window.forum && window.forum.currentView && window.forum.currentView !== "settings")
    );

    this.currentThreadId = this.isInForum ? this.detectThread() : null;

    if (prevInForum !== this.isInForum) this.onForumToggle();
    if (prevThreadId !== this.currentThreadId) this.onThreadChange();
  }

  detectThread() {
    if (window.forum?.currentThread && window.forum.currentView === "discussion")
      return +window.forum.currentThread.id;

    const hashMatch = location.hash.match(/thread-(\d+)/);
    if (hashMatch) return +hashMatch[1];

    const param = new URLSearchParams(location.search).get("thread");
    if (param) return +param;

    const marker = document.querySelector('[data-current-thread-id]');
    if (marker) return +marker.dataset.currentThreadId;

    return null;
  }

  onForumToggle() {
    // console.log(`📍 ${this.isInForum ? "Entered" : "Left"} forum view`);
    
    if (this.isInForum && !this.isWsConnected) {
      this.openWebSocket();
    }
    
    this.renderAllBadges();
  }

  onThreadChange() {
    // console.log(`🔄 Thread changed: ${this.currentThreadId}`);
    
    if (this.isInForum && this.currentThreadId) {
      this.clearThreadNotifications(this.currentThreadId);
    }
    
    this.renderAllBadges();
  }

  // 🔥 ENHANCED: Better thread notification clearing with WebSocket sync
  clearThreadNotifications(threadId) {
    // console.log(`🧹 Clearing notifications for thread ${threadId}`);
    
    const hadNotifications = this.threadNotifications.has(threadId);
    this.threadNotifications.delete(threadId);
    this.recomputeTotal();

    // 🔥 NEW: Immediately update UI
    this.renderAllBadges();

    // 🔥 ENHANCED: Better API call with proper error handling and WebSocket sync
    fetch(`/api/forum/threads/${threadId}/mark-read`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      }
    })
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      // console.log(`✅ Thread ${threadId} marked as read:`, data);
      
      // 🔥 NEW: Force a badge update after successful API call
      if (data.updated_counts) {
        this.handleUnreadCountUpdate(data.updated_counts);
      }
    })
    .catch(err => {
      // console.warn('Could not mark thread read:', err);
      
      // 🔥 NEW: If API call fails, restore notifications
      if (hadNotifications) {
        this.threadNotifications.set(threadId, 1); // Assume 1 notification
        this.recomputeTotal();
      }
    });
  }

  clearAllNotifications() {
    // console.log(`🧹 Clearing all forum notifications`);
    this.threadNotifications.clear();
    this.totalUnreadCount = 0;
    this.renderAllBadges();
  }

  openWebSocket() {
    if (this.isWsConnected) return;

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/api/forum/ws/global`;

    // console.log("🔔 Connecting forum notification WS...");
    this.ws = new WebSocket(url);

    this.ws.onopen = () => { 
      this.isWsConnected = true;  
      this.wsReconnectTries = 0; 
      // console.log("✅ Forum notification WebSocket connected");
      this.fetchInitialCounts();
    };
    this.ws.onclose = () => { 
      this.isWsConnected = false; 
      // console.log("❌ Forum notification WebSocket disconnected");
      this.retryWebSocket(); 
    };
    this.ws.onerror = (error) => { 
      this.isWsConnected = false; 
      // console.error("🚨 Forum notification WebSocket error:", error);
    };
    this.ws.onmessage = (e) => this.handleWSMessage(JSON.parseSafe(e.data));
  }

  retryWebSocket() {
    if (this.wsReconnectTries >= 6) return;
    const delay = Math.min(5000 * ++this.wsReconnectTries, 30000);
    // console.log(`🔄 Retrying WebSocket connection in ${delay}ms (attempt ${this.wsReconnectTries})`);
    setTimeout(() => this.openWebSocket(), delay);
  }

  handleWSMessage(msg) {
    // console.log("📨 Forum notification WebSocket message:", msg);
    
    switch (msg?.type) {
      case "new_notification":  
        return this.handleNewNotification(msg.notification);
      case "notification_read":
        return this.handleNotificationRead(msg);
      case "unread_count_updated":
        return this.handleUnreadCountUpdate(msg);
      case "initial_data":
        return this.processInitialData(msg.notifications || []);
      case "thread_marked_read":
        return this.handleThreadMarkedRead(msg);
    }
  }

  // 🔥 NEW: Handle server notification when thread is marked as read
  handleThreadMarkedRead(data) {
    // console.log(`📖 Thread ${data.thread_id} marked as read by user ${data.user_id}`);
    
    if (data.thread_id) {
      this.threadNotifications.delete(data.thread_id);
      this.recomputeTotal();
      
      // 🔥 NEW: Force immediate UI update
      this.renderAllBadges();
    }
  }

  isForumNotification(n) {
    if (!n) return false;
    return (
      n.notification_data?.source === "forum" ||
      n.title?.startsWith("[Forum]") ||
      ["forum_mention", "forum_reply", "forum_new_message"].includes(n.type)
    );
  }

  getThreadId(n) {
    return +(
      n.notification_data?.thread_id ||
      n.thread_id ||
      (n.content?.match(/thread[ _](\d+)/i)?.[1] ?? 0)
    );
  }

  handleNewNotification(notification) {
    if (!this.isForumNotification(notification)) return;
    
    const threadId = this.getThreadId(notification);
    if (!threadId) return;
    
    if (this.isInForum && this.currentThreadId === threadId) {
      // console.log(`📖 Ignoring notification for currently viewed thread ${threadId}`);
      return;
    }
    
    const currentCount = this.threadNotifications.get(threadId) || 0;
    this.threadNotifications.set(threadId, currentCount + 1);
    this.recomputeTotal();
    
    // console.log(`📬 New notification for thread ${threadId}, count: ${currentCount + 1}`);
  }

  handleNotificationRead(data) {
    // console.log("📖 Notification read:", data);
    
    if (data.thread_id) {
      const currentCount = this.threadNotifications.get(data.thread_id) || 0;
      if (currentCount > 0) {
        this.threadNotifications.set(data.thread_id, Math.max(0, currentCount - 1));
        if (currentCount - 1 <= 0) {
          this.threadNotifications.delete(data.thread_id);
        }
        this.recomputeTotal();
      }
    }
  }

  // 🔥 ENHANCED: Better unread count update handling
  handleUnreadCountUpdate(data) {
    // console.log(`📊 Received unread count update:`, data);
    
    let shouldRenderBadges = false;
    
    if (data.thread_id !== undefined) {
      if (data.thread_unread_count === 0) {
        if (this.threadNotifications.has(data.thread_id)) {
          this.threadNotifications.delete(data.thread_id);
          shouldRenderBadges = true;
        }
      } else {
        const currentCount = this.threadNotifications.get(data.thread_id) || 0;
        if (currentCount !== data.thread_unread_count) {
          this.threadNotifications.set(data.thread_id, data.thread_unread_count);
          shouldRenderBadges = true;
        }
      }
    }
    
    if (data.total_forum_unread !== undefined) {
      if (this.totalUnreadCount !== data.total_forum_unread) {
        this.totalUnreadCount = data.total_forum_unread;
        shouldRenderBadges = true;
      }
    } else if (shouldRenderBadges) {
      this.recomputeTotal();
      return; // recomputeTotal calls renderAllBadges
    }
    
    if (shouldRenderBadges) {
      this.renderAllBadges();
    }
  }

  processInitialData(notifications) {
    // console.log(`📥 Processing ${notifications.length} initial notifications`);
    
    const threadCounts = new Map();
    
    notifications
      .filter(n => this.isForumNotification(n) && !n.is_read)
      .forEach(n => {
        const threadId = this.getThreadId(n);
        if (!threadId) return;
        
        if (this.isInForum && this.currentThreadId === threadId) return;
        
        threadCounts.set(threadId, (threadCounts.get(threadId) || 0) + 1);
      });
    
    this.threadNotifications = threadCounts;
    this.recomputeTotal();
    
    // console.log(`✅ Loaded ${this.totalUnreadCount} unread notifications across ${threadCounts.size} threads`);
  }

  recomputeTotal() {
    const prevTotal = this.totalUnreadCount;
    this.totalUnreadCount = [...this.threadNotifications.values()].reduce((a, b) => a + b, 0);
    
    // console.log(`🔢 Total unread count: ${prevTotal} → ${this.totalUnreadCount}`);
    
    // 🔥 NEW: Always render badges when total changes
    this.renderAllBadges();
  }

  // 🔥 ENHANCED: Better badge rendering with force update option
  renderAllBadges(forceUpdate = false) {
    // Update main forum links
    const links = document.querySelectorAll(
      'a[href*="/forum"], a[href*="/api/forum"], .back-to-forum, [data-forum-back]'
    );
    links.forEach(el => this.paintBadge(el, this.totalUnreadCount));

    // 🔥 NEW: Update back button via callback
    if (this.backButtonUpdateCallback) {
      this.backButtonUpdateCallback(this.totalUnreadCount);
    }

    // Update thread list badges
    if (document.querySelector('.thread-item') || forceUpdate) {
      this.paintThreadList();
    }
    
    // console.log(`🎨 Rendered badges - Total: ${this.totalUnreadCount}, Threads: ${this.threadNotifications.size}`);
  }

  paintBadge(el, count) {
    if (!el) return;
    
    if (getComputedStyle(el).position === "static") {
      el.style.position = "relative";
    }
    
    let badge = el.querySelector(":scope > .forum-notification-badge");

    if (count > 0) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "forum-notification-badge";
        el.appendChild(badge);
      }
      badge.textContent = count > 99 ? "99+" : count;
      badge.style.display = "flex";

      badge.classList.add("badge-pulse");
      setTimeout(() => badge.classList.remove("badge-pulse"), 800);
    } else if (badge) {
      badge.style.display = "none";
    }
  }

  // 🔥 ENHANCED: Better thread list badge painting with immediate updates
  paintThreadList() {
    document.querySelectorAll('.thread-item[data-thread-id]').forEach(item => {
      const threadId = +item.dataset.threadId;
      const count = this.threadNotifications.get(threadId) || 0;
      
      // 🔥 NEW: Find the best target for the badge
      let target = item.querySelector('.thread-actions') || 
                   item.querySelector('.thread-icons') || 
                   item.querySelector('.thread-header') ||
                   item;
      
      this.paintBadge(target, count);
      
      // 🔥 NEW: Also update any existing unread badges in the thread item
      const existingBadges = item.querySelectorAll('.status-badge.unread');
      existingBadges.forEach(badge => {
        if (count > 0) {
          badge.textContent = `${count} unread`;
          badge.style.display = 'inline-flex';
        } else {
          badge.style.display = 'none';
        }
      });
    });
  }

  async fetchInitialCounts() {
    try {
      // console.log("🔄 Fetching fresh notification counts...");
      
      const response = await fetch('/api/forum/notifications?limit=100');
      if (!response.ok) return;
      
      const data = await response.json();
      this.processInitialData(data.notifications || []);
      
    } catch (error) {
      // console.error("❌ Error fetching initial counts:", error);
    }
  }

  // 🔥 PUBLIC API METHODS
  getTotalUnreadCount() { 
    return this.totalUnreadCount; 
  }
  
  getThreadNotificationCount(threadId) { 
    return this.threadNotifications.get(threadId) || 0; 
  }
  
  onThreadEntered(threadId) {
    // console.log(`👁️ User entered thread ${threadId}`);
    this.currentThreadId = threadId;
    this.clearThreadNotifications(threadId);
  }
  
  updateForumStatus() { 
    this.refreshLocationState(); 
  }
  
  onThreadListRender() { 
    this.paintThreadList(); 
  }

  // 🔥 NEW: Force update method for external calls
  forceUpdate() {
    // console.log("🔄 Force updating all badges");
    this.renderAllBadges(true);
  }

  // 🔥 NEW: Update specific thread badge
  updateThreadBadges() {
    if (document.querySelector('.thread-item')) {
      this.paintThreadList();
    }
  }

  injectBadgeStyles() {
    if (document.getElementById('forum-badge-css')) return;
    const css = `
      .forum-notification-badge {
        position: absolute;
        top: -8px;
        right: -8px;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 18px;
        min-width: 18px;
        height: 18px;
        padding: 0 4px;
        border-radius: 50%;
        background: var(--error-color);
        color: #fff;
        font: 700 .7rem/1 system-ui;
        border: 2px solid var(--bg-primary);
        z-index: 10;
        line-height: 1;
      }
      .forum-notification-badge.badge-pulse {
        animation: badgePulse .6s ease-out;
      }
      @keyframes badgePulse {
        0% { transform: scale(1); }
        50% { transform: scale(1.2); }
        100% { transform: scale(1); }
      }
    `;
    const style = document.createElement('style');
    style.id = 'forum-badge-css';
    style.textContent = css;
    document.head.appendChild(style);
  }
}

function bootForumBadgeManager() {
  if (!window.enhancedForumNotificationManager && window.currentUserId) {
    new EnhancedForumNotificationManager();
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootForumBadgeManager);
} else {
  bootForumBadgeManager();
}