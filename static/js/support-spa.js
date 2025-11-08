// support-spa.js - SPA wrapper for Support Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { SupportController } = await import(`./support-shared-spa.js?v=${v}`);

export class SupportSPA {
    constructor() {
        this.controller = new SupportController('spa');
    }

    getRequiredStyles() {
        const v = window.spaRouter?.cacheVersion || Date.now();
        return [`/static/css/support.css?v=${v}`];
    }

    getPageTitle() {
        return 'Support';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.supportController = this.controller;
    }

    async destroy() {
        delete window.supportController;
        return await this.controller.destroy();
    }
}