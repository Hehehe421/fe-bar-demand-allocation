-- Stage D — native OLTP schema the app writes to.
--
-- These are NOT mirrors of Delta tables. The synced tables (public.*) are the
-- read path; everything here is write-first transactional state that has no
-- upstream in the lakehouse, because the decision does not exist until a
-- planner makes it. That is the whole point of the stage: today the allocation
-- decision is made verbally in a Monday meeting and never recorded, so nobody
-- can measure whether planner overrides beat the model.
--
-- Written as idempotent DDL so provision.py can be re-run.

CREATE SCHEMA IF NOT EXISTS allocation;

-- ---------------------------------------------------------------------------
-- allocation_decisions: one row per decision a planner commits in the app
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS allocation.allocation_decisions (
    decision_id        BIGSERIAL    PRIMARY KEY,
    sku_id             TEXT         NOT NULL,
    retailer_code      TEXT         NOT NULL,
    week_end_date      DATE         NOT NULL,
    -- what the deterministic allocator proposed
    recommended_units  INTEGER      NOT NULL CHECK (recommended_units >= 0),
    -- what the planner actually committed; differs from recommended on override
    planner_units      INTEGER      NOT NULL CHECK (planner_units >= 0),
    action             TEXT         NOT NULL
                       CHECK (action IN ('accept', 'override', 'defer')),
    planner_id         TEXT         NOT NULL,
    rationale_text     TEXT,
    -- links the decision back to the agent run that informed it, so the brief
    -- the planner read is recoverable from the decision record
    agent_trace_id     TEXT,
    -- carried so accuracy can be attributed to the model version in force
    model_version      INTEGER,
    severity_score     NUMERIC(6, 2),
    units_at_risk      INTEGER,
    revenue_at_risk_usd NUMERIC(14, 2),
    decided_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- override magnitude, generated so it cannot drift from its inputs
    override_delta_units INTEGER GENERATED ALWAYS AS
                         (planner_units - recommended_units) STORED
);

COMMENT ON TABLE allocation.allocation_decisions IS
'Operational record of planner allocation decisions. Write-first: has no
upstream Delta source, because the decision does not exist until it is made.
Synced back into Unity Catalog as gold.fct_allocation_decisions so the decision
is auditable, joinable to outcomes and answerable in Genie. SYNTHETIC DATA.';

COMMENT ON COLUMN allocation.allocation_decisions.override_delta_units IS
'planner_units - recommended_units. Positive means the planner shipped more than
the model proposed. This is the column that makes "do overrides beat the model"
a measurable question rather than an opinion.';

-- The app's Monday worklist reads by week and retailer; the index matches.
CREATE INDEX IF NOT EXISTS ix_decisions_week_retailer
    ON allocation.allocation_decisions (week_end_date DESC, retailer_code);
CREATE INDEX IF NOT EXISTS ix_decisions_sku
    ON allocation.allocation_decisions (sku_id);
-- partial index: overrides are the rare, interesting case
CREATE INDEX IF NOT EXISTS ix_decisions_overrides
    ON allocation.allocation_decisions (decided_at DESC)
    WHERE action = 'override';

-- ---------------------------------------------------------------------------
-- agent_interactions: every agent call made from the app
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS allocation.agent_interactions (
    interaction_id     BIGSERIAL    PRIMARY KEY,
    sku_id             TEXT,
    retailer_code      TEXT,
    planner_id         TEXT         NOT NULL,
    question           TEXT         NOT NULL,
    answer             TEXT,
    tools_called       TEXT[],
    trace_id           TEXT,
    llm_endpoint       TEXT,
    prompt_provenance  TEXT,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    latency_ms         INTEGER,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE allocation.agent_interactions IS
'Every agent invocation made from the app, with the resolved LLM endpoint, the
prompt provenance, the tools called and the token cost. Lets agent usage and
spend be attributed per planner and per SKU, and lets a decision be traced back
to the brief that informed it. SYNTHETIC DATA.';

CREATE INDEX IF NOT EXISTS ix_interactions_created
    ON allocation.agent_interactions (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_interactions_sku
    ON allocation.agent_interactions (sku_id, retailer_code);
