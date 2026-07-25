CREATE TABLE jobs (
    id          BIGSERIAL       PRIMARY KEY,
    source      VARCHAR(50)     NOT NULL,
    source_id   VARCHAR(255)    NOT NULL,
    title       TEXT,
    company     VARCHAR(255),
    location    VARCHAR(255),
    is_remote   BOOLEAN,
    salary_min  INTEGER,
    salary_max  INTEGER,
    description TEXT,
    url         VARCHAR(2048),
    raw_text    TEXT,
    posted_at   TIMESTAMPTZ,
    scraped_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_jobs_source UNIQUE (source, source_id)
);
