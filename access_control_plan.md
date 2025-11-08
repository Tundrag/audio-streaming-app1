# Access Control Notes

## Observations

- Album tier restrictions live on `Album.tier_restrictions`, track overrides on `Track.tier_requirements`, and voice entitlements flow through `CampaignTier.voice_access` plus `check_voice_access`.
- Segment handlers currently call `check_tier_access` on every `.ts` request, causing repeated DB work; frontend also calls `/api/tracks/{id}/check-access`, so the logic is duplicated.
- Logs from 2025-02-18 show master/playlist requests but **no segment fetches**, suggesting playback stalls before media fragments are requested; no 401/403 were reported on HLS endpoints.

## Proposed Solution Outline

1. **Unified evaluator:** extract a single helper (e.g., `AuthorizationService.evaluate_access(user, track, voice_id)`) that covers album, track, and voice rules and returns a structured result + reason.
2. **Grant token/cache:** when playlist or metadata endpoints approve playback, issue a signed token or Redis entry keyed by `(session_id, track_id, voice_id)` with a short TTL (~10 min) and the current `content_version`.
3. **Segment validation:** require the frontend to include the grant token (alongside the existing `stream_id`) on segment requests. Segment routes validate the token; only on cache miss do they re-run `evaluate_access`.
4. **Invalidation:** creator actions that edit album/track restrictions or tier voice lists trigger cache invalidation (bump `content_version` or delete Redis keys) so stale grants are refused.
5. **Frontend updates:** persistent player stores the grant token returned by `/api/tracks/{id}/check-access` / metadata calls and forwards it on playlist + segment requests; voice switches request fresh tokens.

## Next Actions (paused)

- Implement `authorization.py` service and refactor routes to use it.
- Add Redis-backed grant store + invalidation hooks.
- Update HLS routes and frontend once tests confirm segment fetches resume.
