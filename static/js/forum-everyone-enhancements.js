/**
 * forum-everyone-enhancements.js - @everyone mention support
 * Add this to your existing forum JavaScript modules
 */

// Enhanced ForumMessages class with @everyone support
class ForumMessagesWithEveryone extends ForumMessages {
    constructor() {
        super();
        
        // @everyone specific properties
        this.canUseEveryone = false;
        this.everyoneWarningTimeout = null;
        this.everyoneMentionCount = 0;
        this.lastEveryoneUsage = 0;
        this.everyoneRateLimit = 3; // Default rate limit
        this.rateLimitWindow = 24 * 60 * 60 * 1000; // 24 hours in ms
        
        // Load user permissions
        this.loadEveryonePermissions();
    }
    
    async loadEveryonePermissions() {
        try {
            const response = await fetch('/api/forum/settings/everyone-mentions');
            if (response.ok) {
                const data = await response.json();
                this.canUseEveryone = data.can_use_everyone;
                this.allowEveryoneMentions = data.allow_everyone_mentions;
                console.log(`📢 @everyone permissions loaded: can_use=${this.canUseEveryone}, allow_receive=${this.allowEveryoneMentions}`);
            }
        } catch (error) {
            console.error('Error loading @everyone permissions:', error);
        }
    }
    
    // Override the mention autocomplete to include @everyone
    async showMentionAutocomplete(textArea, query) {
        try {
            // If user types "every" or similar, show @everyone option
            const showEveryone = 'everyone'.startsWith(query.toLowerCase()) && 
                                query.length >= 2;
            
            // Get regular user suggestions
            const response = await fetch(`/api/forum/users/search?q=${encodeURIComponent(query)}&limit=5`);
            const users = response.ok ? await response.json() : [];
            
            // Combine @everyone with user suggestions
            const allSuggestions = [];
            
            if (showEveryone) {
                allSuggestions.push({
                    username: 'everyone',
                    display_name: 'everyone',
                    role: 'Everyone',
                    badge_color: '#ef4444',
                    is_everyone: true
                });
            }
            
            allSuggestions.push(...users);
            
            if (allSuggestions.length === 0) {
                return this.hideMentionAutocomplete();
            }

            if (!this.mentionAutocomplete) {
                this.mentionAutocomplete = document.createElement('div');
                this.mentionAutocomplete.className = 'mention-autocomplete';
                textArea.parentElement.appendChild(this.mentionAutocomplete);
            }

            this.mentionAutocomplete.innerHTML = allSuggestions.map((suggestion, index) => {
                const isEveryone = suggestion.is_everyone;
                const itemClass = `mention-item ${index === 0 ? 'selected' : ''} ${isEveryone ? 'everyone' : ''}`;
                
                if (isEveryone) {
                    return `
                        <div class="${itemClass}" data-username="${suggestion.username}">
                            <div class="user-avatar" style="background-color: ${suggestion.badge_color}">
                                📢
                            </div>
                            <div>
                                <div style="font-weight: 700; color: #ef4444;">@${suggestion.username}</div>
                                <div style="font-size: 0.8rem; color: #dc2626; font-weight: 500;">Notify everyone in forum</div>
                            </div>
                        </div>
                    `;
                } else {
                    return `
                        <div class="${itemClass}" data-username="${suggestion.username}">
                            <div class="user-avatar" style="background-color: ${suggestion.badge_color}">
                                ${suggestion.username.substring(0, 2).toUpperCase()}
                            </div>
                            <div>
                                <div style="font-weight: 500;">${suggestion.username}</div>
                                <div style="font-size: 0.8rem; color: var(--text-secondary);">${suggestion.role}</div>
                            </div>
                        </div>
                    `;
                }
            }).join('');

            this.mentionAutocomplete.style.display = 'block';
            this.selectedMentionIndex = 0;

            // Add click handlers
            this.mentionAutocomplete.querySelectorAll('.mention-item').forEach((item) => {
                item.addEventListener('click', () => {
                    const username = item.dataset.username;
                    if (username === 'everyone') {
                        this.insertEveryoneMention(textArea, username);
                    } else {
                        this.insertMention(textArea, username);
                    }
                });
            });
            
        } catch (error) {
            console.error('Error loading mention suggestions:', error);
        }
    }
    
    insertEveryoneMention(textArea, username) {
        // Check rate limiting
        if (!this.checkEveryoneRateLimit()) {
            this.showEveryoneRateLimitWarning();
            this.hideMentionAutocomplete();
            return;
        }
        
        // Show warning before inserting
        this.showEveryoneWarning(textArea);
        
        // Insert the mention
        this.insertMention(textArea, username);
        
        // Track usage
        this.lastEveryoneUsage = Date.now();
        this.everyoneMentionCount++;
    }
    
    checkEveryoneRateLimit() {
        const now = Date.now();
        const timeSinceLastUse = now - this.lastEveryoneUsage;
        
        // Reset count if outside the rate limit window
        if (timeSinceLastUse > this.rateLimitWindow) {
            this.everyoneMentionCount = 0;
        }
        
        return this.everyoneMentionCount < this.everyoneRateLimit;
    }
    
    showEveryoneWarning(textArea) {
        // Remove existing warning
        const existingWarning = textArea.parentElement.querySelector('.everyone-mention-warning');
        if (existingWarning) {
            existingWarning.remove();
        }
        
        // Create warning
        const warning = document.createElement('div');
        warning.className = 'everyone-mention-warning show';
        warning.innerHTML = `
            <i class="fas fa-exclamation-triangle"></i>
            <span>You're about to notify everyone in the forum!</span>
        `;
        
        textArea.parentElement.appendChild(warning);
        
        // Auto-hide after 5 seconds
        clearTimeout(this.everyoneWarningTimeout);
        this.everyoneWarningTimeout = setTimeout(() => {
            warning.classList.remove('show');
            setTimeout(() => warning.remove(), 300);
        }, 5000);
    }
    
    showEveryoneRateLimitWarning() {
        const remaining = Math.ceil((this.rateLimitWindow - (Date.now() - this.lastEveryoneUsage)) / (60 * 60 * 1000));
        
        window.showToast ? 
            window.showToast(`Rate limit exceeded. You can use @everyone again in ${remaining} hours.`, 'error') :
            alert(`Rate limit exceeded. You can use @everyone again in ${remaining} hours.`);
    }
    
    // Override message rendering to handle @everyone styling
    renderMessage(message, isNew = false) {
        const baseHtml = super.renderMessage(message, isNew);
        
        // Check if message has @everyone mention
        const hasEveryone = message.mentions && message.mentions.includes('everyone');
        
        if (hasEveryone) {
            // Add special class and badge
            const parser = new DOMParser();
            const doc = parser.parseFromString(baseHtml, 'text/html');
            const messageItem = doc.querySelector('.message-item');
            
            if (messageItem) {
                messageItem.classList.add('has-everyone-mention');
                
                // Add @everyone badge to message header
                const messageHeader = messageItem.querySelector('.message-header');
                if (messageHeader) {
                    const everyoneBadge = document.createElement('span');
                    everyoneBadge.className = 'everyone-mention-badge';
                    everyoneBadge.innerHTML = '<i class="fas fa-bullhorn"></i> @everyone';
                    messageHeader.appendChild(everyoneBadge);
                }
            }
            
            return doc.body.innerHTML;
        }
        
        return baseHtml;
    }
    
    // Handle @everyone WebSocket notifications
    handleEveryoneMention(data) {
        console.log('📢 @everyone mention received:', data);
        
        // Show special notification
        this.showEveryoneMentionNotification(data);
        
        // Play sound if enabled
        if (this.allowEveryoneMentions && this.everyoneSoundEnabled) {
            this.playEveryoneSound();
        }
        
        // Update counter
        this.showEveryoneMentionCounter(data.notification_count);
    }
    
    showEveryoneMentionNotification(data) {
        const notification = document.createElement('div');
        notification.className = 'notification everyone-mention-notification';
        notification.innerHTML = `
            <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem;">
                <i class="fas fa-bullhorn" style="font-size: 1.2rem;"></i>
                <strong>@everyone mention</strong>
            </div>
            <div style="margin-bottom: 0.5rem;">
                <strong>${data.sender.username}</strong> mentioned everyone in 
                <strong>"${data.thread_title}"</strong>
            </div>
            <div style="display: flex; gap: 0.5rem;">
                <button onclick="forum.viewThread(${data.thread_id}); this.closest('.notification').remove();" 
                        style="flex: 1; padding: 0.25rem 0.5rem; background: white; color: #ef4444; border: 1px solid white; border-radius: 4px; cursor: pointer; font-weight: 500;">
                    View Thread
                </button>
                <button onclick="this.closest('.notification').remove();" 
                        style="padding: 0.25rem 0.5rem; background: transparent; color: white; border: 1px solid rgba(255,255,255,0.5); border-radius: 4px; cursor: pointer;">
                    Dismiss
                </button>
            </div>
        `;
        
        // Position and show notification
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            max-width: 350px;
            z-index: 2000;
            padding: 1rem;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        `;
        
        document.body.appendChild(notification);
        
        // Auto-remove after 10 seconds
        setTimeout(() => {
            if (notification.parentElement) {
                notification.style.opacity = '0';
                notification.style.transform = 'translateX(100%)';
                setTimeout(() => notification.remove(), 300);
            }
        }, 10000);
    }
    
    showEveryoneMentionCounter(count) {
        let counter = document.getElementById('everyoneMentionCounter');
        
        if (!counter) {
            counter = document.createElement('div');
            counter.id = 'everyoneMentionCounter';
            counter.className = 'everyone-mention-counter';
            document.body.appendChild(counter);
        }
        
        counter.innerHTML = `
            <i class="fas fa-bullhorn"></i>
            ${count} people notified
        `;
        
        counter.classList.add('show');
        
        // Hide after 3 seconds
        setTimeout(() => {
            counter.classList.remove('show');
        }, 3000);
    }
    
    playEveryoneSound() {
        try {
            // Create a more attention-grabbing sound for @everyone
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            
            // Create a sequence of tones
            const tones = [800, 1000, 800]; // Hz frequencies
            const duration = 200; // ms per tone
            
            tones.forEach((frequency, index) => {
                setTimeout(() => {
                    const oscillator = audioContext.createOscillator();
                    const gainNode = audioContext.createGain();
                    
                    oscillator.connect(gainNode);
                    gainNode.connect(audioContext.destination);
                    
                    oscillator.frequency.setValueAtTime(frequency, audioContext.currentTime);
                    oscillator.type = 'sine';
                    
                    gainNode.gain.setValueAtTime(0.1, audioContext.currentTime);
                    gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + duration / 1000);
                    
                    oscillator.start(audioContext.currentTime);
                    oscillator.stop(audioContext.currentTime + duration / 1000);
                }, index * duration);
            });
        } catch (error) {
            console.log('Could not play @everyone sound:', error);
        }
    }
    
    // Override WebSocket message handler to include @everyone
    handleWebSocketMessage(data) {
        // Handle @everyone specific messages
        if (data.type === 'everyone_mention') {
            this.handleEveryoneMention(data);
            return;
        }
        
        // Call parent handler for other messages
        super.handleWebSocketMessage(data);
    }
    
    // Add @everyone settings methods
    async updateEveryoneSettings(allowMentions) {
        try {
            const response = await fetch('/api/forum/settings/everyone-mentions', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ allow_everyone_mentions: allowMentions })
            });
            
            if (response.ok) {
                const result = await response.json();
                this.allowEveryoneMentions = result.allow_everyone_mentions;
                
                window.showToast ? 
                    window.showToast(result.message, 'success') :
                    console.log(result.message);
                    
                return result;
            } else {
                throw new Error('Failed to update settings');
            }
        } catch (error) {
            console.error('Error updating @everyone settings:', error);
            window.showToast ? 
                window.showToast('Failed to update @everyone settings', 'error') :
                alert('Failed to update @everyone settings');
        }
    }
}

// Settings UI for @everyone preferences
function renderEveryoneSettingsSection() {
    return `
        <div class="everyone-settings-section">
            <h3>
                <i class="fas fa-bullhorn"></i>
                @everyone Mentions
            </h3>
            
            <div class="everyone-permission-indicator ${window.forum.canUseEveryone ? 'allowed' : 'denied'}">
                <i class="fas fa-${window.forum.canUseEveryone ? 'check-circle' : 'times-circle'}"></i>
                ${window.forum.canUseEveryone ? 
                    'You can use @everyone to notify all forum users' : 
                    'Only creators and team members can use @everyone'
                }
            </div>
            
            <div class="setting-item">
                <label class="setting-label">Receive @everyone Notifications</label>
                <div class="setting-description">
                    Get notified when someone uses @everyone in any thread. You can disable this if you find these notifications too frequent.
                </div>
                <div class="setting-toggle">
                    <div class="toggle-switch ${window.forum.allowEveryoneMentions ? 'active' : ''}" 
                         onclick="forum.toggleEveryoneNotifications(this)">
                    </div>
                    <span class="toggle-label">
                        ${window.forum.allowEveryoneMentions ? 'Enabled' : 'Disabled'}
                    </span>
                </div>
            </div>
            
            ${window.forum.canUseEveryone ? `
                <div class="setting-item">
                    <label class="setting-label">@everyone Usage Guidelines</label>
                    <div class="setting-description">
                        <ul style="margin: 0; padding-left: 1.5rem; color: var(--text-secondary); font-size: 0.9rem;">
                            <li>Use @everyone sparingly for important announcements</li>
                            <li>Consider the time zone of your audience</li>
                            <li>Limit usage: ${window.forum.everyoneRateLimit} times per 24 hours for team members</li>
                            <li>Creators have unlimited usage but should use responsibly</li>
                        </ul>
                    </div>
                </div>
            ` : ''}
        </div>
    `;
}

// Toggle function for @everyone notifications
function toggleEveryoneNotifications(toggleElement) {
    const isCurrentlyEnabled = toggleElement.classList.contains('active');
    const newState = !isCurrentlyEnabled;
    
    // Update UI optimistically
    toggleElement.classList.toggle('active', newState);
    const label = toggleElement.nextElementSibling;
    label.textContent = newState ? 'Enabled' : 'Disabled';
    
    // Update server
    window.forum.updateEveryoneSettings(newState).then(result => {
        if (!result || !result.success) {
            // Revert UI on failure
            toggleElement.classList.toggle('active', isCurrentlyEnabled);
            label.textContent = isCurrentlyEnabled ? 'Enabled' : 'Disabled';
        }
    });
}

// Replace the default ForumMessages class
window.ForumMessages = ForumMessagesWithEveryone;

console.log('📢 @everyone mention support loaded!');