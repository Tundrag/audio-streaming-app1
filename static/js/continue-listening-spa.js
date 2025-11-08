// continue-listening-spa.js - SPA wrapper for Continue Listening Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { ContinueListeningController } = await import(`./continue-listening-shared-spa.js?v=${v}`);

export class ContinueListeningSPA {
    constructor() {
        this.controller = new ContinueListeningController('spa');
    }

    getRequiredStyles() {
        return ['/static/css/continue-listening.css'];
    }

    getPageTitle() {
        return 'Continue Listening';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.continueListeningController = this.controller;
    }

    async destroy() {
        delete window.continueListeningController;
        return await this.controller.destroy();
    }
}
