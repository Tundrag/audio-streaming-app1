// persistence.js – Voice-aware HLS audio player with version-aware cache busting and minimal UI noise

if (typeof Hls === 'undefined') {
  throw new Error('Hls.js is required for this player to function.');
}

function getDeviceConfig() {
  const cores = navigator.hardwareConcurrency || 4;
  const isMobile = /android|iphone|ipad|ipod|blackberry|iemobile|opera mini/i.test(navigator.userAgent);
  if (isMobile) return { maxBufferLength: 300, backBufferLength: 60, enableWorker: false };
  if (cores <= 4) return { maxBufferLength: 480, backBufferLength: 90, enableWorker: true };
  return { maxBufferLength: 720, backBufferLength: 120, enableWorker: true };
}

/* ----------------------- Progress persistence ----------------------- */
class PlaybackProgress {
  constructor(player) {
    this.player = player;
    this.lastSyncedTime = 0;
    this.lastSyncedTrackId = null;
    this.syncEnabled = true;
    this.lastSyncTimestamp = 0;
    this.minSyncInterval = 30000;
    this.isSeekingInProgress = false;
    this.saveQueue = [];
    this.maxQueueSize = 10;
    this.maxRetries = 3;
    this.setupSync();
  }

  setupSync() {
    const syncProgress = () => this.syncProgress(true);
    ['play', 'pause', 'seeking', 'ended'].forEach(ev =>
      this.player.audio.addEventListener(ev, syncProgress)
    );
    setInterval(syncProgress, 30000);
    window.addEventListener('beforeunload', () => this.forceSyncBeforeUnload());
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) syncProgress();
      else this.processQueue();
    });
    window.addEventListener('online', () => this.processQueue());
  }

  forceSyncBeforeUnload() {
    if (!this.syncEnabled || !this.player.currentTrackId || !this.player.audio.duration) return;
    const progressData = this.buildProgressData();
    if (navigator.sendBeacon) {
      const blob = new Blob([JSON.stringify(progressData)], { type: 'application/json' });
      navigator.sendBeacon('/api/progress/save', blob);
    }
  }

  buildProgressData() {
    const currentTime = Math.floor(this.player.audio.currentTime || 0);
    const duration = Math.floor(this.player.audio.duration || 0);
    const progressData = {
      track_id: this.player.currentTrackId,
      position: currentTime,
      duration: duration,
      completed: duration > 0 && currentTime >= duration * 0.9,
      client_time: Date.now(),
      voice_id: this.player.currentVoice,
      track_type: this.player.trackType
    };
    const idx = this.player.getActiveWordIndexLocal();
    if (idx >= 0) {
      progressData.word_position = {
        word_index: idx,
        supports_word_sync: true,
        voice_id: this.player.currentVoice
      };
    }
    return progressData;
  }

  async syncProgress(forceSave = false) {
    if (!this.syncEnabled || !this.player.currentTrackId || !this.player.audio.duration || this.isSeekingInProgress) return;
    const currentTime = Math.floor(this.player.audio.currentTime || 0);
    const now = Date.now();
    if (!forceSave && now - this.lastSyncTimestamp < this.minSyncInterval) return;
    if (!forceSave && this.player.currentTrackId === this.lastSyncedTrackId && Math.abs(currentTime - this.lastSyncedTime) < 5) return;
    const progressData = this.buildProgressData();
    try {
      if (!navigator.onLine) {
        this.queueSave(progressData);
        return;
      }
      const res = await fetch('/api/progress/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(progressData)
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this.lastSyncedTime = currentTime;
      this.lastSyncedTrackId = this.player.currentTrackId;
      this.lastSyncTimestamp = now;
    } catch {
      this.queueSave(progressData);
    }
  }

  queueSave(progressData) {
    this.saveQueue = this.saveQueue.filter(
      item => item.track_id !== progressData.track_id || Math.abs(item.position - progressData.position) > 5
    );
    this.saveQueue.push({ ...progressData, queuedAt: Date.now(), retries: 0 });
    if (this.saveQueue.length > this.maxQueueSize) this.saveQueue.shift();
  }

  async processQueue() {
    if (!navigator.onLine || !this.saveQueue.length) return;
    const list = [...this.saveQueue];
    this.saveQueue = [];
    for (const q of list) {
      if (q.retries >= this.maxRetries) continue;
      try {
        const res = await fetch('/api/progress/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(q)
        });
        if (!res.ok) throw new Error();
      } catch {
        q.retries++;
        if (q.retries < this.maxRetries) this.saveQueue.push(q);
      }
    }
  }

  async saveBeforeReinit() {
    if (!this.player.currentTrackId || !this.player.audio.duration) return;
    await this.syncProgress(true);
    this.saveQueue = this.saveQueue.filter(i => i.track_id !== this.player.currentTrackId);
    if (navigator.onLine) await this.processQueue();
  }

  pauseSync() { this.syncEnabled = false; }
  resumeSync() { this.syncEnabled = true; this.processQueue(); }

  async loadProgress(trackId, voiceId = null) {
    try {
      let url = `/api/progress/load/${encodeURIComponent(trackId)}`;
      if (voiceId) url += `?voice=${encodeURIComponent(voiceId)}`;
      const response = await fetch(url);
      if (response.ok) {
        const progress = await response.json();
        if (progress?.position > 0) {
          this.lastSyncedTime = progress.position;
          this.lastSyncedTrackId = trackId;
          // Log if position was translated from a different voice
          if (progress.voice_translated) {
            console.log(`✓ Position translated from voice ${progress.last_voice_id} to ${voiceId} (word ${progress.word_position})`);
          }
          return progress;
        }
      }
      return null;
    } catch { return null; }
  }

  destroy() { this.pauseSync(); this.saveQueue = []; }
}

/* ----------------------- Network monitor ----------------------- */
class NetworkMonitor {
  constructor() {
    this.samples = [];
    this.maxSamples = 10;
    this.quality = 'good';
    this.startMonitoring();
  }
  startMonitoring() {
    if (navigator.connection) navigator.connection.addEventListener('change', () => this.assessQuality());
    setInterval(() => this.assessQuality(), 15000);
  }
  addBandwidthSample(bps) {
    this.samples.push(bps);
    if (this.samples.length > this.maxSamples) this.samples.shift();
    this.assessQuality();
  }
  assessQuality() {
    const avg = this.samples.reduce((a,b)=>a+b,0)/(this.samples.length||1);
    const c = navigator.connection;
    if (avg > 1_000_000 || (c?.effectiveType === '4g' && c?.downlink > 2)) this.quality = 'excellent';
    else if (avg > 500_000 || c?.effectiveType === '4g') this.quality = 'good';
    else if (avg > 200_000 || c?.effectiveType === '3g') this.quality = 'fair';
    else this.quality = 'poor';
    document.dispatchEvent(new CustomEvent('networkQualityChanged', { detail: { quality: this.quality, bandwidth: avg, connection: c } }));
  }
  getQuality() { return this.quality; }
}

/* ----------------------- Download manager ----------------------- */
class VoiceAwareDownloadManager {
  constructor(player) { this.player = player; this.activeDownloads = new Map(); this.downloadCallbacks = new Map(); }

  async checkTrackAccess(trackId, albumId = null) {
    try {
      const trackRes = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/check-access`);
      if (trackRes.status === 403) {
        const data = await trackRes.json();
        return { hasAccess: false, error: data.error?.message || 'This content requires a higher tier subscription', type: 'track_access' };
      }
      if (!trackRes.ok) return { hasAccess: false, error: 'Failed to verify track access', type: 'track_access' };
      if (albumId) {
        const albumRes = await fetch(`/api/albums/${encodeURIComponent(albumId)}/check-access`);
        if (albumRes.status === 403) {
          const data = await albumRes.json();
          return { hasAccess: false, error: data.error?.message || 'This content requires a higher tier subscription', type: 'album_access' };
        }
      }
      return { hasAccess: true };
    } catch { return { hasAccess: false, error: 'Error checking access permissions', type: 'network_error' }; }
  }

  getDownloadKey(trackId, voiceId = null) { return voiceId ? `${trackId}_${voiceId}` : trackId; }

  async startDownload(trackId, albumId = null, voiceId = null, onProgress = null, onComplete = null, onError = null) {
    const key = this.getDownloadKey(trackId, voiceId);
    try {
      this.downloadCallbacks.set(key, { onProgress, onComplete, onError });
      const access = await this.checkTrackAccess(trackId, albumId);
      if (!access.hasAccess) { onError?.(access.error, access.type); return null; }
      if (this.activeDownloads.has(key)) { return key; }

      this.activeDownloads.set(key, { status: 'starting', progress: 0 });
      onProgress?.(0, 'Starting download...');
      let url = `/api/tracks/${encodeURIComponent(trackId)}/download`;
      if (voiceId) url += `?voice=${encodeURIComponent(voiceId)}`;
      const res = await fetch(url);
      if (res.status === 403) {
        const data = await res.json();
        const msg = data.detail?.downloads_used !== undefined
          ? `Download limit reached (${data.detail.downloads_used}/${data.detail.downloads_limit}).`
          : data.detail?.message || 'Access denied';
        this.activeDownloads.delete(key);
        onError?.(msg, 'download_limit');
        return null;
      }
      if (!res.ok) { this.activeDownloads.delete(key); onError?.('Failed to start download', 'start_error'); return null; }
      const data = await res.json();
      if (data.downloads_remaining !== undefined) {}
      this.pollDownloadStatus(trackId, voiceId);
      return key;
    } catch (e) {
      this.activeDownloads.delete(key);
      onError?.(e?.message || 'Download failed', 'network_error');
      return null;
    }
  }

  async pollDownloadStatus(trackId, voiceId = null) {
    const key = this.getDownloadKey(trackId, voiceId);
    const cb = this.downloadCallbacks.get(key);
    if (!this.activeDownloads.has(key)) return;
    try {
      let url = `/api/tracks/${encodeURIComponent(trackId)}/status`;
      if (voiceId) url += `?voice=${encodeURIComponent(voiceId)}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error('Failed to check download status');
      const s = await res.json();
      if (s.status === 'error') { this.activeDownloads.delete(key); this.downloadCallbacks.delete(key); cb?.onError?.(s.error || 'Download failed', 'download_error'); return; }
      const progress = s.progress || 0;
      this.activeDownloads.set(key, { status: s.status, progress, queuePosition: s.queue_position });
      let message = 'Processing...';
      if (s.status === 'queued' && s.queue_position) message = `Queued (Position: ${s.queue_position})`;
      else if (s.progress) message = `${Math.round(s.progress)}%`;
      cb?.onProgress?.(progress, message, s.queue_position);
      if (s.status === 'completed') { this.activeDownloads.delete(key); this.downloadCallbacks.delete(key); await this.downloadFile(trackId, voiceId); cb?.onComplete?.(); return; }
      setTimeout(() => this.pollDownloadStatus(trackId, voiceId), 1000);
    } catch (e) { this.activeDownloads.delete(key); this.downloadCallbacks.delete(key); cb?.onError?.(e?.message || 'Status check failed', 'status_error'); }
  }

  async downloadFile(trackId, voiceId = null) {
    try {
      let url = `/api/tracks/${encodeURIComponent(trackId)}/file`;
      if (voiceId) url += `?voice=${encodeURIComponent(voiceId)}`;
      const link = document.createElement('a');
      link.href = url; link.style.display = 'none'; document.body.appendChild(link); link.click(); document.body.removeChild(link);
    } catch {}
  }

  isDownloading(trackId, voiceId = null) { return this.activeDownloads.has(this.getDownloadKey(trackId, voiceId)); }
  getDownloadStatus(trackId, voiceId = null) { return this.activeDownloads.get(this.getDownloadKey(trackId, voiceId)) || null; }
  cancelDownload(trackId, voiceId = null) { const key = this.getDownloadKey(trackId, voiceId); this.activeDownloads.delete(key); this.downloadCallbacks.delete(key); }
}

/* ----------------------- PersistentPlayer ----------------------- */
class PersistentPlayer {
  constructor() {
    const deviceConfig = getDeviceConfig();
    this.hlsConfig = {
      debug: false,
      enableWorker: deviceConfig.enableWorker,
      startFragPrefetch: true,
      fragLoadingMaxRetry: 8,
      manifestLoadingMaxRetry: 8,
      levelLoadingMaxRetry: 8,
      testBandwidth: true,
      progressive: true,
      maxBufferLength: deviceConfig.maxBufferLength,
      backBufferLength: deviceConfig.backBufferLength,
      maxMaxBufferLength: deviceConfig.maxBufferLength * 2,
      maxBufferHole: 0.3,
      nudgeOffset: 0.1,
      nudgeMaxRetry: 3,
      maxFragLookUpTolerance: 0.25,
      fragLoadingTimeOut: 20000,
      manifestLoadingTimeOut: 10000,
      levelLoadingTimeOut: 10000,
      fragLoadingMaxRetryTimeout: 64000,
      levelLoadingMaxRetryTimeout: 64000,
      manifestLoadingMaxRetryTimeout: 64000,
    };

    this.DEFAULT_COVER = '/static/images/default-cover.jpg';

    this.currentVoice = null;
    this.trackType = 'audio';
    this.currentTrackId = sessionStorage.getItem('currentTrackId') || null;
    this.isPlayerPage = window.location.pathname.startsWith('/player/');

    this.trackMetadata = {
      id: null, title: null, album: null, coverPath: null,
      voice: null, trackType: 'audio', albumId: null,
      defaultVoice: null, content_version: 1
    };

    this.audio = document.getElementById('audioPlayer') || new Audio();
    if (!this.audio.id) { this.audio.id = 'audioPlayer'; document.body.appendChild(this.audio); }

    this.recoveryAttempts = 0;
    this.maxRecoveryAttempts = 5;

    this.networkMonitor = new NetworkMonitor();
    this.downloadManager = new VoiceAwareDownloadManager(this);

    this.segmentProgress = {
      isMonitoring: false,
      pollInterval: null,
      currentToast: null,
      monitoringTrackId: null,
      monitoringVoice: null,
      lastUpdateTime: null,
      staleCheckInterval: null
    };

    this.wordIndexProvider = null;
    this.isValidatingVoice = false;
    this._isSaving = false;
    this._voiceLock = false;

    this.initializeElements();
    this.setupEventListeners();
    this.setupMiniPlayer();
    this.progress = new PlaybackProgress(this);
    this.loadExistingState();
  }

  setWordIndexProvider(providerFn) { this.wordIndexProvider = typeof providerFn === 'function' ? providerFn : null; }
  getActiveWordIndexLocal() { if (this.wordIndexProvider) { const i = this.wordIndexProvider(); if (typeof i === 'number' && i >= 0) return i; } return -1; }

  async checkAccess(trackId, albumId = null, voiceId = null) { return await this.downloadManager.checkTrackAccess(trackId, albumId, voiceId); }
  async downloadTrack(trackId, albumId = null, voiceId = null, onProgress = null, onComplete = null, onError = null) { return await this.downloadManager.startDownload(trackId, albumId, voiceId, onProgress, onComplete, onError); }
  isTrackDownloading(trackId, voiceId = null) { return this.downloadManager.isDownloading(trackId, voiceId); }
  getTrackDownloadStatus(trackId, voiceId = null) { return this.downloadManager.getDownloadStatus(trackId, voiceId); }
  cancelTrackDownload(trackId, voiceId = null) { this.downloadManager.cancelDownload(trackId, voiceId); }

  generateHlsUrl(trackId, voice = null) {
    const voiceToUse = voice || sessionStorage.getItem(`voice_pref_${trackId}`) || this.currentVoice;
    const version = Number(this.trackMetadata?.content_version) || Number(this.trackMetadata?.cache_bust) || Date.now();
    if (this.trackType === 'tts' && voiceToUse) return `/hls/${trackId}/voice/${voiceToUse}/master.m3u8?v=${version}`;
    return `/hls/${trackId}/master.m3u8?v=${version}`;
  }

  async isVoiceAvailable(trackId, voiceId) {
    if (!voiceId || this.trackType !== 'tts') return true;
    try {
      const response = await fetch(`/api/tracks/${encodeURIComponent(trackId)}/voices`);
      if (!response.ok) return false;
      const data = await response.json();
      const generatedVoices = data.generated_voices || [];
      const defaultVoice = data.default_voice;
      return generatedVoices.includes(voiceId) || voiceId === defaultVoice;
    } catch { return true; }
  }

  async validateAndResolveVoice(trackId, preferredVoice) {
    if (!preferredVoice) {
      const def = this.trackMetadata?.defaultVoice || this.getTrackDefaultVoice();
      return def;
    }
    if (this.trackType !== 'tts') return preferredVoice;
    try {
      const ok = await this.isVoiceAvailable(trackId, preferredVoice);
      if (ok) return preferredVoice;
      const def = this.trackMetadata?.defaultVoice || this.getTrackDefaultVoice();
      sessionStorage.removeItem(`voice_pref_${trackId}`);
      return def;
    } catch {
      const def = this.trackMetadata?.defaultVoice || this.getTrackDefaultVoice();
      return def;
    }
  }

  getVoiceDisplayName(voiceId) { if (!voiceId) return 'Default'; return voiceId.replace(/^en-(US|GB)-/, '').replace('Neural', ''); }

  getTrackDefaultVoice() {
    const sources = [
      this.trackMetadata?.default_voice,
      window.trackData?.default_voice,
      this.trackMetadata?.voice
    ];
    for (const s of sources) if (s) return s;
    const playerData = document.querySelector('#player-data');
    if (playerData) {
      try {
        const data = JSON.parse(playerData.textContent);
        return data.track?.default_voice || null;
      } catch {}
    }
    return null;
  }

  async seekToWord(wordIndex, voiceId = null) {
    if (!this.currentTrackId || wordIndex < 0) return false;
    const voice = voiceId || this.currentVoice; if (!voice) return false;
    try {
      const response = await fetch(`/api/tracks/${encodeURIComponent(this.currentTrackId)}/time-for-word?word_index=${wordIndex}&voice_id=${encodeURIComponent(voice)}`);
      if (!response.ok) return false;
      const data = await response.json();
      if (data.status !== 'found' || data.time == null) return false;
      return await this.seekToTimeWithPrecision(data.time);
    } catch { return false; }
  }

  async seekToTimeWithPrecision(targetTime, tolerance = 0.1) {
    if (!this.audio.duration || targetTime < 0 || targetTime > this.audio.duration) return false;
    const cur = this.audio.currentTime;
    if (Math.abs(cur - targetTime) < tolerance) return true;
    try {
      this.progress.isSeekingInProgress = true;
      this.audio.currentTime = targetTime;
      await new Promise(resolve => {
        const onSeeked = () => {
          this.audio.removeEventListener('seeked', onSeeked);
          this.progress.isSeekingInProgress = false;
          this.progress.syncProgress(true);
          resolve();
        };
        this.audio.addEventListener('seeked', onSeeked, { once: true });
      });
      return true;
    } catch {
      this.progress.isSeekingInProgress = false;
      return false;
    }
  }

  async getCurrentWordIndex() {
    if (!this.currentTrackId || !this.currentVoice || !this.audio.currentTime) return -1;
    try {
      const response = await fetch(`/api/tracks/${encodeURIComponent(this.currentTrackId)}/word-at-time?time=${this.audio.currentTime}&voice_id=${encodeURIComponent(this.currentVoice)}`);
      if (!response.ok) return -1;
      const data = await response.json();
      return (typeof data.word_index === 'number' && data.word_index >= 0) ? data.word_index : -1;
    } catch { return -1; }
  }

  setupMiniPlayer() {
    this.miniPlayer = document.getElementById('miniPlayer');
    if (!this.miniPlayer) return;
    const miniPlayerContent = this.miniPlayer.querySelector('.mini-player-content');
    if (!miniPlayerContent) return;
    const closeButton = document.createElement('button');
    closeButton.id = 'closeMiniPlayer';
    closeButton.className = 'mini-control-btn';
    closeButton.title = 'Close Player';
    closeButton.innerHTML = '<i class="fas fa-times"></i>';
    miniPlayerContent.appendChild(closeButton);
    this.elements.closeBtn = closeButton;
    closeButton.addEventListener('click', (e) => { e.stopPropagation(); this.closeMiniPlayer(); });
    // Note: Initial visibility is handled by template inline style
  }

  initializeElements() {
    this.miniPlayer = document.getElementById('miniPlayer');
    const elements = {
      cover: document.getElementById('miniPlayerCover'),
      title: document.getElementById('miniPlayerTitle'),
      album: document.getElementById('miniPlayerAlbum'),
      playPauseBtn: document.getElementById('miniPlayerPlayPause'),
      playIcon: document.getElementById('miniPlayerPlayIcon'),
      rewindBtn: document.getElementById('miniPlayerRewind'),
      forwardBtn: document.getElementById('miniPlayerForward'),
      expandBtn: document.getElementById('expandPlayer'),
      progressBar: document.getElementById('miniPlayerProgress'),
      progressContainer: document.getElementById('miniPlayerProgressBar'),
      currentTime: document.getElementById('miniPlayerCurrentTime'),
      duration: document.getElementById('miniPlayerDuration')
    };
    this.elements = Object.fromEntries(Object.entries(elements).filter(([, el]) => el));
    const requestIdle = window.requestIdleCallback || (cb => setTimeout(cb, 1));
    const cancelIdle = window.cancelIdleCallback || clearTimeout;
    const scheduleIdleSave = () => {
      if (this.idleSaveHandle) cancelIdle(this.idleSaveHandle);
      this.idleSaveHandle = requestIdle(() => this.saveState(), { timeout: 30000 });
    };
    ['play','pause','seeking','ended','ratechange'].forEach(ev => this.audio.addEventListener(ev, scheduleIdleSave));
    document.addEventListener('visibilitychange', scheduleIdleSave);
    window.addEventListener('beforeunload', () => this.saveState());
  }

  setupEventListeners() {
    if (this.elements.playPauseBtn) this.elements.playPauseBtn.addEventListener('click', () => this.togglePlay());
    if (this.elements.rewindBtn) this.elements.rewindBtn.addEventListener('click', () => this.seek(-15));
    if (this.elements.forwardBtn) this.elements.forwardBtn.addEventListener('click', () => this.seek(15));
    if (this.elements.expandBtn) this.elements.expandBtn.addEventListener('click', () => this.expandPlayer());

    if (this.elements.progressContainer) {
      this.elements.progressContainer.addEventListener('click', async (e) => {
        const rect = this.elements.progressContainer.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        const targetTime = pos * this.audio.duration;
        const ok = await this.seekToTimeWithPrecision(targetTime);
        if (!ok) this.seek(targetTime - this.audio.currentTime);
      });
    }

    document.querySelectorAll('[data-speed]').forEach(btn => {
      btn.addEventListener('click', (e) => this.setPlaybackSpeed(parseFloat(e.currentTarget.getAttribute('data-speed'))));
    });
    const speedSelect = document.getElementById('speedSelect');
    if (speedSelect) speedSelect.addEventListener('change', (e) => this.setPlaybackSpeed(parseFloat(e.target.value)));

    this.audio.addEventListener('timeupdate', () => { this.needsUI = true; });
    const step = () => { if (this.needsUI) { this.updateProgress(); this.needsUI = false; } requestAnimationFrame(step); };
    requestAnimationFrame(step);

    this.audio.addEventListener('play', () => this.handlePlay());
    this.audio.addEventListener('pause', () => this.handlePause());
    this.audio.addEventListener('ended', () => this.handleEnded());
    this.audio.addEventListener('error', (e) => this.handleError(e));

    document.addEventListener('keydown', (e) => {
      if (e.target.matches('input, textarea')) return;
      switch (e.code) {
        case 'Space': e.preventDefault(); this.togglePlay(); break;
        case 'ArrowLeft': this.seek(e.shiftKey ? -10 : -15); break;
        case 'ArrowRight': this.seek(e.shiftKey ? 10 : 15); break;
        case 'KeyM': this.toggleMute(); break;
      }
    });

    window.addEventListener('online', () => this.handleNetworkChange());
    window.addEventListener('offline', () => this.handleNetworkChange());
    window.addEventListener('popstate', () => this.updatePlayerState());
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { this.saveState(); }
      else { this.refreshVersionAndMaybeReinit(); this.updatePlayerState(); }
    });
  }

  setTrackMetadata(trackId, title, album, coverPath, voice = null, trackType = 'audio', albumId = null, contentVersion = null) {
    if (this._voiceLock) return;
    this._voiceLock = true;
    try {
      // ✅ Prevent overwriting good metadata with defaults if we're already playing this track
      if (this.currentTrackId === trackId && this.trackMetadata.id === trackId) {
        if ((!title || title === 'Unknown Track') && this.trackMetadata.title && this.trackMetadata.title !== 'Unknown Track') {
          return;  // Don't overwrite good metadata with "Unknown Track"
        }
      }

      const savedVoicePreference = sessionStorage.getItem(`voice_pref_${trackId}`);
      const preferredVoice = savedVoicePreference || this.currentVoice || voice;
      const trackDefaultVoice = this.getTrackDefaultVoice();
      this.trackMetadata = {
        id: trackId,
        title: title || 'Unknown Track',
        album: album || 'Unknown Album',
        coverPath: coverPath || this.DEFAULT_COVER,
        voice: preferredVoice,
        trackType: trackType || 'audio',
        albumId,
        defaultVoice: trackDefaultVoice,
        content_version: contentVersion || 1
      };
      this.currentTrackId = trackId;
      this.currentVoice = preferredVoice;
      this.trackType = trackType || 'audio';
      this.saveTrackMetadata();
    } finally {
      this._voiceLock = false;
    }
  }

  saveTrackMetadata() {
    if (this._isSaving || !this.trackMetadata.id) return;
    try {
      this._isSaving = true;
      const metadata = {
        ...this.trackMetadata,
        voice: this.currentVoice || this.trackMetadata.voice,
        trackType: this.trackType || this.trackMetadata.trackType || 'audio',
        lastUpdated: Date.now()
      };
      sessionStorage.setItem('trackMetadata', JSON.stringify(metadata));
      sessionStorage.setItem('currentTrackId', this.trackMetadata.id);
      if (this.currentVoice) {
        sessionStorage.setItem(`voice_pref_${this.trackMetadata.id}`, this.currentVoice);
        sessionStorage.setItem('currentVoice', this.currentVoice);
      }
      if (this.trackType) sessionStorage.setItem('trackType', this.trackType);
    } finally {
      this._isSaving = false;
    }
  }

  loadTrackMetadata() {
    try {
      const stored = sessionStorage.getItem('trackMetadata');
      if (!stored) return false;
      const metadata = JSON.parse(stored);
      if (!metadata.id) return false;
      const savedVoice =
        sessionStorage.getItem(`voice_pref_${metadata.id}`) ||
        metadata.voice ||
        sessionStorage.getItem('currentVoice') ||
        this.getTrackDefaultVoice();
      this.trackMetadata = {
        id: metadata.id,
        title: metadata.title || 'Unknown Track',
        album: metadata.album || 'Unknown Album',
        coverPath: metadata.coverPath || this.DEFAULT_COVER,
        voice: savedVoice,
        trackType: metadata.trackType || 'audio',
        albumId: metadata.albumId,
        defaultVoice: metadata.defaultVoice || this.getTrackDefaultVoice(),
        content_version: metadata.content_version || 1
      };
      this.currentTrackId = metadata.id;
      this.trackType = metadata.trackType || 'audio';
      this.currentVoice = savedVoice;
      return true;
    } catch { return false; }
  }

  saveState() {
    if (this._isSaving || !this.currentTrackId || !this.trackMetadata.id) return;
    try {
      this._isSaving = true;
      const state = {
        trackId: this.trackMetadata.id,
        title: this.trackMetadata.title,
        album: this.trackMetadata.album,
        coverPath: this.trackMetadata.coverPath,
        voice: this.currentVoice || this.trackMetadata.voice,
        trackType: this.trackType || this.trackMetadata.trackType,
        albumId: this.trackMetadata.albumId,
        isPlaying: !this.audio.paused,
        volume: this.audio.volume,
        muted: this.audio.muted,
        playbackRate: this.audio.playbackRate,
        currentTime: this.audio.currentTime,
        timestamp: Date.now(),
        voiceLastUpdated: Date.now(),
        content_version: this.trackMetadata.content_version || 1
      };
      sessionStorage.setItem(`playerState_${this.currentTrackId}`, JSON.stringify(state));
      sessionStorage.setItem('currentTrackId', this.currentTrackId);
      if (this.currentVoice) {
        sessionStorage.setItem('currentVoice', this.currentVoice);
        sessionStorage.setItem(`voice_pref_${this.currentTrackId}`, this.currentVoice);
      }
      const metadata = {
        ...this.trackMetadata,
        voice: this.currentVoice,
        trackType: this.trackType,
        lastUpdated: Date.now()
      };
      sessionStorage.setItem('trackMetadata', JSON.stringify(metadata));
    } finally { this._isSaving = false; }
  }

  wireHlsEvents(hls) {
    if (!hls) return;
    hls.on(Hls.Events.ERROR, (event, data) => this.handleHlsError(event, data));
    hls.on(Hls.Events.FRAG_LOADED, (_event, data) => {
      const s = data.stats;
      if (s?.total && s?.trequest) {
        const bps = (s.total * 8) / ((s.tload - s.trequest) / 1000);
        if (Number.isFinite(bps)) this.networkMonitor.addBandwidthSample(bps);
      }
    });
    hls.on(Hls.Events.FRAG_CHANGED, (_evt, data) => {
      document.dispatchEvent(new CustomEvent('hlsFragChanged', { detail: { sn: data?.frag?.sn ?? null } }));
    });
  }

  async validateAndResolveVoiceWithRetry(trackId, preferredVoice, maxRetries = 3) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try { return await this.validateAndResolveVoice(trackId, preferredVoice); }
      catch (error) { if (attempt === maxRetries) throw error; await new Promise(r => setTimeout(r, 1000 * Math.pow(2, attempt - 1))); }
    }
  }

  async loadProgressWithRetry(trackId, voiceId, maxRetries = 3) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try { return await this.progress.loadProgress(trackId, voiceId); }
      catch (error) { if (attempt === maxRetries) throw error; await new Promise(r => setTimeout(r, 1000 * Math.pow(2, attempt - 1))); }
    }
  }

  async refreshContentVersion(trackId, voiceId = null) {
    try {
      let url = voiceId
        ? `/api/tracks/${encodeURIComponent(trackId)}/voice/${encodeURIComponent(voiceId)}/metadata`
        : `/api/tracks/${encodeURIComponent(trackId)}/metadata`;
      const res = await fetch(url);
      if (!res.ok) return null;
      const meta = await res.json();
      const fresh =
        Number(meta?.track?.content_version) ||
        Number(meta?.track?.cache_bust) ||
        Date.now();
      if (fresh && fresh !== this.trackMetadata.content_version) {
        this.trackMetadata.content_version = fresh;
        sessionStorage.setItem('trackMetadata', JSON.stringify(this.trackMetadata));
      }
      return fresh;
    } catch { return null; }
  }

  async refreshVersionAndMaybeReinit() {
    if (!this.currentTrackId) return;
    const oldV = this.trackMetadata.content_version;
    await this.refreshContentVersion(this.currentTrackId, this.trackType === 'tts' ? this.currentVoice : null);
    if (this.trackMetadata.content_version !== oldV) {
      const wasPlaying = !this.audio.paused;
      const pos = this.audio.currentTime;
      const rate = this.audio.playbackRate;
      if (this.hls) { this.hls.destroy(); this.hls = null; }
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();
      await this.initializeTrackForPlayback({ voice: this.currentVoice });
      this.audio.playbackRate = rate;
      if (pos > 0) this.audio.currentTime = pos;
      if (wasPlaying) this.audio.play().catch(()=>{});
    }
  }

  /* ---------- Core init ---------- */
  async initializeTrackForPlayback(opts = {}) {
    if (!this.trackMetadata.id) return;
    const overrideVoice = opts.voice;
    try {
      if (this.hls) { this.hls.destroy(); this.hls = null; }
      const savedState = JSON.parse(sessionStorage.getItem(`playerState_${this.currentTrackId}`) || '{}');
      if (overrideVoice) this.currentVoice = overrideVoice;
      let resolvedVoice;
      try { resolvedVoice = await this.validateAndResolveVoiceWithRetry(this.currentTrackId, this.currentVoice); }
      catch { resolvedVoice = this.trackMetadata.defaultVoice || this.getTrackDefaultVoice() || this.currentVoice; }
      if (resolvedVoice !== this.currentVoice) {
        this.currentVoice = resolvedVoice;
        if (this.voiceExtension) { this.voiceExtension.currentVoice = resolvedVoice; this.voiceExtension.updateVoiceButton?.(); }
        sessionStorage.setItem(`voice_pref_${this.currentTrackId}`, resolvedVoice);
      }

      await this.refreshContentVersion(this.currentTrackId, this.trackType === 'tts' ? this.currentVoice : null);

      let progressData;
      try { progressData = await this.loadProgressWithRetry(this.currentTrackId, this.currentVoice); }
      catch { progressData = null; }
      const startPosition = progressData?.position > 0 ? progressData.position : 0;
      const hlsUrl = this.generateHlsUrl(this.currentTrackId, this.currentVoice);

      if (Hls.isSupported()) {
        this.hls = new Hls(this.hlsConfig);
        this.wireHlsEvents(this.hls);
        await new Promise((resolve, reject) => {
          let settled = false;
          const hardTimeout = setTimeout(() => { if (settled) return; settled = true; reject(new Error('HLS timeout')); }, 25000);
          this.hls.once(Hls.Events.MANIFEST_PARSED, () => { if (settled) return; settled = true; clearTimeout(hardTimeout); resolve(); });
          this.hls.once(Hls.Events.ERROR, (_evt, data) => { if (data.fatal && !settled) { settled = true; clearTimeout(hardTimeout); reject(new Error('HLS Fatal Error')); } });
          this.hls.loadSource(hlsUrl);
          this.hls.attachMedia(this.audio);
        });
        if (startPosition > 0) {
          await new Promise(resolve => {
            const t = setTimeout(resolve, 5000);
            this.audio.addEventListener('loadedmetadata', () => { clearTimeout(t); this.audio.currentTime = startPosition; resolve(); }, { once: true });
          });
        }
        if (savedState.volume !== undefined) this.audio.volume = savedState.volume;
        if (savedState.muted) this.audio.muted = true;
        if (savedState.playbackRate) this.audio.playbackRate = savedState.playbackRate;
        setTimeout(() => this.progress.resumeSync(), 500);
        if (savedState.isPlaying) { setTimeout(() => { this.audio.play().catch(()=>{}); }, 300); }
      } else if (this.audio.canPlayType('application/vnd.apple.mpegurl')) {
        this.audio.src = hlsUrl;
        if (startPosition > 0) {
          await new Promise(resolve => {
            const t = setTimeout(resolve, 8000);
            this.audio.addEventListener('loadedmetadata', () => { clearTimeout(t); this.audio.currentTime = startPosition; resolve(); }, { once: true });
          });
        }
        if (savedState.volume !== undefined) this.audio.volume = savedState.volume;
        if (savedState.muted) this.audio.muted = true;
        if (savedState.playbackRate) this.audio.playbackRate = savedState.playbackRate;
        setTimeout(() => this.progress.resumeSync(), 500);
        if (savedState.isPlaying) { setTimeout(() => { this.audio.play().catch(()=>{}); }, 300); }
      }

      if (navigator.onLine) { await this.checkAndStartProgressMonitoring(); }
    } catch (_) {
      // silent: keep UI minimal; play button spinner handles the feedback
    } finally {
      this.showMiniPlayerLoading(false);
    }
  }

  loadExistingState() {
    const hasMetadata = this.loadTrackMetadata();
    if (!hasMetadata) {
      const savedTrackId = sessionStorage.getItem('currentTrackId');
      if (savedTrackId) {
        this.currentTrackId = savedTrackId;
        this.currentVoice =
          sessionStorage.getItem(`voice_pref_${savedTrackId}`) ||
          sessionStorage.getItem('currentVoice') ||
          this.getTrackDefaultVoice();
        this.trackType = sessionStorage.getItem('trackType') || 'audio';
        const savedState = JSON.parse(sessionStorage.getItem(`playerState_${savedTrackId}`) || '{}');
        if (savedState.title || savedState.album || savedState.coverPath) {
          this.trackMetadata = {
            id: savedTrackId,
            title: savedState.title || 'Unknown Track',
            album: savedState.album || 'Unknown Album',
            coverPath: savedState.coverPath || this.DEFAULT_COVER,
            voice: savedState.voice || this.currentVoice,
            trackType: savedState.trackType || this.trackType,
            albumId: savedState.albumId,
            defaultVoice: this.getTrackDefaultVoice(),
            content_version: savedState.content_version || 1
          };
        }
      }
    }
    if (this.currentTrackId && this.trackMetadata.id) {
      this.showMiniPlayerLoading(true);
      this.initializeTrackForPlayback();
      this.updateMiniPlayerUI();
      this.updatePlayerState();
    }
  }

  async playTrack(trackId, title, album, coverPath, shouldAutoPlay = null, voice = null, trackType = 'audio', albumId = null) {
    const perfStart = performance.now();
    console.log(`[PlayerPerf] playTrack(${trackId}) start (voice=${voice || 'default'}, type=${trackType})`);
    try {
      // ✅ OPTIMIZATION: Run all independent network requests in parallel
      const parallelStart = performance.now();
      const [metaData, progressData] = await Promise.all([
        // Fetch metadata (also gets content_version, eliminating need for refreshContentVersion later)
        (async () => {
          const metaUrl = (trackType === 'tts' && (voice || this.currentVoice))
            ? `/api/tracks/${encodeURIComponent(trackId)}/voice/${encodeURIComponent(voice || this.currentVoice)}/metadata`
            : `/api/tracks/${encodeURIComponent(trackId)}/metadata`;
          try {
            const res = await fetch(metaUrl);
            if (res.ok) return await res.json();
          } catch {}
          return null;
        })(),
        // Load progress
        (async () => {
          try {
            return await this.progress.loadProgress(trackId, voice || this.currentVoice);
          } catch {
            return null;
          }
        })()
      ]);
      console.log(`[PlayerPerf] Parallel requests completed in ${(performance.now() - parallelStart).toFixed(1)}ms`);

      const contentVersion = metaData?.track?.content_version || null;

      // ✅ Save the previous track ID BEFORE setTrackMetadata changes it
      const previousTrackId = this.currentTrackId;

      this.setTrackMetadata(trackId, title, album, coverPath, voice, trackType, albumId, contentVersion);

      if (navigator.onLine && !this.isPlayerPage) {
        console.log('🔍 [Access Check] Starting check for track:', trackId);
        const accessStart = performance.now();
        const accessCheck = await this.checkAccess(trackId, albumId, voice || this.currentVoice);
        console.log('🔍 [Access Check] Result:', accessCheck, `(took ${(performance.now() - accessStart).toFixed(1)}ms)`);

        if (!accessCheck.hasAccess) {
          console.error('❌ Access check failed, exiting playTrack');
          console.log('🔐 Access denied - attempting to show upgrade modal');
          console.log('🔐 Error message:', accessCheck.error);
          console.log('🔍 window.showUpgradeModal exists?', typeof window.showUpgradeModal);
          console.log('🔍 upgradeAccessModal element exists?', !!document.getElementById('upgradeAccessModal'));
          console.log('🔍 upgradeMessage element exists?', !!document.getElementById('upgradeMessage'));

          // Show upgrade modal if available
          if (typeof window.showUpgradeModal === 'function') {
            console.log('✅ Calling window.showUpgradeModal...');
            window.showUpgradeModal(accessCheck.error || 'This content requires a higher tier subscription');
            console.log('✅ window.showUpgradeModal called');
          } else {
            console.warn('⚠️ showUpgradeModal not available, cannot show upgrade prompt');
            console.log('🔍 Available window functions:', Object.keys(window).filter(k => k.includes('Modal') || k.includes('Upgrade')));
          }
          return;
        }
        console.log('✅ [Access Check] Access granted');
      }

      // ✅ OPTIMIZATION: Start progress monitoring asynchronously (don't wait)
      if (navigator.onLine) {
        this.checkAndStartProgressMonitoring().catch(() => {});
      }

      const savedState = JSON.parse(sessionStorage.getItem(`playerState_${trackId}`) || '{}');
      let shouldPlay = false;

      if (shouldAutoPlay !== null) {
        shouldPlay = shouldAutoPlay;
      } else if (savedState.hasOwnProperty('isPlaying')) {
        shouldPlay = savedState.isPlaying;
      } else {
        // ✅ When clicking a track explicitly (even if same track), always auto-play it
        shouldPlay = true;
      }

      if (this.hls) { this.hls.destroy(); this.hls = null; }
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();

      // ✅ OPTIMIZATION: Use metadata from parallel fetch to validate voice (avoid extra API call)
      let resolvedVoice = voice || this.currentVoice;
      if (trackType === 'tts' && metaData?.voice_info) {
        const availableVoices = metaData.voice_info.available_voices || [];
        const defaultVoice = metaData.voice_info.current_voice;
        if (resolvedVoice && !availableVoices.includes(resolvedVoice) && resolvedVoice !== defaultVoice) {
          resolvedVoice = defaultVoice || resolvedVoice;
          sessionStorage.removeItem(`voice_pref_${trackId}`);
        }
      }
      if (resolvedVoice !== this.currentVoice) {
        this.currentVoice = resolvedVoice;
        sessionStorage.setItem(`voice_pref_${trackId}`, resolvedVoice);
      }

      // ✅ REMOVED: redundant refreshContentVersion - we already got this from metadata fetch above

      // ✅ Use progress data from parallel fetch
      const pd2 = progressData;
      console.log(`[PlayerPerf] Using cached progress data`);
      // ✅ If track was completed, reset to beginning. Otherwise resume from saved position.
      const startPosition = (!pd2 || pd2.completed || pd2.position <= 0) ? 0 : pd2.position;

      this.updateMiniPlayerUI();
      this.audio.playbackRate = savedState.playbackRate || 1.0;
      const hlsUrl = this.generateHlsUrl(trackId, this.currentVoice);
      this.progress.pauseSync();

      if (Hls.isSupported()) {
        const hlsStart = performance.now();
        this.hls = new Hls(this.hlsConfig);
        this.wireHlsEvents(this.hls);
        await new Promise((resolve, reject) => {
          this.hls.once(Hls.Events.MANIFEST_PARSED, () => {
            console.log(`[PlayerPerf] HLS manifest parsed in ${(performance.now() - hlsStart).toFixed(1)}ms`);
            resolve();
          });
          this.hls.once(Hls.Events.ERROR, (_evt, data) => {
            if (data.fatal) {
              console.error('❌ HLS fatal error:', data);
              reject(new Error('HLS Fatal Error'));
            }
          });
          this.hls.loadSource(hlsUrl);
          this.hls.attachMedia(this.audio);
        });
        this.audio.addEventListener('loadedmetadata', () => {
          if (startPosition > 0) this.audio.currentTime = startPosition;
        }, { once: true });
        if (savedState.volume !== undefined) this.audio.volume = savedState.volume;
        if (savedState.muted) this.audio.muted = true;
        if (savedState.playbackRate) this.setPlaybackSpeed(savedState.playbackRate);
        setTimeout(() => this.progress.resumeSync(), 500);

        if (shouldPlay) {
          this.audio.play().catch(err => {
            console.error('❌ Auto-play failed:', err);
          });
        }
        this.saveState();
        this.updatePlayerState();
      }
    } catch (error) {
      console.error('❌ playTrack error:', error);
      this.progress.resumeSync();
      console.log(`[PlayerPerf] playTrack(${trackId}) failed after ${(performance.now() - perfStart).toFixed(1)}ms`);
    } finally {
      this.showMiniPlayerLoading(false);
      console.log(`[PlayerPerf] playTrack(${trackId}) total ${(performance.now() - perfStart).toFixed(1)}ms`);
    }
  }

  async togglePlay() {
    if (this.audio.paused) {
      if (!this.currentTrackId) return;
      if (this.hls || this.audio.src) {
        try {
          const oldV = this.trackMetadata.content_version;
          await this.refreshContentVersion(this.currentTrackId, this.trackType === 'tts' ? this.currentVoice : null);
          if (oldV !== this.trackMetadata.content_version) {
            if (this.hls) { this.hls.destroy(); this.hls = null; }
            this.audio.pause();
            this.audio.removeAttribute('src');
            this.audio.load();
            await this.initializeTrackForPlayback();
            return;
          }
        } catch {}
      }
      if (navigator.onLine && !this.isPlayerPage) {
        this.showMiniPlayerLoading(true);
        try {
          console.log('🔍 [Play Method] Checking access for track:', this.currentTrackId);
          const access = await this.checkAccess(this.currentTrackId, this.trackMetadata.albumId, this.currentVoice);
          console.log('🔍 [Play Method] Access result:', access);
          this.showMiniPlayerLoading(false);

          if (!access.hasAccess) {
            console.log('🔐 Play attempt blocked - attempting to show upgrade modal');
            console.log('🔐 Error message:', access.error);
            console.log('🔍 window.showUpgradeModal exists?', typeof window.showUpgradeModal);
            console.log('🔍 upgradeAccessModal element exists?', !!document.getElementById('upgradeAccessModal'));

            // Show upgrade modal if available
            if (typeof window.showUpgradeModal === 'function') {
              console.log('✅ Calling window.showUpgradeModal from play()...');
              window.showUpgradeModal(access.error || 'This content requires a higher tier subscription');
              console.log('✅ window.showUpgradeModal called from play()');
            } else {
              console.warn('⚠️ showUpgradeModal not available in play()');
            }
            return;
          }
          console.log('✅ [Play Method] Access granted');
        } catch (err) {
          console.error('❌ [Play Method] Error during access check:', err);
          this.showMiniPlayerLoading(false);
          return;
        }
      }
      if (!this.hls && !this.audio.src) {
        this.showMiniPlayerLoading(true);
        this.initializeTrackForPlayback().then(() =>
          this.audio.play().catch(()=>{})
        );
        return;
      }
      this.audio.play().catch(()=>{});
    } else {
      this.audio.pause();
    }
  }

  showMiniPlayerLoading(isLoading) {
    if (!this.elements.playPauseBtn) return;
    if (isLoading) {
      this.elements.playPauseBtn.disabled = true;
      if (this.elements.playIcon) this.elements.playIcon.className = 'fas fa-spinner fa-spin';
    } else {
      this.elements.playPauseBtn.disabled = false;
      if (this.elements.playIcon) this.elements.playIcon.className = `fas ${this.audio.paused ? 'fa-play' : 'fa-pause'}`;
    }
  }

  seek(seconds) {
    if (!this.currentTrackId || !this.audio.duration) return;
    const newTime = Math.max(0, Math.min(this.audio.currentTime + seconds, this.audio.duration));
    this.seekToTimeWithPrecision(newTime).then(success => {
      if (!success) {
        this.progress.isSeekingInProgress = true;
        this.audio.addEventListener('seeked', () => {
          this.progress.syncProgress(true);
          setTimeout(() => { this.progress.isSeekingInProgress = false; }, 500);
        }, { once: true });
        this.audio.currentTime = newTime;
      }
    });
  }

  toggleMute() { this.audio.muted = !this.audio.muted; }

  setPlaybackSpeed(speed) {
    speed = Math.max(0.25, Math.min(3, speed));
    this.audio.playbackRate = speed;
    const speedSelect = document.getElementById('speedSelect');
    if (speedSelect) {
      const opt = Array.from(speedSelect.options).find(o => parseFloat(o.value) === speed);
      if (opt) speedSelect.value = opt.value;
    }
    document.querySelectorAll('[data-speed]').forEach(btn =>
      btn.classList.toggle('active', parseFloat(btn.getAttribute('data-speed')) === speed)
    );
    document.dispatchEvent(new CustomEvent('playbackSpeedChanged', { detail: { speed } }));
    this.saveState();
  }

  updateProgress() {
    if (!this.audio.duration) return;
    const pct = (this.audio.currentTime / this.audio.duration) * 100;
    this.updateElement('progressBar', 'style.width', `${pct}%`);
    this.updateElement('currentTime', 'textContent', this.formatTime(this.audio.currentTime));
    this.updateElement('duration', 'textContent', this.formatTime(this.audio.duration));
  }

  formatTime(seconds) {
    if (isNaN(seconds)) return '0:00:00';
    const s = Math.floor(seconds);
    const hrs = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const secs = s % 60;
    return `${hrs}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }

  updateElement(key, propPath, value) {
    if (!this.elements[key]) return;
    const props = propPath.split('.');
    let obj = this.elements[key];
    for (let i = 0; i < props.length - 1; i++) {
      if (obj[props[i]] === undefined) return;
      obj = obj[props[i]];
    }
    obj[props[props.length - 1]] = value;
  }

  handlePlay() {
    if (this.elements.playIcon) this.elements.playIcon.classList.replace('fa-play', 'fa-pause');
    this.saveState();
  }
  handlePause() {
    if (this.elements.playIcon) this.elements.playIcon.classList.replace('fa-pause', 'fa-play');
    this.saveState();
  }
  handleEnded() {
    this.handlePause();
    this.audio.currentTime = 0;

    const autoPlayEnabled = localStorage.getItem('autoPlayEnabled') === 'true';

    if (autoPlayEnabled && window.location.pathname.startsWith('/player/')) {
      sessionStorage.setItem('autoPlayNext', 'true');

      const nextBtn = document.getElementById('nextTrackBtn');
      if (nextBtn) {
        nextBtn.click();
      } else {
        if (window.albumData?.ordered_track_ids && window.currentTrackId) {
          const trackIds = window.albumData.ordered_track_ids;
          const currentIndex = trackIds.indexOf(window.currentTrackId);
          if (currentIndex >= 0 && currentIndex < trackIds.length - 1) {
            const nextTrackId = trackIds[currentIndex + 1];
            const nextUrl = `/player/${nextTrackId}`;

            // ✅ FIX: Use full page reload for instant loading (faster than SPA)
            window.location.href = nextUrl;
          } else {
            // No more tracks, show toast
            this.showToast('Playlist ended', 'info', 3000);
          }
        } else {
          console.error('❌ Fallback failed: No album data or currentTrackId');
        }
      }
    }
  }

  handleError(_) {
    if (!navigator.onLine) return;
    if (this.recoveryAttempts < this.maxRecoveryAttempts) {
      this.recoveryAttempts++;
      setTimeout(() => { if (this.currentTrackId) this.reinitializeStream(); }, 1000 * this.recoveryAttempts);
    }
  }

  handleHlsError(_event, data) {
    if (!data.fatal) return;
    if (data.type === Hls.ErrorTypes.NETWORK_ERROR &&
        (data.details === Hls.ErrorDetails.FRAG_LOAD_ERROR ||
         data.details === Hls.ErrorDetails.FRAG_LOAD_TIMEOUT ||
         data.details === Hls.ErrorDetails.MANIFEST_LOAD_TIMEOUT)) {
      this.checkAndStartProgressMonitoring();
      return;
    }
    switch (data.type) {
      case Hls.ErrorTypes.MEDIA_ERROR:
        if (this.recoveryAttempts < this.maxRecoveryAttempts) { this.recoveryAttempts++; this.hls.recoverMediaError(); return; }
        break;
      case Hls.ErrorTypes.NETWORK_ERROR:
        if (this.recoveryAttempts < this.maxRecoveryAttempts) {
          this.recoveryAttempts++;
          setTimeout(() => { if (this.hls) this.hls.startLoad(); }, 1000 * Math.pow(2, this.recoveryAttempts - 1));
          return;
        }
        break;
      case Hls.ErrorTypes.KEY_SYSTEM_ERROR:
        this.showToast('Content not authorized', 'error');
        break;
    }
    this.reinitializeStream();
  }

  async handleNetworkChange() {
    const isOnline = navigator.onLine;
    if (isOnline) {
      await this.progress.processQueue();
      await this.refreshContentVersion(this.currentTrackId, this.trackType === 'tts' ? this.currentVoice : null);
      if (this.hls && this.currentTrackId && !this.audio.paused) this.reinitializeStream();
    } else {
      await this.progress.syncProgress(true);
    }
  }

  async reinitializeStream() {
    if (!navigator.onLine || !this.currentTrackId) return;
    await this.refreshContentVersion(this.currentTrackId, this.trackType === 'tts' ? this.currentVoice : null);
    const pos = this.audio.currentTime;
    const speed = this.audio.playbackRate;
    const play = !this.audio.paused;
    if (this.hls) { this.hls.destroy(); this.hls = null; }
    setTimeout(async () => {
      const hlsUrl = this.generateHlsUrl(this.currentTrackId, this.currentVoice);
      this.hls = new Hls(this.hlsConfig);
      this.wireHlsEvents(this.hls);
      this.hls.loadSource(hlsUrl);
      this.hls.attachMedia(this.audio);
      this.hls.once(Hls.Events.MANIFEST_PARSED, () => {
        this.audio.playbackRate = speed;
        if (pos > 0) this.audio.currentTime = pos;
        if (play) this.audio.play().catch(()=>{});
        this.recoveryAttempts = 0;
      });
    }, 1000 * Math.pow(2, this.recoveryAttempts));
  }

  updateMiniPlayerUI() {
    // ✅ If trackMetadata is missing but we have currentTrackId, try to reload from sessionStorage
    if (!this.trackMetadata.id && this.currentTrackId) {
      this.loadTrackMetadata();
    }

    if (!this.trackMetadata.id) {
      return;
    }

    this.updateElement('title', 'textContent', this.trackMetadata.title);
    this.updateElement('album', 'textContent', this.trackMetadata.album);
    this.updateElement('cover', 'src', this.trackMetadata.coverPath);

    // ✅ Ensure cover image and info are visible (defensive check for desktop mode)
    if (this.elements.cover) {
      this.elements.cover.style.display = '';  // Clear any inline display:none
      this.elements.cover.style.visibility = 'visible';
      // Verify the src was set
      if (this.elements.cover.src && !this.elements.cover.src.endsWith(this.trackMetadata.coverPath)) {
        // Force update if src doesn't match
        this.elements.cover.src = this.trackMetadata.coverPath;
      }
    }

    // ✅ Ensure title and album elements are visible
    if (this.elements.title) {
      this.elements.title.style.display = '';
      this.elements.title.style.visibility = 'visible';
    }
    if (this.elements.album) {
      this.elements.album.style.display = '';
      this.elements.album.style.visibility = 'visible';
    }

    if (this.elements.playIcon) this.elements.playIcon.className = `fas ${this.audio.paused ? 'fa-play' : 'fa-pause'}`;
  }

  showToast(message, type = 'info', duration = null) {
    // kept for critical notices only (e.g., DRM). Avoid spamming.
    const existing = document.querySelector('.player-toast:not(.simple-progress-toast)');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = `player-toast toast-${type}`;
    toast.textContent = message;
    const base = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);padding:12px 24px;border-radius:8px;z-index:10000;transition:all .3s ease;opacity:1;font-weight:500;box-shadow:0 4px 12px rgba(0,0,0,0.15);';
    const t = {
      info:'background-color:rgba(59,130,246,.9);color:#fff;',
      success:'background-color:rgba(34,197,94,.9);color:#fff;',
      warning:'background-color:rgba(245,158,11,.9);color:#fff;',
      error:'background-color:rgba(239,68,68,.9);color:#fff;'
    };
    toast.style.cssText = base + (t[type] || t.info);
    document.body.appendChild(toast);
    const ms = duration || (type === 'error' ? 4000 : 2500);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(-50%) translateY(20px)';
      setTimeout(() => toast.remove(), 300);
    }, ms);
  }

  /* ---------- Segment Progress Monitoring ---------- */
  async startSegmentProgressMonitoring(trackId, voiceId = null) {
    if (this.segmentProgress.isMonitoring) this.stopSegmentProgressMonitoring();
    this.segmentProgress.isMonitoring = true;
    this.segmentProgress.monitoringTrackId = trackId;
    this.segmentProgress.monitoringVoice = voiceId;
    this.segmentProgress.lastUpdateTime = Date.now();

    // Poll for progress updates every 2 seconds
    this.segmentProgress.pollInterval = setInterval(async () => {
      try {
        let url = `/api/segment-progress/${encodeURIComponent(trackId)}`;
        if (this.trackType === 'tts' && voiceId) url += `?voice=${encodeURIComponent(voiceId)}`;
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        this.handleSegmentProgress(data, trackId, voiceId);
      } catch {}
    }, 2000);

    // Check for stale progress every 5 seconds (remove toast if no update for 30 seconds)
    this.segmentProgress.staleCheckInterval = setInterval(() => {
      const timeSinceLastUpdate = Date.now() - (this.segmentProgress.lastUpdateTime || 0);
      if (timeSinceLastUpdate > 30000) { // 30 seconds without update
        this.stopSegmentProgressMonitoring();
      }
    }, 5000);
  }

  handleSegmentProgress(progressData, trackId, voiceId = null) {
    if (!progressData) return;
    if (this.segmentProgress.monitoringTrackId !== trackId || this.segmentProgress.monitoringVoice !== voiceId) return;

    // ✅ Update last update timestamp
    this.segmentProgress.lastUpdateTime = Date.now();

    const { status, percentage, current, total, formatted } = progressData;
    if (status === 'complete' || status === 'error' || status === 'not_found') {
      if (status === 'complete') {
        this.showSimpleProgressToast(100, 'complete', null, null, voiceId, true);
        this.refreshContentVersion(trackId, voiceId || this.currentVoice).then(() => this.reinitializeStream()).catch(()=>{});
      }
      this.stopSegmentProgressMonitoring();
      return;
    }
    if (percentage !== undefined && percentage > 0) {
      this.showSimpleProgressToast(
        percentage,
        status,
        formatted?.current ?? current,
        formatted?.total ?? total,
        voiceId
      );
    }
  }

  stopSegmentProgressMonitoring() {
    this.segmentProgress.isMonitoring = false;
    this.segmentProgress.monitoringTrackId = null;
    this.segmentProgress.monitoringVoice = null;
    this.segmentProgress.lastUpdateTime = null;

    if (this.segmentProgress.pollInterval) {
      clearInterval(this.segmentProgress.pollInterval);
      this.segmentProgress.pollInterval = null;
    }

    if (this.segmentProgress.staleCheckInterval) {
      clearInterval(this.segmentProgress.staleCheckInterval);
      this.segmentProgress.staleCheckInterval = null;
    }

    this.hideSimpleProgressToast();
  }

  showSimpleProgressToast(percent, status = 'creating_segments', currentTime = null, totalTime = null, voiceId = null, quickDismiss = false) {
    this.hideSimpleProgressToast();
    const toast = document.createElement('div');
    toast.className = 'simple-progress-toast';
    const roundedPercent = Math.round(percent * 10) / 10;
    const displayPercent = roundedPercent % 1 === 0 ? Math.round(roundedPercent) : roundedPercent;
    let message;
    if (this.trackType === 'tts' && voiceId) {
      const voiceName = this.getVoiceDisplayName(voiceId);
      message = status === 'complete'
        ? `${voiceName} ready!`
        : currentTime && totalTime
          ? `Preparing ${voiceName}... ${displayPercent}% (${currentTime}/${totalTime})`
          : `Preparing ${voiceName}... ${displayPercent}%`;
    } else {
      message = status === 'complete'
        ? 'Track ready!'
        : currentTime && totalTime
          ? `Preparing track... ${displayPercent}% (${currentTime}/${totalTime})`
          : `Preparing track... ${displayPercent}%`;
    }
    toast.textContent = message;
    // ✅ Reduced z-index to 9999 so regular toasts (z-index: 10000) appear on top
    let baseStyle = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);color:#fff;padding:12px 20px;border-radius:25px;font-size:14px;font-weight:500;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,.3);transition:all .3s ease;max-width:320px;text-align:center;backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,.1)';
    if (status === 'complete') baseStyle += ';background:rgba(34,197,94,.95)';
    else if (this.trackType === 'tts' && voiceId) baseStyle += ';background:rgba(59,130,246,.9)';
    else baseStyle += ';background:rgba(59,130,246,.9)';
    toast.style.cssText = baseStyle;
    this.segmentProgress.currentToast = toast;
    document.body.appendChild(toast);
    if (status === 'complete' || quickDismiss) setTimeout(() => this.hideSimpleProgressToast(), 1200);
  }

  hideSimpleProgressToast() {
    if (this.segmentProgress.currentToast) { this.segmentProgress.currentToast.remove(); this.segmentProgress.currentToast = null; }
  }

  async checkAndStartProgressMonitoring() {
    if (!this.currentTrackId) return;
    try {
      let url = `/api/segment-progress/${encodeURIComponent(this.currentTrackId)}`;
      if (this.trackType === 'tts' && this.currentVoice) url += `?voice=${encodeURIComponent(this.currentVoice)}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        if (data.status === 'creating_segments' || data.status === 'processing') {
          if (!this.segmentProgress.isMonitoring || this.segmentProgress.monitoringTrackId !== this.currentTrackId || this.segmentProgress.monitoringVoice !== this.currentVoice) {
            this.startSegmentProgressMonitoring(this.currentTrackId, this.currentVoice);
          }
          if (data.percentage > 0) {
            this.showSimpleProgressToast(
              data.percentage,
              data.status,
              data.formatted?.current ?? data.current,
              data.formatted?.total ?? data.total,
              this.currentVoice
            );
          }
        }
      }
    } catch {}
  }

  async closeMiniPlayer() {
    if (this.currentTrackId) { await this.progress.syncProgress(true); this.saveState(); }
    if (!this.audio.paused) this.audio.pause();
    if (this.hls) { this.hls.destroy(); this.hls = null; }
    this.stopSegmentProgressMonitoring();
    if (this.miniPlayer) {
      this.miniPlayer.classList.remove('active');
      setTimeout(() => { this.miniPlayer.style.display = 'none'; }, 200);
    }
    ['currentTrackId','trackMetadata','currentVoice','trackType'].forEach(k => sessionStorage.removeItem(k));
    if (this.currentTrackId) sessionStorage.removeItem(`playerState_${this.currentTrackId}`);
    this.currentTrackId = null; this.currentVoice = null; this.trackType = 'audio';
    this.trackMetadata = { id: null, title: null, album: null, coverPath: null, voice: null, trackType: 'audio', albumId: null, defaultVoice: null, content_version: 1 };
  }

  async expandPlayer() {
    if (!this.currentTrackId || !navigator.onLine) return;
    try {
      await this.progress.syncProgress(true);
      const state = {
        trackId: this.currentTrackId,
        isPlaying: !this.audio.paused,
        position: this.audio.currentTime,
        playbackRate: this.audio.playbackRate,
        voice: this.currentVoice,
        trackType: this.trackType
      };
      sessionStorage.setItem('expandedPlayerState', JSON.stringify(state));

      // ✅ SPA NAVIGATION: Use SPA router instead of full page reload
      const playerUrl = this.trackType === 'tts' && this.currentVoice
        ? `/player/${encodeURIComponent(this.currentTrackId)}?voice=${encodeURIComponent(this.currentVoice)}`
        : `/player/${encodeURIComponent(this.currentTrackId)}`;

      if (window.navigateTo) {
        window.navigateTo(playerUrl);
      } else {
        // Fallback to traditional navigation if SPA router not available
        window.location.href = playerUrl;
      }
    } catch {}
  }

  updatePlayerState() {
    this.isPlayerPage = window.location.pathname.startsWith('/player/');
    if (!this.isPlayerPage && this.currentTrackId && this.trackMetadata.id) this.showMiniPlayer();
    else if (this.miniPlayer) this.hideMiniPlayer();
  }

  showMiniPlayer() {
    // Re-query DOM if element is missing (SPA navigation support)
    if (!this.miniPlayer) {
      this.miniPlayer = document.getElementById('miniPlayer');
      if (!this.miniPlayer) return; // Still doesn't exist, bail out
    }
    // Don't show miniplayer on player page
    if (this.isPlayerPage) return;
    this.updateMiniPlayerUI();
    this.miniPlayer.style.display = 'flex';
    requestAnimationFrame(() => this.miniPlayer.classList.add('active'));
  }

  hideMiniPlayer() {
    if (!this.miniPlayer) return;
    this.miniPlayer.classList.remove('active');
    // Always hide display, regardless of page type
    setTimeout(() => {
      this.miniPlayer.style.display = 'none';
    }, 200);
  }

  destroy() {
    this.stopSegmentProgressMonitoring();
    if (this.idleSaveHandle) { const cancelIdle = window.cancelIdleCallback || clearTimeout; cancelIdle(this.idleSaveHandle); }
    this.progress.destroy();
    if (this.hls) { this.hls.destroy(); this.hls = null; }
    this.audio.pause();
    this.audio.removeAttribute('src');
    this.networkMonitor = null;
    this.downloadManager = null;
    this.currentVoice = null;
    this.trackType = 'audio';
  }
}

/* ----------------------- Bootstrap ----------------------- */
document.addEventListener('DOMContentLoaded', () => {
  if (!window.persistentPlayer) window.persistentPlayer = new PersistentPlayer();
  window.persistentToast = (msg, type = 'info', duration = null, opts = {}) =>
    window.persistentPlayer?.showToast(msg, type, duration, opts);
  window.showToast = (msg, type = 'info', duration = null) =>
    window.persistentToast?.(msg, type, duration) || void 0;
  window.dispatchEvent(new Event('persistentPlayerReady'));
});
