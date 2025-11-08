// script.js

// Global State Variables
let currentPosition = 0;
let scrollSpeed = 0.5;
let isAutoScrolling = true;
let userPermissions = null;
let userInfo = null;
let animationFrameId = null;
let isCarouselDuplicated = false;
let isAppInitialized = false;
let autoScrollInterval = null;
let autoScrollResumeTimeout = null;
let isUserInteracting = false;
const RESUME_DELAY = 3000;

// Initialize the application once the DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, starting initialization...');
    initializeApp();
    highlightActiveNavLinks();
    setupResumeButtons();
    loadAllCarousels();
});

// Cleanup on page unload
window.addEventListener('beforeunload', cleanupCarousel);

// Main Initialization Function
async function initializeApp() {
    if (isAppInitialized) {
        console.warn('initializeApp() has already been called.');
        return;
    }
    isAppInitialized = true;

    try {
        await initializeUserState();
        await loadUserTierInfo();
        await initializeCarousel();
        attachGlobalEventListeners();
    } catch (error) {
        console.error('Error during initialization:', error);
    }
}

// Initialize User State
async function initializeUserState() {
    try {
        userPermissions = await fetchPermissions();
        updateUIForPermissions();
    } catch (error) {
        console.error('Error initializing user state:', error);
        handleAuthenticationError(error);
    }
}

// Fetch Permissions from API
async function fetchPermissions() {
    try {
        const response = await fetch('/api/permissions');
        if (response.status === 303) {
            window.location.href = '/login';
            return null;
        }
        if (!response.ok) throw new Error('Failed to fetch permissions');
        const permissions = await response.json();
        console.log('Loaded permissions:', permissions);
        return permissions;
    } catch (error) {
        console.error('Error fetching permissions:', error);
        return null;
    }
}

// Update UI Based on Permissions
function updateUIForPermissions() {
    if (userPermissions) {
        const createBtn = document.querySelector('.btn-create-album');
        if (createBtn) {
            createBtn.style.display = userPermissions.can_create ? 'block' : 'none';
        }

        const creatorNav = document.querySelector('[href="/creator/team"]');
        if (creatorNav) {
            creatorNav.style.display = userPermissions.can_delete ? 'block' : 'none';
        }

        const deleteButtons = document.querySelectorAll('.btn-danger, .delete-track');
        deleteButtons.forEach(btn => {
            btn.style.display = userPermissions.can_delete ? 'block' : 'none';
        });

        const bulkDeleteOptions = document.querySelectorAll('.bulk-delete-option');
        bulkDeleteOptions.forEach(option => {
            option.style.display = userPermissions.can_delete ? 'block' : 'none';
        });
    }
}

// Load User Tier Information
async function loadUserTierInfo() {
    try {
        const response = await fetch('/api/user/tier');
        if (!response.ok) throw new Error('Failed to fetch tier info');

        const data = await response.json();
        const userSection = document.querySelector('.user-status');
        if (userSection && data.tier) {
            userSection.innerHTML = `
                <div class="user-info">
                    <h3>${data.tier.name}</h3>
                    ${data.is_patreon ? `
                        <div class="tier-details">
                            <p class="tier-price">${data.tier.amount}</p>
                            ${data.tier.description ? `
                                <p class="tier-description">${data.tier.description}</p>
                            ` : ''}
                        </div>
                    ` : ''}
                </div>
            `;
        }
    } catch (error) {
        console.error('Error loading tier info:', error);
    }
}

// Initialize Carousel Functionality
async function initializeCarousel() {
    console.log('Initializing carousel...');
    const carouselContainer = document.querySelector('.carousel-container');
    if (!carouselContainer) {
        console.error('Carousel container not found');
        return;
    }

    setupCarouselListeners(carouselContainer);
    await loadPopularTracks();
    startAutoScroll();
}

// Setup Carousel Event Listeners
function setupCarouselListeners(container) {
    container.addEventListener('mouseenter', () => {
        stopAutoScroll();
        isUserInteracting = true;
    });
    
    container.addEventListener('mouseleave', () => {
        isUserInteracting = false;
        if (!autoScrollResumeTimeout) {
            startAutoScroll();
        }
    });

    container.addEventListener('scroll', () => {
        if (!isUserInteracting) {
            pauseAutoScrollWithResume();
        }
    }, { passive: true });

    let touchStartX = 0;
    container.addEventListener('touchstart', (e) => {
        touchStartX = e.changedTouches[0].screenX;
        stopAutoScroll();
        isUserInteracting = true;
    }, { passive: true });

    container.addEventListener('touchmove', () => {
        stopAutoScroll();
        isUserInteracting = true;
        if (autoScrollResumeTimeout) {
            clearTimeout(autoScrollResumeTimeout);
            autoScrollResumeTimeout = null;
        }
    }, { passive: true });

    container.addEventListener('touchend', (e) => {
        const touchEndX = e.changedTouches[0].screenX;
        const difference = touchStartX - touchEndX;
        
        if (Math.abs(difference) > 50) {
            moveCarousel(difference > 0 ? 1 : -1);
        }
        
        pauseAutoScrollWithResume();
    }, { passive: true });
}

// Pause auto-scroll with automatic resume
function pauseAutoScrollWithResume() {
    stopAutoScroll();
    isUserInteracting = true;
    
    if (autoScrollResumeTimeout) {
        clearTimeout(autoScrollResumeTimeout);
    }
    
    autoScrollResumeTimeout = setTimeout(() => {
        isUserInteracting = false;
        startAutoScroll();
    }, RESUME_DELAY);
}

// Load Popular Tracks from API
async function loadPopularTracks() {
    try {
        const response = await fetch('/api/popular-tracks');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const tracks = await response.json();
        const carousel = document.getElementById('popularTracksCarousel');
        if (!carousel) return;

        carousel.innerHTML = '';

        if (tracks.length === 0) {
            carousel.innerHTML = '<div class="loading">No popular tracks yet</div>';
            return;
        }

        const tracksHtml = tracks.map(track => createPopularTrackTemplate(track)).join('');
        carousel.innerHTML = tracksHtml;

        if (tracks.length > 3) {
            duplicateCarouselItems();
        }
    } catch (error) {
        console.error('Error in loadPopularTracks:', error);
        const carousel = document.getElementById('popularTracksCarousel');
        if (carousel) {
            carousel.innerHTML = '<div class="loading">Error loading popular tracks</div>';
        }
    }
}

// Duplicate Carousel Items for Continuous Scrolling
function duplicateCarouselItems() {
    if (isCarouselDuplicated) return;
    
    const carousel = document.getElementById('popularTracksCarousel');
    const wrapper = carousel.querySelector('.track-wrapper');
    if (!wrapper || wrapper.children.length < 4) return;

    const clone = wrapper.cloneNode(true);
    carousel.appendChild(clone);
    isCarouselDuplicated = true;
}

// Carousel Movement Function
function moveCarousel(direction) {
    const carousel = document.getElementById('popularTracksCarousel');
    if (!carousel) return;

    const cards = carousel.querySelectorAll('.album-preview-card');
    if (!cards.length) return;

    const cardWidth = cards[0].offsetWidth + 16;
    const moveDistance = direction * cardWidth;
    const totalWidth = cardWidth * cards.length;
    
    currentPosition -= moveDistance;

    if (Math.abs(currentPosition) >= totalWidth) {
        currentPosition = 0;
    }
    if (currentPosition > 0) {
        currentPosition = -totalWidth + cardWidth;
    }

    carousel.style.transition = 'transform 0.3s ease';
    carousel.style.transform = `translateX(${currentPosition}px)`;

    carousel.addEventListener('transitionend', function handler() {
        carousel.style.transition = 'none';
        carousel.removeEventListener('transitionend', handler);
    });
}

// Scroll carousel by amount
function scrollCarousel(carouselId, scrollAmount) {
    stopAutoScroll();
    isUserInteracting = true;
    
    const carousel = document.getElementById(carouselId);
    if (!carousel) {
        console.error(`Carousel not found: ${carouselId}`);
        return;
    }
    
    const container = carousel.closest('.carousel-container');
    if (container) {
        container.scrollBy({
            left: scrollAmount,
            behavior: 'smooth'
        });
    } else {
        carousel.scrollBy({
            left: scrollAmount,
            behavior: 'smooth'
        });
    }
    
    pauseAutoScrollWithResume();
}

// Cleanup carousel on page unload
function cleanupCarousel() {
    stopAutoScroll();
    if (autoScrollResumeTimeout) {
        clearTimeout(autoScrollResumeTimeout);
        autoScrollResumeTimeout = null;
    }
}

// Start Auto-Scroll
function startAutoScroll() {
    if (autoScrollInterval || isUserInteracting) return;
    
    isAutoScrolling = true;
    autoScrollInterval = setInterval(() => {
        const carousel = document.getElementById('popularTracksCarousel');
        if (!carousel) return;
        
        const container = carousel.closest('.carousel-container');
        if (!container) return;
        
        const cards = carousel.querySelectorAll('.album-preview-card');
        if (!cards.length) return;
        
        const lastCard = cards[cards.length - 1];
        const containerRect = container.getBoundingClientRect();
        const lastCardRect = lastCard.getBoundingClientRect();
        
        // Check if last card is visible in viewport
        const isLastCardVisible = lastCardRect.right <= containerRect.right + 50;
        
        if (isLastCardVisible) {
            // Last card is visible, go back to start
            container.scrollTo({
                left: 0,
                behavior: 'smooth'
            });
        } else {
            // Continue scrolling forward
            container.scrollBy({
                left: 140,
                behavior: 'smooth'
            });
        }
    }, 3000);
}

// Stop Auto-Scroll
function stopAutoScroll() {
    if (!autoScrollInterval) return;
    
    isAutoScrolling = false;
    clearInterval(autoScrollInterval);
    autoScrollInterval = null;
}

// Load all carousels with data
function loadAllCarousels() {
    loadCarouselData('/api/popular-tracks', 'popularTracksCarousel', createPopularTrackTemplate);
    loadCarouselData('/api/albums/recent-updates', 'recentUpdatesCarousel', createRecentUpdateTemplate);
    loadCarouselData('/api/albums/recent-additions', 'recentAdditionsCarousel', createRecentAdditionTemplate);
    loadMyAlbums();
}

// Generic carousel data loader
function loadCarouselData(apiUrl, carouselId, templateFn) {
    fetch(apiUrl)
        .then(response => response.json())
        .then(data => {
            console.log(`${carouselId} data:`, data);
            updateCarouselContent(carouselId, data, templateFn);
        })
        .catch(err => {
            console.error(`Error fetching ${carouselId}:`, err);
        });
}

// Update carousel content
function updateCarouselContent(carouselId, data, templateFn) {
    const track = document.getElementById(carouselId);
    if (!track) return;
    
    track.innerHTML = "";
    
    if (!data || !Array.isArray(data) || data.length === 0) {
        track.innerHTML = '<div class="loading">No items available</div>';
        return;
    }
    
    data.forEach(item => {
        track.insertAdjacentHTML('beforeend', templateFn(item));
    });
}

// Load My Albums
function loadMyAlbums() {
    const myAlbumsCarousel = document.getElementById('myAlbumsCarousel');
    if (!myAlbumsCarousel || myAlbumsCarousel.children.length > 0) return;
    
    fetch('/api/my-albums')
        .then(response => response.json())
        .then(data => {
            console.log("My albums data:", data);
            
            if (!data || data.length === 0) {
                const container = document.querySelector('.my-albums-carousel');
                const carouselWrapper = container.querySelector('.carousel-wrapper');
                if (carouselWrapper) {
                    carouselWrapper.remove();
                }
                
                container.innerHTML += `
                    <div class="no-content">
                        <i class="fas fa-music"></i>
                        <p>No albums in your collection yet</p>
                        <a href="/collection" class="btn-primary">Browse Collection</a>
                    </div>
                `;
            } else {
                updateCarouselContent('myAlbumsCarousel', data, createMyAlbumTemplate);
            }
        })
        .catch(err => {
            console.error('Error fetching my albums:', err);
        });
}

// Template Generators
function createPopularTrackTemplate(item) {
    return `
        <div class="album-preview-card" onclick="navigateToAlbum('${item.album_id || item.id}')">
            <div class="album-cover-container">
                <img src="${item.cover_path}" alt="${item.title}" class="album-cover" onerror="this.src='/static/images/default-album.jpg'">
                <div class="album-hover">
                    <i class="fas fa-play"></i>
                </div>
            </div>
            <div class="album-info">
                <h3>${item.title}</h3>
                <p>${item.album_title || 'Album'}</p>
                ${item.total_plays ? `<div class="plays">${item.total_plays} plays</div>` : ''}
            </div>
        </div>
    `;
}

function createRecentUpdateTemplate(album) {
    return `
        <div class="album-preview-card" onclick="navigateToAlbum('${album.id}')">
            <div class="album-cover-container">
                <img src="${album.cover_path}" alt="${album.title}" class="album-cover" onerror="this.src='/static/images/default-album.jpg'">
                <div class="album-hover">
                    <i class="fas fa-play"></i>
                </div>
            </div>
            <div class="album-info">
                <h3>${album.title}</h3>
                <p>${album.track_count} tracks</p>
                <div class="album-meta">
                    <span class="latest-update">Updated ${album.latest_update ? album.latest_update.split("T")[0] : 'Recently'}</span>
                    ${album.latest_track ? `<span class="new-track">Latest: ${album.latest_track.title}</span>` : ''}
                </div>
            </div>
        </div>
    `;
}

function createRecentAdditionTemplate(album) {
    return `
        <div class="album-preview-card" onclick="navigateToAlbum('${album.id}')">
            <div class="album-cover-container">
                <img src="${album.cover_path}" alt="${album.title}" class="album-cover" onerror="this.src='/static/images/default-album.jpg'">
                <div class="album-hover">
                    <i class="fas fa-play"></i>
                </div>
            </div>
            <div class="album-info">
                <h3>${album.title}</h3>
                <p>${album.track_count} tracks</p>
                <div class="album-meta">
                    <span class="time-added">Added ${album.created_at ? album.created_at.split("T")[0] : 'Recently'}</span>
                </div>
            </div>
        </div>
    `;
}

function createMyAlbumTemplate(album) {
    return `
        <div class="album-preview-card" onclick="navigateToAlbum('${album.id}')">
            <div class="album-cover-container">
                <img src="${album.cover_path}" alt="${album.title}" class="album-cover" onerror="this.src='/static/images/default-album.jpg'">
                <div class="album-hover">
                    <i class="fas fa-play"></i>
                </div>
            </div>
            <div class="album-info">
                <h3>${album.title}</h3>
                <p>${album.track_count || album.tracks?.length || 0} tracks</p>
            </div>
        </div>
    `;
}

// Handle Image Load Errors
function handleImageError(img) {
    img.src = '/static/images/default-album.jpg';
    img.alt = `Default cover for ${img.dataset.albumTitle || 'Unknown Album'}`;
}

// Navigate to Album Page
function navigateToAlbum(albumId) {
    if (!albumId) {
        console.error('Album ID is missing');
        return;
    }
    console.log(`Navigating to album: ${albumId}`);
    window.location.href = `/album/${albumId}`;
}

// Format Time (Utility Function)
function formatTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = Math.floor(seconds % 60);
    return `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
}

// Handle Authentication Errors
function handleAuthenticationError(error) {
    if (error.status === 401 || error.status === 403) {
        window.location.href = '/login';
    } else {
        console.error('Authentication error:', error);
        alert('Authentication error. Please try again.');
    }
}

// Attach Global Event Listeners
function attachGlobalEventListeners() {
    window.addEventListener('resize', handleCarouselResize);

    const prevButtons = document.querySelectorAll('.carousel-button.prev');
    const nextButtons = document.querySelectorAll('.carousel-button.next');

    prevButtons.forEach(button => {
        button.addEventListener('click', () => {
            stopAutoScroll();
            isUserInteracting = true;
            
            const containerId = button.parentElement.querySelector('.carousel-container')?.id;
            if (containerId === 'popularTracksContainer') {
                moveCarousel(-1);
            }
            
            pauseAutoScrollWithResume();
        });
    });

    nextButtons.forEach(button => {
        button.addEventListener('click', () => {
            stopAutoScroll();
            isUserInteracting = true;
            
            const containerId = button.parentElement.querySelector('.carousel-container')?.id;
            if (containerId === 'popularTracksContainer') {
                moveCarousel(1);
            }
            
            pauseAutoScrollWithResume();
        });
    });
}

// Handle Carousel Resize
function handleCarouselResize() {
    const carousel = document.getElementById('popularTracksCarousel');
    if (!carousel) return;

    const cards = carousel.querySelectorAll('.album-preview-card');
    if (!cards.length) return;
}

// Highlight Active Navigation Links
function highlightActiveNavLinks() {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });
}

// Show Success Message
function showSuccessMessage(message) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'success-message';
    messageDiv.textContent = message;
    document.body.appendChild(messageDiv);

    setTimeout(() => {
        messageDiv.remove();
    }, 2500);
}

// Setup Resume Button Click Handlers
function setupResumeButtons() {
    const resumeButtons = document.querySelectorAll('.resume-btn');
    resumeButtons.forEach(button => {
        button.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const trackElement = e.target.closest('.progress-track');
            if (trackElement) {
                const trackId = trackElement.dataset.trackId;
                const savedPosition = parseFloat(trackElement.dataset.position) || 0;

                // ✅ Extract track data directly from DOM (no API call needed!)
                const titleElement = trackElement.querySelector('.track-title');
                const albumElement = trackElement.querySelector('.track-album');
                const coverElement = trackElement.querySelector('.track-thumbnail');

                const trackData = {
                    title: titleElement ? titleElement.textContent.trim() : 'Unknown Track',
                    album_title: albumElement ? albumElement.textContent.trim() : 'Unknown Album',
                    cover_path: coverElement ? coverElement.src : '/static/images/default-album.jpg'
                };

                console.log('🎵 Resuming track from DOM data:', trackData);

                // Use persistent player if available
                if (window.persistentPlayer && typeof window.persistentPlayer.playTrack === 'function') {
                    try {
                        await window.persistentPlayer.playTrack(
                            trackId,
                            trackData.title,
                            trackData.album_title,
                            trackData.cover_path,
                            true  // Auto-play
                        );

                        setTimeout(() => {
                            if (window.persistentPlayer.audio) {
                                window.persistentPlayer.audio.currentTime = savedPosition;
                            }
                        }, 500);

                        button.innerHTML = '<i class="fas fa-pause"></i> Playing';
                        showSuccessMessage('Resuming playback...');
                    } catch (error) {
                        console.error('❌ Error resuming track:', error);
                        // Fallback to navigation
                        window.location.href = `/player/${trackId}`;
                    }
                } else {
                    // Fallback: navigate to player page
                    console.log('persistentPlayer not available, navigating to player page');
                    window.location.href = `/player/${trackId}`;
                }
            }
        });
    });
}

// Navigate to Player
function playTrack(trackId, title, album, coverPath, audioUrl) {
    window.persistentPlayer.playTrack({
        id: trackId,
        title: title,
        album: album,
        coverUrl: coverPath,
        audioUrl: audioUrl
    });
}

// Add to Collection
function addToCollection(albumId, event) {
    event.stopPropagation();
    fetch(`/api/collection/add/${albumId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showSuccessMessage('Added to collection');
        }
    })
    .catch(error => console.error('Error:', error));
}