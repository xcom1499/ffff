PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
	id INTEGER PRIMARY KEY,
	tg_id INTEGER UNIQUE NOT NULL,
	token TEXT UNIQUE NOT NULL,
	created_at INTEGER NOT NULL,
	consent_accepted INTEGER NOT NULL DEFAULT 0,
	accepts_questions INTEGER NOT NULL DEFAULT 1,
	last_active INTEGER
);

CREATE TABLE IF NOT EXISTS questions (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	to_user INTEGER NOT NULL,
	from_user INTEGER NOT NULL,
	text TEXT,
	media_type TEXT,
	file_id TEXT,
	msg_id INTEGER,
	created_at INTEGER NOT NULL,
	read_at INTEGER,
	answered INTEGER NOT NULL DEFAULT 0,
	archived INTEGER NOT NULL DEFAULT 0,
	FOREIGN KEY (to_user) REFERENCES users(id),
	FOREIGN KEY (from_user) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_questions_to ON questions(to_user, created_at);
CREATE INDEX IF NOT EXISTS idx_questions_from ON questions(from_user, created_at);
CREATE INDEX IF NOT EXISTS idx_questions_msg ON questions(to_user, msg_id);

CREATE TABLE IF NOT EXISTS answers (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	question_id INTEGER NOT NULL,
	from_user INTEGER NOT NULL,
	text TEXT,
	media_type TEXT,
	file_id TEXT,
	created_at INTEGER NOT NULL,
	FOREIGN KEY (question_id) REFERENCES questions(id)
);

CREATE TABLE IF NOT EXISTS blocks (
	blocker INTEGER NOT NULL,
	blocked INTEGER NOT NULL,
	PRIMARY KEY (blocker, blocked)
);

CREATE TABLE IF NOT EXISTS reports (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	reporter INTEGER NOT NULL,
	target_user INTEGER NOT NULL,
	question_id INTEGER,
	reason TEXT,
	created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	user_id INTEGER NOT NULL,
	target_user_id INTEGER,
	type TEXT NOT NULL,
	expires_at INTEGER NOT NULL,
	FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS metrics (
	key TEXT PRIMARY KEY,
	value INTEGER NOT NULL
);