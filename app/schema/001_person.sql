-- person: minimal demo table (id auto-increment, name)
CREATE TABLE IF NOT EXISTS person (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);
