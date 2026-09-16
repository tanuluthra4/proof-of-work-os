PRAGMA foreign_keys = ON;

-- USERS

CREATE TABLE if NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- PROFILES

CREATE TABLE if NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    full_name TEXT,
    headline TEXT,
    bio TEXT,
    location TEXT,
    github_username TEXT,
    linkedin_url TEXT,
    portfolio_url TEXT,
    profile_picture TEXT,
    profile_visibility TEXT DEFAULT 'private',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

--- PROJECTS

CREATE TABLE if NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active',
    start_date TEXT,
    end_date TEXT,
    github_url TEXT,
    live_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

--- TASKS

CREATE TABLE if NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending',
    priority TEXT DEFAULT 'medium',
    due_date TEXT,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

--- SKILLS

CREATE TABLE if NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT DEFAULT 'other',
    description TEXT
);

--- PROJECT_SKILLS

CREATE TABLE if NOT EXISTS project_skills (
    project_id INTEGER NOT NULL,
    skill_id INTEGER NOT NULL,
    PRIMARY KEY (project_id, skill_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
);


--- EVIDENCE

CREATE TABLE if NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER,
    task_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    evidence_type TEXT NOT NULL,
    evidence_url TEXT,
    file_path TEXT,
    verification_status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
);

--- EVIDENCE_SKILLS

CREATE TABLE if NOT EXISTS evidence_skills (
    evidence_id INTEGER NOT NULL,
    skill_id INTEGER NOT NULL,
    confidence REAL DEFAULT 1.0,
    mapping_source TEXT DEFAULT 'manual',
    PRIMARY KEY (evidence_id, skill_id),
    FOREIGN KEY (evidence_id) REFERENCES evidence(id) ON DELETE CASCADE,
    FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
);

--- REPOSITORIES

CREATE TABLE if NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER,
    provider TEXT DEFAULT 'github',
    repository_name TEXT NOT NULL,
    repository_url TEXT NOT NULL UNIQUE,
    description TEXT,
    default_branch TEXT DEFAULT 'main',
    visibility TEXT DEFAULT 'private',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

--- VERIFICATIONS

CREATE TABLE if NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    verified_by INTEGER,
    verification_method TEXT DEFAULT 'manual',
    verified_at TIMESTAMP,
    comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (evidence_id) REFERENCES evidence(id) ON DELETE CASCADE,
    FOREIGN KEY (verified_by) REFERENCES users(id) ON DELETE SET NULL
);

--- INDEXES

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_evidence_user ON evidence(user_id);
CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence(project_id);
CREATE INDEX IF NOT EXISTS idx_verifications_evidence ON verifications(evidence_id);