// creator-management-spa.js - SPA Mode Wrapper
// Thin wrapper that creates the controller in SPA mode

// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { CreatorManagementController } = await import(`./creator-management-shared.js?v=${v}`);

export class CreatorManagementSPA {
    constructor() {
        console.log('🎛️ [DEBUG] CreatorManagementSPA constructor called');
        this.controller = new CreatorManagementController('spa');
        console.log('🎛️ [DEBUG] Controller instance created:', this.controller);
    }
    
    getRequiredStyles() {
        const v = window.spaRouter?.cacheVersion || Date.now();
        const styles = [`/static/css/creator-management.css?v=${v}`];
        console.log('🎛️ [DEBUG] getRequiredStyles returning:', styles);
        return styles;
    }

    getPageTitle() {
        return 'Creator Management';
    }

    async render() {
        console.log('🎛️ [DEBUG] render() called');
        const result = await this.controller.render();
        console.log('🎛️ [DEBUG] render() result:', result ? 'HTML returned' : 'Empty string (redirect expected)');
        return result;
    }
    
    async mount() {
        console.log('🎛️ [DEBUG] mount() called');
        await this.controller.mount();
        window.creatorManagementController = this.controller;
        console.log('✅ [DEBUG] mount() completed');
    }
    
    async destroy() {
        console.log('🎛️ [DEBUG] destroy() called');
        delete window.creatorManagementController;
        return await this.controller.destroy();
    }
}