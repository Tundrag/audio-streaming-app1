// catalog-shared-spa.js - Universal controller for Catalog page (SSR and SPA modes)

export class CatalogController {
  constructor(mode = 'spa') {
    this.mode = mode; // 'ssr' or 'spa'

    /** @private {HTMLElement|null} Root container where we render the shell. */
    this.container = null;
    /** @private {HTMLElement|null} Search input element. */
    this.searchInput = null;
    /** @private {HTMLElement|null} Alphabet filter element. */
    this.alphabetFilter = null;
    /** @private {HTMLElement|null} Sections wrapper. */
    this.albumSections = null;
    /** @private {Record<string, Array<Object>>} Grouped albums data. */
    this.albumsByLetter = {};
    /** @private {Array<Function>} Cleanup callbacks for event listeners. */
    this.cleanupFunctions = [];
    /** @private {number|null} Debounce timer id. */
    this.searchTimeout = null;

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
    console.log(`📚 Catalog: Mounting in ${this.mode} mode...`);

    this.container = document.querySelector('main') || document.body;
    if (!this.container) throw new Error('CatalogController mount: root container not found');

    if (this.mode === 'ssr') {
      // SSR: Read bootstrap data from DOM if available
      this.hydrateFromDOM();
    }

    // Cache element refs inside the shell
    this.searchInput    = this.container.querySelector('#albumSearch');
    this.alphabetFilter = this.container.querySelector('#alphabetFilter');
    this.albumSections  = this.container.querySelector('#albumSections');

    if (!this.searchInput || !this.alphabetFilter || !this.albumSections) {
      throw new Error('CatalogController mount: expected elements not found in shell');
    }

    // Fetch data if not hydrated
    if (!this.albumsByLetter || Object.keys(this.albumsByLetter).length === 0) {
      await this.fetchAlbums();
    }

    this.renderSections(this.albumsByLetter);

    // Attach listeners (with cleanup tracking)
    this.attachEventListeners();

    console.log('✅ Catalog: Mounted successfully');
  }

  // ✅ Read data from DOM (SSR mode)
  hydrateFromDOM() {
    const bootstrapScript = document.getElementById('catalog-bootstrap-data');
    if (bootstrapScript) {
      try {
        this.bootstrapData = JSON.parse(bootstrapScript.textContent);
        this.albumsByLetter = this.bootstrapData.albums_by_letter || {};
        console.log('📦 Hydrated catalog data from DOM');
      } catch (error) {
        console.error('Error parsing bootstrap data:', error);
      }
    }
  }

  // ✅ Generate HTML for SPA mode
  generateHTML() {
    return `
      <div class="directory-container">
        <div class="search-container">
          <i class="fas fa-search search-icon"></i>
          <input
            type="text"
            class="search-input"
            placeholder="Search albums..."
            id="albumSearch"
            aria-label="Search albums"
          >
        </div>

        <div class="alphabet-filter" id="alphabetFilter">
          <button class="letter-btn active" data-letter="all">All</button>
          <button class="letter-btn" data-letter="#">#</button>
          ${'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').map(l => `
            <button class="letter-btn" data-letter="${l}">${l}</button>
          `).join('')}
        </div>

        <div id="albumSections">
          <!-- Sections populated in mount() after fetching data -->
          <div class="letter-section loading" data-letter="init">
            <h2 class="letter-heading">
              Loading…
              <span class="album-count">( … )</span>
            </h2>
          </div>
        </div>
      </div>
    `;
  }

  // =============================================================================
  // DATA
  // =============================================================================

  /**
   * Fetch catalog data and store as `albumsByLetter`.
   * Endpoint: GET /api/catalog -> { albums_by_letter: { "A": [...], ... } }
   * @private
   */
  async fetchAlbums() {
    try {
      const res = await fetch('/api/catalog');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.albumsByLetter = data.albums_by_letter || {};
    } catch (err) {
      console.error('❌ Error fetching albums:', err);
      this.showError('Failed to load catalog');
      throw err;
    }
  }

  // =============================================================================
  // RENDER SECTIONS
  // =============================================================================

  /**
   * Render all letter sections into #albumSections.
   * @param {Record<string, Array<Object>>} byLetter
   * @private
   */
  renderSections(byLetter) {
    if (!this.albumSections) return;

    const html = Object.entries(byLetter)
      .map(([letter, albums]) => this.tplLetterSection(letter, albums))
      .join('');

    this.albumSections.innerHTML = html || `
      <div class="error-container" style="text-align:center;padding:2rem;color:#666;">
        <i class="fas fa-exclamation-circle" style="font-size:2rem;color:#ef4444;"></i>
        <h2>No albums found</h2>
        <p>Try adjusting your filters or search.</p>
      </div>
    `;
  }

  /**
   * Template for a single letter section.
   * @param {string} letter
   * @param {Array<Object>} albums
   * @returns {string}
   * @private
   */
  tplLetterSection(letter, albums = []) {
    if (!albums.length) return '';
    return `
      <div class="letter-section" data-letter="${letter}">
        <h2 class="letter-heading">
          ${letter}
          <span class="album-count">(${albums.length})</span>
        </h2>
        <ul class="album-list">
          ${albums.map(a => this.tplAlbumLink(a)).join('')}
        </ul>
      </div>
    `;
  }

  /**
   * Template for a single album link.
   * @param {{id:string,title:string,tier_restrictions?:any}} album
   * @returns {string}
   * @private
   */
  tplAlbumLink(album) {
    const crown = album.tier_restrictions ? '<i class="fas fa-crown crown-icon"></i>' : '';
    return `
      <li>
        <a href="/album/${album.id}" class="album-link" data-album-id="${album.id}" data-spa-link>
          ${this.escape(album.title)}
          ${crown}
        </a>
      </li>
    `;
  }

  // =============================================================================
  // EVENTS
  // =============================================================================

  /**
   * Bind alphabet filter and search with cleanup tracking.
   * @private
   */
  attachEventListeners() {
    // Ensure previous listeners are gone
    this.cleanup();

    // Letter buttons
    const onLetterClick = (e) => {
      const btn = e.target.closest('.letter-btn');
      if (!btn) return;

      // Active state
      this.alphabetFilter.querySelectorAll('.letter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const selectedLetter = btn.dataset.letter;
      const q = (this.searchInput.value || '').toLowerCase();
      this.applyFilters(selectedLetter, q);
    };
    this.alphabetFilter.addEventListener('click', onLetterClick);
    this.cleanupFunctions.push(() => this.alphabetFilter.removeEventListener('click', onLetterClick));

    // Search with debounce
    const onSearch = (e) => {
      clearTimeout(this.searchTimeout);
      this.searchTimeout = setTimeout(() => {
        const q = e.target.value.toLowerCase();
        const activeLetter = this.alphabetFilter.querySelector('.letter-btn.active')?.dataset.letter || 'all';
        this.applyFilters(activeLetter, q);
      }, 200);
    };
    this.searchInput.addEventListener('input', onSearch);
    this.cleanupFunctions.push(() => {
      this.searchInput.removeEventListener('input', onSearch);
      clearTimeout(this.searchTimeout);
    });
  }

  /**
   * Filter visible sections and links by letter and search query.
   * @param {string} letter
   * @param {string} query
   * @private
   */
  applyFilters(letter, query) {
    const sections = this.container.querySelectorAll('.letter-section');
    sections.forEach(section => {
      const isLetterMatch = (letter === 'all') || (section.dataset.letter === letter);
      let visibleInSection = false;

      section.querySelectorAll('.album-link').forEach(link => {
        const title = link.textContent.trim().toLowerCase();
        const matchesSearch = !query || title.includes(query);
        const li = link.parentElement;
        const show = isLetterMatch && matchesSearch;
        li.style.display = show ? '' : 'none';
        if (show) visibleInSection = true;
      });

      section.style.display = visibleInSection ? '' : 'none';
    });

    this.updateLetterButtonStates(query);
  }

  /**
   * Disable letter buttons that have no visible albums under current search.
   * @param {string} query
   * @private
   */
  updateLetterButtonStates(query) {
    const allLinks = Array.from(this.container.querySelectorAll('.album-link'));

    this.alphabetFilter.querySelectorAll('.letter-btn').forEach(btn => {
      const letter = btn.dataset.letter;
      if (letter === 'all') return;

      let hasAlbums = false;
      if (letter === '#') {
        hasAlbums = allLinks.some(a => {
          const t = a.textContent.trim();
          const first = (t[0] || '').toUpperCase();
          return !/[A-Z]/.test(first) && t.toLowerCase().includes(query);
        });
      } else {
        hasAlbums = allLinks.some(a => {
          const t = a.textContent.trim();
          return (t[0] || '').toUpperCase() === letter && t.toLowerCase().includes(query);
        });
      }

      btn.classList.toggle('disabled', !hasAlbums);
    });
  }

  // =============================================================================
  // UTIL
  // =============================================================================

  /**
   * Remove all bound listeners and pending timers.
   */
  cleanup() {
    this.cleanupFunctions.forEach(fn => fn());
    this.cleanupFunctions = [];
    if (this.searchTimeout) {
      clearTimeout(this.searchTimeout);
      this.searchTimeout = null;
    }
  }

  /**
   * Render an inline error in the container.
   * @param {string} message
   * @private
   */
  showError(message) {
    if (!this.container) return;
    this.container.innerHTML = `
      <div class="error-container" style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:400px;text-align:center;">
        <i class="fas fa-exclamation-circle" style="font-size:3rem;color:#ef4444;margin-bottom:1rem;"></i>
        <h2 style="font-size:1.5rem;margin-bottom:0.5rem;">Error</h2>
        <p style="margin-bottom:1.5rem;color:#666;">${this.escape(message)}</p>
        <button class="btn-primary" style="padding:0.5rem 0.75rem;border-radius:6px;border:none;background:#60a5fa;color:#fff;cursor:pointer;" onclick="location.reload()">Try Again</button>
      </div>
    `;
  }

  /**
   * Basic HTML escape utility.
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

  /**
   * Destroy phase: unbind listeners, clear timers.
   * @returns {Promise<void>}
   */
  async destroy() {
    console.log('🧹 Catalog: Destroying...');
    this.cleanup();
  }
}
