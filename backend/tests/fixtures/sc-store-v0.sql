-- `name` only tells users apart on the admin command line; it need not be unique.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    consented_at TEXT,
    banned_at TEXT,
    ban_note TEXT
);
-- Only a hash of each session's token is kept; the times are epoch seconds.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    last_active_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_by_expiry ON sessions(expires_at);
-- A Google or GitHub identity a user signs in with; each belongs to one user only.
CREATE TABLE IF NOT EXISTS sign_in_methods (
    user_id TEXT NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (provider, subject)
);
CREATE INDEX IF NOT EXISTS sign_in_methods_by_user ON sign_in_methods(user_id);
CREATE TABLE IF NOT EXISTS producer_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    form_of_address TEXT,
    gender TEXT,
    timezone TEXT,
    updated_at TEXT NOT NULL,
    birthday_month INTEGER,
    birthday_day INTEGER
);
-- `title` is its conversation title (`titles`), NULL until it is named, if ever.
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    title TEXT
);
-- A user's conversations, with each character, newest last: listing them, finding the
-- current one, and every join from a user to what is in their conversations.
CREATE INDEX IF NOT EXISTS conversations_by_user
    ON conversations(user_id, character_id, created_at);
-- `hurt` and `out_of_line`, on the user's message of a typed turn (`Typed`), are what the
-- turn took off her affinity and whether it was out of line; NULL for a turn of a call, or
-- one recorded before typed turns were counted.
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'character')),
    text TEXT,
    ja TEXT,
    zh TEXT,
    segments TEXT,
    created_at TEXT NOT NULL,
    affinity TEXT,
    hurt REAL,
    out_of_line INTEGER
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS conversation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    affinity TEXT,
    gift_id TEXT,
    liked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS conversation_events_by_conversation
    ON conversation_events(conversation_id, created_at);
-- A photo she sent with a reply (GLOSSARY.md「照片」, ADR-0024), one at most to a reply: the
-- photo tag she wrote for it, as she wrote it; whether it is being generated, was sent or was
-- not, and why not (`reason`); and once sent the picture itself, a WebP, read only on its own.
CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id),
    tag TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'ready', 'failed')),
    reason TEXT,
    image BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS photos_by_conversation ON photos(conversation_id);
-- Each call that connected in a conversation (GLOSSARY.md「接通」), for stats alone: when it
-- connected and how long it lasted once it had, as its activity record says; nothing of what
-- was said. Not in the conversation's record: she knows nothing of it (ADR-0021).
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    connected_at TEXT NOT NULL,
    duration_s REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_by_conversation ON calls(conversation_id);
-- What she said first in a conversation (GLOSSARY.md「搭话」), as the model wrote it, delivery
-- cues and all, as her replies are kept; whether the user has seen it (GLOSSARY.md「没看过的
-- 搭话」); and the id of its activity record, which outlives neither the user's name on it nor
-- 90 days, so no reference.
CREATE TABLE IF NOT EXISTS reach_outs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    ja TEXT NOT NULL,
    zh TEXT NOT NULL,
    segments TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    record_id INTEGER
);
CREATE INDEX IF NOT EXISTS reach_outs_by_conversation ON reach_outs(conversation_id, created_at);
-- The identities banned users signed in with, kept when those users were deleted so that
-- deleting is not unbanning; only a keyed hash of each (`accounts.identity_hash`), no one's.
CREATE TABLE IF NOT EXISTS banned_identities (
    identity_hash TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);
