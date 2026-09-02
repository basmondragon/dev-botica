"""The two enums the ledger names, and a CHECK on every status.

Django `choices` are a form-layer convenience: they emit a plain varchar and
stop nothing. The threat S0 names is not the API -- it is "a management command
or a bad backfill", which does not go through a serializer. So `role` and
`location_type` become real Postgres types, and the four status columns become
checked text exactly as *Data* describes them: a pilot that needs a sede
`en montaje` before it opens costs a CHECK change and a label, not a type
migration.

`audit_log.actor_user_id` stops being a foreign key in the same migration. A
`SET_NULL` cascade on it issues `UPDATE audit_log`, and the runtime role holds
no UPDATE on that table -- so every hard delete of a person failed with a
permission error instead of deleting them. The trail stamps `actor_email` at
write time precisely so it needs no reference to survive.
"""

import django.db.models.deletion
from django.db import migrations, models

ENUMS = r"""
CREATE TYPE role AS ENUM ('platform_admin', 'owner', 'admin', 'cashier');
CREATE TYPE location_type AS ENUM ('store', 'warehouse', 'distribution_center');

ALTER TABLE users       ALTER COLUMN role TYPE role USING role::role;
ALTER TABLE invitations ALTER COLUMN role TYPE role USING role::role;
ALTER TABLE locations   ALTER COLUMN type TYPE location_type
                             USING type::location_type;
"""

DROP_ACTOR_FK = r"""
DO $$
DECLARE constraint_name text;
BEGIN
    SELECT conname INTO constraint_name
      FROM pg_constraint
     WHERE conrelid = 'audit_log'::regclass
       AND contype = 'f'
       AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                            WHERE attrelid = 'audit_log'::regclass
                              AND attname = 'actor_user_id')];
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE audit_log DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;
"""

ADD_ACTOR_FK = r"""
ALTER TABLE audit_log
    ADD CONSTRAINT audit_log_actor_user_id_fk
    FOREIGN KEY (actor_user_id) REFERENCES users(id)
    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
"""

ENUMS_REVERSE = r"""
ALTER TABLE locations   ALTER COLUMN type TYPE varchar(32) USING type::text;
ALTER TABLE invitations ALTER COLUMN role TYPE varchar(20) USING role::text;
ALTER TABLE users       ALTER COLUMN role TYPE varchar(20) USING role::text;

DROP TYPE location_type;
DROP TYPE role;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0003_platform_admin_self_write")]

    operations = [
        migrations.RunSQL(sql=ENUMS, reverse_sql=ENUMS_REVERSE),
        migrations.AddConstraint(
            model_name="tenant",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["active", "suspended"])),
                name="tenant_status_is_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="location",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["active", "closed"])),
                name="location_status_is_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["active", "suspended"])),
                name="user_status_is_declared",
            ),
        ),
        migrations.AddConstraint(
            model_name="invitation",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["pending", "accepted", "revoked"])),
                name="invitation_status_is_declared",
            ),
        ),
        # A sede with people homed to it is closed, not deleted: SET_NULL would
        # write the exact row `cashier_has_a_home_location` forbids.
        migrations.AlterField(
            model_name="user",
            name="location",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="users",
                to="core.location",
            ),
        ),
        # The trail stamps its actor rather than referencing one.
        #
        # The column already holds every actor id ever written, so this drops
        # the constraint and keeps the data. A `RemoveField` plus an `AddField`
        # would name the same column and still drop and recreate it, emptying
        # a table nothing in the product is allowed to rewrite.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="auditlog", name="actor_user"),
                migrations.AddField(
                    model_name="auditlog",
                    name="actor_user_id",
                    field=models.UUIDField(blank=True, null=True),
                ),
            ],
            database_operations=[
                migrations.RunSQL(sql=DROP_ACTOR_FK, reverse_sql=ADD_ACTOR_FK),
            ],
        ),
    ]
