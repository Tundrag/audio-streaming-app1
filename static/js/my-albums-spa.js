// my-albums-spa.js - SPA wrapper for My Albums Page
// ✅ Use dynamic import with cache busting from global APP_VERSION
const v = window.APP_VERSION || Date.now();
const { MyAlbumsController } = await import(`./my-albums-shared-spa.js?v=${v}`);

export class MyAlbumsSPA {
    constructor() {
        this.controller = new MyAlbumsController('spa');
    }

    getRequiredStyles() {
        return ['/static/css/my-albums.css'];
    }

    getPageTitle() {
        return 'My Albums';
    }

    async render() {
        return await this.controller.render();
    }

    async mount() {
        await this.controller.mount();
        window.myAlbumsController = this.controller;
    }

    async destroy() {
        delete window.myAlbumsController;
        return await this.controller.destroy();
    }
}
