// player-spa.js - SPA wrapper for Player Page (following team pattern)
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { PlayerController } = await import(`./player-shared-spa.js?v=${v}`);

export class PlayerSPA {
    constructor(trackId) {
        this.trackId = trackId;
        this.controller = new PlayerController('spa', trackId); // ✅ Create in 'spa' mode
    }

    getRequiredStyles() {
        // ✅ Single source of truth: All player CSS in one file
        return ['/static/css/player.css'];
    }

    getPageTitle() {
        // ✅ Delegate to controller for page title (with truncation)
        if (this.controller && typeof this.controller.getPageTitle === 'function') {
            return this.controller.getPageTitle();
        }
        return 'Player';
    }

    async render() {
        // console.log(`🎵 PlayerSPA: Rendering player for track ${this.trackId}...`);
        // ✅ Controller generates HTML (like team pattern)
        return await this.controller.render();
    }

    async mount() {
        // console.log('🎵 PlayerSPA: Mounting player controller...');

        // ✅ Load required scripts for player functionality
        await this.loadRequiredScripts();

        await this.controller.mount();
        window.playerController = this.controller;

        // ✅ Re-initialize voice extension to attach to new DOM
        if (window.voiceExtension) {
            // console.log('🎵 PlayerSPA: Re-initializing voice extension...');
            window.voiceExtension.init();

            // ✅ Manually trigger onTrackChanged since playTrack was already called
            const trackData = this.controller.trackData;
            if (trackData) {
                const trackId = trackData.id;
                const voice = trackData.current_voice || trackData.default_voice;
                const trackType = trackData.track_type || 'audio';

                // console.log('🎵 PlayerSPA: Triggering voice extension track update', {
                //     trackId,
                //     voice,
                //     trackType
                // });

                window.voiceExtension.onTrackChanged(trackId, voice, trackType);
            }

            // console.log('✅ PlayerSPA: Voice extension re-initialized');
        }

        // console.log('✅ PlayerSPA: Controller mounted');
    }

    /**
     * Load required JavaScript files for player functionality
     */
    async loadRequiredScripts() {
        // ✅ Generate cache bust parameter (use server version if available, otherwise timestamp)
        const cacheBust = window.APP_VERSION || Date.now();

        const scripts = [
            `/static/js/comment-system.js?v=${cacheBust}`,
            `/static/js/ReadAlongUI.js?v=${cacheBust}`,
            `/static/js/ReadAlongContent.js?v=${cacheBust}`,
            `/static/js/ReadAlongCore.js?v=${cacheBust}`
        ];

        const loadScript = (src) => {
            return new Promise((resolve, reject) => {
                // ✅ Extract base path without query params for duplicate checking
                const basePath = src.split('?')[0];

                // ✅ Check if ANY version of this script is already loaded
                const existing = Array.from(document.querySelectorAll('script[src]')).find(script => {
                    const scriptSrc = script.getAttribute('src');
                    if (!scriptSrc) return false;

                    // Compare base paths (without query params)
                    const scriptBasePath = scriptSrc.split('?')[0];
                    return scriptBasePath === basePath || scriptBasePath.endsWith(basePath) || basePath.endsWith(scriptBasePath);
                });

                if (existing) {
                    // console.log(`✅ Script already loaded: ${basePath}`);
                    resolve();
                    return;
                }

                const script = document.createElement('script');
                script.src = src;
                script.onload = () => {
                    // console.log(`✅ Loaded script: ${src}`);
                    resolve();
                };
                script.onerror = () => {
                    // console.error(`❌ Failed to load script: ${src}`);
                    reject(new Error(`Failed to load ${src}`));
                };
                document.head.appendChild(script);
            });
        };

        // console.log('🎵 PlayerSPA: Loading required scripts...');

        // Load scripts sequentially to maintain dependency order
        for (const src of scripts) {
            await loadScript(src);
        }

        // console.log('✅ PlayerSPA: All required scripts loaded');
    }

    async destroy() {
        // console.log('🎵 PlayerSPA: Destroying player controller...');
        if (this.controller) {
            await this.controller.destroy();
        }
        delete window.playerController;
        this.controller = null;
        // console.log('✅ PlayerSPA: Controller destroyed');
    }
}
