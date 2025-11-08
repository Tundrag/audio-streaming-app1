// forum-spa-wrapper.js
// Full, hardened SPA wrapper for the Forum with cache-busting + safe early-click handling

// --- Early-click proxy shim --------------------------------------------------
// Captures inline onclick calls like forum.viewThread(...) made before ForumCore exists.
// We queue them and flush after the real instance is created in mount().
(() => {
  if (!window.forum) {
    const queue = [];
    const proxy = new Proxy({}, {
      get(_t, prop) {
        // Return a function that queues the call with method name + args
        return (...args) => queue.push({ prop, args });
      }
    });
    window.__forumCallQueue = queue;
    window.forum = proxy;
  }
})();
window.forum = window.forum || new Proxy({}, {
  get: () => () => { /* no-op until real forum instance mounts */ }
});
// ----------------------------------------------------------------------------
export class ForumSPAWrapper {
  constructor() {
    this.forumInstance = null;
    this.scriptsLoaded = false;
    this.styleElement = null;
    this.cacheVersion = this.getCacheVersion();
  }

  async render() {
    console.log('🎬 ForumSPAWrapper: Starting render, fetching /forum...');
    const resp = await fetch('/forum', { headers: { 'X-SPA-Request': 'true' } });
    if (!resp.ok) {
      console.error(`❌ ForumSPAWrapper: Failed to fetch /forum - ${resp.status}`);
      throw new Error(`Failed to load forum HTML (${resp.status})`);
    }

    const html = await resp.text();
    console.log(`✅ ForumSPAWrapper: Fetched HTML (${html.length} chars)`);

    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');

    // Inline <style> from the SSR page so SPA view matches styles
    this.extractAndInjectCSS(doc);

    // Prefer the <main> contents if present
    const main = doc.querySelector('main');
    if (main) {
      console.log('✅ ForumSPAWrapper: Returning <main> innerHTML');
      return main.innerHTML;
    }

    // Fallback to the forum container itself
    const container = doc.querySelector('#forumSPA');
    if (container) {
      console.log('✅ ForumSPAWrapper: Returning #forumSPA outerHTML');
      return container.outerHTML;
    }

    // Last-resort: return whole HTML
    console.warn('⚠️ ForumSPAWrapper: No main or #forumSPA found, returning full HTML');
    return html;
  }

  extractAndInjectCSS(doc) {
    // Remove previously injected styles (hot navigation)
    if (this.styleElement) {
      this.styleElement.remove();
      this.styleElement = null;
    }

    const styles = doc.querySelectorAll('style');
    let css = '';
    styles.forEach(s => { css += (s.textContent || '') + '\n'; });

    if (css.trim()) {
      const el = document.createElement('style');
      el.id = 'forum-spa-styles';
      el.textContent = css;
      document.head.appendChild(el);
      this.styleElement = el;
    }
  }

  async mount() {
    console.log('🔧 ForumSPAWrapper: Starting mount...');

    // Ensure user context exists
    if (!window.forumUserData) {
      window.forumUserData = {
        id: window.currentUserId ?? null,
        username: 'User',
        is_creator: !!window.isCreator,
        is_team: !!window.isTeam,
        is_patreon: false,
        is_kofi: false
      };
      console.log('✅ ForumSPAWrapper: Created forumUserData');
    }

    // Load dependent scripts once
    if (!this.scriptsLoaded) {
      console.log('📜 ForumSPAWrapper: Loading forum scripts...');
      await this.loadForumScripts();
      this.scriptsLoaded = true;
      console.log('✅ ForumSPAWrapper: Scripts loaded');
    } else {
      console.log('✅ ForumSPAWrapper: Scripts already loaded');
    }

    // Wait a tick to let any globals settle (icons, theme, etc.)
    await new Promise(r => setTimeout(r, 150));

    // Ensure container exists
    const host = document.getElementById('forumSPA');
    if (!host) {
      console.error('❌ ForumSPAWrapper: #forumSPA container not found after render!');
      return;
    }
    console.log('✅ ForumSPAWrapper: Found #forumSPA container');

    // Verify class is present - use the final enhanced class
    if (!window.EnhancedForumSPA) {
      console.error('❌ ForumSPAWrapper: EnhancedForumSPA not found after script load');
      return;
    }
    console.log('✅ ForumSPAWrapper: EnhancedForumSPA class available');

    // Create the real instance (using the final complete class)
    console.log('🏗️ ForumSPAWrapper: Creating new EnhancedForumSPA instance...');
    this.forumInstance = new window.EnhancedForumSPA();
    console.log('✅ ForumSPAWrapper: Forum instance created');

    // Replace proxy with the real instance for future calls BEFORE flushing queue
    window.forum = this.forumInstance;

    // Note: If reusing existing instance, it's already initialized from SSR
    // If new instance, wait for init to complete
    if (this.forumInstance._initPromise) {
      console.log('🔧 ForumSPAWrapper: Waiting for forum.init() to complete...');
      await this.forumInstance.waitForInit();
      console.log('✅ ForumSPAWrapper: Forum initialized');
    } else {
      console.log('✅ ForumSPAWrapper: Using existing initialized instance');
    }

    // Flush queued early onclick calls (from the proxy shim)
    if (Array.isArray(window.__forumCallQueue)) {
      for (const { prop, args } of window.__forumCallQueue) {
        try {
          const fn = this.forumInstance?.[prop];
          if (typeof fn === 'function') fn.apply(this.forumInstance, args);
        } catch (err) {
          console.error('forum-spa-wrapper: queued call failed', prop, err);
        }
      }
      window.__forumCallQueue.length = 0;
    }

    // ✅ Ensure event listeners are attached for SPA mode
    await this.reattachEventListeners();

    // Optional: small delegation to make sure notifications/settings links work even
    // if inline handlers are not present. Harmless if unused.
    this.delegateBasicClicks();

    console.log('✅ Forum SPA mounted successfully');
  }

  async reattachEventListeners() {
    // Wait for DOM to settle
    await new Promise(r => setTimeout(r, 100));

    // Re-attach global forum event listeners that might have been lost in SPA navigation
    const backButton = document.querySelector('.back-button');
    if (backButton && !backButton.onclick) {
      backButton.onclick = () => this.forumInstance?.goBack();
    }

    const settingsButton = document.querySelector('[onclick*="navigateToSettings"]');
    if (settingsButton && !settingsButton.onclick) {
      settingsButton.onclick = () => this.forumInstance?.navigateToSettings();
    }

    // Re-attach tab buttons
    document.querySelectorAll('[data-forum-action="switchTab"]').forEach(btn => {
      if (!btn.onclick) {
        btn.onclick = () => this.forumInstance?.switchTab(btn.dataset.tab);
      }
    });

    console.log('✅ Forum event listeners re-attached');
  }

  delegateBasicClicks() {
    const root = document.getElementById('forumSPA');
    if (!root) return;

    // Comprehensive event delegation for all forum actions
    root.addEventListener('click', (e) => {
      // Tab switching
      const tabBtn = e.target.closest('[data-forum-action="switchTab"]');
      if (tabBtn && this.forumInstance?.switchTab) {
        e.preventDefault();
        this.forumInstance.switchTab(tabBtn.dataset.tab);
        return;
      }

      // Back button (multiple selectors for different contexts)
      const backBtn = e.target.closest('.back-button') ||
                      e.target.closest('[data-forum-back]') ||
                      e.target.closest('[onclick*="backToThreads"]');
      if (backBtn) {
        e.preventDefault();
        if (this.forumInstance?.backToThreads) {
          this.forumInstance.backToThreads();
        } else if (this.forumInstance?.goBack) {
          this.forumInstance.goBack();
        }
        return;
      }

      // Settings button
      const settingsBtn = e.target.closest('[onclick*="navigateToSettings"]');
      if (settingsBtn && this.forumInstance?.navigateToSettings) {
        e.preventDefault();
        this.forumInstance.navigateToSettings();
        return;
      }

      // Thread viewing
      const threadItem = e.target.closest('[data-thread-id]');
      if (threadItem && this.forumInstance?.viewThread) {
        e.preventDefault();
        const id = parseInt(threadItem.dataset.threadId, 10);
        if (!Number.isNaN(id)) this.forumInstance.viewThread(id);
        return;
      }

      // Message actions - reply
      const replyBtn = e.target.closest('[onclick*="showReplyInput"]');
      if (replyBtn && this.forumInstance?.showReplyInput) {
        e.preventDefault();
        const match = replyBtn.getAttribute('onclick')?.match(/showReplyInput\((\d+)\)/);
        if (match) this.forumInstance.showReplyInput(parseInt(match[1], 10));
        return;
      }

      // Message actions - thread creation
      const threadBtn = e.target.closest('[onclick*="createThreadFromMessage"]');
      if (threadBtn && this.forumInstance?.createThreadFromMessage) {
        e.preventDefault();
        const match = threadBtn.getAttribute('onclick')?.match(/createThreadFromMessage\((\d+)\)/);
        if (match) this.forumInstance.createThreadFromMessage(parseInt(match[1], 10));
        return;
      }

      // Message actions - like
      const likeBtn = e.target.closest('[onclick*="toggleMessageLike"]');
      if (likeBtn && this.forumInstance?.toggleMessageLike) {
        e.preventDefault();
        const match = likeBtn.getAttribute('onclick')?.match(/toggleMessageLike\((\d+)\)/);
        if (match) this.forumInstance.toggleMessageLike(parseInt(match[1], 10));
        return;
      }

      // Message actions - edit
      const editBtn = e.target.closest('[onclick*="showEditInput"]');
      if (editBtn && this.forumInstance?.showEditInput) {
        e.preventDefault();
        const match = editBtn.getAttribute('onclick')?.match(/showEditInput\((\d+)\)/);
        if (match) this.forumInstance.showEditInput(parseInt(match[1], 10));
        return;
      }

      // Message actions - delete
      const deleteBtn = e.target.closest('[onclick*="deleteMessage"]');
      if (deleteBtn && this.forumInstance?.deleteMessage) {
        e.preventDefault();
        const match = deleteBtn.getAttribute('onclick')?.match(/deleteMessage\((\d+)\)/);
        if (match) this.forumInstance.deleteMessage(parseInt(match[1], 10));
        return;
      }

      // ✅ GENERIC FALLBACK: Handle any onclick="forum.methodName(...)"
      const anyOnclickBtn = e.target.closest('[onclick*="forum."]');
      if (anyOnclickBtn) {
        const onclick = anyOnclickBtn.getAttribute('onclick');
        if (onclick && onclick.includes('forum.')) {
          e.preventDefault();

          // Extract method name and arguments
          const match = onclick.match(/forum\.(\w+)\((.*?)\)/);
          if (match) {
            const methodName = match[1];
            const argsString = match[2];

            // Parse arguments (simple eval for numbers, strings)
            let args = [];
            if (argsString.trim()) {
              try {
                // Split by comma but be careful with nested calls
                args = argsString.split(',').map(arg => {
                  arg = arg.trim();
                  // If it's a number
                  if (/^\d+$/.test(arg)) return parseInt(arg, 10);
                  // If it's a quoted string
                  if (/^['"].*['"]$/.test(arg)) return arg.slice(1, -1);
                  // Otherwise return as-is
                  return arg;
                });
              } catch (err) {
                console.warn('Failed to parse onclick args:', argsString);
              }
            }

            // Call the method if it exists
            if (typeof this.forumInstance?.[methodName] === 'function') {
              console.log(`🔧 Delegating forum.${methodName}(${args.join(', ')})`);
              this.forumInstance[methodName](...args);
            } else {
              console.warn(`Method forum.${methodName} not found`);
            }
          }
          return;
        }
      }
    });

    console.log('✅ Forum click delegation active');
  }

  async destroy() {
    console.log('🧹 ForumSPAWrapper: Starting destroy...');

    // Instance cleanup
    try {
      if (this.forumInstance?.destroy) {
        console.log('🧹 ForumSPAWrapper: Calling forumInstance.destroy()...');
        await this.forumInstance.destroy();
        console.log('✅ ForumSPAWrapper: Forum instance destroyed');
      }
    } catch (e) {
      console.warn('⚠️ ForumSPAWrapper: error during forumInstance.destroy()', e);
    } finally {
      this.forumInstance = null;
    }

    // Close any global socket the forum may have left around
    if (window.forumWebSocket) {
      console.log('🧹 ForumSPAWrapper: Closing forumWebSocket...');
      try { window.forumWebSocket.close(); } catch {}
      window.forumWebSocket = null;
    }

    // Remove injected CSS
    if (this.styleElement) {
      console.log('🧹 ForumSPAWrapper: Removing injected CSS...');
      this.styleElement.remove();
      this.styleElement = null;
    }

    // Clear global reference to avoid stale handles
    if (window.forum && window.forum !== this.forumInstance) {
      // If a proxy is in place because user clicked while tearing down,
      // leave it; otherwise null out.
      if (!window.__forumCallQueue?.length) {
        console.log('🧹 ForumSPAWrapper: Clearing window.forum...');
        window.forum = null;
      }
    }

    console.log('✅ ForumSPAWrapper: Destroy complete');
  }

  // --- Assets loading --------------------------------------------------------

  async loadForumScripts() {
    // Ordered list (dependencies first)
    const scripts = [
      '/static/js/forum-websockets.js',
      '/static/js/forum-core.js',
      '/static/js/forum-thread-settings.js',
      '/static/js/forum-messages.js',
      '/static/js/forum-settings.js'
    ];

    const v = this.cacheVersion;
    for (const base of scripts) {
      const src = `${base}?v=${encodeURIComponent(v)}`;
      const name = base.split('/').pop();
      // Avoid duplicate loads in case of remount
      const already = document.querySelector(`script[src*="${name}"]`);
      if (already) continue;
      await this.injectScript(src);
    }
  }

  injectScript(src) {
    return new Promise((resolve, reject) => {
      const el = document.createElement('script');
      el.src = src;
      el.async = false; // preserve order
      el.onload = resolve;
      el.onerror = (e) => {
        console.error('forum-spa-wrapper: failed to load', src, e);
        reject(new Error(`Failed to load ${src}`));
      };
      document.head.appendChild(el);
    });
  }

  // Try to reuse the global cache-bust number so everything stays in lockstep
  getCacheVersion() {
    // Prefer the spa-router version if present
    const routers = document.querySelectorAll('script[src*="spa-router.js"]');
    for (const s of routers) {
      const m = s.src.match(/spa-router\.js\?v=(\d+)/);
      if (m) return m[1];
    }
    // Fallback: any script with ?v=
    const any = document.querySelector('script[src*="?v="]');
    if (any) {
      const m = any.src.match(/\?v=(\d+)/);
      if (m) return m[1];
    }
    // Last resort: timestamp
    return Date.now().toString();
  }
}
