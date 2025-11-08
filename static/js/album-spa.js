/**
 * Album SPA Module
 * Handles album detail pages in SPA mode
 */
export class AlbumSPA {
    constructor(albumId) {
        this.albumId = albumId;
        this.albumDetails = null;
    }

    getRequiredStyles() {
        return ['/static/css/album-detail.css'];
    }

    getPageTitle() {
        let title = null;

        // Try to get album title from various sources
        // 1. From the cached title during render
        if (this.albumTitle) {
            title = this.albumTitle;
        } else if (this.albumDetails?.albumData?.title) {
            // 2. From the album data stored during initialization
            title = this.albumDetails.albumData.title;
        } else if (window.albumTitle) {
            // 3. From window variable
            title = window.albumTitle;
        } else {
            // 4. From the DOM - album title (h2.album-title)
            const albumTitleEl = document.querySelector('.album-title');
            if (albumTitleEl) {
                title = albumTitleEl.textContent.trim();
            }
        }

        // If we found a title, truncate if needed
        if (title) {
            if (title.length > 20) {
                return title.substring(0, 20) + '...';
            }
            return title;
        }

        // 5. Fallback
        return 'Album';
    }

    async render() {
        // // console.log(`AlbumSPA: Rendering album ${this.albumId}...`);
        
        try {
            // Fetch the album page HTML
            const response = await fetch(`/album/${encodeURIComponent(this.albumId)}`);
            
            if (!response.ok) {
                if (response.status === 403) {
                    const data = await response.json().catch(() => ({}));
                    if (typeof showUpgradeModal === 'function') {
                        showUpgradeModal(data.error?.message || 'Access denied');
                    }
                    throw new Error('Access denied');
                }
                throw new Error(`HTTP ${response.status}: Failed to load album`);
            }
            
            const html = await response.text();
            // console.log('AlbumSPA: Received HTML, length:', html.length);
            
            // Parse the HTML
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            
            // Try multiple strategies to find the content
            let contentElement = null;
            
            // Strategy 1: Look for main tag
            contentElement = doc.querySelector('main');
            
            // Strategy 2: Look for album-detail div (specific to album page)
            if (!contentElement) {
                contentElement = doc.querySelector('.album-detail')?.parentElement;
            }
            
            // Strategy 3: Look for container main
            if (!contentElement) {
                contentElement = doc.querySelector('.container main');
            }
            
            // Strategy 4: Look for any element with album content
            if (!contentElement) {
                const albumDetail = doc.querySelector('.album-detail');
                if (albumDetail) {
                    // Create a wrapper
                    contentElement = document.createElement('div');
                    contentElement.appendChild(albumDetail.cloneNode(true));
                }
            }
            
            if (!contentElement || !contentElement.innerHTML.trim()) {
                // console.error('AlbumSPA: Could not find content. Document structure:', doc.body.innerHTML.substring(0, 500));
                throw new Error('Could not find album content in page');
            }
            
            // console.log('AlbumSPA: Found content element');

            // Extract and cache album title for getPageTitle()
            const albumTitleEl = doc.querySelector('.album-title');
            if (albumTitleEl) {
                this.albumTitle = albumTitleEl.textContent.trim();
                // console.log('AlbumSPA: Extracted album title:', this.albumTitle);
            }

            // Extract inline scripts that contain album data
            this.inlineScripts = [];
            const scripts = doc.querySelectorAll('script:not([src])');

            scripts.forEach(script => {
                const content = script.textContent.trim();
                if (content && (
                    content.includes('window.albumId') ||
                    content.includes('window.albumTracks') ||
                    content.includes('window.userPermissions')
                )) {
                    this.inlineScripts.push(content);
                }
            });

            // // console.log(`AlbumSPA: Found ${this.inlineScripts.length} inline scripts`);
            
            // Extract styles from the page
            const styles = Array.from(doc.querySelectorAll('style'))
                .map(style => style.textContent)
                .join('\n');
            
            // Build output HTML
            let output = '';
            
            if (styles) {
                output += `<style>${styles}</style>\n`;
            }
            
            output += contentElement.innerHTML;
            
            return output;
            
        } catch (error) {
            // console.error('AlbumSPA: Error fetching album:', error);
            return this.generateErrorHTML(error.message);
        }
    }
    
    async mount() {
        // console.log('AlbumSPA: Mounting...');
        
        try {
            // Step 1: Execute inline scripts (sets window.albumId, etc.)
            if (this.inlineScripts && this.inlineScripts.length > 0) {
                this.executeInlineScripts();
            } else {
                // console.warn('AlbumSPA: No inline scripts found - album data may not be available');
            }
            
            // Step 2: Wait a moment for DOM
            await new Promise(resolve => setTimeout(resolve, 100));
            
            // Step 3: Verify we have the required data
            if (!window.albumId) {
                // console.error('AlbumSPA: window.albumId not set after executing scripts');
                throw new Error('Album data not available');
            }
            
            // // console.log(`AlbumSPA: Album ID set to ${window.albumId}`);
            
            // Step 4: Load required scripts
            await this.loadRequiredScripts();
            
            // Step 5: Wait for classes to be available
            await this.waitForClasses();
            
            // Step 6: Initialize album
            await this.initializeAlbum();
            
            // console.log('AlbumSPA: Mounted successfully');
            
        } catch (error) {
            // console.error('AlbumSPA: Mount error:', error);
            if (typeof showToast === 'function') {
                showToast('Failed to initialize album page: ' + error.message, 'error');
            }
        }
    }
    
    executeInlineScripts() {
        // // console.log(`AlbumSPA: Executing ${this.inlineScripts.length} inline scripts`);
        
        this.inlineScripts.forEach((scriptContent, index) => {
            try {
                // Execute in global scope
                const scriptFunc = new Function(scriptContent);
                scriptFunc();
                // // console.log(`AlbumSPA: Executed inline script ${index + 1}`);
            } catch (error) {
                console.error(`❌ AlbumSPA: Error in inline script ${index + 1}:`, error);
            }
        });
        
        // Verify key variables were set
        // console.log('AlbumSPA: After script execution:', {
        //     albumId: window.albumId,
        //     albumTracks: window.albumTracks ? `${window.albumTracks.length} tracks` : 'undefined',
        //     userPermissions: window.userPermissions ? 'set' : 'undefined'
        // });
    }
    
    async loadRequiredScripts() {
        // console.log('AlbumSPA: Loading required scripts...');
        
        // ✅ First, check if all classes already exist
        const requiredClasses = ['AlbumDetails', 'AudioUpload', 'TTSManager', 'DocumentExtractionManager'];
        const allClassesExist = requiredClasses.every(className => typeof window[className] !== 'undefined');
        
        if (allClassesExist) {
            // console.log('AlbumSPA: All classes already available, skipping script load');
            return;
        }
        
        // ✅ Generate cache bust parameter (use server version if available, otherwise timestamp)
        const cacheBust = window.APP_VERSION || Date.now();
        
        const scripts = [
            `/static/js/AlbumDetails.js?v=${cacheBust}`,
            `/static/js/AudioUpload.js?v=${cacheBust}`,
            `/static/js/TTSManager.js?v=${cacheBust}`,
            `/static/js/DocumentExtractionManager.js?v=${cacheBust}`
        ];
        
        for (const src of scripts) {
            // ✅ Extract base path without query params
            const basePath = src.split('?')[0];
            
            // ✅ Check if ANY version of this script is already loaded (handles cache bust params)
            const existing = Array.from(document.querySelectorAll('script[src]')).find(script => {
                const scriptSrc = script.getAttribute('src');
                if (!scriptSrc) return false;
                
                // Compare base paths (without query params)
                const scriptBasePath = scriptSrc.split('?')[0];
                return scriptBasePath.endsWith(basePath) || basePath.endsWith(scriptBasePath);
            });
            
            if (existing) {
                // // console.log(`AlbumSPA: Script already loaded: ${basePath}`);
                continue;
            }
            
            // ✅ Load the script
            try {
                await new Promise((resolve, reject) => {
                    const script = document.createElement('script');
                    script.src = src;
                    script.onload = () => {
                        // // console.log(`AlbumSPA: Loaded ${src}`);
                        resolve();
                    };
                    script.onerror = () => {
                        console.error(`❌ AlbumSPA: Failed to load ${src}`);
                        reject(new Error(`Failed to load ${src}`));
                    };
                    document.head.appendChild(script);
                });
                
                // ✅ Wait a bit for the script to execute and define its class
                await new Promise(resolve => setTimeout(resolve, 50));
                
            } catch (error) {
                console.error(`❌ AlbumSPA: Error loading ${basePath}:`, error);
                // Continue loading other scripts even if one fails
            }
        }
        
        // console.log('AlbumSPA: Script loading complete');
    }
    
    async waitForClasses() {
        const requiredClasses = ['AlbumDetails', 'AudioUpload', 'TTSManager', 'DocumentExtractionManager'];
        let attempts = 0;
        
        // console.log('AlbumSPA: Waiting for classes...');
        
        while (attempts < 50) {
            const availability = {};
            requiredClasses.forEach(cls => {
                availability[cls] = typeof window[cls] !== 'undefined';
            });
            
            const allAvailable = Object.values(availability).every(v => v);
            
            if (allAvailable) {
                // console.log('AlbumSPA: All classes available');
                return;
            }
            
            if (attempts % 10 === 0) {
                // // console.log(`AlbumSPA: Waiting for classes (attempt ${attempts})`, availability);
            }
            
            await new Promise(resolve => setTimeout(resolve, 100));
            attempts++;
        }
        
        // Log which classes are missing
        const missing = requiredClasses.filter(cls => typeof window[cls] === 'undefined');
        // console.error('AlbumSPA: Timeout waiting for classes. Missing:', missing);
        throw new Error(`Required classes not available: ${missing.join(', ')}`);
    }
    
    async initializeAlbum() {
        // console.log('AlbumSPA: Initializing album...');
        
        // Clean up existing instance
        if (window.albumDetail?.cleanup) {
            // console.log('AlbumSPA: Cleaning up previous instance');
            window.albumDetail.cleanup();
        }
        
        // Clear any existing instances
        window.albumDetail = null;
        window.audioUpload = null;
        window.ttsManager = null;
        window.documentManager = null;
        
        // Verify data is set
        if (!window.albumId) {
            throw new Error('window.albumId not set');
        }
        
        if (typeof AlbumDetails === 'undefined') {
            throw new Error('AlbumDetails class not available');
        }
        
        // console.log('AlbumSPA: Creating AlbumDetails instance for album:', window.albumId);
        
        // Create instances (AlbumDetails initializes itself)
        const albumDetails = new AlbumDetails();
        
        // Wait for initialization to complete
        await new Promise(resolve => {
            const checkInit = () => {
                if (albumDetails.isInitialized) {
                    resolve();
                } else {
                    setTimeout(checkInit, 100);
                }
            };
            checkInit();
        });
        
        // Create related managers
        const audioUpload = typeof AudioUpload !== 'undefined' ? new AudioUpload(albumDetails) : null;
        const ttsManager = typeof TTSManager !== 'undefined' ? new TTSManager(albumDetails) : null;
        const documentManager = (typeof DocumentExtractionManager !== 'undefined' && ttsManager) ? 
            new DocumentExtractionManager(ttsManager) : null;
        
        // Link them
        if (audioUpload) albumDetails.audioUpload = audioUpload;
        if (ttsManager) albumDetails.ttsManager = ttsManager;
        if (documentManager) albumDetails.documentManager = documentManager;
        
        // Set globals
        window.albumDetail = albumDetails;
        window.audioUpload = audioUpload;
        window.ttsManager = ttsManager;
        window.documentManager = documentManager;
        
        // Set up global functions
        window.openAddTrackModal = () => {
            try {
                return albumDetails.isInitialized && audioUpload ? audioUpload.openAddTrackModal() : null;
            } catch (error) {
                if (typeof showToast === 'function') showToast('Error opening modal', 'error');
            }
        };
        
        window.closeAddTrackModal = () => {
            try {
                return albumDetails.isInitialized && audioUpload ? audioUpload.closeAddTrackModal() : null;
            } catch (error) {
                if (typeof showToast === 'function') showToast('Error closing modal', 'error');
            }
        };
        
        window.openTTSModal = () => {
            try {
                return albumDetails.isInitialized && ttsManager ? ttsManager.openTTSModal() : null;
            } catch (error) {
                if (typeof showToast === 'function') showToast('Error opening TTS modal', 'error');
            }
        };
        
        window.closeTTSModal = () => {
            try {
                return albumDetails.isInitialized && ttsManager ? ttsManager.closeTTSModal() : null;
            } catch (error) {
                if (typeof showToast === 'function') showToast('Error closing TTS modal', 'error');
            }
        };
        
        this.albumDetails = albumDetails;
        // console.log('AlbumSPA: Album initialized successfully');
    }    
    async destroy() {
        // console.log('AlbumSPA: Destroying...');
        
        if (this.albumDetails?.cleanup) {
            this.albumDetails.cleanup();
        }
        
        // Clear globals
        window.albumDetail = null;
        window.audioUpload = null;
        window.ttsManager = null;
        window.documentManager = null;
        window.openAddTrackModal = null;
        window.closeAddTrackModal = null;
        window.openTTSModal = null;
        window.closeTTSModal = null;
        window.albumId = null;
        window.albumTracks = null;
        window.userPermissions = null;
        
        // console.log('AlbumSPA: Destroyed');
    }
    
    generateErrorHTML(errorMessage) {
        return `
            <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 400px; color: #ef4444;">
                <i class="fas fa-exclamation-circle" style="font-size: 3rem; margin-bottom: 20px;"></i>
                <p>Error loading album: ${this.escapeHtml(errorMessage)}</p>
                <button onclick="history.back()" class="btn btn-primary" style="margin-top: 20px;">Go Back</button>
            </div>
        `;
    }
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}