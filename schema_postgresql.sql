-- PostgreSQL Schema for GrantGuard
-- Converted from MySQL to PostgreSQL syntax

-- Enable UUID extension if needed
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ======================
-- USERS TABLE
-- ======================
CREATE TABLE IF NOT EXISTS users (
    user_id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE,
    role VARCHAR(20) CHECK (role IN ('PI', 'Admin', 'Finance')) NOT NULL DEFAULT 'PI',
    password VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ======================
-- AWARDS TABLE
-- ======================
CREATE TABLE IF NOT EXISTS awards (
    award_id SERIAL PRIMARY KEY,
    created_by_email VARCHAR(255),
    title VARCHAR(200) NOT NULL,
    sponsor VARCHAR(100),
    sponsor_type VARCHAR(50),
    amount DECIMAL(15,2),
    start_date DATE,
    end_date DATE,
    status VARCHAR(50) DEFAULT 'Draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_budget DECIMAL(12,2),
    pi_id INTEGER,
    department VARCHAR(255),
    college VARCHAR(255),
    contact_email VARCHAR(255),
    abstract TEXT,
    keywords VARCHAR(500),
    collaborators TEXT,
    budget_personnel DECIMAL(15,2),
    budget_equipment DECIMAL(15,2),
    budget_travel DECIMAL(15,2),
    budget_materials DECIMAL(15,2),
    CONSTRAINT awards_pi_id_fkey FOREIGN KEY (pi_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS awards_pi_id_idx ON awards(pi_id);

ALTER TABLE awards
ALTER COLUMN status SET DEFAULT 'Draft';

ALTER TABLE awards
  ADD COLUMN IF NOT EXISTS ai_review_notes TEXT;

-- ======================
-- POLICIES TABLE
-- ======================
CREATE TABLE IF NOT EXISTS policies (
    policy_id SERIAL PRIMARY KEY,
    policy_level VARCHAR(20)
        CHECK (policy_level IN ('University', 'Federal', 'Sponsor')) NOT NULL,
    source_name VARCHAR(100),
    policy_text TEXT
);

-- ======================
-- TRANSACTIONS TABLE
-- ======================
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id SERIAL PRIMARY KEY,
    award_id INTEGER,
    user_id INTEGER,
    category VARCHAR(100),
    description TEXT,
    amount DECIMAL(12,2),
    date_submitted DATE,
    status VARCHAR(20) DEFAULT 'Pending',
    CONSTRAINT transactions_status_check
        CHECK (status IN ('Pending', 'Approved', 'Declined')),
    CONSTRAINT transactions_award_id_fkey FOREIGN KEY (award_id)
        REFERENCES awards(award_id) ON DELETE CASCADE,
    CONSTRAINT transactions_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS transactions_award_id_idx ON transactions(award_id);
CREATE INDEX IF NOT EXISTS transactions_user_id_idx ON transactions(user_id);

-- ======================
-- BUDGET_LINES TABLE (generic)
-- ======================
CREATE TABLE IF NOT EXISTS budget_lines (
    line_id SERIAL PRIMARY KEY,
    award_id INTEGER,
    category VARCHAR(100),
    allocated_amount DECIMAL(12,2),
    spent_amount DECIMAL(12,2) DEFAULT 0.00,
    committed_amount DECIMAL(12,2) DEFAULT 0.00,
    CONSTRAINT budget_lines_award_id_fkey FOREIGN KEY (award_id)
        REFERENCES awards(award_id) ON DELETE CASCADE
);

-- Add committed_amount column if it doesn't exist
ALTER TABLE budget_lines
  ADD COLUMN IF NOT EXISTS committed_amount DECIMAL(12,2) DEFAULT 0.00;

CREATE INDEX IF NOT EXISTS budget_lines_award_id_idx ON budget_lines(award_id);

-- ======================
-- LLM_RESPONSES TABLE
-- ======================
CREATE TABLE IF NOT EXISTS llm_responses (
    response_id SERIAL PRIMARY KEY,
    transaction_id INTEGER,
    llm_decision VARCHAR(30)
        CHECK (llm_decision IN ('Allow', 'Allow with Prior Approval', 'Disallow')),
    reason TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT llm_responses_transaction_id_fkey FOREIGN KEY (transaction_id)
        REFERENCES transactions(transaction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS llm_responses_transaction_id_idx
    ON llm_responses(transaction_id);

-- ======================
-- ADMIN USER SEED
-- ======================
INSERT INTO users (name, email, role, password)
VALUES ('Admin User', 'admin@example.com', 'Admin', 'adminpassword')
ON CONFLICT (email) DO NOTHING;

--------------------------------------------------------------------------------
-- NEW TABLES FOR DETAILED BUDGET SECTIONS (Personnel, Travel, Materials/Supplies)
--------------------------------------------------------------------------------

-- 1) PERSONNEL EXPENSE INFORMATION
-- Each row = one person on a given award.
CREATE TABLE IF NOT EXISTS personnel_expenses (
    personnel_id SERIAL PRIMARY KEY,
    award_id INTEGER NOT NULL,
    person_name VARCHAR(200) NOT NULL,
    position_title VARCHAR(200),
    hours_for_years DECIMAL(10,2),          -- "Hours for year(s)" field
    same_each_year BOOLEAN DEFAULT FALSE,   -- checkbox
    year_start INTEGER,                     -- optional: first year (e.g., 1,2,3)
    year_end INTEGER,                       -- optional: last year if spans multiple
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT personnel_award_id_fkey FOREIGN KEY (award_id)
        REFERENCES awards(award_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS personnel_award_id_idx
    ON personnel_expenses(award_id);

-- 2) TRAVEL EXPENSES INFORMATION
-- One table for both Domestic and International. travel_type distinguishes them.
CREATE TABLE IF NOT EXISTS travel_expenses (
    travel_id SERIAL PRIMARY KEY,
    award_id INTEGER NOT NULL,
    travel_type VARCHAR(20) CHECK (travel_type IN ('Domestic', 'International')) NOT NULL,
    travel_name VARCHAR(255),
    description TEXT,
    year INTEGER,                           -- "Select Year"
    start_date DATE,
    end_date DATE,
    flight_cost DECIMAL(12,2),             -- "Flight $"
    taxi_per_day DECIMAL(12,2),            -- "Taxi/Uber $/day"
    food_lodge_per_day DECIMAL(12,2),      -- "Food & Lodge $/day"
    num_days INTEGER,                       -- "Days"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT travel_award_id_fkey FOREIGN KEY (award_id)
        REFERENCES awards(award_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS travel_award_id_idx
    ON travel_expenses(award_id);

-- 3) MATERIALS AND SUPPLIES
CREATE TABLE IF NOT EXISTS material_supplies (
    material_id SERIAL PRIMARY KEY,
    award_id INTEGER NOT NULL,
    material_type VARCHAR(255),            -- dropdown "Select Material or Supply"
    cost DECIMAL(12,2),                    -- "Enter Cost"
    description TEXT,                      -- "Enter Description"
    year INTEGER,                          -- "Select Year"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT material_award_id_fkey FOREIGN KEY (award_id)
        REFERENCES awards(award_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS material_award_id_idx
    ON material_supplies(award_id);
-- Extra JSONB columns on awards to store detailed budget sections
ALTER TABLE awards
  ADD COLUMN IF NOT EXISTS personnel_json JSONB;

ALTER TABLE awards
  ADD COLUMN IF NOT EXISTS domestic_travel_json JSONB;

ALTER TABLE awards
  ADD COLUMN IF NOT EXISTS international_travel_json JSONB;

ALTER TABLE awards
  ADD COLUMN IF NOT EXISTS materials_json JSONB;

-- ======================
-- SUBAWARDS TABLE
-- ======================
CREATE TABLE IF NOT EXISTS subawards (
    subaward_id SERIAL PRIMARY KEY,
    award_id INTEGER NOT NULL,
    subrecipient_name VARCHAR(200) NOT NULL,
    subrecipient_contact VARCHAR(255),
    subrecipient_email VARCHAR(255),
    amount DECIMAL(15,2) NOT NULL,
    start_date DATE,
    end_date DATE,
    description TEXT,
    status VARCHAR(50) DEFAULT 'Pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by_email VARCHAR(255),
    CONSTRAINT subawards_award_id_fkey FOREIGN KEY (award_id)
        REFERENCES awards(award_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS subawards_award_id_idx ON subawards(award_id);
CREATE INDEX IF NOT EXISTS subawards_status_idx ON subawards(status);

-- Budget lines for subawards (similar to main awards)
CREATE TABLE IF NOT EXISTS subaward_budget_lines (
    line_id SERIAL PRIMARY KEY,
    subaward_id INTEGER NOT NULL,
    category VARCHAR(100),
    allocated_amount DECIMAL(12,2),
    spent_amount DECIMAL(12,2) DEFAULT 0.00,
    committed_amount DECIMAL(12,2) DEFAULT 0.00,
    CONSTRAINT subaward_budget_lines_subaward_id_fkey FOREIGN KEY (subaward_id)
        REFERENCES subawards(subaward_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS subaward_budget_lines_subaward_id_idx ON subaward_budget_lines(subaward_id);

-- Transactions for subawards
CREATE TABLE IF NOT EXISTS subaward_transactions (
    transaction_id SERIAL PRIMARY KEY,
    subaward_id INTEGER NOT NULL,
    user_id INTEGER,
    category VARCHAR(100),
    description TEXT,
    amount DECIMAL(12,2),
    date_submitted DATE,
    status VARCHAR(20) DEFAULT 'Pending',
    CONSTRAINT subaward_transactions_status_check
        CHECK (status IN ('Pending', 'Approved', 'Declined')),
    CONSTRAINT subaward_transactions_subaward_id_fkey FOREIGN KEY (subaward_id)
        REFERENCES subawards(subaward_id) ON DELETE CASCADE,
    CONSTRAINT subaward_transactions_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS subaward_transactions_subaward_id_idx ON subaward_transactions(subaward_id);
CREATE INDEX IF NOT EXISTS subaward_transactions_user_id_idx ON subaward_transactions(user_id);
