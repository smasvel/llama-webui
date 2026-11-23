CREATE TABLE IF NOT EXISTS accounts(username text PRIMARY KEY, password_hash text NOT NULL, role text NOT NULL CHECK(role IN ('user','admin')));
CREATE TABLE IF NOT EXISTS chats(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, owner text NOT NULL REFERENCES accounts(username), title text NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, chat bigint NOT NULL REFERENCES chats(id) ON DELETE CASCADE, role text NOT NULL, content text NOT NULL);
CREATE TABLE IF NOT EXISTS events(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, created timestamptz NOT NULL DEFAULT now(), actor text NOT NULL, action text NOT NULL);
CREATE TABLE IF NOT EXISTS model_tests(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, prompt text NOT NULL, result text NOT NULL);
CREATE TABLE IF NOT EXISTS settings(id integer PRIMARY KEY CHECK(id=1), temperature double precision NOT NULL CHECK(temperature BETWEEN 0 AND 2));
INSERT INTO settings VALUES (1,0.7) ON CONFLICT DO NOTHING;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT true;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
