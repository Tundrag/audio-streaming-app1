// Complete UPDATED login.js - Works with new fixed backend
// Supports the new staged registration flow with atomic device registration

let currentOTPId = null;
let registeredEmail = '';
let registeredUsername = '';
let userPin = '';

// Plans toggle functionality (unchanged)
function togglePlans() {
  const plansSection = document.getElementById('plansSection');
  const buttonText = document.getElementById('plansButtonText');
  
  if (plansSection.classList.contains('show')) {
    plansSection.classList.remove('show');
    buttonText.textContent = 'Show Membership Plans';
  } else {
    plansSection.classList.add('show');
    buttonText.textContent = 'Hide Membership Plans';
  }
}

function scrollToPlans() {
  const plansSection = document.getElementById('plansSection');
  
  if (!plansSection.classList.contains('show')) {
    togglePlans();
  }
  
  setTimeout(() => {
    plansSection.scrollIntoView({ 
      behavior: 'smooth', 
      block: 'start' 
    });
  }, 300);
}

// Modal management functions
function openModal(tab = 'register') {
  document.getElementById('signInModal').classList.add('active');
  document.body.style.overflow = 'hidden';
  switchTab(tab);
}

function closeModal() {
  document.getElementById('signInModal').classList.remove('active');
  document.body.style.overflow = '';
  clearErrors();
  resetForms();
}

function openOtpModal() {
  document.getElementById('signInModal').classList.remove('active');
  document.getElementById('otpModal').classList.add('active');
}

function closeOtpModal() {
  document.getElementById('otpModal').classList.remove('active');
  document.getElementById('signInModal').classList.add('active');
  document.getElementById('otpCode').value = '';
  clearErrors();
}

function openSuccessModal() {
  document.getElementById('otpModal').classList.remove('active');
  document.getElementById('successModal').classList.add('active');
  document.getElementById('successEmail').textContent = registeredEmail;
  document.getElementById('successPin').textContent = userPin;
  createConfetti();
}

function closeSuccessModal() {
  document.getElementById('successModal').classList.remove('active');
  document.body.style.overflow = '';
}

function closeSuccessAndLogin() {
  closeSuccessModal();
  openModal('signin');
  document.getElementById('kofiEmail').value = registeredEmail;
}

function openGetPinModal() {
  document.getElementById('signInModal').classList.remove('active');
  document.getElementById('getPinModal').classList.add('active');
}

function closeGetPinModal() {
  document.getElementById('getPinModal').classList.remove('active');
  document.getElementById('signInModal').classList.add('active');
  document.getElementById('getPinForm').reset();
  document.getElementById('pinResult').style.display = 'none';
  clearErrors();
}

function openForgotPinModal() {
  closeModal();
  document.getElementById('forgotPinModal').classList.add('active');
}

function closeForgotPinModal() {
  document.getElementById('forgotPinModal').classList.remove('active');
  document.getElementById('forgotPinForm').reset();
  document.getElementById('forgotPinResult').style.display = 'none';
  document.getElementById('forgotPinError').style.display = 'none';
}

function openGuideModal() {
  document.getElementById('guideModal').classList.add('active');
  document.body.style.overflow = 'hidden';
}

function closeGuideModal() {
  document.getElementById('guideModal').classList.remove('active');
  document.body.style.overflow = '';
}

// Tab switching
function switchTab(tab) {
  const tabs = document.querySelectorAll('.login-tab');
  const registerForm = document.getElementById('registerForm');
  const kofiForm = document.getElementById('kofiForm');
  const creatorForm = document.getElementById('creatorForm');

  tabs.forEach(t => t.classList.remove('active'));
  
  registerForm.style.display = 'none';
  kofiForm.style.display = 'none';
  creatorForm.style.display = 'none';
  
  if (tab === 'register') {
    tabs[0].classList.add('active');
    registerForm.style.display = 'block';
    document.getElementById('modalTitle').textContent = 'Start Free Trial';
  } else if (tab === 'signin') {
    tabs[1].classList.add('active');
    kofiForm.style.display = 'block';
    document.getElementById('modalTitle').textContent = 'Sign In';
  } else if (tab === 'creator') {
    tabs[2].classList.add('active');
    creatorForm.style.display = 'block';
    document.getElementById('modalTitle').textContent = 'Creator Access';
  }
  
  clearErrors();
}

// Password toggle
function togglePassword(inputId) {
  const input = document.getElementById(inputId);
  const icon = input.parentNode.querySelector('.password-toggle i');
  
  if (input.type === 'password') {
    input.type = 'text';
    icon.classList.remove('fa-eye');
    icon.classList.add('fa-eye-slash');
  } else {
    input.type = 'password';
    icon.classList.remove('fa-eye-slash');
    icon.classList.add('fa-eye');
  }
}

// Form helpers
function resetForms() {
  document.getElementById('registerForm').reset();
  document.getElementById('kofiForm').reset();
  document.getElementById('creatorForm').reset();
  currentOTPId = null;
  registeredEmail = '';
  registeredUsername = '';
  userPin = '';
}

function clearErrors() {
  document.querySelectorAll('.error-message:not([id]), .success-message:not([id])')
    .forEach(el => el.remove());
  
  ['creatorError', 'kofiError', 'kofiSuccess'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  
  const pinVerification = document.querySelector('.pin-verification');
  if (pinVerification) {
    pinVerification.remove();
  }
}

function showError(message, container = null) {
  clearErrors();
  const errorDiv = document.createElement('div');
  errorDiv.className = 'error-message';
  errorDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i>${message}`;
  
  if (container) {
    container.insertBefore(errorDiv, container.firstChild);
  } else {
    const activeForm = document.querySelector('form[style*="block"], form:not([style*="none"])');
    if (activeForm) {
      // Check if this is the registration form
      if (activeForm.id === 'registerForm') {
        // Find the trial-info div and insert after it
        const trialInfo = activeForm.querySelector('.trial-info');
        if (trialInfo) {
          trialInfo.insertAdjacentElement('afterend', errorDiv);
          return;
        }
      }
      // Fallback to original behavior
      activeForm.insertBefore(errorDiv, activeForm.firstChild);
    }
  }
}
function showSuccess(message, container = null) {
  clearErrors();
  const successDiv = document.createElement('div');
  successDiv.className = 'success-message';
  successDiv.style.display = 'flex';
  successDiv.innerHTML = `<i class="fas fa-check-circle"></i>${message}`;
  
  if (container) {
    container.insertBefore(successDiv, container.firstChild);
  } else {
    const activeForm = document.querySelector('form[style*="block"], form:not([style*="none"])');
    if (activeForm) {
      activeForm.insertBefore(successDiv, activeForm.firstChild);
    }
  }
}

function showErrorMessage(message, formType = 'kofi') {
  const modal = document.querySelector('.modal');
  const existingErrors = document.querySelectorAll('.error-message:not([id])');
  existingErrors.forEach(error => error.remove());
  
  if (message.includes('Invalid creator PIN')) {
    const errors = [
      'Check the creator pin post on the creator page',
      'Click the eye icon to view the PIN and verify it\'s entered correctly',
      'Make sure you\'re entering this month\'s correct PIN'
    ];
    
    errors.forEach(errorText => {
      const errorDiv = document.createElement('div');
      errorDiv.className = 'error-message';
      errorDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i>${errorText}`;
      
      const form = document.getElementById(formType + 'Form');
      form.insertAdjacentElement('beforebegin', errorDiv);
    });
  } else if (message.includes('Email not found')) {
    const errors = [
      'Make sure you are using the correct email (check Settings on Ko-fi account)',
      'Make sure you are a supporter of webaudio', 
      'Email not found or support is inactive'
    ];
    
    errors.forEach(errorText => {
      const errorDiv = document.createElement('div');
      errorDiv.className = 'error-message';
      errorDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i>${errorText}`;
      
      const form = document.getElementById(formType + 'Form');
      form.insertAdjacentElement('beforebegin', errorDiv);
    });
  } else {
    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-message';
    errorDiv.innerHTML = `<i class="fas fa-exclamation-circle"></i>${message}`;
    
    const form = document.getElementById(formType + 'Form');
    form.insertAdjacentElement('beforebegin', errorDiv);
  }
}

function showPinVerificationMessage() {
  const modal = document.querySelector('.modal');
  let pinVerificationDiv = modal.querySelector('.pin-verification');
  
  if (!pinVerificationDiv) {
    pinVerificationDiv = document.createElement('div');
    pinVerificationDiv.className = 'pin-verification';
    pinVerificationDiv.style.cssText = `
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 16px;
      margin-bottom: 20px;
      background: #f0fdf4;
      color: #16a34a;
      border: 1px solid #bbf7d0;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 500;
    `;
    pinVerificationDiv.innerHTML = '<i class="fas fa-check-circle"></i>PIN verified';
    
    const kofiForm = document.getElementById('kofiForm');
    kofiForm.insertAdjacentElement('beforebegin', pinVerificationDiv);
  } else {
    pinVerificationDiv.style.display = 'flex';
  }
}

// Confetti animation
function createConfetti() {
  const container = document.getElementById('confettiContainer');
  container.innerHTML = '';
  
  for (let i = 0; i < 50; i++) {
    const confetti = document.createElement('div');
    confetti.className = 'confetti';
    confetti.style.left = Math.random() * 100 + '%';
    confetti.style.animationDelay = Math.random() * 3 + 's';
    confetti.style.animationDuration = (Math.random() * 3 + 2) + 's';
    container.appendChild(confetti);
  }
}

// ✅ MOBILE-OPTIMIZED: Device Registration Confirmation Modal
function showDeviceRegistrationConfirmation() {
  const webauthnAvailable = window.strictGuestAbusePreventionSystem?.isAvailable();
  const securityType = webauthnAvailable ? 'Passkey protection will be enabled' : 'Browser-based protection will be used';
  const securityIcon = webauthnAvailable ? '🔐' : '🛡️';
  
  const confirmationHTML = `
    <div id="deviceConfirmationModal" class="modal-overlay">
      <div class="modal" style="
        max-width: min(90vw, 500px); 
        width: 100%; 
        max-height: 90vh; 
        overflow-y: auto; 
        margin: auto;
        position: relative;
        box-sizing: border-box;
      ">
        <button class="modal-close" onclick="cancelDeviceRegistration()" style="
          position: sticky;
          top: 0;
          right: 0;
          z-index: 10;
          background: rgba(255,255,255,0.9);
          backdrop-filter: blur(5px);
        ">&times;</button>
        
        <div style="padding: 1rem;">
          <h2 style="font-size: clamp(1.2rem, 4vw, 1.5rem); margin-bottom: 1rem; text-align: center;">🔒 Device Registration Required</h2>
          
          <div style="
            background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); 
            border: 2px solid #f59e0b; 
            border-radius: 12px; 
            padding: 1rem; 
            margin: 1rem 0;
          ">
            <p style="margin: 0 0 1rem 0; font-weight: 600; color: #92400e; font-size: clamp(0.9rem, 3vw, 1rem);">
              <strong>Important:</strong> This will register your device for one-time trial access.
            </p>
            
            <ul style="list-style: none; padding: 0; margin: 1rem 0; font-size: clamp(0.85rem, 2.5vw, 0.95rem);">
              <li style="padding: 0.4rem 0; color: #78350f; font-weight: 500; display: flex; align-items: flex-start; gap: 0.5rem; line-height: 1.4;">
                <span style="flex-shrink: 0;">✅</span>
                <span>Only <strong>ONE trial per device</strong> is allowed</span>
              </li>
              <li style="padding: 0.4rem 0; color: #78350f; font-weight: 500; display: flex; align-items: flex-start; gap: 0.5rem; line-height: 1.4;">
                <span style="flex-shrink: 0;">🔒</span>
                <span>Device security features will be activated</span>
              </li>
              <li style="padding: 0.4rem 0; color: #78350f; font-weight: 500; display: flex; align-items: flex-start; gap: 0.5rem; line-height: 1.4;">
                <span style="flex-shrink: 0;">📱</span>
                <span>Your device fingerprint will be stored securely</span>
              </li>
              <li style="padding: 0.4rem 0; color: #78350f; font-weight: 500; display: flex; align-items: flex-start; gap: 0.5rem; line-height: 1.4;">
                <span style="flex-shrink: 0;">${securityIcon}</span>
                <span>${securityType}</span>
              </li>
            </ul>
            
            <div style="
              background: #fecaca; 
              border: 2px solid #ef4444; 
              border-radius: 8px; 
              padding: 1rem; 
              margin-top: 1rem; 
              text-align: center;
            ">
              <strong style="color: #dc2626; font-size: clamp(0.95rem, 3vw, 1.1rem); display: block; line-height: 1.3;">
                ⚠️ Once registered, you cannot use this device for another trial
              </strong>
            </div>
          </div>
          
          <div style="
            display: flex; 
            flex-direction: column;
            gap: 0.75rem; 
            margin-top: 1.5rem; 
            align-items: center;
          ">
            <button class="btn-primary" onclick="proceedWithRegistration()" style="
              background: linear-gradient(135deg, #10b981 0%, #059669 100%); 
              color: white; 
              border: none; 
              padding: 0.75rem 1.25rem; 
              border-radius: 8px; 
              font-weight: 600; 
              cursor: pointer; 
              transition: all 0.3s ease; 
              font-size: clamp(0.9rem, 3vw, 1rem);
              width: 100%;
              max-width: 300px;
            ">
              I Understand - Continue Registration
            </button>
            <button class="btn-secondary" onclick="cancelDeviceRegistration()" style="
              background: #6b7280; 
              color: white; 
              border: none; 
              padding: 0.75rem 1.25rem; 
              border-radius: 8px; 
              font-weight: 600; 
              cursor: pointer; 
              transition: all 0.3s ease; 
              font-size: clamp(0.9rem, 3vw, 1rem);
              width: 100%;
              max-width: 300px;
            ">
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  `;
  
  document.body.insertAdjacentHTML('beforeend', confirmationHTML);
  document.getElementById('deviceConfirmationModal').classList.add('active');
  document.body.style.overflow = 'hidden';
}

function cancelDeviceRegistration() {
  const modal = document.getElementById('deviceConfirmationModal');
  if (modal) {
    modal.remove();
  }
  document.body.style.overflow = '';
  console.log('User cancelled device registration');
}

// ✅ FIXED: Registration form - Show confirmation modal FIRST
document.getElementById('registerForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  
  const username = document.getElementById('registerUsername').value.trim();
  const email = document.getElementById('registerEmail').value.trim();
  
  if (!username || !email) {
    showError('Please fill in all fields');
    return;
  }
  
  // Store values for later use
  registeredEmail = email;
  registeredUsername = username;
  
  // STEP 1: Show device registration confirmation FIRST
  showDeviceRegistrationConfirmation();
});

// ✅ FIXED: Proceed with registration AFTER user confirmation
async function proceedWithRegistration() {
  const submitBtn = document.getElementById('registerSubmit');
  
  try {
    // Close confirmation modal
    const confirmationModal = document.getElementById('deviceConfirmationModal');
    if (confirmationModal) {
      confirmationModal.remove();
    }
    document.body.style.overflow = '';
    
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<div class="spinner"></div>Checking device...';
    
    // STEP 2: Light system check (Layer 1) - NO DEVICE RECORDING YET
    if (!window.lightGuestAbusePreventionSystem) {
      showError('Device verification system not loaded. Please refresh the page.');
      return;
    }
    
    const lightCheck = await window.lightGuestUtils.preCheckRegistration(registeredEmail);
    if (!lightCheck.allowed) {
      const userFriendlyMessage = window.lightGuestUtils.showBlockedRegistrationMessage(lightCheck);
      showError(userFriendlyMessage);
      return;
    }
    
    submitBtn.innerHTML = '<div class="spinner"></div>Checking device fingerprint...';
    
    // STEP 3: Passkey system check (Layer 2) - NO DEVICE RECORDING YET
    let passkeyBlocked = false;
    let passkeyCredentialId = null;
    let webauthnAvailable = false;
    
    if (window.strictGuestAbusePreventionSystem?.isAvailable()) {
      webauthnAvailable = true;
      console.log('🔐 Running strict WebAuthn passkey check...');
      const passkeyCheck = await window.strictGuestAbusePreventionSystem.silentCredentialCheck();
      
      if (passkeyCheck.hasCredential) {
        passkeyBlocked = true;
        passkeyCredentialId = passkeyCheck.credentialId;
        showError('This device already has a trial passkey registered. Only one trial per device is allowed.');
        return;
      }
    }
    
    submitBtn.innerHTML = '<div class="spinner"></div>Getting device info...';
    
    // STEP 4: Get device data (for server validation only)
    const deviceData = await window.lightGuestUtils.getDeviceData();
    
    submitBtn.innerHTML = '<div class="spinner"></div>Requesting trial...';
    
    // STEP 5: Send registration request (NO DEVICE RECORDING YET!)
    const response = await fetch('/api/guest-trial/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: registeredUsername,
        email: registeredEmail,
        // Device data for server validation only
        ...deviceData,
        // Passkey info for server validation  
        webauthn_available: webauthnAvailable,
        passkey_check_passed: !passkeyBlocked,
        existing_passkey_id: passkeyCredentialId
      })
    });
    
    const data = await response.json();
    
    if (response.ok && data.status === 'success') {
      // Store OTP session info but DON'T mark trial as used yet
      currentOTPId = data.otp_id;
      
      openOtpModal();
      console.log('✅ Registration initiated - OTP sent (device NOT registered yet)');
    } else {
      // Handle server-side blocks
      if (data.detail) {
        if (data.detail.includes('device has already been used')) {
          showError('This device has already been used for a trial. Only one trial per device is allowed.');
        } else if (data.detail.includes('email has already been used')) {
          showError('This email has already been used for a trial. Please consider supporting on Ko-fi.');
        } else if (data.detail.includes('already registered')) {
          showError('This email is already registered. Please use the regular login.');
        } else if (data.detail.includes('passkey already registered')) {
          showError('This device already has a trial passkey registered. Only one trial per device is allowed.');
        } else {
          showError(data.detail);
        }
      } else {
        showError('Registration failed. Please try again.');
      }
    }
    
  } catch (error) {
    console.error('Registration error:', error);
    showError('Connection error. Please check your internet connection and try again.');
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<i class="fas fa-rocket"></i>Start Free Trial';
  }
}

// ✅ FIXED: OTP Verification - Uses new staged backend endpoints
document.getElementById('verifyOTPBtn').addEventListener('click', async function() {
  const verifyBtn = this;
  const otpCode = document.getElementById('otpCode').value.trim();
  
  if (!otpCode || otpCode.length !== 6 || !/^\d{6}$/.test(otpCode)) {
    showError('Please enter a valid 6-digit verification code', document.getElementById('otpModal').querySelector('.otp-modal'));
    return;
  }
  
  if (!currentOTPId) {
    showError('Session expired. Please start registration again.', document.getElementById('otpModal').querySelector('.otp-modal'));
    return;
  }
  
  verifyBtn.disabled = true;
  verifyBtn.innerHTML = '<div class="spinner"></div>Verifying...';
  
  try {
    // STEP 6: Verify OTP (staged - don't create user yet) - NEW ENDPOINT
    const response = await fetch('/api/guest-trial/verify-otp-staged', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        otp_code: otpCode,
        otp_id: currentOTPId
      })
    });
    
    const data = await response.json();
    
    if (!response.ok || data.status !== 'success') {
      showError(data.detail || data.message || 'Verification failed. Please try again.', document.getElementById('otpModal').querySelector('.otp-modal'));
      return;
    }
    
    console.log('✅ OTP verified successfully');
    
    // STEP 7: Create passkey if required (BEFORE device registration)
    let passkeyData = null;
    if (data.requires_passkey) {
      verifyBtn.innerHTML = '<div class="spinner"></div>Creating device passkey...';
      
      if (!window.strictGuestAbusePreventionSystem?.isAvailable()) {
        showError('Device security system not available. Please refresh and try again.', document.getElementById('otpModal').querySelector('.otp-modal'));
        return;
      }
      
      try {
        passkeyData = await window.strictGuestAbusePreventionSystem.createTrialPasskey({
          email: registeredEmail,
          username: registeredUsername
        });
        
        console.log('✅ Passkey created successfully');
        
      } catch (passkeyError) {
        console.error('❌ Passkey creation failed:', passkeyError);
        
        // IMPORTANT: Abort registration on server to prevent device blocking
        await abortRegistrationOnServer(currentOTPId, 'passkey_creation_failed');
        
        let errorMessage = 'You need to save the device passkey to claim your trial.';
        
        if (passkeyError.name === 'NotAllowedError') {
          errorMessage = 'You need to save the device passkey to claim your trial. Please try again and click "Continue" when prompted.';
        } else if (passkeyError.name === 'AbortError') {
          errorMessage = 'Passkey creation was cancelled. You need to save the device passkey to claim your trial.';
        } else if (passkeyError.name === 'NotSupportedError') {
          errorMessage = 'Passkeys are not supported on this device. Please try from a different device or browser.';
        } else if (passkeyError.message) {
          errorMessage = passkeyError.message;
        }
        
        showError(errorMessage, document.getElementById('otpModal').querySelector('.otp-modal'));
        return;
      }
    }
    
    // STEP 8: Complete registration atomically (user + device + passkey) - NEW ENDPOINT
    verifyBtn.innerHTML = '<div class="spinner"></div>Completing registration...';
    
    const finalizeResponse = await fetch('/api/guest-trial/complete-registration', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        otp_id: currentOTPId,
        passkey_data: passkeyData,
        register_device: true  // ✅ NOW register device atomically
      })
    });
    
    const finalizeData = await finalizeResponse.json();
    
    if (finalizeResponse.ok && finalizeData.status === 'success') {
      userPin = finalizeData.creator_pin || '123456';
      
      // ✅ ONLY NOW mark trial as used on client (after complete success)
      if (window.lightGuestUtils) {
        window.lightGuestUtils.markTrialUsed(registeredEmail);
      }
      
      openSuccessModal();
      console.log('✅ Guest trial registration completed with device protection');
      console.log(`✅ Device registered: ${finalizeData.device_registered}`);
      console.log(`✅ Passkey protected: ${finalizeData.passkey_protected}`);
      
    } else {
      // Final registration failed - abort to prevent device blocking
      await abortRegistrationOnServer(currentOTPId, 'completion_failed');
      showError(finalizeData.detail || finalizeData.message || 'Registration failed. Please try again.', document.getElementById('otpModal').querySelector('.otp-modal'));
    }
    
  } catch (error) {
    console.error('Verification error:', error);
    
    // Try to abort to prevent device blocking
    await abortRegistrationOnServer(currentOTPId, 'network_error');
    
    showError('Connection error. Please try again.', document.getElementById('otpModal').querySelector('.otp-modal'));
  } finally {
    verifyBtn.disabled = false;
    verifyBtn.innerHTML = '<i class="fas fa-check"></i>Verify Code';
  }
});

// ✅ NEW: Abort registration helper to prevent device blocking
async function abortRegistrationOnServer(otpId, reason) {
  try {
    await fetch('/api/guest-trial/abort-registration', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        otp_id: otpId,
        reason: reason
      })
    });
    console.log('🔄 Registration aborted on server to prevent device blocking');
  } catch (error) {
    console.error('Failed to abort registration:', error);
  }
}

// Resend OTP (unchanged)
document.getElementById('resendOTPBtn').addEventListener('click', async function() {
  const resendBtn = this;
  
  if (!currentOTPId) {
    showError('Session expired. Please start registration again.', document.getElementById('otpModal').querySelector('.otp-modal'));
    return;
  }
  
  resendBtn.disabled = true;
  resendBtn.textContent = 'Sending...';
  
  try {
    const response = await fetch('/api/pin/retrieve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        otp_id: currentOTPId
      })
    });
    
    const data = await response.json();
    
    if (response.ok && data.status === 'success') {
      showSuccess('New verification code sent!', document.getElementById('otpModal').querySelector('.otp-modal'));
    } else {
      showError(data.detail || data.message || 'Failed to resend code. Please try again.', document.getElementById('otpModal').querySelector('.otp-modal'));
    }
    
  } catch (error) {
    console.error('Resend error:', error);
    showError('Connection error. Please try again.', document.getElementById('otpModal').querySelector('.otp-modal'));
  } finally {
    resendBtn.disabled = false;
    resendBtn.textContent = 'Resend Code';
  }
});

// Ko-fi login (unchanged)
document.getElementById('kofiForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  
  const submitBtn = document.getElementById('kofiSubmit');
  const successDiv = document.getElementById('kofiSuccess');
  const email = document.getElementById('kofiEmail').value;
  const pin = document.getElementById('kofiPin').value;
  
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<div class="spinner"></div>Verifying device...';
  if (successDiv) successDiv.style.display = 'none';
  
  const existingErrors = document.querySelectorAll('.error-message:not([id])');
  existingErrors.forEach(error => error.remove());
  
  const pinVerification = document.querySelector('.pin-verification');
  if (pinVerification) {
    pinVerification.remove();
  }
  
  const emailInput = document.getElementById('kofiEmail');
  if (emailInput) emailInput.classList.remove('error');

  try {
    submitBtn.innerHTML = '<div class="spinner"></div>Checking device fingerprint...';
    
    let deviceFingerprint = null;
    if (window.strictGuestAbusePreventionSystem) {
      try {
        const deviceData = await window.strictGuestAbusePreventionSystem.getRequestData();
        deviceFingerprint = deviceData.device_fingerprint;
        console.log('✅ Device fingerprint obtained for login:', deviceFingerprint?.substring(0, 10) + '...');
      } catch (fpError) {
        console.warn('Device fingerprint generation failed:', fpError);
      }
    }
    
    submitBtn.innerHTML = '<div class="spinner"></div>Signing in...';
    
    let response = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        email: email,
        creator_pin: pin,
        login_type: 'kofi'
      })
    });

    if (response.ok) {
      if (response.redirected) {
        if (successDiv) {
          successDiv.textContent = 'Success! Redirecting...';
          successDiv.style.display = 'block';
        }
        setTimeout(() => window.location.href = response.url, 1000);
        return;
      }

      let data;
      try {
        data = await response.json();
      } catch (e) {
        if (successDiv) {
          successDiv.textContent = 'Success! Redirecting...';
          successDiv.style.display = 'block';
        }
        setTimeout(() => window.location.reload(), 1000);
        return;
      }
      
      if (!data.error) {
        if (successDiv) {
          successDiv.textContent = 'Success! Redirecting...';
          successDiv.style.display = 'block';
        }
        setTimeout(() => window.location.reload(), 1000);
        return;
      }
      
      console.log('Ko-fi login failed, trying trial login...');
    }
    
    try {
      const formData = new URLSearchParams({
        email: email,
        creator_pin: pin
      });
      
      if (deviceFingerprint) {
        formData.append('device_fingerprint', deviceFingerprint);
      }
      
      const trialResponse = await fetch('/api/guest-trial/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData
      });
      
      if (trialResponse.ok) {
        const contentType = trialResponse.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
          const trialData = await trialResponse.json();
          if (trialData.status === 'success') {
            console.log('✅ Trial login successful with device verification');
            if (successDiv) {
              successDiv.textContent = 'Success! Redirecting...';
              successDiv.style.display = 'block';
            }
            setTimeout(() => window.location.href = '/dashboard', 1000);
            return;
          } else {
            if (trialData.code === 'DEVICE_MISMATCH') {
              showErrorMessage('Trial access is restricted to the device you registered with. Please use the original device.', 'kofi');
            } else if (trialData.code === 'NO_STORED_FINGERPRINT') {
              showErrorMessage('Device verification failed. Please contact support.', 'kofi');
            } else {
              showErrorMessage(trialData.error || 'Login failed. Please check your credentials.', 'kofi');
            }
            return;
          }
        } else {
          if (successDiv) {
            successDiv.textContent = 'Success! Redirecting...';
            successDiv.style.display = 'block';
          }
          setTimeout(() => window.location.href = '/dashboard', 1000);
          return;
        }
      } else {
        const trialData = await trialResponse.json();
        if (trialData.code === 'DEVICE_MISMATCH') {
          showErrorMessage('Trial access is restricted to the device you registered with.', 'kofi');
        } else {
          showErrorMessage(trialData.error || 'Login failed. Please check your credentials.', 'kofi');
        }
        return;
      }
    } catch (trialError) {
      console.error('Trial login error:', trialError);
      showErrorMessage('Connection error. Please try again.', 'kofi');
      return;
    }

  } catch (error) {
    console.error('Login error:', error);
    showErrorMessage('Connection error. Please try again.', 'kofi');
  } finally {
    if (successDiv && !successDiv.style.display.includes('block')) {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="fas fa-sign-in-alt"></i>Sign In';
      }
    }
  }
});

// Close modals on outside click
window.addEventListener('click', function(e) {
  const modals = ['signInModal', 'otpModal', 'successModal', 'getPinModal', 'forgotPinModal', 'guideModal', 'deviceConfirmationModal'];
  modals.forEach(modalId => {
    const modal = document.getElementById(modalId);
    if (modal && e.target === modal) {
      if (modalId === 'signInModal') closeModal();
      else if (modalId === 'otpModal') closeOtpModal();
      else if (modalId === 'successModal') closeSuccessModal();
      else if (modalId === 'getPinModal') closeGetPinModal();
      else if (modalId === 'forgotPinModal') closeForgotPinModal();
      else if (modalId === 'guideModal') closeGuideModal();
      else if (modalId === 'deviceConfirmationModal') cancelDeviceRegistration();
    }
  });
});

// Auto-fill inputs on paste
document.getElementById('otpCode').addEventListener('paste', function(e) {
  setTimeout(() => {
    const value = this.value.replace(/\D/g, '').substring(0, 6);
    this.value = value;
  }, 10);
});

['kofiPin'].forEach(id => {
  document.getElementById(id).addEventListener('paste', function(e) {
    setTimeout(() => {
      const value = this.value.replace(/\D/g, '').substring(0, 6);
      this.value = value;
    }, 10);
  });
});

// Smooth scrolling
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
  anchor.addEventListener('click', function (e) {
    e.preventDefault();
    const target = document.querySelector(this.getAttribute('href'));
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
});

// Initialize both prevention systems when page loads
document.addEventListener('DOMContentLoaded', function() {
  // Initialize light system
  if (window.lightGuestAbusePreventionSystem) {
    window.lightGuestAbusePreventionSystem.init().then(() => {
      console.log('✅ Light abuse prevention system initialized');
    }).catch(error => {
      console.error('❌ Failed to initialize light prevention:', error);
    });
  }
  
  // Initialize strict system
  if (window.strictGuestAbusePreventionSystem) {
    window.strictGuestAbusePreventionSystem.init().then(() => {
      console.log('✅ Strict passkey prevention system initialized');
    }).catch(error => {
      console.error('❌ Failed to initialize passkey prevention:', error);
    });
  } else {
    console.warn('⚠️ Passkey prevention system not loaded - light prevention only');
  }
});

// Creator login form handler
document.getElementById('creatorForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  
  const submitBtn = document.getElementById('creatorSubmit');
  const errorDiv = document.getElementById('creatorError');
  const email = document.getElementById('creatorEmail').value;
  const password = document.getElementById('creatorPassword').value;
  
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<div class="spinner"></div>Signing in...';
  if (errorDiv) errorDiv.style.display = 'none';
  
  try {
    const response = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        email: email,
        password: password,
        login_type: 'creator'
      })
    });

    if (response.ok) {
      if (response.redirected) {
        window.location.href = response.url;
        return;
      }
      
      const data = await response.json();
      if (!data.error) {
        window.location.reload();
        return;
      }
      
      if (errorDiv) {
        errorDiv.textContent = data.error || 'Invalid credentials';
        errorDiv.style.display = 'block';
      }
    } else {
      if (errorDiv) {
        errorDiv.textContent = 'Login failed. Please check your credentials.';
        errorDiv.style.display = 'block';
      }
    }
  } catch (error) {
    console.error('Creator login error:', error);
    if (errorDiv) {
      errorDiv.textContent = 'Connection error. Please try again.';
      errorDiv.style.display = 'block';
    }
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<i class="fas fa-sign-in-alt"></i>Sign In as Creator';
  }
});

// Get PIN form handler
document.getElementById('getPinForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  
  const submitBtn = document.getElementById('getPinSubmit');
  const email = document.getElementById('getPinEmail').value;
  const resultDiv = document.getElementById('pinResult');
  const pinDisplay = document.getElementById('retrievedPin');
  
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<div class="spinner"></div>Retrieving...';
  resultDiv.style.display = 'none';
  
  try {
    const response = await fetch('/api/pin/retrieve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email })
    });
    
    const data = await response.json();
    
    if (response.ok && data.status === 'success') {
      pinDisplay.textContent = data.pin;
      resultDiv.style.display = 'block';
    } else {
      showError(data.detail || 'PIN not found for this email', document.querySelector('#getPinModal .modal'));
    }
    
  } catch (error) {
    console.error('Get PIN error:', error);
    showError('Connection error. Please try again.', document.querySelector('#getPinModal .modal'));
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<i class="fas fa-key"></i>Get My PIN';
  }
});

// Forgot PIN form handler
document.getElementById('forgotPinForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  
  const submitBtn = document.getElementById('forgotPinSubmit');
  const email = document.getElementById('forgotPinEmail').value;
  const resultDiv = document.getElementById('forgotPinResult');
  const errorDiv = document.getElementById('forgotPinError');
  const pinDisplay = document.getElementById('forgotRetrievedPin');
  
  submitBtn.disabled = true;
  submitBtn.innerHTML = '<div class="spinner"></div>Retrieving...';
  resultDiv.style.display = 'none';
  errorDiv.style.display = 'none';
  
  try {
    const response = await fetch('/api/pin/retrieve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email })
    });
    
    const data = await response.json();
    
    if (response.ok && data.status === 'success') {
      pinDisplay.textContent = data.pin;
      resultDiv.style.display = 'block';
    } else {
      errorDiv.textContent = data.detail || 'PIN not found for this email';
      errorDiv.style.display = 'block';
    }
    
  } catch (error) {
    console.error('Forgot PIN error:', error);
    errorDiv.textContent = 'Connection error. Please try again.';
    errorDiv.style.display = 'block';
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = '<i class="fas fa-key"></i>Get My PIN';
  }
});

// Additional utility functions for better UX
function validateEmail(email) {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return emailRegex.test(email);
}

function validateUsername(username) {
  if (!username || username.length < 2 || username.length > 30) {
    return false;
  }
  return /^[a-zA-Z0-9_\-\s]+$/.test(username);
}

// Enhanced form validation
document.getElementById('registerEmail').addEventListener('blur', function() {
  const email = this.value.trim();
  if (email && !validateEmail(email)) {
    this.classList.add('error');
    showError('Please enter a valid email address');
  } else {
    this.classList.remove('error');
  }
});

document.getElementById('registerUsername').addEventListener('blur', function() {
  const username = this.value.trim();
  if (username && !validateUsername(username)) {
    this.classList.add('error');
    showError('Username must be 2-30 characters and contain only letters, numbers, spaces, hyphens and underscores');
  } else {
    this.classList.remove('error');
  }
});

// Enhanced OTP input handling
document.getElementById('otpCode').addEventListener('input', function() {
  this.value = this.value.replace(/\D/g, '');
  if (this.value.length === 6) {
    // Auto-submit when 6 digits are entered
    setTimeout(() => {
      if (this.value.length === 6) {
        document.getElementById('verifyOTPBtn').click();
      }
    }, 500);
  }
});

// Enhanced PIN input handling
['kofiPin', 'creatorPassword'].forEach(id => {
  const element = document.getElementById(id);
  if (element) {
    element.addEventListener('input', function() {
      if (id === 'kofiPin') {
        this.value = this.value.replace(/\D/g, '').substring(0, 6);
      }
    });
  }
});

// Keyboard shortcuts for better UX
document.addEventListener('keydown', function(e) {
  // Escape key closes modals
  if (e.key === 'Escape') {
    const activeModal = document.querySelector('.modal-overlay.active');
    if (activeModal) {
      const modalId = activeModal.id;
      if (modalId === 'signInModal') closeModal();
      else if (modalId === 'otpModal') closeOtpModal();
      else if (modalId === 'successModal') closeSuccessModal();
      else if (modalId === 'getPinModal') closeGetPinModal();
      else if (modalId === 'forgotPinModal') closeForgotPinModal();
      else if (modalId === 'guideModal') closeGuideModal();
      else if (modalId === 'deviceConfirmationModal') cancelDeviceRegistration();
    }
  }
  
  // Enter key in OTP field triggers verification
  if (e.key === 'Enter' && e.target.id === 'otpCode') {
    e.preventDefault();
    document.getElementById('verifyOTPBtn').click();
  }
});

// Connection status monitoring
let isOnline = navigator.onLine;

window.addEventListener('online', function() {
  if (!isOnline) {
    isOnline = true;
    // Connection restored - no console spam on phone unlock
  }
});

window.addEventListener('offline', function() {
  if (isOnline) {
    isOnline = false;
    showError('Connection lost. Please check your internet connection.');
  }
});

// Performance monitoring for debugging
if (window.performance && window.performance.mark) {
  window.performance.mark('login_script_loaded');
}

// Enhanced error logging for debugging
window.addEventListener('error', function(e) {
  console.error('JavaScript error:', {
    message: e.message,
    filename: e.filename,
    line: e.lineno,
    column: e.colno,
    stack: e.error?.stack
  });
});

// Unhandled promise rejection logging
window.addEventListener('unhandledrejection', function(e) {
  console.error('Unhandled promise rejection:', e.reason);
});

console.log('✅ All login functions loaded successfully');