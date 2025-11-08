-- WebSocket Session Health Monitor
-- Run every 5 minutes post-deployment for first 24 hours
-- Usage: psql -h localhost -U $DB_USER -d audio_streaming_db -f monitor-websocket-sessions.sql

\echo '=== WebSocket Session Health Monitor ==='
\echo ''
\echo 'Report generated at:' `date`
\echo ''

-- Section 1: Critical Leaks (should be ZERO)
\echo '=== CRITICAL: Long-Lived Idle Sessions (should be ZERO) ==='
SELECT
    pid,
    state,
    NOW() - state_change AS age,
    LEFT(query, 100) AS query_preview,
    application_name
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db'
  AND state = 'idle in transaction'
  AND NOW() - state_change > interval '1 minute'
ORDER BY age DESC
LIMIT 10;

\echo ''

-- Section 2: Connection Pool Health
\echo '=== Connection Pool Summary ==='
SELECT
    state,
    COUNT(*) as count,
    ROUND(AVG(EXTRACT(EPOCH FROM (NOW() - state_change))), 2) as avg_age_seconds,
    MAX(NOW() - state_change) as max_age
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db'
GROUP BY state
ORDER BY count DESC;

\echo ''

-- Section 3: Alert Conditions
\echo '=== ALERTS ==='
SELECT
    CASE
        WHEN idle_in_transaction_count > 5 THEN '🚨 CRITICAL: Idle in transaction count HIGH (' || idle_in_transaction_count || ' sessions)'
        WHEN max_idle_age > interval '30 seconds' THEN '⚠️  WARNING: Long idle in transaction detected (max: ' || max_idle_age || ')'
        WHEN total_connections > 700 THEN '⚠️  WARNING: Connection count approaching limit (' || total_connections || '/750)'
        WHEN total_connections > 500 THEN '⚠️  INFO: Connection count elevated (' || total_connections || '/750)'
        ELSE '✅ OK: All metrics normal (' || total_connections || ' connections, ' || idle_in_transaction_count || ' idle in transaction)'
    END as alert_status
FROM (
    SELECT
        COUNT(*) FILTER (WHERE state = 'idle in transaction') as idle_in_transaction_count,
        MAX(NOW() - state_change) FILTER (WHERE state = 'idle in transaction') as max_idle_age,
        COUNT(*) as total_connections
    FROM pg_stat_activity
    WHERE datname = 'audio_streaming_db'
) alerts;

\echo ''

-- Section 4: Recent Activity (last 30 seconds)
\echo '=== Recent Query Activity (last 30 seconds) ==='
SELECT
    state,
    COUNT(*) as queries_executed
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db'
  AND state_change > NOW() - interval '30 seconds'
GROUP BY state
ORDER BY queries_executed DESC;

\echo ''

-- Section 5: Long-Running Queries (potential issues)
\echo '=== Long-Running Queries (>5 seconds, potential issues) ==='
SELECT
    pid,
    state,
    NOW() - query_start AS query_duration,
    LEFT(query, 100) AS query_preview
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db'
  AND state != 'idle'
  AND NOW() - query_start > interval '5 seconds'
ORDER BY query_duration DESC
LIMIT 5;

\echo ''
\echo '=== End of Report ==='
