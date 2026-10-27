BEGIN TRANSACTION;
CREATE TABLE banned_identities (
    identity_hash TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);
CREATE TABLE calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    connected_at TEXT NOT NULL,
    duration_s REAL NOT NULL
);
CREATE TABLE conversation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    affinity TEXT,
    gift_id TEXT,
    liked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    title TEXT
, work_id TEXT REFERENCES work_scopes(id));
INSERT INTO "conversations" VALUES('c','u','mano','2020-01-02T00:00:00+00:00','title','sc');
CREATE TABLE "memories" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id),
        source_character_id TEXT NOT NULL,
        conversation_id TEXT REFERENCES conversations(id),
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        superseded_by INTEGER,
        work_id TEXT NOT NULL REFERENCES work_scopes(id),
        UNIQUE(user_id,work_id,text)
    );
INSERT INTO "memories" VALUES(9,'u','mano',NULL,'制作人喜欢茶','2020-01-02T00:00:00+00:00',17,'sc');
INSERT INTO "memories" VALUES(17,'u','removed_character','c','制作人喜欢咖啡','2020-01-03T00:00:00+00:00',NULL,'sc');
CREATE TABLE memory_insert_guard (id INTEGER PRIMARY KEY);
CREATE TABLE memory_vectors (
    memory_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    PRIMARY KEY (memory_id, model)
);
INSERT INTO "memory_vectors" VALUES(17,'u','test-model',X'0000803F00000000');
CREATE TABLE message_insert_guard (id INTEGER PRIMARY KEY);
CREATE TABLE messages (
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
INSERT INTO "messages" VALUES(7,'c','user','原文',NULL,NULL,NULL,'2020-01-03T00:00:00+00:00',NULL,NULL,NULL);
CREATE TABLE photos (
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
CREATE TABLE producer_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    form_of_address TEXT,
    gender TEXT,
    timezone TEXT,
    updated_at TEXT NOT NULL,
    birthday_month INTEGER,
    birthday_day INTEGER
);
CREATE TABLE reach_outs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    ja TEXT NOT NULL,
    zh TEXT NOT NULL,
    segments TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    record_id INTEGER
);
CREATE TABLE reset_card_grants (granted_at TEXT NOT NULL,count INTEGER NOT NULL);
INSERT INTO "reset_card_grants" VALUES('2026-01-01T00:00:00+00:00',2);
INSERT INTO "reset_card_grants" VALUES('2026-01-01T00:00:00+00:00',2);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    last_active_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE sign_in_methods (
    user_id TEXT NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (provider, subject)
);
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    consented_at TEXT,
    banned_at TEXT,
    ban_note TEXT
);
INSERT INTO "users" VALUES('u','test','2020-01-01T00:00:00+00:00',NULL,NULL,NULL);
CREATE TABLE work_scopes (id TEXT PRIMARY KEY NOT NULL);
INSERT INTO "work_scopes" VALUES('sc');
INSERT INTO "work_scopes" VALUES('demo');
CREATE INDEX sessions_by_user ON sessions(user_id);
CREATE INDEX sessions_by_expiry ON sessions(expires_at);
CREATE INDEX sign_in_methods_by_user ON sign_in_methods(user_id);
CREATE INDEX conversations_by_user
    ON conversations(user_id, character_id, created_at);
CREATE INDEX messages_by_conversation ON messages(conversation_id, id);
CREATE INDEX conversation_events_by_conversation
    ON conversation_events(conversation_id, created_at);
CREATE INDEX photos_by_conversation ON photos(conversation_id);
CREATE INDEX calls_by_conversation ON calls(conversation_id);
CREATE INDEX reach_outs_by_conversation ON reach_outs(conversation_id, created_at);
CREATE INDEX conversations_by_work ON conversations(user_id, work_id, character_id, created_at);
CREATE VIEW sc_conversations AS SELECT id,user_id,character_id,created_at,title,rowid AS rowid FROM conversations WHERE work_id='sc';
CREATE TRIGGER conversation_scope_required BEFORE INSERT ON conversations
        WHEN NEW.work_id IS NULL OR NOT EXISTS (SELECT 1 FROM work_scopes WHERE id=NEW.work_id)
        OR NEW.id IS NULL OR NEW.user_id IS NULL OR NEW.character_id IS NULL
        OR NEW.character_id='' OR NEW.character_id GLOB '*[^a-z0-9_-]*'
        BEGIN SELECT RAISE(ABORT, 'explicit registered work and local identity required'); END;
CREATE TRIGGER conversation_identity_immutable BEFORE UPDATE OF id,user_id,character_id,work_id ON conversations
        WHEN NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id
        OR NEW.character_id IS NOT OLD.character_id OR NEW.work_id IS NOT OLD.work_id
        BEGIN SELECT RAISE(ABORT, 'conversation identity is immutable'); END;
CREATE TRIGGER conversation_no_replace BEFORE INSERT ON conversations
        WHEN EXISTS (SELECT 1 FROM conversations WHERE id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'existing conversation cannot be replaced'); END;
CREATE TRIGGER conversation_events_sc_insert BEFORE INSERT ON conversation_events
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc')
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END;
CREATE TRIGGER conversation_events_sc_update BEFORE UPDATE ON conversation_events
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc')
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END;
CREATE TRIGGER photos_sc_insert BEFORE INSERT ON photos
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc')
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END;
CREATE TRIGGER photos_sc_update BEFORE UPDATE ON photos
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc')
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END;
CREATE TRIGGER calls_sc_insert BEFORE INSERT ON calls
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc')
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END;
CREATE TRIGGER calls_sc_update BEFORE UPDATE ON calls
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc')
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END;
CREATE TRIGGER reach_outs_sc_insert BEFORE INSERT ON reach_outs
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc')
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END;
CREATE TRIGGER reach_outs_sc_update BEFORE UPDATE ON reach_outs
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc')
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END;
CREATE TRIGGER message_insert_probe BEFORE INSERT ON messages
            BEGIN
                DELETE FROM message_insert_guard;
                INSERT INTO message_insert_guard SELECT id FROM messages WHERE id=NEW.id;
            END;
CREATE TRIGGER message_no_replace AFTER INSERT ON messages
            BEGIN
                SELECT RAISE(ABORT, 'existing message cannot be replaced')
                    WHERE EXISTS (SELECT 1 FROM message_insert_guard WHERE id=NEW.id);
                DELETE FROM message_insert_guard;
            END;
CREATE TRIGGER message_parent_immutable BEFORE UPDATE OF conversation_id ON messages
            WHEN NEW.conversation_id IS NOT OLD.conversation_id
            BEGIN SELECT RAISE(ABORT, 'message parent is immutable'); END;
CREATE TRIGGER photos_message_insert BEFORE INSERT ON photos
                WHEN NOT EXISTS (SELECT 1 FROM messages m WHERE m.id=NEW.message_id AND m.conversation_id=NEW.conversation_id)
                BEGIN SELECT RAISE(ABORT, 'photo message/conversation mismatch'); END;
CREATE TRIGGER photos_message_update BEFORE UPDATE ON photos
                WHEN NOT EXISTS (SELECT 1 FROM messages m WHERE m.id=NEW.message_id AND m.conversation_id=NEW.conversation_id)
                BEGIN SELECT RAISE(ABORT, 'photo message/conversation mismatch'); END;
CREATE INDEX memory_vectors_by_user ON memory_vectors(user_id, model);
CREATE INDEX memories_by_conversation ON memories(conversation_id);
CREATE INDEX memories_by_scope ON memories(user_id,work_id,id);
CREATE TRIGGER memory_scope_insert BEFORE INSERT ON memories
            WHEN NOT EXISTS (SELECT 1 FROM work_scopes WHERE id=NEW.work_id)
            OR NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.user_id)
            OR (NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations WHERE id=NEW.conversation_id AND user_id=NEW.user_id AND work_id=NEW.work_id))
            OR (NEW.superseded_by IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM memories WHERE id=NEW.superseded_by AND user_id=NEW.user_id AND work_id=NEW.work_id))
            OR EXISTS (WITH RECURSIVE chain(id) AS (SELECT NEW.superseded_by UNION
                SELECT m.superseded_by FROM memories m JOIN chain ON m.id=chain.id)
                SELECT 1 FROM chain WHERE id=NEW.id AND 'INSERT'='UPDATE')
            BEGIN SELECT RAISE(ABORT, 'memory scope association mismatch'); END;
CREATE TRIGGER memory_vector_insert BEFORE INSERT ON memory_vectors
            WHEN NOT EXISTS (SELECT 1 FROM memories WHERE id=NEW.memory_id AND user_id=NEW.user_id)
            BEGIN SELECT RAISE(ABORT, 'memory vector owner mismatch'); END;
CREATE TRIGGER memory_scope_update BEFORE UPDATE ON memories
            WHEN NOT EXISTS (SELECT 1 FROM work_scopes WHERE id=NEW.work_id)
            OR NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.user_id)
            OR (NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations WHERE id=NEW.conversation_id AND user_id=NEW.user_id AND work_id=NEW.work_id))
            OR (NEW.superseded_by IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM memories WHERE id=NEW.superseded_by AND user_id=NEW.user_id AND work_id=NEW.work_id))
            OR EXISTS (WITH RECURSIVE chain(id) AS (SELECT NEW.superseded_by UNION
                SELECT m.superseded_by FROM memories m JOIN chain ON m.id=chain.id)
                SELECT 1 FROM chain WHERE id=NEW.id AND 'UPDATE'='UPDATE')
            BEGIN SELECT RAISE(ABORT, 'memory scope association mismatch'); END;
CREATE TRIGGER memory_vector_update BEFORE UPDATE ON memory_vectors
            WHEN NOT EXISTS (SELECT 1 FROM memories WHERE id=NEW.memory_id AND user_id=NEW.user_id)
            BEGIN SELECT RAISE(ABORT, 'memory vector owner mismatch'); END;
CREATE TRIGGER memory_vector_identity BEFORE UPDATE OF memory_id,user_id,model ON memory_vectors
        WHEN NEW.memory_id IS NOT OLD.memory_id OR NEW.user_id IS NOT OLD.user_id OR NEW.model IS NOT OLD.model
        BEGIN SELECT RAISE(ABORT, 'memory vector identity is immutable'); END;
CREATE TRIGGER memory_identity_immutable BEFORE UPDATE OF id,user_id,work_id ON memories
        WHEN NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id OR NEW.work_id IS NOT OLD.work_id
        BEGIN SELECT RAISE(ABORT, 'memory identity is immutable'); END;
CREATE TRIGGER memory_insert_probe BEFORE INSERT ON memories BEGIN
        DELETE FROM memory_insert_guard;
        INSERT INTO memory_insert_guard SELECT id FROM memories
            WHERE id=NEW.id OR (user_id=NEW.user_id AND work_id=NEW.work_id AND text=NEW.text);
        END;
CREATE TRIGGER memory_no_replace AFTER INSERT ON memories BEGIN
        SELECT RAISE(ABORT, 'existing memory cannot be replaced')
            WHERE EXISTS (SELECT 1 FROM memory_insert_guard g WHERE g.id=NEW.id
                OR NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=g.id));
        DELETE FROM memory_insert_guard;
        END;
CREATE TRIGGER memory_delete_links AFTER DELETE ON memories BEGIN
        DELETE FROM memory_vectors WHERE memory_id=OLD.id;
        UPDATE memories SET superseded_by=OLD.superseded_by WHERE superseded_by=OLD.id;
        END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('messages',7);
INSERT INTO "sqlite_sequence" VALUES('memories',17);
COMMIT;
PRAGMA user_version=2;
