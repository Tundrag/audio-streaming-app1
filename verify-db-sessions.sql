-- Database Session Health Check Script
-- Run after deploying fixes to verify session leak resolution
-- Usage: psql -h localhost -U $DB_USER -d audio_streaming_db -f verify-db-sessions.sql

\echo '========================================='
\echo 'DATABASE SESSION HEALTH CHECK'
\echo '========================================='
\echo ''

-- 1. Long-lived idle transactions (SHOULD BE ZERO)
\echo '1. LONG-LIVED IDLE TRANSACTIONS (Should be ZERO or very few < 5 seconds old)'
\echo '----------------------------------------------------------------------'
SELECT
    pid,
    state,
    NOW() - state_change AS idle_duration,
    NOW() - xact_start AS transaction_duration,
    LEFT(query, 100) AS query_preview
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db'
  AND state = 'idle in transaction'
  AND NOW() - state_change > interval '30 seconds'
ORDER BY state_change;

\echo ''
\echo '2. CONNECTION COUNT BY STATE'
\echo '----------------------------------------------------------------------'
SELECT state, COUNT(*) as count
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db'
GROUP BY state
ORDER BY count DESC;

\echo ''
\echo '3. TOTAL CONNECTION POOL UTILIZATION'
\echo '----------------------------------------------------------------------'
SELECT
    COUNT(*) as total_connections,
    COUNT(*) FILTER (WHERE state = 'active') as active,
    COUNT(*) FILTER (WHERE state = 'idle') as idle,
    COUNT(*) FILTER (WHERE state = 'idle in transaction') as idle_in_transaction,
    ROUND(100.0 * COUNT(*) FILTER (WHERE state = 'idle in transaction') / NULLIF(COUNT(*), 0), 2) as idle_in_tx_percent
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db';

\echo ''
\echo '4. LONGEST RUNNING QUERIES'
\echo '----------------------------------------------------------------------'
SELECT
    pid,
    state,
    NOW() - query_start AS query_duration,
    LEFT(query, 80) AS query_preview
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db'
  AND state = 'active'
  AND query NOT LIKE '%pg_stat_activity%'
ORDER BY query_start
LIMIT 10;

\echo ''
\echo '5. CONNECTIONS PER APPLICATION'
\echo '----------------------------------------------------------------------'
SELECT
    application_name,
    COUNT(*) as connection_count,
    COUNT(*) FILTER (WHERE state = 'idle in transaction') as idle_in_tx
FROM pg_stat_activity
WHERE datname = 'audio_streaming_db'
GROUP BY application_name
ORDER BY connection_count DESC;

\echo ''
\echo '========================================='
\echo 'EXPECTED RESULTS AFTER FIXES:'
\echo '========================================='
\echo '1. Zero (or very few, < 5 sec old) "idle in transaction" sessions'
\echo '2. Total connections < 100 even under load'
\echo '3. idle_in_tx_percent < 1%'
\echo '4. No queries running for more than a few minutes'
\echo '========================================='
