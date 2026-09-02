-- Two Postgres roles (A1). The migration role owns every table; the runtime role
-- owns none, which is the precondition every policy in this system rests on --
-- a table's owner bypasses its own RLS whatever the policy says.
\set migration_user  `echo "$BOTICA_MIGRATION_USER"`
\set migration_pw    `echo "$BOTICA_MIGRATION_PASSWORD"`
\set runtime_user    `echo "$BOTICA_RUNTIME_USER"`
\set runtime_pw      `echo "$BOTICA_RUNTIME_PASSWORD"`
\set db              `echo "$POSTGRES_DB"`

CREATE ROLE :"migration_user" LOGIN CREATEDB BYPASSRLS PASSWORD :'migration_pw';
CREATE ROLE :"runtime_user"   LOGIN PASSWORD :'runtime_pw' NOINHERIT;

GRANT :"runtime_user" TO :"migration_user";

ALTER DATABASE :"db" OWNER TO :"migration_user";

\connect :"db"

ALTER SCHEMA public OWNER TO :"migration_user";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"runtime_user";

ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"runtime_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_user" IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO :"runtime_user";
