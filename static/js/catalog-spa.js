// catalog-spa.js - SPA wrapper for Catalog Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { CatalogController } = await import(`./catalog-shared-spa.js?v=${v}`);

export class CatalogSPA {
    constructor() {
        this.controller = new CatalogController('spa');
    }

    getRequiredStyles() {
        return ['/static/css/catalog.css'];
    }

    getPageTitle() {
        return 'Catalog';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.catalogController = this.controller;
    }

    async destroy() {
        delete window.catalogController;
        return await this.controller.destroy();
    }
}

// Export default for compatibility
export default CatalogSPA;
