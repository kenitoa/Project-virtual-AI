ALTER TABLE turns ADD COLUMN session_id TEXT NOT NULL DEFAULT '';
ALTER TABLE session_summaries ADD COLUMN session_id TEXT NOT NULL DEFAULT '';
CREATE INDEX turns_session ON turns(viewer_id, session_id);
CREATE INDEX summaries_session ON session_summaries(viewer_id, session_id);
