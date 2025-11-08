// benefits-management-ssr-init.js - SSR initialization for Benefits Management page
import { BenefitsManagementController } from './benefits-management-shared.js';

// Initialize SSR controller
const controller = new BenefitsManagementController('ssr');
await controller.mount();
window.benefitsController = controller;

console.log('✅ Benefits Management SSR initialized');
