// home-shared-spa.js - Universal controller for Home page (SSR and SPA modes)

export class HomeController {
  constructor(mode = 'spa') {
    this.mode = mode; // 'ssr' or 'spa'

    /** @private Auto-scroll timer handle */
    this.autoScrollInterval = null;
    /** @private Timer to resume auto-scroll after user interaction */
    this.autoScrollResumeTimeout = null;
    /** @private Whether the user is currently interacting with the carousel */
    this.isUserInteracting = false;
    /** @private Delay before resuming auto-scroll (ms) */
    this.RESUME_DELAY = 3000;

    /** @private Permissions payload from /api/permissions */
    this.permissions = null;
    /** @private User tier payload from /api/user/tier */
    this.userTier = null;

    /** @private Only duplicate the popular carousel once */
    this.isCarouselDuplicated = false;

    /** @private Bound resize handler (for cleanup) */
    this.resizeHandler = null;

    // Bootstrap data from SSR
    this.bootstrapData = null;
  }

  // ✅ For SPA mode: generate HTML
  async render() {
    if (this.mode === 'ssr') {
      throw new Error('render() should not be called in SSR mode');
    }

    return this.generateHTML();
  }

  // ✅ For both modes: attach event listeners and initialize
  async mount() {
    // console.log(`🏠 Home: Mounting in ${this.mode} mode...`);

    if (this.mode === 'ssr') {
      // SSR: Read bootstrap data from DOM if available
      this.hydrateFromDOM();
    }

    // 1) Load user state
    await this.loadPermissions();
    await this.loadUserTier();

    // 2) Static bindings
    this.setupStaticEventListeners();
    this.setupResizeHandler();

    // 3) Load dynamic content into each section
    await this.loadAllContent();

    // 4) Post-load UI tweaks
    this.applyPermissionsUI();
    this.highlightActiveNav();

    // 5) Start the auto-scroll for popular tracks
    this.startAutoScroll();

    // console.log('✅ Home: Mounted successfully');
  }

  // ✅ Read data from DOM (SSR mode)
  hydrateFromDOM() {
    const bootstrapScript = document.getElementById('home-bootstrap-data');
    if (bootstrapScript) {
      try {
        this.bootstrapData = JSON.parse(bootstrapScript.textContent);
        // console.log('📦 Hydrated home data from DOM:', this.bootstrapData);

        // Pre-populate permissions and user tier from bootstrap if available
        if (this.bootstrapData.permissions) {
          this.permissions = this.bootstrapData.permissions;
        }
        if (this.bootstrapData.userTier) {
          this.userTier = this.bootstrapData.userTier;
          this.renderTierInfo();
        }
      } catch (error) {
        // console.error('Error parsing bootstrap data:', error);
      }
    }
  }

  // ✅ Generate HTML for SPA mode
  generateHTML() {
    return `
      ${this.styles()}
      ${this.layout()}
    `;
  }

  // =============================================================================
  // USER / SESSION
  // =============================================================================

  /**
   * Fetch user permissions. Redirect to /login if unauthenticated.
   * @private
   */
  async loadPermissions() {
    // Skip if we already have hydrated data
    if (this.permissions && this.mode === 'ssr') {
      // console.log('✅ Using hydrated permissions');
      return;
    }

    try {
      // console.log('🔐 Loading permissions from backend...');
      const response = await fetch('/api/permissions');

      if (response.status === 401 || response.status === 303) {
        // console.warn('⚠️ Not authenticated, redirecting to login');
        window.location.assign('/login');
        return;
      }

      if (!response.ok) throw new Error('Failed to fetch permissions');

      this.permissions = await response.json();
      // console.log('✅ Permissions loaded:', this.permissions);
    } catch (error) {
      // console.error('❌ Error loading permissions:', error);
      this.permissions = {
        can_create: false,
        can_edit: false,
        can_delete: false,
        can_download: false,
      };
    }
  }

  /**
   * Fetch user tier and render tier info block if present.
   * @private
   */
  async loadUserTier() {
    // Skip if we already have hydrated data
    if (this.userTier && this.mode === 'ssr') {
      // console.log('✅ Using hydrated user tier');
      return;
    }

    try {
      // console.log('💎 Loading user tier info...');
      const response = await fetch('/api/user/tier');
      if (!response.ok) {
        // console.log('⚠️ Could not load tier info');
        return;
      }
      this.userTier = await response.json();
      // console.log('✅ User tier loaded:', this.userTier);
      this.renderTierInfo();
    } catch (error) {
      // console.error('❌ Error loading tier info:', error);
    }
  }

  /**
   * Injects the user tier info into the .user-status element (if present).
   * @private
   */
  renderTierInfo() {
    if (!this.userTier?.tier) return;
    const userSection = document.querySelector('.user-status');
    if (!userSection) return;

    userSection.style.display = 'block';
    userSection.innerHTML = `
      <div class="user-info">
        <h3>${this.escape(this.userTier.tier.name)}</h3>
        ${this.userTier.is_patreon ? `
          <div class="tier-details">
            <p class="tier-price">${this.escape(this.userTier.tier.amount)}</p>
            ${this.userTier.tier.description ? `
              <p class="tier-description">${this.escape(this.userTier.tier.description)}</p>
            ` : ''}
          </div>
        ` : ''}
      </div>
    `;
  }

  /**
   * Shows/hides UI affordances based on loaded permissions.
   * @private
   */
  applyPermissionsUI() {
    if (!this.permissions) return;
    // console.log('🎨 Updating UI based on permissions:', this.permissions);

    const show = (selector, visible) => {
      document.querySelectorAll(selector).forEach((el) => {
        el.style.display = visible ? '' : 'none';
      });
    };

    show('.btn-create-album, .create-btn', this.permissions.can_create);
    show('.btn-danger, .delete-track, .delete-album-btn', this.permissions.can_delete);
    show('.btn-edit, .edit-track-btn, .edit-album-btn', this.permissions.can_edit);
    show('.download-btn, .btn-download', this.permissions.can_download);

    // console.log('✅ UI updated for permissions');
  }

  /**
   * Highlights current nav link if the app has a sidebar/header.
   * @private
   */
  highlightActiveNav() {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach((link) => {
      const linkPath = link.getAttribute('href') || link.getAttribute('data-route') || '';
      link.classList.toggle('active', linkPath === currentPath || linkPath === currentPath.replace(/^\//, ''));
    });
  }

  // =============================================================================
  // EVENTS / ROUTING
  // =============================================================================

  /**
   * Bind static event listeners (carousel arrows, containers).
   * Also sets up **delegated** SPA navigation so dynamically inserted cards work.
   * @private
   */
  setupStaticEventListeners() {
    // console.log('🎯 Setting up event listeners...');

    // Carousel arrow buttons
    document.querySelectorAll('.carousel-button').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const carouselName = e.currentTarget.dataset.carousel;
        const direction = e.currentTarget.classList.contains('prev') ? -1 : 1;
        const scrollAmount = carouselName === 'popularTracks' ? 140 : 110;
        const containerId = `${carouselName}Container`;
        this.scrollCarousel(containerId, direction * scrollAmount);
      });
    });

    // Hover/touch handlers to manage auto-scroll pausing
    document.querySelectorAll('.carousel-container').forEach((container) => {
      // Pause/resume on mouse hover
      container.addEventListener('mouseenter', () => {
        if (container.id === 'popularTracksContainer') {
          this.stopAutoScroll();
          this.isUserInteracting = true;
        }
      });
      container.addEventListener('mouseleave', () => {
        if (container.id === 'popularTracksContainer') {
          this.isUserInteracting = false;
          this.startAutoScroll();
        }
      });

      // Passive scroll listener pauses/resumes auto-scroll
      container.addEventListener(
        'scroll',
        () => {
          if (!this.isUserInteracting && container.id === 'popularTracksContainer') {
            this.pauseAutoScrollWithResume();
          }
        },
        { passive: true }
      );
    });

    // Delegated SPA navigation:
    // - anchors with [data-spa-link]
    // - any element with [data-spa-href]
    // This covers both static and dynamically injected content.
    document.addEventListener(
      'click',
      (e) => {
        const spaA = e.target.closest('a[data-spa-link][href^="/"]');
        if (spaA) {
          e.preventDefault();
          this.navigate(spaA.getAttribute('href'));
          return;
        }

        const spaEl = e.target.closest('[data-spa-href^="/"]');
        if (spaEl) {
          e.preventDefault();
          this.navigate(spaEl.getAttribute('data-spa-href'));
        }
      },
      { capture: true }
    );
  }

  /**
   * Resize handler stub (in case you need to react to viewport changes).
   * @private
   */
  setupResizeHandler() {
    this.resizeHandler = () => {
      // You can add responsive recalculations here if needed
      const carousel = document.getElementById('popularTracksCarousel');
      if (carousel) {
        const cards = carousel.querySelectorAll('.album-preview-card');
        if (cards.length > 0) {
          // console.log('🔄 Window resized, carousel has', cards.length, 'cards');
        }
      }
    };
    window.addEventListener('resize', this.resizeHandler);
  }

  /**
   * SPA-safe navigation helper with graceful fallback.
   * @param {string} path
   * @private
   */
  navigate(path) {
    if (!path) return;
    if (window.spaRouter && typeof window.spaRouter.navigate === 'function') {
      window.spaRouter.navigate(path);
    } else if (window.router && typeof window.router.navigate === 'function') {
      window.router.navigate(path);
    } else {
      window.location.assign(path);
    }
  }

  // =============================================================================
  // CONTENT LOADING
  // =============================================================================

  /**
   * Load all dynamic sections in parallel.
   * @private
   */
  async loadAllContent() {
    // console.log('📦 Loading all content...');

    await Promise.all([
      this.loadPopularTracks(),
      this.loadRecentUpdates(),
      this.loadRecentAdditions(),
      this.loadMyAlbums(),
      this.loadContinueListening(),
    ]);

    // console.log('✅ All content loaded');
  }

  /**
   * Load Popular Tracks and render into #popularTracksCarousel.
   * @private
   */
  async loadPopularTracks() {
    const carousel = document.getElementById('popularTracksCarousel');
    if (!carousel) {
      // console.error('❌ Popular tracks carousel not found');
      return;
    }

    try {
      // console.log('🎵 Loading popular tracks...');
      const response = await fetch('/api/popular-tracks');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const tracks = await response.json();
      if (!Array.isArray(tracks) || tracks.length === 0) {
        carousel.innerHTML = '<div class="loading">No popular tracks yet</div>';
        return;
      }

      carousel.innerHTML = tracks.map((t) => this.tplPopularTrack(t)).join('');

      // Duplicate for infinite scroll if enough tracks
      if (tracks.length > 3) {
        this.duplicateCarouselItems('popularTracksCarousel');
      }

      // console.log(`✅ Loaded ${tracks.length} popular tracks`);
    } catch (error) {
      // console.error('❌ Error loading popular tracks:', error);
      carousel.innerHTML = '<div class="loading">Error loading tracks</div>';
    }
  }

  /**
   * Load Recently Updated albums into #recentUpdatesCarousel.
   * @private
   */
  async loadRecentUpdates() {
    const el = document.getElementById('recentUpdatesCarousel');
    if (!el) return;

    try {
      // console.log('🔄 Loading recent updates...');
      const response = await fetch('/api/albums/recent-updates');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const albums = await response.json();

      if (!albums?.length) {
        el.innerHTML = '<div class="loading">No recent updates</div>';
        return;
      }

      el.innerHTML = albums.map((a) => this.tplRecentUpdate(a)).join('');
      // console.log(`✅ Loaded ${albums.length} recent updates`);
    } catch (error) {
      // console.error('❌ Error loading recent updates:', error);
      el.innerHTML = '<div class="loading">Error loading updates</div>';
    }
  }

  /**
   * Load Recent Additions into #recentAdditionsCarousel.
   * @private
   */
  async loadRecentAdditions() {
    const el = document.getElementById('recentAdditionsCarousel');
    if (!el) return;

    try {
      // console.log('➕ Loading recent additions...');
      const response = await fetch('/api/albums/recent-additions');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const albums = await response.json();

      if (!albums?.length) {
        el.innerHTML = '<div class="loading">No recent additions</div>';
        return;
      }

      el.innerHTML = albums.map((a) => this.tplRecentAddition(a)).join('');
      // console.log(`✅ Loaded ${albums.length} recent additions`);
    } catch (error) {
      // console.error('❌ Error loading recent additions:', error);
      el.innerHTML = '<div class="loading">Error loading additions</div>';
    }
  }

  /**
   * Load user's albums into #myAlbumsCarousel.
   * @private
   */
  async loadMyAlbums() {
    const carousel = document.getElementById('myAlbumsCarousel');
    const wrapper = document.getElementById('myAlbumsWrapper');
    if (!carousel || !wrapper) return;

    try {
      // console.log('📀 Loading my albums...');
      const response = await fetch('/api/home/data');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const albums = payload?.data?.my_albums || [];

      if (!albums.length) {
        wrapper.innerHTML = `
          <div class="no-content">
            <i class="fas fa-music"></i>
            <p>No albums in your collection yet</p>
            <a href="/collection" class="btn-primary" data-spa-link>Browse Collection</a>
          </div>
        `;
        return;
      }

      carousel.innerHTML = albums.map((a) => this.tplMyAlbum(a)).join('');
      // console.log(`✅ Loaded ${albums.length} albums in collection`);
    } catch (error) {
      // console.error('❌ Error loading my albums:', error);
      carousel.innerHTML = '<div class="loading">Error loading albums</div>';
    }
  }

  /**
   * Load "Continue Listening" list into #continueListeningTracks.
   * @private
   */
  async loadContinueListening() {
    const section = document.getElementById('continueListeningSection');
    const container = document.getElementById('continueListeningTracks');
    const viewAllLink = document.getElementById('continueListeningViewAll');
    if (!container) return;

    try {
      // console.log('⏯️ Loading continue listening...');
      const response = await fetch('/api/home/data');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const tracks = payload?.data?.continue_listening || [];
      const totalInProgress = payload?.data?.total_in_progress || 0;

      // Hide entire section if no tracks
      if (!tracks.length || tracks.length === 0) {
        if (section) section.style.display = 'none';
        // console.log('✅ No in-progress tracks, hiding section');
        return;
      }

      // Show section if we have tracks
      if (section) section.style.display = 'block';

      // Show "View All" link if there are more than 2 tracks
      if (viewAllLink) {
        viewAllLink.style.display = totalInProgress > 2 ? 'flex' : 'none';
      }

      container.innerHTML = tracks.map((t) => this.tplContinueListening(t)).join('');

      // Bind resume buttons
      container.querySelectorAll('.resume-btn').forEach((btn) => {
        btn.addEventListener('click', (e) => this.handleResumeClick(e));
      });

      // console.log(`✅ Loaded ${tracks.length} in-progress tracks (${totalInProgress} total) - section visible`);
    } catch (error) {
      // console.error('❌ Error loading continue listening:', error);
      if (section) section.style.display = 'none';
    }
  }

  // =============================================================================
  // TEMPLATE BUILDERS
  // =============================================================================

  /** @private */
  tplPopularTrack(item) {
    const albumId = item.album_id || item.id;
    return `
      <div class="album-preview-card" data-spa-href="/album/${albumId}">
        <div class="album-cover-container">
          <img src="${item.cover_path}" alt="${this.escape(item.title)}" class="album-cover"
               onerror="this.src='/static/images/default-album.jpg'">
          <div class="album-hover"><i class="fas fa-play"></i></div>
        </div>
        <div class="album-info">
          <h3>${this.escape(item.title)}</h3>
          <p>${this.escape(item.album_title || 'Album')}</p>
          ${item.total_plays ? `<p class="plays">${item.total_plays} plays</p>` : ''}
        </div>
      </div>
    `;
  }

  /** @private */
  tplRecentUpdate(album) {
    return `
      <div class="album-preview-card" data-spa-href="/album/${album.id}">
        <div class="album-cover-container">
          <img src="${album.cover_path}" alt="${this.escape(album.title)}" class="album-cover"
               onerror="this.src='/static/images/default-album.jpg'">
          <div class="album-hover"><i class="fas fa-play"></i></div>
        </div>
        <div class="album-info">
          <h3>${this.escape(album.title)}</h3>
          <p>${album.track_count} tracks</p>
          <div class="album-meta">
            <span class="latest-update">Updated ${album.latest_update ? album.latest_update.split('T')[0] : 'Recently'}</span>
            ${album.latest_track ? `<span class="new-track">Latest: ${this.escape(album.latest_track.title)}</span>` : ''}
          </div>
        </div>
      </div>
    `;
  }

  /** @private */
  tplRecentAddition(album) {
    return `
      <div class="album-preview-card" data-spa-href="/album/${album.id}">
        <div class="album-cover-container">
          <img src="${album.cover_path}" alt="${this.escape(album.title)}" class="album-cover"
               onerror="this.src='/static/images/default-album.jpg'">
          <div class="album-hover"><i class="fas fa-play"></i></div>
        </div>
        <div class="album-info">
          <h3>${this.escape(album.title)}</h3>
          <p>${album.track_count} tracks</p>
          <div class="album-meta">
            <span class="time-added">Added ${album.created_at ? album.created_at.split('T')[0] : 'Recently'}</span>
          </div>
        </div>
      </div>
    `;
  }

  /** @private */
  tplMyAlbum(album) {
    return `
      <div class="album-preview-card" data-spa-href="/album/${album.id}">
        <div class="album-cover-container">
          <img src="${album.cover_path}" alt="${this.escape(album.title)}" class="album-cover"
               onerror="this.src='/static/images/default-album.jpg'">
          <div class="album-hover"><i class="fas fa-play"></i></div>
        </div>
        <div class="album-info">
          <h3>${this.escape(album.title)}</h3>
          <p>${album.track_count || 0} tracks</p>
        </div>
      </div>
    `;
  }

  /** @private */
  tplContinueListening(track) {
    return `
      <div class="progress-track" data-track-id="${track.id}" data-position="${track.position || 0}">
        <img src="${track.cover_path}" alt="${this.escape(track.title)}" class="track-thumbnail"
             onerror="this.src='/static/images/default-album.jpg'">
        <div class="track-info">
          <div class="track-title">${this.escape(track.title)}</div>
          <div class="track-album">${this.escape(track.album_title)}</div>
          <div class="progress-container">
            <div class="progress-bar" style="width: ${track.progress}%"></div>
          </div>
        </div>
        <button class="resume-btn"><i class="fas fa-play"></i> Resume</button>
      </div>
    `;
  }

  // =============================================================================
  // CAROUSEL LOGIC
  // =============================================================================

  /**
   * Duplicate a carousel's innerHTML once for "infinite" scroll feel.
   * @param {string} carouselId
   * @private
   */
  duplicateCarouselItems(carouselId) {
    if (this.isCarouselDuplicated) return;
    const carousel = document.getElementById(carouselId);
    if (!carousel) return;
    carousel.innerHTML = carousel.innerHTML + carousel.innerHTML;
    this.isCarouselDuplicated = true;
    // console.log('🔄 Duplicated carousel items for infinite scroll');
  }

  /**
   * Scroll a horizontal container smoothly by a fixed amount.
   * @param {string} containerId
   * @param {number} scrollAmount
   * @private
   */
  scrollCarousel(containerId, scrollAmount) {
    if (containerId === 'popularTracksContainer') {
      this.stopAutoScroll();
      this.isUserInteracting = true;
    }

    const container = document.getElementById(containerId);
    if (!container) {
      // console.error(`❌ Carousel container not found: ${containerId}`);
      return;
    }

    container.scrollBy({ left: scrollAmount, behavior: 'smooth' });

    if (containerId === 'popularTracksContainer') {
      this.pauseAutoScrollWithResume();
    }
  }

  /**
   * Pause auto-scroll briefly, then resume after RESUME_DELAY.
   * @private
   */
  pauseAutoScrollWithResume() {
    this.stopAutoScroll();
    this.isUserInteracting = true;

    if (this.autoScrollResumeTimeout) {
      clearTimeout(this.autoScrollResumeTimeout);
    }

    this.autoScrollResumeTimeout = setTimeout(() => {
      this.isUserInteracting = false;
      this.startAutoScroll();
    }, this.RESUME_DELAY);
  }

  /**
   * Starts auto-scroll for the popular tracks carousel.
   * @private
   */
  startAutoScroll() {
    if (this.autoScrollInterval || this.isUserInteracting) return;

    // console.log('▶️ Starting auto-scroll');

    this.autoScrollInterval = setInterval(() => {
      const container = document.getElementById('popularTracksContainer');
      const carousel = document.getElementById('popularTracksCarousel');
      if (!container || !carousel) return;

      const cards = carousel.querySelectorAll('.album-preview-card');
      if (!cards.length) return;

      const lastCard = cards[cards.length - 1];
      const containerRect = container.getBoundingClientRect();
      const lastCardRect = lastCard.getBoundingClientRect();
      const isLastCardVisible = lastCardRect.right <= containerRect.right + 50;

      if (isLastCardVisible) {
        container.scrollTo({ left: 0, behavior: 'smooth' });
      } else {
        container.scrollBy({ left: 140, behavior: 'smooth' });
      }
    }, 3000);
  }

  /**
   * Stops the auto-scroll timer.
   * @private
   */
  stopAutoScroll() {
    if (!this.autoScrollInterval) return;
    // console.log('⏸️ Stopping auto-scroll');
    clearInterval(this.autoScrollInterval);
    this.autoScrollInterval = null;
  }

  // =============================================================================
  // PLAYBACK + UTILITIES
  // =============================================================================

  /**
   * Resume button handler for Continue Listening tiles.
   * @param {MouseEvent} e
   * @private
   */
  async handleResumeClick(e) {
    e.preventDefault();
    e.stopPropagation();

    const trackElement = e.target.closest('.progress-track');
    if (!trackElement) return;

    const trackId = trackElement.dataset.trackId;
    const savedPosition = parseFloat(trackElement.dataset.position) || 0;

    try {
      if (window.persistentPlayer) {
        // ✅ Extract track data directly from DOM (no API call needed!)
        const titleElement = trackElement.querySelector('.track-title');
        const albumElement = trackElement.querySelector('.track-album');
        const coverElement = trackElement.querySelector('.track-thumbnail');

        const trackData = {
          title: titleElement ? titleElement.textContent.trim() : 'Unknown Track',
          album_title: albumElement ? albumElement.textContent.trim() : 'Unknown Album',
          cover_path: coverElement ? coverElement.src : '/static/images/default-album.jpg'
        };

        // console.log('🎵 Resuming track from DOM data:', trackData);

        await window.persistentPlayer.playTrack(
          trackId,
          trackData.title,
          trackData.album_title,
          trackData.cover_path,
          true  // ✅ Auto-play when resuming
        );

        setTimeout(() => {
          if (window.persistentPlayer.audio) {
            window.persistentPlayer.audio.currentTime = savedPosition;
          }
        }, 500);

        this.toast('Resuming playback...');
      } else {
        // Fallback: navigate to the dedicated player page
        window.location.assign(`/player/${trackId}`);
      }
    } catch (error) {
      // console.error('❌ Error resuming track:', error);
      this.toast('Error resuming track', 'error');
    }
  }

  /**
   * Show a lightweight toast (local-only fallback if global showToast not present).
   * @param {string} message
   * @param {'success'|'error'|'info'} [type='success']
   * @private
   */
  toast(message, type = 'success') {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type, 2500);
      return;
    }
    const existing = document.querySelector('.success-message');
    if (existing) existing.remove();
    const div = document.createElement('div');
    div.className = 'success-message';
    div.textContent = message;
    if (type === 'error') div.style.background = '#ef4444';
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 2500);
  }

  /**
   * Escape helper to prevent HTML injection.
   * @param {string} s
   * @returns {string}
   * @private
   */
  escape(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // =============================================================================
  // STATIC MARKUP (CSS + HTML)
  // =============================================================================

  /**
   * Inline styles copied from your existing Home page to preserve appearance.
   * @private
   */
  styles() {
    return `
<style>
  :root {
    --surface: #2d2d2d;
    --border-color: #444444;
    --shadow: rgba(0, 0, 0, 0.2);
    --card-bg: #1f2937;
    --text-primary: #ffffff;
    --text-secondary: #aaaaaa;
    --bg-primary: #1a1a1a;
    --button-primary: #3b82f6;
    --button-hover: #2563eb;
    --hover-bg: #3a3a3a;
    --progress-fill: #3b82f6;
    --success-color: #10b981;
    --border-hover-color: #555555;
  }
  .home-content { max-width: 1200px; margin: 0 auto; padding: 1.5rem 1rem; }
  .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
  .section-header h2 { font-size: 1.4rem; font-weight: 600; color: var(--text-primary); }
  .view-all-link { display: flex; align-items: center; gap: 0.5rem; font-weight: 500; color: var(--button-primary); transition: color 0.2s ease; text-decoration: none; cursor: pointer; }
  .view-all-link:hover { color: var(--button-hover); }

  .album-preview-card { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; transition: transform 0.2s, box-shadow 0.2s, border-color 0.2s; cursor: pointer; display: flex; flex-direction: column; position: relative; }
  .album-preview-card:hover { transform: translateY(-3px); box-shadow: 0 6px 12px rgba(0,0,0,0.15); border-color: var(--border-hover-color); }
  .album-cover-container { position: relative; width: 100%; aspect-ratio: 1; overflow: hidden; background: rgba(0,0,0,0.2); }
  .album-cover { width: 100%; height: 100%; object-fit: cover; transition: transform 0.3s; }
  .album-preview-card:hover .album-cover { transform: scale(1.05); }
  .album-hover { position: absolute; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; opacity: 0; transition: opacity 0.3s; }
  .album-preview-card:hover .album-hover { opacity: 1; }
  .album-hover i { color: white; font-size: 2.3rem; text-shadow: 0 2px 4px rgba(0,0,0,0.3); transform: translateY(10px); transition: transform 0.3s; }
  .album-preview-card:hover .album-hover i { transform: translateY(0); }
  .album-info { padding: 0.5rem; background: var(--card-bg); flex-grow: 1; display: flex; flex-direction: column; gap: 0.15rem; color: white; }
  .album-info h3 { font-size: 0.75rem; font-weight: 600; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .album-info p { font-size: 0.7rem; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin: 0; }

  .popular-tracks { background: var(--surface); border: 1px solid var(--border-color); border-radius: 12px; padding: 1rem; margin-bottom: 1.5rem; box-shadow: 0 2px 4px var(--shadow); position: relative; }
  .popular-tracks h2 { font-size: 1.4rem; font-weight: 600; color: var(--text-primary); margin-bottom: 0.75rem; }
  .carousel-wrapper { position: relative; }
  .carousel-container { width: 100%; height: 220px; background: var(--surface); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.5rem; overflow-x: scroll; overflow-y: hidden; display: flex; align-items: flex-start; scrollbar-width: none; scroll-behavior: smooth; }
  .carousel-container::-webkit-scrollbar { display: none; }
  .carousel-track { display: flex; flex-direction: row; flex-wrap: nowrap; gap: 0.6rem; height: 190px; width: max-content; align-items: flex-start; padding: 0.25rem; }
  .popular-tracks .album-preview-card { flex: 0 0 auto; width: 120px; min-width: 120px; height: 190px; display: inline-flex; flex-direction: column; }
  .popular-tracks .album-cover-container { width: 100%; height: 120px; min-height: 120px; max-height: 120px; flex: 0 0 auto; }

  .carousel-button { position: absolute; top: 50%; transform: translateY(-50%); width: 30px; height: 30px; background: #fff; border: none; border-radius: 50%; cursor: pointer; z-index: 10; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); transition: all 0.2s ease; }
  .carousel-button.prev { left: 0.5rem; }
  .carousel-button.next { right: 0.5rem; }
  .carousel-button i { color: #1a1a1a; font-size: 0.85rem; }
  .carousel-button:hover { background: rgba(255,255,255,0.9); transform: translateY(-50%) scale(1.1); }
  .carousel-button:active { transform: translateY(-50%) scale(0.95); }

  .loading { text-align: center; padding: 2rem; color: var(--text-secondary); }

  .small-carousel .carousel-container { height: 170px; }
  .small-carousel .carousel-track { height: 140px; }
  .small-carousel .album-preview-card { width: 100px; min-width: 100px; height: 160px; }
  .small-carousel .album-cover-container { height: 100px; min-height: 100px; max-height: 100px; }

  .continue-listening { background: var(--surface); border: 1px solid var(--border-color); border-radius: 12px; padding: 0.75rem; margin-bottom: 1.5rem; box-shadow: var(--shadow) 0 2px 4px; width: 100%; position: relative; box-sizing: border-box; }
  .tracks-list { display: grid; gap: 0.5rem; }
  .progress-track { display: grid; grid-template-columns: 40px 1fr auto; gap: 0.5rem; align-items: center; padding: 0.5rem; background: var(--surface); border: 1px solid var(--border-color); border-radius: 8px; transition: all 0.2s ease; }
  .progress-track:hover { background: var(--hover-bg); transform: translateX(4px); }
  .track-thumbnail { width: 40px; height: 40px; border-radius: 4px; object-fit: cover; }
  .track-info { overflow: hidden; }
  .track-title { font-size: 0.85rem; font-weight: 600; margin-bottom: 0.25rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .track-album { font-size: 0.75rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text-secondary); }
  .progress-container { grid-column: 1 / -1; height: 3px; background: var(--border-color); border-radius: 2px; overflow: hidden; margin-top: 0.5rem; }
  .progress-bar { height: 100%; background: var(--progress-fill); border-radius: 2px; transition: width 0.3s ease; }
  .resume-btn { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0.75rem; background: var(--button-primary); color: white; border: none; border-radius: 6px; font-size: 0.75rem; font-weight: 500; cursor: pointer; transition: all 0.2s ease; }
  .resume-btn:hover { background: var(--button-hover); transform: translateY(-1px); }

  .no-content { text-align: center; padding: 2rem 1rem; color: var(--text-secondary); }
  .no-content i { font-size: 1.75rem; margin-bottom: 0.75rem; opacity: 0.7; }
  .btn-primary { display: inline-flex; align-items: center; padding: 0.5rem 0.75rem; background: var(--button-primary); color: white; border-radius: 6px; font-weight: 500; text-decoration: none; transition: background-color 0.2s ease; }
  .btn-primary:hover { background: var(--button-hover); }
  .album-meta { margin-top: 0.2rem; font-size: 0.65rem; color: var(--text-secondary); line-height: 1.3; }
  .latest-update, .time-added, .new-track { display: block; }
  .new-track { color: var(--button-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .user-status { background: var(--surface); border: 1px solid var(--border-color); border-radius: 8px; padding: 1rem; margin-bottom: 1rem; display: none; }
  .user-info h3 { font-size: 1rem; font-weight: 600; margin: 0 0 0.5rem 0; color: var(--text-primary); }
  .tier-details { font-size: 0.85rem; color: var(--text-secondary); }
  .tier-price { font-weight: 600; color: var(--button-primary); margin: 0.25rem 0; }
  .tier-description { margin: 0.25rem 0 0 0; }

  .success-message { position: fixed; bottom: 1rem; right: 1rem; background: var(--success-color); color: white; padding: 0.75rem 1.25rem; border-radius: 6px; font-size: 0.85rem; box-shadow: 0 2px 8px rgba(0,0,0,0.3); z-index: 1000; animation: slideIn 0.3s ease, fadeOut 0.5s ease 2s forwards; }
  @keyframes slideIn { from { transform: translateX(400px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
  @keyframes fadeOut { from { opacity: 1; } to { opacity: 0; } }

  /* Permission-based defaults (hidden; shown by JS) */
  .btn-create-album, .create-btn { display: none; }
  .btn-danger, .delete-track, .delete-album-btn { display: none; }
</style>
    `;
  }

  /**
   * HTML shell that matches your previous layout/IDs exactly.
   * @private
   */
  layout() {
    return `
<div class="home-content">
  <!-- Continue Listening (shown at top if user has tracks in progress) -->
  <section class="continue-listening" id="continueListeningSection" style="display:none;">
    <div class="section-header">
      <h2>Continue Listening</h2>
      <a href="/continue-listening" class="view-all-link" id="continueListeningViewAll" style="display:none;" data-spa-link>
        View All <i class="fas fa-arrow-right"></i>
      </a>
    </div>
    <div class="tracks-list" id="continueListeningTracks">
      <div class="loading">Loading in-progress tracks...</div>
    </div>
  </section>

  <!-- Popular Tracks -->
  <section class="popular-tracks">
    <h2>Popular Tracks</h2>
    <div class="carousel-wrapper">
      <button class="carousel-button prev" data-carousel="popularTracks">
        <i class="fas fa-chevron-left"></i>
      </button>
      <div class="carousel-container" id="popularTracksContainer">
        <div class="carousel-track" id="popularTracksCarousel">
          <div class="loading">Loading popular tracks...</div>
        </div>
      </div>
      <button class="carousel-button next" data-carousel="popularTracks">
        <i class="fas fa-chevron-right"></i>
      </button>
    </div>
  </section>

  <!-- Recently Updated -->
  <section class="popular-tracks small-carousel">
    <div class="section-header">
      <h2>Recently Updated</h2>
    </div>
    <div class="carousel-wrapper">
      <button class="carousel-button prev" data-carousel="recentUpdates">
        <i class="fas fa-chevron-left"></i>
      </button>
      <div class="carousel-container" id="recentUpdatesContainer">
        <div class="carousel-track" id="recentUpdatesCarousel">
          <div class="loading">Loading recent updates...</div>
        </div>
      </div>
      <button class="carousel-button next" data-carousel="recentUpdates">
        <i class="fas fa-chevron-right"></i>
      </button>
    </div>
  </section>

  <!-- Recent Additions -->
  <section class="popular-tracks small-carousel">
    <div class="section-header">
      <h2>Recent Additions</h2>
    </div>
    <div class="carousel-wrapper">
      <button class="carousel-button prev" data-carousel="recentAdditions">
        <i class="fas fa-chevron-left"></i>
      </button>
      <div class="carousel-container" id="recentAdditionsContainer">
        <div class="carousel-track" id="recentAdditionsCarousel">
          <div class="loading">Loading recent additions...</div>
        </div>
      </div>
      <button class="carousel-button next" data-carousel="recentAdditions">
        <i class="fas fa-chevron-right"></i>
      </button>
    </div>
  </section>

  <!-- My Albums -->
  <section class="popular-tracks small-carousel">
    <div class="section-header">
      <h2>My Albums</h2>
      <a href="/my-albums" class="view-all-link" data-spa-link>
        View All <i class="fas fa-arrow-right"></i>
      </a>
    </div>
    <div class="carousel-wrapper" id="myAlbumsWrapper">
      <button class="carousel-button prev" data-carousel="myAlbums">
        <i class="fas fa-chevron-left"></i>
      </button>
      <div class="carousel-container" id="myAlbumsContainer">
        <div class="carousel-track" id="myAlbumsCarousel">
          <div class="loading">Loading my albums...</div>
        </div>
      </div>
      <button class="carousel-button next" data-carousel="myAlbums">
        <i class="fas fa-chevron-right"></i>
      </button>
    </div>
  </section>

</div>
    `;
  }

  /**
   * Lifecycle destroy: stop timers and listeners.
   * @returns {Promise<void>}
   */
  async destroy() {
    // console.log('🏠 Home: Destroying...');

    this.stopAutoScroll();

    if (this.autoScrollResumeTimeout) {
      clearTimeout(this.autoScrollResumeTimeout);
      this.autoScrollResumeTimeout = null;
    }

    if (this.resizeHandler) {
      window.removeEventListener('resize', this.resizeHandler);
      this.resizeHandler = null;
    }

    // console.log('✅ Home: Destroyed');
  }
}

/* ---------------------------------------------------------------------------
   Optional: keep a global helper for legacy handlers, but make it SPA-first.
--------------------------------------------------------------------------- */
if (typeof window !== 'undefined') {
  window.navigateToAlbum = function (albumId) {
    if (!albumId) return console.error('❌ Album ID is missing');
    const path = `/album/${albumId}`;
    if (window.spaRouter?.navigate) return window.spaRouter.navigate(path);
    if (window.router?.navigate) return window.router.navigate(path);
    window.location.assign(path);
  };
}
