// player-shared-spa.js - Universal controller for Player page (SSR and SPA modes)

/**
 * PlayerController - Universal player page controller
 *
 * External Dependencies:
 * - /static/js/comment-system.js - Comment system functionality
 * - /static/js/ReadAlongUI.js - Read-along UI components
 * - /static/js/ReadAlongContent.js - Read-along content management
 * - /static/js/ReadAlongCore.js - Read-along core functionality
 * - window.persistentPlayer - Global persistent player instance (from persistence.js)
 * - window.readAlongSPAOverlay - Read-along overlay system
 * - window.liveCommentUpdater - Live comment update system
 * - window.showUpgradeModal() - Upgrade modal (defined in base.html)
 *
 * Bootstrap Data (SSR mode):
 * - window.currentTrackId
 * - window.currentTrackTitle
 * - window.currentAlbumTitle
 * - window.currentAlbumCoverPath
 * - window.currentAlbumId
 * - window.currentTrackType
 * - window.trackData (from #player-data JSON)
 * - window.albumData (from #player-data JSON)
 * - window.userData (from #player-data JSON)
 */

export class PlayerController {
  constructor(mode = 'spa', trackId = null) {
    this.mode = mode; // 'ssr' or 'spa'

    // Player state
    this.trackId = trackId; // ✅ Accept trackId for SPA mode
    this.trackTitle = null;
    this.albumTitle = null;
    this.albumCoverPath = null;
    this.albumId = null;
    this.trackType = null;

    // Data objects
    this.trackData = null;
    this.albumData = null;
    this.userData = null;
    this.streamConfigData = null;

    // Sleep timer state
    this.sleepTimerInterval = null;
    this.sleepEndTime = null;

    // Player conflict resolver
    this.playerConflictResolver = null;

    // Dragging state for progress bar
    this.isDragging = false;

    // Bootstrap data from SSR
    this.bootstrapData = null;
  }

  // ============================================================================
  // LIFECYCLE METHODS
  // ============================================================================

  /**
   * Get page title for SPA router
   */
  getPageTitle() {
    let title = null;

    // Try to get album title from various sources
    if (this.albumTitle) {
      title = this.albumTitle;
    } else if (this.albumData?.title) {
      title = this.albumData.title;
    } else if (window.currentAlbumTitle) {
      title = window.currentAlbumTitle;
    } else if (this.trackTitle) {
      // Fallback to track title if available
      title = this.trackTitle;
    } else if (this.trackData?.title) {
      title = this.trackData.title;
    } else {
      // Last resort
      return 'Player';
    }

    // Truncate to 20 characters if too long
    if (title && title.length > 20) {
      return title.substring(0, 20) + '...';
    }

    return title || 'Player';
  }

  /**
   * For SPA mode: Fetch data and generate HTML
   */
  async render() {
    if (this.mode === 'ssr') {
      throw new Error('render() should not be called in SSR mode');
    }


    try {
      const perfStart = performance.now();
      // Extract voice parameter from URL if present
      const urlParams = new URLSearchParams(window.location.search);
      const voiceParam = urlParams.get('voice');
      const url = voiceParam
        ? `/player/${encodeURIComponent(this.trackId)}?voice=${encodeURIComponent(voiceParam)}`
        : `/player/${encodeURIComponent(this.trackId)}`;

      // Fetch the player page HTML from server to extract data
      const response = await fetch(url);

      if (!response.ok) {
        if (response.status === 403) {
          const data = await response.json().catch(() => ({}));
          if (typeof showUpgradeModal === 'function') {
            showUpgradeModal(data.error?.message || 'Access denied');
          }
          throw new Error('Access denied');
        }
        throw new Error(`HTTP ${response.status}: Failed to load player data`);
      }

      const html = await response.text();

      // Parse HTML to extract bootstrap data
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');
      const playerDataElement = doc.getElementById('player-data');

      if (playerDataElement) {
        const data = JSON.parse(playerDataElement.textContent);
        this.trackData = data.track;
        this.userData = data.user;
        this.albumData = data.album;
        this.streamConfigData = data.stream_config;

        // Extract track info
        this.trackId = this.trackData?.id;
        this.trackTitle = this.trackData?.title;
        this.trackType = this.trackData?.track_type || 'audio';
        this.albumId = this.albumData?.id;
        this.albumTitle = this.albumData?.title;
        this.albumCoverPath = this.albumData?.cover_path;

      } else {
        throw new Error('Could not find player data in response');
      }

      // Generate HTML from data
      return this.generateHTML();

    } catch (error) {
      throw error;
    }
  }

  /**
   * Generate HTML for SPA mode (same structure as player.html template)
   */
  generateHTML() {
    const track = this.trackData || {};
    const album = this.albumData || {};
    const user = this.userData || {};

    // Generate prev/next track links
    const prevTrack = this.getPrevTrack();
    const nextTrack = this.getNextTrack();

    return `
<!-- ✅ Bootstrap data for hydration (needed for both SSR and SPA) -->
<script id="player-data" type="application/json">
${JSON.stringify({
  track: track,
  album: album,
  user: user,
  stream_config: this.streamConfigData
})}
</script>

<!-- ✅ Global variables for backwards compatibility -->
<script>
  window.currentTrackId = "${track.id || ''}";
  window.currentTrackTitle = "${this.escapeHtml(track.title || '')}";
  window.currentAlbumTitle = "${this.escapeHtml(album.title || '')}";
  window.currentAlbumCoverPath = "${album.cover_path || ''}";
  window.currentTrackFilePath = "${track.file_path || ''}";
  window.currentAlbumId = "${album.id || ''}";
  window.currentTrackType = "${track.track_type || 'audio'}";

  window.currentUserData = {
    id: ${user.id || 'null'},
    username: "${this.escapeHtml(user.username || '')}",
    role: "${user.role || ''}",
    is_creator: ${user.is_creator || false},
    is_team: ${user.is_team || false}
  };

  window.trackData = null;
  window.userData = null;

  document.addEventListener('DOMContentLoaded', function() {
    try {
        const playerDataElement = document.getElementById('player-data');
        if (playerDataElement) {
            const data = JSON.parse(playerDataElement.textContent);
            window.trackData = data.track;
            window.userData = data.user;
            window.albumData = data.album;
            window.streamConfigData = data.stream_config;
        }
    } catch (e) {
    }
});

  // ✅ REMOVED: Body class now managed by PlayerController mount/destroy
  // document.body.classList.add('player-page');
</script>

<div class="player-container">
    <div class="player-background" style="background-image: url('${album.cover_path || ''}')"></div>

    <div class="player-header">
        <a href="/album/${album.id || ''}" class="back-btn" data-spa-href="/album/${album.id || ''}">
            <i class="fas fa-chevron-left"></i>
            Back to Album
        </a>
    </div>

    <div class="player-content">
        <img src="${album.cover_path || ''}" alt="${this.escapeHtml(album.title || '')}" class="album-art">

        <div class="track-info">
            <h1 class="track-title">${this.escapeHtml(track.title || '')}</h1>
            <div class="album-title">${this.escapeHtml(album.title || '')}</div>
        </div>

        <!-- Social Buttons -->
        <div class="social-actions social-actions-inline">
            <button class="social-btn like-track-btn" id="likeTrackBtn" title="Like this track">
                <i class="far fa-heart"></i>
                <span class="count" id="likeCount">0</span>
            </button>
            <button class="social-btn comment-btn" id="commentBtn" title="View comments">
                <i class="far fa-comment"></i>
                <span class="count" id="commentCount">0</span>
            </button>
            <button class="social-btn share-btn" id="shareBtn" title="Share this track">
                <i class="fas fa-share-alt"></i>
                <span class="count" id="shareCount">0</span>
            </button>
        </div>

        <div class="player-controls">
            <div class="progress-container">
                <div class="progress-bar" id="progressBar">
                    <div class="player-progress" id="progress">
                        <div class="progress-knob"></div>
                    </div>
                    <div class="progress-hover"></div>
                </div>
                <div class="time-info">
                    <span id="currentTime">0:00:00</span>
                    <span id="duration">0:00:00</span>
                </div>
            </div>

            <!-- Main controls -->
            <div class="main-controls">
                <button class="control-btn" id="rewind30Btn" title="Rewind 30 seconds">
                  <div class="time-skip-container">
                    <i class="fas fa-rotate-left fa-fw"></i>
                    <span class="time-skip-label">30</span>
                  </div>
                </button>
                <button class="control-btn" id="rewindBtn" title="Rewind 15 seconds">
                  <div class="time-skip-container">
                    <i class="fas fa-rotate-left fa-fw"></i>
                    <span class="time-skip-label">15</span>
                  </div>
                </button>
                <div class="play-btn-container">
                    <button class="auto-play-info-icon" id="autoPlayInfoBtn" title="Auto-play help">
                        <i class="fas fa-info-circle"></i>
                    </button>
                    <button class="control-btn play-btn" id="playBtn" title="Play/Pause">
                        <div class="play-btn-wrapper">
                            <i class="fas fa-play fa-lg" id="playIcon"></i>
                            <svg class="auto-play-arrows" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
                                <!-- Blue circular background -->
                                <circle cx="50" cy="50" r="45" fill="#4A90E2" opacity="0.9"/>

                                <!-- First curved arrow (top-left, clockwise) -->
                                <path d="M 20,40 A 30,30 0 0,1 40,20" fill="none" stroke="white" stroke-width="5" stroke-linecap="round"/>
                                <polygon points="40,20 35,13 47,16" fill="white"/>

                                <!-- Second curved arrow (bottom-right, clockwise) -->
                                <path d="M 80,60 A 30,30 0 0,1 60,80" fill="none" stroke="white" stroke-width="5" stroke-linecap="round"/>
                                <polygon points="60,80 65,87 53,84" fill="white"/>
                            </svg>
                        </div>
                    </button>
                </div>
                <button class="control-btn" id="forwardBtn" title="Forward 15 seconds">
                  <div class="time-skip-container">
                    <i class="fas fa-rotate-right fa-fw"></i>
                    <span class="time-skip-label">15</span>
                  </div>
                </button>
                <button class="control-btn" id="forward30Btn" title="Forward 30 seconds">
                  <div class="time-skip-container">
                    <i class="fas fa-rotate-right fa-fw"></i>
                    <span class="time-skip-label">30</span>
                  </div>
                </button>

                <!-- Secondary controls -->
                <button class="secondary-btn" id="speedBtn">
                    <i class="fas fa-gauge-high"></i>
                    <span class="speed-display" id="speedDisplay"></span>
                    Speed
                </button>

                <button class="secondary-btn" id="sleepBtn">
                    <i class="fas fa-moon"></i>
                    <span class="sleep-timer-display" id="sleepDisplay"></span>
                    Sleep
                </button>

                <button class="secondary-btn voice-change-btn" id="voiceChangeBtn" style="display: none;" onclick="if(window.voiceExtension) { window.voiceExtension.openVoiceModal(); }">
                    <i class="fas fa-microphone-alt"></i>
                    <span id="currentVoiceDisplay">Voice</span>
                </button>

                <button class="secondary-btn read-along-btn" id="readAlongBtn" style="display: none;">
                    <i class="fas fa-book-open"></i>
                    <span>Read Along</span>
                </button>

                <button class="secondary-btn" id="downloadBtn">
                    <i class="fas fa-download"></i>
                    <span class="download-status" id="downloadStatus"></span>
                    Download
                </button>
            </div>

            <!-- Popup menus -->
            <div class="popup-menu" id="speedMenu">
                <div class="speed-menu">
                    <button class="speed-option" data-speed="0.5">0.5x</button>
                    <button class="speed-option" data-speed="0.75">0.75x</button>
                    <button class="speed-option" data-speed="1.0">1x</button>
                    <button class="speed-option" data-speed="1.25">1.25x</button>
                    <button class="speed-option" data-speed="1.5">1.5x</button>
                    <button class="speed-option" data-speed="1.75">1.75x</button>
                    <button class="speed-option" data-speed="2.0">2x</button>
                    <button class="speed-option" data-speed="2.5">2.5x</button>
                    <button class="speed-option" data-speed="3.0">3x</button>
                </div>
            </div>

            <div class="popup-menu" id="sleepMenu">
                <div class="sleep-menu">
                    <button class="sleep-option" data-minutes="5">5 minutes</button>
                    <button class="sleep-option" data-minutes="15">15 minutes</button>
                    <button class="sleep-option" data-minutes="30">30 minutes</button>
                    <button class="sleep-option" data-minutes="45">45 minutes</button>
                    <button class="sleep-option" data-minutes="60">1 hour</button>
                    <button class="sleep-option" data-minutes="custom">Custom</button>
                    <button class="sleep-option" data-minutes="cancel" style="display: none;">Cancel Timer</button>
                </div>
            </div>

            <div class="track-navigation" id="trackNavigation">
                <!-- Navigation buttons will be populated dynamically in setupTrackNavigation() -->
            </div>
        </div>

        ${this.generateCommentSection(user)}
    </div>
</div>

${this.generateProgressOverlay()}
${this.generateToast()}
${this.generateCustomTimerToast()}
    `;
  }

  /**
   * Helper: Get previous track in album
   */
  getPrevTrack() {
    if (!this.albumData?.ordered_track_ids || !this.trackId) return null;
    const trackIds = this.albumData.ordered_track_ids;
    const currentIndex = trackIds.indexOf(this.trackId);
    if (currentIndex > 0) {
      return { id: trackIds[currentIndex - 1] };
    }
    return null;
  }

  /**
   * Helper: Get next track in album
   */
  getNextTrack() {
    if (!this.albumData?.ordered_track_ids || !this.trackId) return null;
    const trackIds = this.albumData.ordered_track_ids;
    const currentIndex = trackIds.indexOf(this.trackId);
    if (currentIndex >= 0 && currentIndex < trackIds.length - 1) {
      return { id: trackIds[currentIndex + 1] };
    }
    return null;
  }

  /**
   * Helper: Escape HTML to prevent XSS
   */
  escapeHtml(text) {
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
  }

  /**
   * Helper: Generate comment section HTML
   */
  generateCommentSection(user) {
    const firstLetter = user.username ? user.username.charAt(0).toUpperCase() : 'U';

    return `
        <!-- Comment Section -->
        <div class="comment-section" id="commentSection">
            <div class="comment-header">
                <h2>Comments <span id="totalCommentCount">(0)</span></h2>
            </div>

            <div class="comment-form">
                <div class="user-avatar">
                    <div class="avatar-placeholder">${firstLetter}</div>
                </div>
                <form id="commentForm" class="new-comment-form">
                    <input type="hidden" name="track_id" value="${this.trackId || ''}">
                    <input type="hidden" name="timestamp" id="commentTimestamp" value="0">
                    <div class="comment-input-container">
                        <textarea name="content" placeholder="Write a comment..." class="comment-input" id="commentInput" required></textarea>
                        <div class="timestamp-display" id="timestampDisplay" style="display: none;">
                            <i class="fas fa-clock"></i>
                            <input type="text" id="timestampValue" value="0:00" class="timestamp-input" placeholder="mm:ss">
                            <button type="button" id="clearTimestamp" class="clear-timestamp-btn">
                                <i class="fas fa-times"></i>
                            </button>
                        </div>
                    </div>
                    <div class="comment-actions">
                        <button type="button" id="addTimestampBtn" class="add-timestamp-btn">
                            <i class="fas fa-clock"></i> Add current time
                        </button>
                        <button type="submit" class="submit-comment-btn">Comment</button>
                    </div>
                </form>
            </div>

            <div class="comment-list" id="commentList">
                <div class="comments-loading">
                    <i class="fas fa-spinner fa-spin"></i> Loading comments...
                </div>
            </div>

            ${this.generateCommentModals()}
        </div>
    `;
  }

  /**
   * Helper: Generate comment modals HTML
   */
  generateCommentModals() {
    return `
            <!-- Edit Comment Modal -->
            <div class="reply-modal" id="editCommentModal">
                <div class="reply-modal-content">
                    <div class="reply-modal-header">
                        <h3>Edit Comment</h3>
                        <button type="button" class="close-modal-btn" id="closeEditModal">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="reply-modal-body">
                        <form id="editCommentForm">
                            <input type="hidden" id="editCommentId" name="comment_id">
                            <input type="hidden" id="editTimestamp" name="timestamp" value="0">

                            <div class="reply-input-container">
                                <textarea name="content" placeholder="Edit your comment..." class="reply-input" id="editCommentInput" required></textarea>
                                <div class="timestamp-display" id="editTimestampDisplay" style="display: none;">
                                    <i class="fas fa-clock"></i>
                                    <input type="text" id="editTimestampValue" value="0:00" class="timestamp-input" placeholder="mm:ss">
                                    <button type="button" id="clearEditTimestamp" class="clear-timestamp-btn">
                                        <i class="fas fa-times"></i>
                                    </button>
                                </div>
                            </div>

                            <div class="reply-form-actions">
                                <div class="reply-form-tools">
                                    <button type="button" id="addEditTimestampBtn" class="add-timestamp-btn">
                                        <i class="fas fa-clock"></i> Add current time
                                    </button>
                                </div>
                                <div class="reply-form-buttons">
                                    <button type="button" id="cancelEdit" class="cancel-reply-btn">Cancel</button>
                                    <button type="submit" class="submit-reply-btn">Save Changes</button>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Reply Modal -->
            <div class="reply-modal" id="replyModal">
                <div class="reply-modal-content">
                    <div class="reply-modal-header">
                        <h3>Reply to Comment</h3>
                        <button type="button" class="close-modal-btn" id="closeReplyModal">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="reply-modal-body">
                        <div class="parent-comment" id="parentCommentContent"></div>
                        <form id="replyForm">
                            <input type="hidden" id="replyParentId" name="parent_id">
                            <input type="hidden" id="replyTimestamp" name="timestamp" value="0">

                            <div class="reply-input-container">
                                <textarea name="content" placeholder="Write your reply..." class="reply-input" id="replyInput" required></textarea>
                                <div class="timestamp-display" id="replyTimestampDisplay" style="display: none;">
                                    <i class="fas fa-clock"></i>
                                    <input type="text" id="replyTimestampValue" value="0:00" class="timestamp-input" placeholder="mm:ss">
                                    <button type="button" id="clearReplyTimestamp" class="clear-timestamp-btn">
                                        <i class="fas fa-times"></i>
                                    </button>
                                </div>
                            </div>

                            <div class="reply-form-actions">
                                <div class="reply-form-tools">
                                    <button type="button" id="addReplyTimestampBtn" class="add-timestamp-btn">
                                        <i class="fas fa-clock"></i> Add current time
                                    </button>
                                </div>
                                <div class="reply-form-buttons">
                                    <button type="button" id="cancelReply" class="cancel-reply-btn">Cancel</button>
                                    <button type="submit" class="submit-reply-btn">Reply</button>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Delete Confirmation Modal -->
            <div class="confirmation-modal" id="deleteConfirmModal">
                <div class="confirmation-modal-content">
                    <div class="confirmation-modal-header">
                        <h3>Delete Comment</h3>
                        <button type="button" class="close-modal-btn" id="closeDeleteModal">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="confirmation-modal-body">
                        <p>Are you sure you want to delete this comment? This action cannot be undone.</p>
                        <div class="confirmation-modal-actions">
                            <button type="button" id="cancelDelete" class="cancel-btn">Cancel</button>
                            <button type="button" id="confirmDelete" class="delete-btn">Delete</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Share Modal -->
            <div class="share-modal" id="shareModal">
                <div class="share-modal-content">
                    <div class="share-modal-header">
                        <h3>Share This Track</h3>
                        <button type="button" class="close-modal-btn" id="closeShareModal">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="share-modal-body">
                        <div class="share-input-group">
                            <input type="text" id="shareUrl" readonly value="${typeof window !== 'undefined' ? window.location.origin : ''}/player/${this.trackId || ''}">
                            <button id="copyShareUrl" class="copy-btn">
                                <i class="fas fa-copy"></i> Copy
                            </button>
                        </div>
                        <div class="share-options">
                            <a href="#" class="share-option" id="shareTwitter" target="_blank">
                                <i class="fab fa-twitter"></i> Twitter
                            </a>
                            <a href="#" class="share-option" id="shareFacebook" target="_blank">
                                <i class="fab fa-facebook"></i> Facebook
                            </a>
                            <a href="#" class="share-option" id="shareWhatsapp" target="_blank">
                                <i class="fab fa-whatsapp"></i> WhatsApp
                            </a>
                            <a href="#" class="share-option" id="shareTelegram" target="_blank">
                                <i class="fab fa-telegram"></i> Telegram
                            </a>
                        </div>
                    </div>
                </div>
            </div>
    `;
  }

  /**
   * Helper: Generate progress overlay HTML
   */
  generateProgressOverlay() {
    return `
<!-- Progress overlay -->
<div id="segmentProgress" class="segment-progress">
    <div class="segment-progress-content">
        <div class="segment-header">
            <span class="segment-title">Preparing Audio Stream</span>
            <span id="segmentCount" class="segment-count"></span>
        </div>
        <div class="segment-bar-container">
            <div id="segmentProgressBar" class="segment-bar"></div>
        </div>
    </div>
</div>
    `;
  }

  /**
   * Helper: Generate toast HTML
   */
  generateToast() {
    return `<div class="toast" id="toast"></div>`;
  }

  /**
   * Helper: Generate custom timer toast HTML
   */
  generateCustomTimerToast() {
    return `
<div class="custom-timer-toast" id="customTimerToast">
    <h2>Set Custom Sleep Timer</h2>
    <input type="number" id="customTimerInput" min="1" placeholder="Enter minutes">
    <button id="setCustomTimerBtn">Set Timer</button>
</div>
    `;
  }

  /**
   * For SSR mode: Read bootstrap data from the server-rendered HTML
   */
  hydrateFromDOM() {

    // Read JSON bootstrap data first (most reliable source)
    const playerDataElement = document.getElementById('player-data');
    if (playerDataElement) {
      try {
        const data = JSON.parse(playerDataElement.textContent);
        this.trackData = data.track;
        this.userData = data.user;
        this.albumData = data.album;
        this.streamConfigData = data.stream_config;

        // Extract track info from data
        this.trackId = this.trackData?.id;
        this.trackTitle = this.trackData?.title;
        this.trackType = this.trackData?.track_type || 'audio';
        this.albumId = this.albumData?.id;
        this.albumTitle = this.albumData?.title;
        this.albumCoverPath = this.albumData?.cover_path;

      } catch (e) {
        this.createFallbackData();
      }
    } else {
      // Fallback to window variables (for traditional page loads)
      this.trackId = window.currentTrackId;
      this.trackTitle = window.currentTrackTitle;
      this.albumTitle = window.currentAlbumTitle;
      this.albumCoverPath = window.currentAlbumCoverPath;
      this.albumId = window.currentAlbumId;
      this.trackType = window.currentTrackType;

      if (!this.trackId) {
        this.createFallbackData();
      }
    }

    // Store in window for backward compatibility and other scripts
    window.currentTrackId = this.trackId;
    window.currentTrackTitle = this.trackTitle;
    window.currentAlbumTitle = this.albumTitle;
    window.currentAlbumCoverPath = this.albumCoverPath;
    window.currentAlbumId = this.albumId;
    window.currentTrackType = this.trackType;
    window.trackData = this.trackData;
    window.albumData = this.albumData;
    window.userData = this.userData;
  }

  /**
   * For SPA mode: Fetch player data from the server
   */
  async fetchAndRender(trackId) {

    try {
      const response = await fetch(`/api/player/${trackId}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch player data: ${response.status}`);
      }

      const data = await response.json();

      this.trackId = data.track.id;
      this.trackTitle = data.track.title;
      this.albumTitle = data.album.title;
      this.albumCoverPath = data.album.cover_path;
      this.albumId = data.album.id;
      this.trackType = data.track.track_type;

      this.trackData = data.track;
      this.albumData = data.album;
      this.userData = data.user;
      this.streamConfigData = data.stream_config;

      // Store in window for backward compatibility
      window.currentTrackId = this.trackId;
      window.currentTrackTitle = this.trackTitle;
      window.currentAlbumTitle = this.albumTitle;
      window.currentAlbumCoverPath = this.albumCoverPath;
      window.currentAlbumId = this.albumId;
      window.currentTrackType = this.trackType;
      window.trackData = this.trackData;
      window.albumData = this.albumData;
      window.userData = this.userData;


      return this.generateHTML();
    } catch (error) {
      throw error;
    }
  }

  /**
   * Mount the controller and initialize all functionality
   */
  async mount() {

    // ✅ Add player-page class to hide mini-player
    document.body.classList.add('player-page');

    if (this.mode === 'ssr') {
      this.hydrateFromDOM();
    }

    // ✅ Check if we're expanding from miniplayer (before initializing conflict resolver)
    const expandedState = sessionStorage.getItem('expandedPlayerState');
    const isExpandingFromMiniplayer = expandedState ? true : false;

    // Initialize player conflict resolver with playback preservation flag
    this.playerConflictResolver = new PlayerConflictResolver(isExpandingFromMiniplayer);

    // Initialize player functionality
    await this.initAudioPlayer();
    this.initCommentSystem();
    this.setupReadAlongButton();

    // Setup live comment updates after comments load
    setTimeout(() => {
      window.liveCommentUpdater = this.setupLiveCommentUpdates();
      this.updateReadAlongButton();
    }, 1000);

    // Check track access
    setTimeout(() => this.checkTrackAccess(), 1000);

  }

  /**
   * Cleanup when navigating away
   */
  async destroy() {

    // ✅ Remove player-page class to prevent CSS from matching
    document.body.classList.remove('player-page');

    // Reset any inline background styles that might have been set
    document.body.style.removeProperty('background-color');
    document.body.style.removeProperty('background');

    // Clear sleep timer
    if (this.sleepTimerInterval) {
      clearInterval(this.sleepTimerInterval);
      this.sleepTimerInterval = null;
    }

    // Cleanup event listeners
    document.removeEventListener('touchmove', this.dragHandler);
    document.removeEventListener('mousemove', this.dragHandler);
    document.removeEventListener('touchend', this.stopDraggingHandler);
    document.removeEventListener('mouseup', this.stopDraggingHandler);

    // ✅ CRITICAL FIX: Cleanup PlayerConflictResolver to restore original methods
    if (this.playerConflictResolver) {
      this.playerConflictResolver.cleanup();
    }

    // ✅ FIX: Force show miniplayer after cleanup
    if (window.persistentPlayer && window.persistentPlayer.currentTrackId) {

      // Ensure player page flag is false
      window.persistentPlayer.isPlayerPage = false;

      // Update player state to show miniplayer
      window.persistentPlayer.updatePlayerState();

      // Force show miniplayer multiple times to ensure it appears
      const forceShow = () => {
        const miniPlayer = document.getElementById('miniPlayer');
        if (miniPlayer && window.persistentPlayer.currentTrackId) {

          // ✅ CRITICAL: Reload metadata from sessionStorage before showing
          window.persistentPlayer.loadTrackMetadata();

          // Remove any blocking styles
          miniPlayer.style.display = 'flex';
          miniPlayer.style.visibility = 'visible';
          miniPlayer.style.pointerEvents = 'auto';
          miniPlayer.classList.add('active');

          // Force update the UI with refreshed metadata
          window.persistentPlayer.updateMiniPlayerUI();

        } else {
          //   miniPlayerExists: !!miniPlayer,
          //   hasTrackId: !!window.persistentPlayer.currentTrackId
          // });
        }
      };

      // Try immediately and after delay
      forceShow();
      setTimeout(forceShow, 100);
      setTimeout(forceShow, 300);

    }

  }

  // ============================================================================
  // FALLBACK DATA
  // ============================================================================

  createFallbackData() {
    this.trackData = {
      id: this.trackId || window.currentTrackId,
      track_type: this.trackType || window.currentTrackType,
      default_voice: 'en-US-AvaNeural',
      current_voice: 'en-US-AvaNeural'
    };
    this.userData = {
      id: window.currentUserData?.id,
      username: window.currentUserData?.username
    };
    this.albumData = {
      id: this.albumId || window.currentAlbumId,
      ordered_track_ids: []
    };
  }

  // ============================================================================
  // PLAYER CONFLICT RESOLUTION
  // ============================================================================

  // This is implemented as a nested class to match the original structure
  // but could also be a separate export

  // ============================================================================
  // TRACK ACCESS CHECK
  // ============================================================================

  async checkTrackAccess() {
    try {
      const response = await fetch(`/api/tracks/${encodeURIComponent(this.trackId)}/check-access`);

      if (response.status === 403) {
        const data = await response.json();
        const message = data.error?.message || 'This content requires a higher tier subscription';
        this.showUpgradeModal(message);

        // Disable player controls
        const controlsToDisable = [
          'playBtn', 'rewindBtn', 'forwardBtn', 'rewind30Btn',
          'forward30Btn', 'speedBtn', 'sleepBtn', 'progressBar',
          'voiceChangeBtn'
        ];

        controlsToDisable.forEach(id => {
          const element = document.getElementById(id);
          if (element) {
            element.disabled = true;
            element.classList.add('disabled');
            element.style.opacity = '0.5';
            element.style.cursor = 'not-allowed';
          }
        });

        return false;
      }
      return true;
    } catch (error) {
      return false;
    }
  }

  // ============================================================================
  // AUDIO PLAYER INITIALIZATION
  // ============================================================================

  async initAudioPlayer() {
    const initStart = performance.now();
    const waitForPersistentPlayer = (callback, maxAttempts = 10) => {
      let attempts = 0;
      const checkPlayer = () => {
        attempts++;
        if (window.persistentPlayer) {
          window.persistentPlayer.isPlayerPage = true;
          callback();
        } else if (attempts < maxAttempts) {
          setTimeout(checkPlayer, 100);
        } else {
        }
      };
      checkPlayer();
    };

    waitForPersistentPlayer(() => {
      // ✅ Restore player settings FIRST (before playTrack)
      const lastPlayerState = sessionStorage.getItem('lastPlayerState');
      if (lastPlayerState) {
        try {
          const state = JSON.parse(lastPlayerState);
          if (state.volume !== undefined) window.persistentPlayer.audio.volume = state.volume;
          if (state.muted) window.persistentPlayer.audio.muted = state.muted;
          if (state.playbackRate) window.persistentPlayer.setPlaybackSpeed(state.playbackRate);
          sessionStorage.removeItem('lastPlayerState');
        } catch (error) {
        }
      }

      // Check for expanded player state
      const expandedState = sessionStorage.getItem('expandedPlayerState');
      let isExpandedFromMiniplayer = false;
      let shouldAutoPlay = false; // ✅ FIX: Default to false instead of null to prevent auto-play

      if (expandedState) {
        try {
          const state = JSON.parse(expandedState);
          if (state.trackId === this.trackId) {
            isExpandedFromMiniplayer = true;
            shouldAutoPlay = state.isPlaying;
          }
          sessionStorage.removeItem('expandedPlayerState');
        } catch (error) {
        }
      }

      // ✅ FIX: If track is already loaded in persistent player, DON'T reinitialize
      const isTrackAlreadyLoaded = window.persistentPlayer.currentTrackId === this.trackId;

      if (isTrackAlreadyLoaded && isExpandedFromMiniplayer) {

        // Mark as player page and update state
        window.persistentPlayer.isPlayerPage = true;
        window.persistentPlayer.updatePlayerState();

        // Resume playback if it was playing when expanded
        if (shouldAutoPlay && window.persistentPlayer.audio.paused) {
          window.persistentPlayer.audio.play().catch(err => {
          });
        }
      } else {

        // Check if we should auto-play due to auto-play feature
        const autoPlayNext = sessionStorage.getItem('autoPlayNext') === 'true';
        if (autoPlayNext) {
          sessionStorage.removeItem('autoPlayNext');

          // ✅ FIX: Preserve playback settings for the new track
          // Get the playback rate from the audio element (already restored from lastPlayerState)
          const currentPlaybackRate = window.persistentPlayer.audio.playbackRate;
          const currentVolume = window.persistentPlayer.audio.volume;
          const currentMuted = window.persistentPlayer.audio.muted;

          // Save settings to the new track's state (position = 0 for fresh start)
          const newTrackState = {
            isPlaying: true,
            position: 0,
            playbackRate: currentPlaybackRate,
            volume: currentVolume,
            muted: currentMuted
          };
          sessionStorage.setItem(`playerState_${this.trackId}`, JSON.stringify(newTrackState));

          shouldAutoPlay = true;
        }

        // Get voice from trackData
        const currentVoice = this.trackData?.current_voice || null;
        const trackType = this.trackData?.track_type || this.trackType;

        window.persistentPlayer.playTrack(
          this.trackId,
          this.trackTitle,
          this.albumTitle,
          this.albumCoverPath,
          shouldAutoPlay,
          currentVoice,
          trackType,
          this.albumId
        );
      }

      this.setupPlayerControls();
      this.setupProgressBar();
      this.setupMenus();
      this.setupTrackNavigation();
      this.setupVoiceAwareDownload();
      this.setupSleepTimer();
      this.updateHeaderTitle();
    });
  }

  /**
   * Update the header title (for SPA navigation)
   */
  updateHeaderTitle() {
    const headerTitle = document.querySelector('.main-header h1');
    if (headerTitle) {
      const title = this.getPageTitle();
      headerTitle.textContent = title;
      document.title = title;
    }
  }

  // ============================================================================
  // PLAYER CONTROLS
  // ============================================================================

  setupPlayerControls() {
    const elements = {
      playBtn: document.getElementById('playBtn'),
      playIcon: document.getElementById('playIcon'),
      rewindBtn: document.getElementById('rewindBtn'),
      forwardBtn: document.getElementById('forwardBtn'),
      rewind30Btn: document.getElementById('rewind30Btn'),
      forward30Btn: document.getElementById('forward30Btn')
    };

    // Multi-tap handler for play button speed control
    const tapHandler = {
      tapCount: 0,
      tapTimer: null,
      tapWindow: 300, // ms between taps

      registerTap(callback) {
        this.tapCount++;

        // Clear existing timer
        if (this.tapTimer) {
          clearTimeout(this.tapTimer);
        }

        // Wait for tap window to complete
        this.tapTimer = setTimeout(() => {
          callback(this.tapCount);
          this.tapCount = 0;
        }, this.tapWindow);
      },

      adjustSpeed(direction) {
        if (!window.persistentPlayer?.audio) return;

        const currentSpeed = window.persistentPlayer.audio.playbackRate;
        const increment = 0.25;
        const minSpeed = 0.25;
        const maxSpeed = 3.0;

        let newSpeed;
        if (direction === 'increase') {
          newSpeed = Math.min(maxSpeed, currentSpeed + increment);
        } else {
          newSpeed = Math.max(minSpeed, currentSpeed - increment);
        }

        if (newSpeed !== currentSpeed) {
          window.persistentPlayer.setPlaybackSpeed(newSpeed);
          const icon = direction === 'increase' ? '⏩' : '⏪';
          window.persistentPlayer.showToast(`${icon} Speed: ${newSpeed}x`, 'info', 1500);
        } else {
          const icon = direction === 'increase' ? '🚀' : '🐌';
          const limit = direction === 'increase' ? 'Max' : 'Min';
          window.persistentPlayer.showToast(`${icon} ${limit} speed: ${newSpeed}x`, 'info', 1500);
        }
      }
    };

    // Play/Pause with long-press for auto-play toggle
    if (elements.playBtn) {
      let longPressTimer = null;
      let isLongPress = false;

      // Initialize auto-play state
      const autoPlayEnabled = localStorage.getItem('autoPlayEnabled') === 'true';
      if (autoPlayEnabled) {
        elements.playBtn.classList.add('auto-play-active');
      }

      const startLongPress = () => {
        isLongPress = false;
        longPressTimer = setTimeout(() => {
          isLongPress = true;

          // Toggle auto-play
          const currentState = localStorage.getItem('autoPlayEnabled') === 'true';
          const newState = !currentState;
          localStorage.setItem('autoPlayEnabled', newState.toString());

          // Update visual state
          if (newState) {
            elements.playBtn.classList.add('auto-play-active');
            window.persistentPlayer?.showToast('Auto-play enabled', 'success', 2000);
          } else {
            elements.playBtn.classList.remove('auto-play-active');
            window.persistentPlayer?.showToast('Auto-play disabled', 'info', 2000);
          }
        }, 500); // 500ms for long press
      };

      const cancelLongPress = () => {
        if (longPressTimer) {
          clearTimeout(longPressTimer);
          longPressTimer = null;
        }
      };

      const handleClick = () => {
        if (!isLongPress) {
          tapHandler.registerTap((count) => {
            if (count === 2) {
              // Double-tap: increase speed
              tapHandler.adjustSpeed('increase');
            } else if (count === 3) {
              // Triple-tap: decrease speed
              tapHandler.adjustSpeed('decrease');
            } else {
              // Single tap: play/pause
              window.persistentPlayer.togglePlay();
            }
          });
        }
        isLongPress = false;
      };

      // Mouse events
      elements.playBtn.addEventListener('mousedown', startLongPress);
      elements.playBtn.addEventListener('mouseup', cancelLongPress);
      elements.playBtn.addEventListener('mouseleave', cancelLongPress);
      elements.playBtn.addEventListener('click', handleClick);

      // Touch events for mobile
      elements.playBtn.addEventListener('touchstart', (e) => {
        e.preventDefault();
        startLongPress();
      }, { passive: false });

      elements.playBtn.addEventListener('touchend', (e) => {
        e.preventDefault();
        const wasLongPress = isLongPress;
        cancelLongPress();

        // Only handle click if it wasn't a long press
        if (!wasLongPress) {
          handleClick();
        } else {
          // Reset the flag after long press completes
          isLongPress = false;
        }
      }, { passive: false });

      elements.playBtn.addEventListener('touchcancel', (e) => {
        cancelLongPress();
        isLongPress = false;
      });
    }

    // Auto-play info button
    const autoPlayInfoBtn = document.getElementById('autoPlayInfoBtn');
    if (autoPlayInfoBtn) {
      autoPlayInfoBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        showAutoPlayTooltip();
      });
    }

    // Seek controls
    if (elements.rewindBtn) {
      elements.rewindBtn.addEventListener('click', () => {
        window.persistentPlayer.seek(-15);
      });
    }

    if (elements.forwardBtn) {
      elements.forwardBtn.addEventListener('click', () => {
        window.persistentPlayer.seek(15);
      });
    }

    if (elements.rewind30Btn) {
      elements.rewind30Btn.addEventListener('click', () => {
        window.persistentPlayer.seek(-30);
      });
    }

    if (elements.forward30Btn) {
      elements.forward30Btn.addEventListener('click', () => {
        window.persistentPlayer.seek(30);
      });
    }


    // Update play/pause icon
    const updatePlayPauseIcon = () => {
      if (!elements.playIcon) return;
      const iconClass = window.persistentPlayer.audio.paused ? 'fa-play' : 'fa-pause';
      elements.playIcon.className = `fas ${iconClass} fa-lg`;
    };

    // ✅ Sync initial UI state
    updatePlayPauseIcon();

    // Audio event listeners
    window.persistentPlayer.audio.addEventListener('pause', updatePlayPauseIcon);
    window.persistentPlayer.audio.addEventListener('play', updatePlayPauseIcon);
  }

  // ============================================================================
  // PROGRESS BAR
  // ============================================================================

  setupProgressBar() {
    const elements = {
      progressBar: document.getElementById('progressBar'),
      progress: document.getElementById('progress'),
      currentTime: document.getElementById('currentTime'),
      duration: document.getElementById('duration')
    };

    if (!elements.progressBar) return;

    const progressKnob = document.querySelector('.progress-knob');
    const progressHover = elements.progressBar.querySelector('.progress-hover');
    const audio = window.persistentPlayer.audio;

    // Hover preview
    if (progressHover) {
      elements.progressBar.addEventListener('mousemove', (e) => {
        const rect = elements.progressBar.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        progressHover.style.left = `${e.clientX - rect.left}px`;
        progressHover.textContent = window.persistentPlayer.formatTime(pos * audio.duration);
        progressHover.style.display = 'block';
      });

      elements.progressBar.addEventListener('mouseleave', () => {
        progressHover.style.display = 'none';
      });
    }

    // Click to seek
    elements.progressBar.addEventListener('click', (e) => {
      const rect = elements.progressBar.getBoundingClientRect();
      const pos = (e.clientX - rect.left) / rect.width;
      audio.currentTime = pos * audio.duration;
    });

    // Drag to seek
    const startDragging = (e) => {
      this.isDragging = true;
      e.preventDefault();
    };

    this.dragHandler = (e) => {
      if (!this.isDragging) return;
      const rect = elements.progressBar.getBoundingClientRect();
      const clientX = (e.type === 'touchmove' ? e.touches[0].clientX : e.clientX);
      const x = clientX - rect.left;
      const percentage = Math.max(0, Math.min(1, x / rect.width));
      if (elements.progress) {
        elements.progress.style.width = `${percentage * 100}%`;
      }
      const newTime = percentage * audio.duration;
      if (elements.currentTime) {
        elements.currentTime.textContent = window.persistentPlayer.formatTime(newTime);
      }
      audio.currentTime = newTime;
    };

    this.stopDraggingHandler = () => {
      this.isDragging = false;
    };

    if (progressKnob) {
      progressKnob.addEventListener('touchstart', startDragging);
      progressKnob.addEventListener('mousedown', startDragging);
    }

    document.addEventListener('touchmove', this.dragHandler);
    document.addEventListener('mousemove', this.dragHandler);
    document.addEventListener('touchend', this.stopDraggingHandler);
    document.addEventListener('mouseup', this.stopDraggingHandler);

    // Time updates
    audio.addEventListener('timeupdate', () => {
      if (this.isDragging) return;
      const currentTime = audio.currentTime;
      const duration = audio.duration;
      if (duration) {
        const progressPercent = (currentTime / duration) * 100;
        if (elements.progress) {
          elements.progress.style.width = `${progressPercent}%`;
        }
        if (elements.currentTime) {
          elements.currentTime.textContent = window.persistentPlayer.formatTime(currentTime);
        }
        if (elements.duration) {
          elements.duration.textContent = window.persistentPlayer.formatTime(duration);
        }
      }
    });

    audio.addEventListener('loadedmetadata', () => {
      if (elements.duration) {
        elements.duration.textContent = window.persistentPlayer.formatTime(audio.duration);
      }
    });
  }

  // ============================================================================
  // MENUS (SPEED & SLEEP TIMER)
  // ============================================================================

  setupMenus() {
    const speedBtn = document.getElementById('speedBtn');
    const speedMenu = document.getElementById('speedMenu');
    const sleepBtn = document.getElementById('sleepBtn');
    const sleepMenu = document.getElementById('sleepMenu');
    const speedDisplay = document.getElementById('speedDisplay');

    // Setup menu toggle with proper positioning
    const setupMenu = (button, menu) => {
      if (!button || !menu) return;

      let isMenuActive = false;

      const positionMenu = (button, menu) => {
        const rect = button.getBoundingClientRect();
        const menuRect = menu.getBoundingClientRect();

        if (menu.id === 'sleepMenu') {
          menu.style.bottom = `${window.innerHeight - rect.top + 8}px`;
          menu.style.left = `${rect.left - (120 / 2) + rect.width / 2}px`;
        } else {
          const spaceAbove = rect.top;
          const padding = 10;

          if (spaceAbove >= menuRect.height + padding) {
            menu.style.bottom = `${window.innerHeight - rect.top + padding}px`;
            menu.style.top = 'auto';
          } else {
            menu.style.top = `${rect.bottom + padding}px`;
            menu.style.bottom = 'auto';
          }

          menu.style.left = `${Math.max(10, Math.min(window.innerWidth - menuRect.width - 10, rect.left - menuRect.width / 2 + rect.width / 2))}px`;
        }
      };

      const openMenu = () => {
        menu.style.display = 'block';
        menu.style.pointerEvents = 'auto';
        menu.classList.add('visible');
        isMenuActive = true;
      };

      const closeMenu = () => {
        if (!isMenuActive) return;
        menu.classList.remove('visible');
        menu.style.pointerEvents = 'none';
        menu.style.display = 'none';
        isMenuActive = false;
      };

      const toggleMenu = (e) => {
        e.stopPropagation();
        e.preventDefault();

        if (isMenuActive) {
          closeMenu();
          return;
        }

        // Close other menu
        const otherMenuId = menu.id === 'speedMenu' ? 'sleepMenu' : 'speedMenu';
        const otherMenu = document.getElementById(otherMenuId);
        if (otherMenu && otherMenu.classList.contains('visible')) {
          otherMenu.classList.remove('visible');
          otherMenu.style.pointerEvents = 'none';
          otherMenu.style.display = 'none';
        }

        positionMenu(button, menu);
        openMenu();
      };

      button.addEventListener('click', toggleMenu);
      button.addEventListener('touchend', toggleMenu, { passive: false });

      // Close on outside click
      document.addEventListener('click', (e) => {
        if (!menu.contains(e.target) && !button.contains(e.target)) {
          closeMenu();
        }
      });

      document.addEventListener('touchstart', (e) => {
        if (isMenuActive && !menu.contains(e.target) && !button.contains(e.target)) {
          e.preventDefault();
          closeMenu();
        }
      }, { passive: false });

      // Handle menu item selections
      menu.querySelectorAll('.speed-option, .sleep-option').forEach(item => {
        const originalClick = item.onclick;
        item.addEventListener('click', (e) => {
          if (originalClick) originalClick.call(item, e);
          setTimeout(closeMenu, 100);
        });
        item.addEventListener('touchend', (e) => {
          e.preventDefault();
          e.stopPropagation();
          item.click();
        }, { passive: false });
      });

      return closeMenu;
    };

    setupMenu(speedBtn, speedMenu);
    setupMenu(sleepBtn, sleepMenu);

    // Speed options
    document.querySelectorAll('.speed-option').forEach(option => {
      option.addEventListener('click', () => {
        const speed = parseFloat(option.dataset.speed);
        window.persistentPlayer.setPlaybackSpeed(speed);

        document.querySelectorAll('.speed-option').forEach(opt => {
          opt.classList.toggle('active', opt.dataset.speed === option.dataset.speed);
        });

        if (speedDisplay) {
          if (speed !== 1.0) {
            speedDisplay.textContent = `${speed}x`;
            speedDisplay.classList.add('active');
          } else {
            speedDisplay.textContent = '';
            speedDisplay.classList.remove('active');
          }
        }

        this.showToast(`Playback speed set to ${speed}x`);
      });
    });

    // Listen for playback speed changes from gestures or other sources
    document.addEventListener('playbackSpeedChanged', (e) => {
      const speed = e.detail.speed;

      // Update speed display badge
      if (speedDisplay) {
        if (speed !== 1.0) {
          speedDisplay.textContent = `${speed}x`;
          speedDisplay.classList.add('active');
        } else {
          speedDisplay.textContent = '';
          speedDisplay.classList.remove('active');
        }
      }

      // Update active speed option in menu
      document.querySelectorAll('.speed-option').forEach(opt => {
        opt.classList.toggle('active', parseFloat(opt.dataset.speed) === speed);
      });
    });
  }

  // ============================================================================
  // SLEEP TIMER
  // ============================================================================

  setupSleepTimer() {
    const sleepDisplay = document.getElementById('sleepDisplay');
    const customTimerToast = document.getElementById('customTimerToast');
    const customTimerInput = document.getElementById('customTimerInput');
    const setCustomTimerBtn = document.getElementById('setCustomTimerBtn');

    const formatTime = (seconds) => {
      const hrs = Math.floor(seconds / 3600);
      const mins = Math.floor((seconds % 3600) / 60);
      const secs = Math.floor(seconds % 60);
      return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    };

    const updateSleepDisplay = () => {
      const now = Date.now();
      const remaining = Math.max(0, Math.floor((this.sleepEndTime - now) / 1000));
      sleepDisplay.textContent = formatTime(remaining);
      if (remaining <= 0) {
        clearInterval(this.sleepTimerInterval);
        this.sleepTimerInterval = null;
        this.sleepEndTime = null;
        sleepDisplay.classList.remove('active');
        sleepDisplay.textContent = '';
        window.persistentPlayer.audio.pause();
        this.showToast('Sleep timer ended');
      }
    };

    const showCustomTimerToast = () => {
      customTimerToast.classList.add('visible');
      customTimerInput.value = '';
      customTimerInput.focus();
    };

    const hideCustomTimerToast = () => {
      customTimerToast.classList.remove('visible');
    };

    // Sleep timer options
    document.querySelectorAll('.sleep-option').forEach(option => {
      option.addEventListener('click', () => {
        const minutes = option.dataset.minutes;

        if (minutes === 'custom') {
          showCustomTimerToast();
        } else if (minutes !== 'cancel') {
          if (window.persistentPlayer.audio.paused) {
            this.showToast('Cannot set timer while paused.');
            return;
          }
          if (this.sleepTimerInterval) {
            clearInterval(this.sleepTimerInterval);
          }
          const durationInSeconds = parseInt(minutes) * 60;
          this.sleepEndTime = Date.now() + durationInSeconds * 1000;
          sleepDisplay.classList.add('active');
          updateSleepDisplay();
          this.sleepTimerInterval = setInterval(updateSleepDisplay, 1000);
          this.showToast(`Sleep timer set for ${minutes} minute(s)`);
        } else {
          // Cancel timer
          if (this.sleepTimerInterval) {
            clearInterval(this.sleepTimerInterval);
            this.sleepTimerInterval = null;
          }
          this.sleepEndTime = null;
          sleepDisplay.classList.remove('active');
          sleepDisplay.textContent = '';
          this.showToast('Sleep timer canceled');
        }
      });
    });

    // Custom timer
    if (setCustomTimerBtn && customTimerInput) {
      setCustomTimerBtn.addEventListener('click', () => {
        const minutes = parseInt(customTimerInput.value);
        if (isNaN(minutes) || minutes <= 0) {
          this.showToast('Please enter a valid number of minutes.');
          return;
        }
        if (window.persistentPlayer.audio.paused) {
          this.showToast('Cannot set timer while paused.');
          hideCustomTimerToast();
          return;
        }
        if (this.sleepTimerInterval) {
          clearInterval(this.sleepTimerInterval);
        }
        const durationInSeconds = minutes * 60;
        this.sleepEndTime = Date.now() + durationInSeconds * 1000;
        sleepDisplay.classList.add('active');
        updateSleepDisplay();
        this.sleepTimerInterval = setInterval(updateSleepDisplay, 1000);
        hideCustomTimerToast();
        this.showToast(`Sleep timer set for ${minutes} minute(s)`);
      });

      customTimerInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
          setCustomTimerBtn.click();
        }
      });
    }
  }

  // ============================================================================
  // TRACK NAVIGATION
  // ============================================================================

  async setupTrackNavigation() {
    const navContainer = document.getElementById('trackNavigation');
    if (!navContainer) {
      return;
    }

    // Get track order - try ordered_track_ids first, fall back to fetching album data
    let order = Array.isArray(this.albumData?.ordered_track_ids) && this.albumData.ordered_track_ids.length > 0 ?
                this.albumData.ordered_track_ids.slice() : null;

    // If we don't have order data and we have an album ID, fetch it
    if (!order && this.albumId) {
      try {
        const response = await fetch(`/api/albums/${encodeURIComponent(this.albumId)}`);
        if (response.ok) {
          const albumData = await response.json();
          // Build ordered_track_ids from tracks
          const tracks = albumData.tracks || [];
          order = tracks
            .sort((a, b) => (a.order || 0) - (b.order || 0))
            .map(t => String(t.id));
          // Update albumData for future use
          if (this.albumData) {
            this.albumData.ordered_track_ids = order;
          }
        } else {
        }
      } catch (error) {
      }
    }

    const curId = String(this.trackId || '');
    let prevId = null;
    let nextId = null;

    // Compute prev/next track IDs
    if (order && order.length && curId) {
      const idx = order.indexOf(curId);
      prevId = idx > 0 ? order[idx - 1] : null;
      nextId = (idx >= 0 && idx < order.length - 1) ? order[idx + 1] : null;
    } else {
      // No track order available - keep server-rendered buttons
      return;
    }

    // Clear existing buttons only after we successfully computed replacements
    navContainer.innerHTML = '';

    // Create previous button if we have a previous track
    if (prevId) {
      const prevBtn = document.createElement('a');
      prevBtn.href = `/player/${encodeURIComponent(prevId)}`;
      prevBtn.className = 'nav-btn';
      prevBtn.dataset.spaHref = `/player/${encodeURIComponent(prevId)}`;
      prevBtn.dataset.trackId = prevId;
      prevBtn.id = 'prevTrackBtn';
      prevBtn.innerHTML = '<i class="fas fa-chevron-left"></i> Previous Track';
      navContainer.appendChild(prevBtn);
      this.attachNavButtonListener(prevBtn);
    }

    // Create next button if we have a next track
    if (nextId) {
      const nextBtn = document.createElement('a');
      nextBtn.href = `/player/${encodeURIComponent(nextId)}`;
      nextBtn.className = 'nav-btn';
      nextBtn.dataset.spaHref = `/player/${encodeURIComponent(nextId)}`;
      nextBtn.dataset.trackId = nextId;
      nextBtn.id = 'nextTrackBtn';
      nextBtn.innerHTML = 'Next Track <i class="fas fa-chevron-right"></i>';
      navContainer.appendChild(nextBtn);
      this.attachNavButtonListener(nextBtn);
    }

  }

  attachNavButtonListener(btn) {
    const extractIdFromHref = (href) => {
      try {
        const u = new URL(href, location.origin);
        const p = u.pathname.split('/').filter(Boolean);
        return p[0] === 'player' ? p[1] : null;
      } catch {
        return null;
      }
    };

    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const targetId = btn.dataset.trackId || extractIdFromHref(btn.href);
      const autoPlayNextFlag = sessionStorage.getItem('autoPlayNext') === 'true';
      //       console.log(`🔘 Nav button clicked: id=${btn.id}, targetId=${targetId}, autoPlayNext=${autoPlayNextFlag}`);
      //       console.log(`   Button details: href=${btn.href}, dataset.trackId=${btn.dataset.trackId}`);
      //       console.log(`   Current player track: ${window.persistentPlayer?.currentTrackId || 'unknown'}`);

      if (!targetId) {
        console.error('❌ Navigation error: No target track ID found!');
        return this.showToast('Invalid navigation target', 'error');
      }

      // ✅ REMOVED: Access check was causing 10-12 second delay
      // Access is checked after page load in checkTrackAccess() or in playTrack() when appropriate

      // Preserve state
      // Check if auto-play is triggering navigation (from track end)
      const autoPlayNext = sessionStorage.getItem('autoPlayNext') === 'true';
      //       console.log(`💾 Preserving state: autoPlayNext=${autoPlayNext}, wasPlaying=${!window.persistentPlayer.audio.paused}`);

      // ✅ Always save player state (volume, speed, etc.) for restoration
      const s = {
        wasPlaying: autoPlayNext ? true : !window.persistentPlayer.audio.paused,
        currentTime: window.persistentPlayer.audio.currentTime,
        volume: window.persistentPlayer.audio.volume,
        muted: window.persistentPlayer.audio.muted,
        playbackRate: window.persistentPlayer.audio.playbackRate
      };
      sessionStorage.setItem('lastPlayerState', JSON.stringify(s));

      // ✅ FIX: Don't create expandedPlayerState for auto-play navigation
      // Auto-play should use the autoPlayNext flag, not expandedPlayerState
      if (!autoPlayNext) {
        const expandedState = {
          trackId: targetId,
          isPlaying: false, // ✅ Manual navigation should not auto-play
          position: 0, // Start from beginning for manual navigation
          playbackRate: s.playbackRate
        };
        sessionStorage.setItem('expandedPlayerState', JSON.stringify(expandedState));
      }

      // ✅ FIX: Use full page reload for instant loading
      // SPA navigation is slow because it fetches/parses full HTML, full reload is much faster
      const targetUrl = `/player/${encodeURIComponent(targetId)}`;
      window.location.href = targetUrl;
    }, { passive: false });
  }

  // ============================================================================
  // VOICE-AWARE DOWNLOAD
  // ============================================================================

  setupVoiceAwareDownload() {
    const downloadBtn = document.getElementById('downloadBtn');
    const downloadStatus = document.getElementById('downloadStatus');

    if (!downloadBtn) return;

    // Get current voice and track type from trackData
    const getCurrentVoiceAndType = () => {
      const voice = this.trackData?.current_voice || window.persistentPlayer?.currentVoice;
      const trackType = this.trackData?.track_type || window.persistentPlayer?.trackType || 'audio';
      return { voice, trackType };
    };

    downloadBtn.addEventListener('click', async () => {
      if (downloadBtn.classList.contains('loading')) return;

      const { voice, trackType } = getCurrentVoiceAndType();


      try {
        // Start download using persistence.js download manager
        const downloadKey = await window.persistentPlayer.downloadTrack(
          this.trackId,
          this.albumId,
          voice,
          // onProgress callback
          (progress, message, queuePosition) => {
            downloadBtn.classList.add('loading');

            if (queuePosition) {
              downloadBtn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Queued (${queuePosition})`;
              downloadStatus.textContent = `Position: ${queuePosition}`;
            } else {
              downloadBtn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${Math.round(progress)}%`;
              downloadStatus.textContent = message || `${Math.round(progress)}%`;
            }
          },
          // onComplete callback
          () => {
            downloadBtn.classList.remove('loading');
            downloadBtn.innerHTML = '<i class="fas fa-download"></i> Download';
            downloadStatus.textContent = '';
          },
          // onError callback
          (error, errorType) => {
            downloadBtn.classList.remove('loading');
            downloadBtn.innerHTML = '<i class="fas fa-download"></i> Download';
            downloadStatus.textContent = '';


            // Handle specific error types
            if (errorType === 'download_limit' || errorType === 'track_access' || errorType === 'album_access') {
              if (typeof this.showUpgradeModal === 'function') {
                this.showUpgradeModal(error);
              } else {
                this.showToast(error, 'error');
              }
            } else {
              this.showToast(error || 'Download failed', 'error');
            }
          }
        );

        if (!downloadKey) {
          return;
        }


      } catch (error) {
        downloadBtn.classList.remove('loading');
        downloadBtn.innerHTML = '<i class="fas fa-download"></i> Download';
        downloadStatus.textContent = '';
        this.showToast(error.message || 'Download failed', 'error');
      }
    });

    // Check if there's already a download in progress when the page loads
    setTimeout(() => {
      const { voice } = getCurrentVoiceAndType();

      if (window.persistentPlayer?.isTrackDownloading(this.trackId, voice)) {
        const status = window.persistentPlayer.getTrackDownloadStatus(this.trackId, voice);
        if (status) {
          downloadBtn.classList.add('loading');
          const progress = status.progress || 0;
          downloadBtn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${Math.round(progress)}%`;
          downloadStatus.textContent = `${Math.round(progress)}%`;
        }
      }
    }, 1000);
  }

  // ============================================================================
  // READ ALONG FUNCTIONALITY
  // ============================================================================

  setupReadAlongButton() {
    const readAlongBtn = document.getElementById('readAlongBtn');

    if (!readAlongBtn) return;

    readAlongBtn.addEventListener('click', async () => {
      // IMMEDIATE CHECK: Validate track type first
      if (!this.trackData || this.trackData.track_type !== 'tts') {
        this.showToast('Read-along only available for TTS tracks', 'warning');
        return;
      }

      // IMMEDIATE CHECK: Validate voice selection
      const currentVoice = window.persistentPlayer?.currentVoice || this.trackData?.current_voice;
      if (!currentVoice) {
        this.showToast('No voice selected for read-along', 'warning');
        return;
      }

      // NOW: Try to open read-along (access will be checked BEFORE overlay opens)
      if (window.readAlongSPAOverlay) {
        try {
          await window.readAlongSPAOverlay.open(this.trackId, currentVoice);

        } catch (error) {

          // Check if it's an access control error
          if (error.isAccessControl || error.statusCode === 403 ||
              (error.message && (error.message.includes('access not available') ||
                               error.message.includes('tier') ||
                               error.message.includes('subscription')))) {

            this.showToast('Read-along access not available for your tier', 'error');

            setTimeout(() => {
              this.showUpgradeModal(error.message || 'Read-along feature requires a higher tier subscription');
            }, 500);

          } else {
            this.showToast(error.message || 'Failed to open read-along feature', 'error');
          }
        }
      } else {
        // Read-along system not ready
        this.showToast('Read-along feature loading...', 'info');

        // Wait and try once more
        setTimeout(async () => {
          if (window.readAlongSPAOverlay) {
            try {
              await window.readAlongSPAOverlay.open(this.trackId, currentVoice);

            } catch (error) {

              if (error.isAccessControl || error.statusCode === 403 ||
                  (error.message && (error.message.includes('access not available') ||
                                   error.message.includes('tier') ||
                                   error.message.includes('subscription')))) {

                this.showToast('Read-along access not available for your tier', 'error');
                setTimeout(() => {
                  this.showUpgradeModal(error.message || 'Read-along feature requires a higher tier subscription');
                }, 500);

              } else {
                this.showToast('Read-along feature not available', 'error');
              }
            }
          } else {
            this.showToast('Read-along feature not available', 'error');
          }
        }, 1000);
      }
    });
  }

  updateReadAlongButton() {
    const readAlongBtn = document.getElementById('readAlongBtn');
    if (!readAlongBtn) return;

    const trackType = this.trackData?.track_type || this.trackType;
    const hasSourceText = this.trackData?.source_text ||
                         (this.trackData && this.trackData.has_read_along);

    if (trackType === 'tts' && hasSourceText) {
      readAlongBtn.style.display = 'flex';
    } else {
      readAlongBtn.style.display = 'none';
    }
  }

  // ============================================================================
  // COMMENT SYSTEM
  // ============================================================================

  initCommentSystem() {
    // ✅ Set global variables required by comment-system.js
    // These are normally set by inline <script> tags in SSR mode,
    // but in SPA mode the innerHTML scripts don't execute
    window.currentTrackId = this.trackId;
    window.currentTrackTitle = this.trackTitle;
    window.currentAlbumTitle = this.albumTitle;
    window.currentAlbumCoverPath = this.albumCoverPath;
    window.currentTrackFilePath = this.trackData?.file_path;
    window.currentAlbumId = this.albumId;
    window.currentTrackType = this.trackType;

    window.currentUserData = {
      id: this.userData?.id || null,
      username: this.userData?.username || '',
      email: this.userData?.email || '',
      role: this.userData?.role || '',
      is_patreon_subscriber: this.userData?.is_patreon_subscriber || false
    };

    window.trackData = this.trackData;
    window.userData = this.userData;
    window.albumData = this.albumData;
    window.streamConfigData = this.streamConfigData;

    //   trackId: this.trackId,
    //   trackTitle: this.trackTitle,
    //   albumTitle: this.albumTitle,
    //   trackType: this.trackType
    // });

    if (typeof window.initCommentSystem === 'function') {
      window.initCommentSystem();
    }
  }

  setupLiveCommentUpdates() {
    if (typeof window.setupLiveCommentUpdates === 'function') {
      return window.setupLiveCommentUpdates();
    }
    return null;
  }

  // ============================================================================
  // HELPER FUNCTIONS
  // ============================================================================

  showToast(message, type = 'success', duration = 3000) {
    // ✅ Use the same dynamic toast implementation as persistent-player.js
    const existing = document.querySelector('.player-toast:not(.simple-progress-toast)');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `player-toast toast-${type}`;
    toast.textContent = message;

    const base = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);padding:12px 24px;border-radius:8px;z-index:10000;transition:all .3s ease;opacity:1;font-weight:500;box-shadow:0 4px 12px rgba(0,0,0,0.15);';
    const typeStyles = {
      info: 'background-color:rgba(59,130,246,.9);color:#fff;',
      success: 'background-color:rgba(34,197,94,.9);color:#fff;',
      warning: 'background-color:rgba(245,158,11,.9);color:#fff;',
      error: 'background-color:rgba(239,68,68,.9);color:#fff;'
    };
    toast.style.cssText = base + (typeStyles[type] || typeStyles.info);

    document.body.appendChild(toast);

    const ms = duration || (type === 'error' ? 4000 : 2500);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(-50%) translateY(20px)';
      setTimeout(() => toast.remove(), 300);
    }, ms);
  }

  showUpgradeModal(message) {
    if (typeof window.showUpgradeModal === 'function') {
      window.showUpgradeModal(message);
    } else {
      this.showToast(message, 'error', 5000);
    }
  }
}

// ============================================================================
// PLAYER CONFLICT RESOLVER (standalone class)
// ============================================================================

class PlayerConflictResolver {
  constructor(preservePlayback = false) {
    this.isPlayerPage = true;
    this.preservePlayback = preservePlayback;
    this.originalShowMiniPlayer = null;
    this.originalUpdatePlayerState = null;
    this.init();
  }

  init() {
    this.disableMiniPlayer();
    // ✅ REMOVED: Body class is now managed by PlayerController only
    // document.body.classList.add('player-page');
  }

  disableMiniPlayer() {
    const miniPlayer = document.getElementById('miniPlayer');
    if (miniPlayer) {
      miniPlayer.style.display = 'none';
      miniPlayer.style.visibility = 'hidden';
      miniPlayer.style.pointerEvents = 'none';
    }

    if (window.persistentPlayer) {
      // ✅ FIX: Only pause if NOT preserving playback (i.e., NOT expanding from miniplayer)
      if (window.persistentPlayer.audio && !this.preservePlayback) {
        window.persistentPlayer.audio.pause();
      } else if (this.preservePlayback) {
      }

      window.persistentPlayer.hideMiniPlayer();
      window.persistentPlayer.isPlayerPage = true;

      // ✅ Store original methods so we can restore them later
      this.originalShowMiniPlayer = window.persistentPlayer.showMiniPlayer;
      this.originalUpdatePlayerState = window.persistentPlayer.updatePlayerState;

      // Override methods
      window.persistentPlayer.showMiniPlayer = function() {
      };

      window.persistentPlayer.updatePlayerState = function() {
        if (!window.location.pathname.startsWith('/player/')) {
          this.originalUpdatePlayerState.call(this);
        }
      }.bind(this);
    }
  }

  // ✅ NEW: Restore original methods when leaving player page
  cleanup() {

    // ✅ CRITICAL: Remove inline styles from miniPlayer element
    const miniPlayer = document.getElementById('miniPlayer');
    if (miniPlayer) {
      miniPlayer.style.display = '';
      miniPlayer.style.visibility = '';
      miniPlayer.style.pointerEvents = '';
    }

    if (window.persistentPlayer) {
      // Restore original methods
      if (this.originalShowMiniPlayer) {
        window.persistentPlayer.showMiniPlayer = this.originalShowMiniPlayer;
      }
      if (this.originalUpdatePlayerState) {
        window.persistentPlayer.updatePlayerState = this.originalUpdatePlayerState;
      }

      // Reset flags
      window.persistentPlayer.isPlayerPage = false;

      // ✅ Force update player state to show mini-player if track is playing
      if (window.persistentPlayer.currentTrackId) {
        window.persistentPlayer.updatePlayerState();
      }
    }

    // ✅ REMOVED: Body class is now managed by PlayerController only
    // document.body.classList.remove('player-page');

  }
}

// ============================================================================
// AUTO-PLAY TOOLTIP
// ============================================================================

function showAutoPlayTooltip() {
  // Remove any existing tooltips
  const existing = document.querySelector('.auto-play-tooltip-popup');
  if (existing) {
    existing.remove();
  }
  const existingBackdrop = document.querySelector('.auto-play-tooltip-backdrop');
  if (existingBackdrop) {
    existingBackdrop.remove();
  }

  // Get the info button to position tooltip relative to it
  const infoBtn = document.getElementById('autoPlayInfoBtn');
  if (!infoBtn) {
    return;
  }

  // Get button position
  const rect = infoBtn.getBoundingClientRect();
  const tooltipGap = 12; // Gap between button and tooltip

  // Create backdrop
  const backdrop = document.createElement('div');
  backdrop.className = 'auto-play-tooltip-backdrop';

  // Create tooltip popup
  const tooltip = document.createElement('div');
  tooltip.className = 'auto-play-tooltip-popup';

  // Get current theme
  const isDarkTheme = document.documentElement.getAttribute('data-theme') === 'dark';
  const offIconColor = isDarkTheme ? 'rgba(255,255,255,0.3)' : 'rgba(0,0,0,0.3)';

  tooltip.innerHTML = `
    <h3><i class="fas fa-info-circle"></i> Play Button Controls</h3>

    <div class="tooltip-section">
      <div class="tooltip-instruction">
        <i class="fas fa-hand-pointer"></i> <strong>Single tap:</strong> Play/Pause
      </div>
      <div class="tooltip-instruction">
        <i class="fas fa-hand-pointer"></i> <strong>Double-tap:</strong> Increase speed ⏩
      </div>
      <div class="tooltip-instruction">
        <i class="fas fa-hand-pointer"></i> <strong>Triple-tap:</strong> Decrease speed ⏪
      </div>
      <div class="tooltip-instruction">
        <i class="fas fa-hand-pointer"></i> <strong>Long press (0.5s):</strong> Toggle Auto-play
      </div>
    </div>

    <div class="tooltip-section" style="margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(128,128,128,0.2);">
      <p style="margin: 0 0 8px 0; font-size: 13px; opacity: 0.9;"><strong>Auto-Play Status:</strong></p>
      <p class="tooltip-status-indicators" style="margin: 0;">
        <i class="fas fa-circle" style="color: ${offIconColor}; font-size: 6px;"></i> Off: Gray &nbsp;
        <i class="fas fa-circle" style="color: #4A90E2; font-size: 6px;"></i> On: Blue arrows
      </p>
    </div>

    <button class="tooltip-close-btn">Got it!</button>
  `;

  // Add to DOM
  document.body.appendChild(backdrop);
  document.body.appendChild(tooltip);

  // Calculate position - above the info button, centered on it
  const tooltipRect = tooltip.getBoundingClientRect();
  const topPosition = rect.top - tooltipRect.height - tooltipGap;
  const leftPosition = rect.left + (rect.width / 2); // Center of button

  // Set the position
  tooltip.style.top = `${topPosition}px`;
  tooltip.style.left = `${leftPosition}px`;

  //   infoButtonTop: rect.top,
  //   infoButtonLeft: rect.left,
  //   infoButtonCenter: leftPosition,
  //   tooltipHeight: tooltipRect.height,
  //   calculatedTop: topPosition
  // });

  // Close handlers
  const closeBtn = tooltip.querySelector('.tooltip-close-btn');
  const closeTooltip = () => {
    tooltip.style.animation = 'tooltip-fade-out 0.2s ease';
    backdrop.style.animation = 'backdrop-fade-out 0.2s ease';
    setTimeout(() => {
      tooltip.remove();
      backdrop.remove();
    }, 200);
  };

  closeBtn.addEventListener('click', closeTooltip);
  backdrop.addEventListener('click', closeTooltip);

  // Add fade-out animations
  if (!document.getElementById('tooltip-fade-animations')) {
    const style = document.createElement('style');
    style.id = 'tooltip-fade-animations';
    style.textContent = `
      @keyframes tooltip-fade-out {
        from { opacity: 1; transform: translateX(-50%) translateY(0); }
        to { opacity: 0; transform: translateX(-50%) translateY(-10px); }
      }
      @keyframes backdrop-fade-out {
        from { opacity: 1; }
        to { opacity: 0; }
      }
    `;
    document.head.appendChild(style);
  }
}

// Make it globally available
if (typeof window !== 'undefined') {
  window.showAutoPlayTooltip = showAutoPlayTooltip;
}

// ============================================================================
// AJAX PAGE LOAD HANDLER (for backward compatibility)
// ============================================================================

if (typeof window !== 'undefined') {
  // Ensure player initializes when loaded via AJAX
  window.addEventListener('ajaxPageLoaded', function(event) {
    if (event.detail.url.includes('/player/')) {
      // This will be handled by the SPA router mounting the PlayerController
    }
  });
}
