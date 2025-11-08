// collection-spa.js - SPA wrapper for Collection Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { CollectionController } = await import(`./collection-shared-spa.js?v=${v}`);

export class CollectionSPA {
    constructor() {
        this.controller = new CollectionController('spa');
    }

    getRequiredStyles() {
        // Styles are embedded in the controller's HTML
        return [];
    }

    getPageTitle() {
        return 'My Collection';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.collectionController = this.controller;
    }

    async destroy() {
        delete window.collectionController;
        return await this.controller.destroy();
    }
}

// Export default for compatibility
export default CollectionSPA;
