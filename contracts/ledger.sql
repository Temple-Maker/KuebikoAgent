-- KuebikoAgent findings ledger, schema version 0.1.
--
-- SQLite dialect. Times are UTC text in ISO 8601 form ending in Z.
-- Columns ending in _json hold JSON text.
--
-- A finding record (contracts/finding.schema.json) is an observation and never
-- changes. This ledger holds its lifecycle: which run saw it, which fix was
-- proposed, and who or what confirmed the fix.
--
-- Status rules for findings.status:
--   open           first seen, nothing proposed
--   fix-proposed   a row in fixes with state 'proposed'
--   fixed          that fix is 'merged' AND a 'rescan' verification passed
--   verified       a 'tester-session' verification passed after the fix.
--                  Scripted and self-run sessions are synthetic and never
--                  move a finding to 'verified'.
--   false-positive a person rejected the finding
--   wont-fix       a person accepted the barrier, with a reason in the issue

PRAGMA foreign_keys = ON;

CREATE TABLE schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO schema_meta (key, value) VALUES ('schema_version', '0.1');

CREATE TABLE runs (
  run_id        TEXT PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('scan', 'audit', 'eval', 'verify')),
  app           TEXT NOT NULL,
  target_url    TEXT,
  target_repo   TEXT,
  target_commit TEXT,
  profile       TEXT NOT NULL DEFAULT 'wcag22-aa',
  tool_version  TEXT NOT NULL,
  model         TEXT,
  cost_usd      REAL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  summary_json  TEXT            -- the run.json content described in contracts/cli.md
);

CREATE TABLE findings (
  id              TEXT PRIMARY KEY,   -- fingerprint from kuebiko.contracts.finding_id
  first_run_id    TEXT NOT NULL REFERENCES runs (run_id),
  last_run_id     TEXT NOT NULL REFERENCES runs (run_id),
  app             TEXT NOT NULL,
  page            TEXT NOT NULL,      -- URL path, query and fragment, without host or port
  selector        TEXT NOT NULL DEFAULT '',
  criterion_id    TEXT NOT NULL,
  criterion_level TEXT NOT NULL CHECK (criterion_level IN ('A', 'AA', 'AAA')),
  check_id        TEXT NOT NULL,
  source          TEXT NOT NULL CHECK (source IN ('rule', 'agent', 'tester')),
  outcome         TEXT NOT NULL CHECK (outcome IN ('failed', 'cantTell')),
  confidence      TEXT NOT NULL CHECK (confidence IN ('confirmed', 'judged', 'needs-person')),
  severity        TEXT NOT NULL CHECK (severity IN ('blocker', 'serious', 'moderate', 'minor')),
  summary         TEXT NOT NULL,
  record_json     TEXT NOT NULL,      -- the latest full finding record
  status          TEXT NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open', 'fix-proposed', 'fixed', 'verified', 'wont-fix', 'false-positive')),
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE INDEX findings_by_app_status ON findings (app, status);

-- Rule, agent and tester findings about the same place share these four values.
CREATE INDEX findings_by_place ON findings (app, page, selector, criterion_id);

CREATE TABLE fixes (
  id                 INTEGER PRIMARY KEY,
  finding_id         TEXT NOT NULL REFERENCES findings (id),
  repo               TEXT NOT NULL,
  branch             TEXT NOT NULL,
  pr_url             TEXT,
  commit_sha         TEXT,
  state              TEXT NOT NULL DEFAULT 'proposed' CHECK (state IN ('proposed', 'merged', 'rejected')),
  regression_test    TEXT,           -- path of the test added with the fix
  guard_results_json TEXT,           -- result of each guard in docs/gaming-patterns.md
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);

CREATE INDEX fixes_by_finding ON fixes (finding_id);

-- A session holds no names and no contact details. A tester who agrees to
-- re-test a fix comes back with the session code; nobody contacts them.
CREATE TABLE sessions (
  session_code TEXT PRIMARY KEY,
  app          TEXT NOT NULL,
  task         TEXT NOT NULL,
  setup_json   TEXT NOT NULL,        -- JSON array, for example ["NVDA on Windows"]
  result       TEXT NOT NULL CHECK (result IN ('completed', 'completed-with-difficulty', 'blocked')),
  synthetic    INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
  consent      INTEGER NOT NULL CHECK (consent IN (0, 1)),
  follow_up_ok INTEGER NOT NULL DEFAULT 0 CHECK (follow_up_ok IN (0, 1)),
  created_at   TEXT NOT NULL
);

CREATE TABLE verifications (
  id           INTEGER PRIMARY KEY,
  finding_id   TEXT NOT NULL REFERENCES findings (id),
  fix_id       INTEGER REFERENCES fixes (id),
  kind         TEXT NOT NULL CHECK (kind IN ('rescan', 'scripted-session', 'self-session', 'tester-session')),
  result       TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
  run_id       TEXT REFERENCES runs (run_id),
  session_code TEXT REFERENCES sessions (session_code),
  created_at   TEXT NOT NULL,
  CHECK (kind <> 'tester-session' OR session_code IS NOT NULL)
);

CREATE INDEX verifications_by_finding ON verifications (finding_id);

-- A scripted or self-run session is synthetic and never counts as a tester
-- (AGENTS.md rule 8), so it cannot back a 'tester-session' verification.
CREATE TRIGGER verifications_tester_not_synthetic_insert
BEFORE INSERT ON verifications
WHEN NEW.kind = 'tester-session'
  AND (SELECT synthetic FROM sessions WHERE session_code = NEW.session_code) = 1
BEGIN
  SELECT RAISE(ABORT, 'a synthetic session cannot be a tester-session verification');
END;

CREATE TRIGGER verifications_tester_not_synthetic_update
BEFORE UPDATE OF kind, session_code ON verifications
WHEN NEW.kind = 'tester-session'
  AND (SELECT synthetic FROM sessions WHERE session_code = NEW.session_code) = 1
BEGIN
  SELECT RAISE(ABORT, 'a synthetic session cannot be a tester-session verification');
END;

-- The same rule seen from the other side: a session that already backs a
-- 'tester-session' verification cannot be relabelled synthetic afterwards.
CREATE TRIGGER sessions_stay_real_once_verified
BEFORE UPDATE OF synthetic ON sessions
WHEN NEW.synthetic = 1
  AND EXISTS (SELECT 1 FROM verifications
              WHERE kind = 'tester-session' AND session_code = OLD.session_code)
BEGIN
  SELECT RAISE(ABORT, 'a session backing a tester-session verification cannot become synthetic');
END;
