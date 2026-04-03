-- =============================================================================
-- views.sql — Vistas de análisis de tokens para PACI Workflow
--
-- Aplica con:
--   python dashboard.py --create-views
-- o directamente en psql/Neon console.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- v_session_token_usage
-- Agrega tokens de todos los eventos con usage_metadata por sesión.
-- Incluye desglose por agente y status inferido desde session.state.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_session_token_usage AS
WITH agent_tokens AS (
    SELECT
        e.session_id,
        e.event_data->>'author'                                                        AS agent,
        SUM((e.event_data->'usage_metadata'->>'prompt_token_count')::bigint)           AS input_tokens,
        SUM((e.event_data->'usage_metadata'->>'candidates_token_count')::bigint)       AS output_tokens,
        SUM((e.event_data->'usage_metadata'->>'total_token_count')::bigint)            AS total_tokens,
        COUNT(*)                                                                        AS calls
    FROM events e
    WHERE e.app_name = 'paci_workflow'
      AND (e.event_data->'usage_metadata') IS NOT NULL
      AND (e.event_data->'usage_metadata'->>'total_token_count') IS NOT NULL
      AND (e.event_data->'usage_metadata'->>'total_token_count')::bigint > 0
      AND e.event_data->>'author' IS NOT NULL
      AND e.event_data->>'author' NOT IN ('user', 'PaciWorkflow')
    GROUP BY e.session_id, agent
),

session_agg AS (
    SELECT
        session_id,
        SUM(input_tokens)   AS input_tokens,
        SUM(output_tokens)  AS output_tokens,
        SUM(total_tokens)   AS total_tokens,
        jsonb_object_agg(
            agent,
            jsonb_build_object(
                'total_tokens',  total_tokens,
                'input_tokens',  input_tokens,
                'output_tokens', output_tokens,
                'calls',         calls
            )
        ) AS by_agent
    FROM agent_tokens
    GROUP BY session_id
)

SELECT
    s.id                                                                AS session_id,
    s.create_time,
    s.update_time,
    -- Status: explícito en state, o inferido por presencia de outputs
    CASE
        WHEN s.state->>'status' IN ('success', 'fail', 'timeout')     THEN s.state->>'status'
        WHEN (s.state->>'rubrica') IS NOT NULL
         AND length(s.state->>'rubrica') > 100                         THEN 'success'
        ELSE 'unknown'
    END                                                                 AS status,
    COALESCE(a.input_tokens,  0)                                        AS input_tokens,
    COALESCE(a.output_tokens, 0)                                        AS output_tokens,
    COALESCE(a.total_tokens,  0)                                        AS total_tokens,
    COALESCE(a.by_agent, '{}'::jsonb)                                   AS by_agent
FROM sessions s
LEFT JOIN session_agg a ON a.session_id = s.id
WHERE s.app_name = 'paci_workflow'
ORDER BY s.create_time DESC;


-- -----------------------------------------------------------------------------
-- v_session_token_usage_mes
-- Filtra v_session_token_usage al mes calendario actual.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_session_token_usage_mes AS
SELECT *
FROM v_session_token_usage
WHERE create_time >= date_trunc('month', CURRENT_TIMESTAMP);
