# Configuration Migration Plan

**Goal:** Centralize all constants and configuration into `config/` directory
**Approach:** One module at a time, test after each step
**Status:** 🟡 In Progress

---

## 📋 Migration Checklist

### Phase 1: URL Constants (START HERE)
- [ ] **Step 1.1:** Keep existing `config/urls.py` (already created)
- [ ] **Step 1.2:** Update `app.py` to import from `config.urls`
- [ ] **Step 1.3:** Update `enhanced_app_routes_voice.py` to import from `config.urls`
- [ ] **Step 1.4:** Remove old URL constants from both files
- [ ] **Step 1.5:** Test application startup
- [ ] **Step 1.6:** Test media/static URLs work correctly

**Files to modify:**
- `app.py` (lines ~1249-1252)
- `enhanced_app_routes_voice.py` (lines ~34-35)

**Impact:** Low risk, high visibility improvement

---

### Phase 2: TTS Limits
- [ ] **Step 2.1:** Keep existing `config/limits.py` (already created)
- [ ] **Step 2.2:** Update `enhanced_tts_voice_service.py` to import from `config.limits`
- [ ] **Step 2.3:** Replace hardcoded limits with `TTS.GLOBAL_MAX_SLOTS`, `TTS.PER_USER_CAP`
- [ ] **Step 2.4:** Test TTS generation works
- [ ] **Step 2.5:** Verify semaphore limits are correct

**Files to modify:**
- `enhanced_tts_voice_service.py` (lines ~39-49)

**Impact:** Medium risk (affects core TTS functionality)

---

### Phase 3: TTL Constants
- [ ] **Step 3.1:** Keep existing `config/ttl.py` (already created)
- [ ] **Step 3.2:** Update `authorization_service.py` to use `GRANT_TOKEN_TTL`
- [ ] **Step 3.3:** Update Redis state files one by one:
  - [ ] `redis_state/state/upload.py`
  - [ ] `redis_state/cache/text.py`
  - [ ] `redis_state/cache/word_timing.py`
  - [ ] (continue with others as needed)
- [ ] **Step 3.4:** Test token expiration
- [ ] **Step 3.5:** Test cache invalidation

**Files to modify:**
- `authorization_service.py` (line 22)
- `redis_state/` files (15+ files)

**Impact:** Medium risk (affects caching and auth)

---

### Phase 4: File Paths
- [ ] **Step 4.1:** Keep existing `config/paths.py` (already created)
- [ ] **Step 4.2:** Gradually replace hardcoded paths in services
- [ ] **Step 4.3:** Test file uploads/downloads
- [ ] **Step 4.4:** Test document extraction

**Impact:** Low risk (most are duplicates anyway)

---

### Phase 5: General Constants
- [ ] **Step 5.1:** Keep existing `config/constants.py` (already created)
- [ ] **Step 5.2:** Update `UNLIMITED_VALUE` usage in `app.py`
- [ ] **Step 5.3:** Update `DEFAULT_BITRATE` usage
- [ ] **Step 5.4:** Update `PLATFORM_TYPES` in `platform_router.py`

**Impact:** Low risk

---

### Phase 6: Settings (Environment Variables)
- [ ] **Step 6.1:** Keep existing `config/settings.py` (already created)
- [ ] **Step 6.2:** Gradually migrate env var access to use `settings` object
- [ ] **Step 6.3:** This is OPTIONAL - can be done later

**Impact:** Low priority (can wait)

---

## 🎯 Recommended Order

**Start with Phase 1 (URLs)** - It's the simplest and most visible:
1. URLs are constants (never change)
2. Only 2 files to modify
3. Easy to test
4. Quick win!

**Then Phase 2 (TTS Limits)** - Second easiest:
1. Only 1 file to modify
2. Clear improvement
3. Easy to validate

**Then Phase 3, 4, 5 as needed**

---

## ✅ Success Criteria

After each phase:
- [ ] Application starts without errors
- [ ] No import errors
- [ ] Feature still works (test the specific feature)
- [ ] No regression in other features

---

## 🚨 Rollback Plan

If something breaks:
1. Git revert the changes
2. Or: Comment out the import and restore old constants
3. Test works again
4. Fix issue, try again

---

## 📝 Notes

- **URL prefixes ARE constants** - They never change between dev/staging/prod
  - `/media` is always `/media`
  - `/api` is always `/api`
  - Production servers don't use different URLs

- **Settings vs Constants:**
  - **Constants:** Values that NEVER change (URLs, limits, magic numbers)
  - **Settings:** Values that change per environment (DB credentials, API keys)

---

## 🎬 Let's Start!

**Ready to begin Phase 1 (URLs)?**
- Simplest migration
- Only 2 files
- 5 minute task
- Immediate improvement
