CREATE TABLE store_meta (id TEXT NOT NULL);
CREATE TABLE viewers (id TEXT PRIMARY KEY, platform TEXT NOT NULL CHECK(platform='local'), allowed INTEGER NOT NULL CHECK(allowed IN (0,1)));
CREATE TABLE turns (id TEXT PRIMARY KEY, viewer_id TEXT NOT NULL REFERENCES viewers(id) ON DELETE CASCADE, input TEXT NOT NULL, final TEXT NOT NULL, displayed INTEGER NOT NULL CHECK(displayed IN (0,1)), playback TEXT NOT NULL CHECK(playback IN ('not_started','completed','failed','cancelled','unknown')), created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE memories (id TEXT PRIMARY KEY, viewer_id TEXT NOT NULL REFERENCES viewers(id) ON DELETE CASCADE, fact TEXT NOT NULL, source TEXT NOT NULL CHECK(source='local_operator'), evidence TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE session_summaries (id TEXT PRIMARY KEY, viewer_id TEXT NOT NULL REFERENCES viewers(id) ON DELETE CASCADE, summary TEXT NOT NULL, evidence TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX turns_expiry ON turns(expires_at);
CREATE INDEX memories_expiry ON memories(expires_at);
CREATE INDEX summaries_expiry ON session_summaries(expires_at);
