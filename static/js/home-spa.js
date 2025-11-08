// home-spa.js - SPA wrapper for Home Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { HomeController } = await import(`./home-shared-spa.js?v=${v}`);

export class HomeSPA {
    constructor() {
        this.controller = new HomeController('spa');
    }

    getRequiredStyles() {
        // Styles are embedded in the controller's HTML
        return [];
    }

    getPageTitle() {
        return 'Home';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.homeController = this.controller;
    }

    async destroy() {
        delete window.homeController;
        return await this.controller.destroy();
    }
}
