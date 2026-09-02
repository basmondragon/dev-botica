import { useMemo, useState } from "react";
import { ApiError } from "@/api/client";
import {
  useDeletePerson,
  useInvitationLink,
  useInvitations,
  useInvite,
  useLocations,
  usePeople,
  useResendInvitation,
  useRevokeInvitation,
  useUpdatePerson,
  type Invitation,
  type Me,
  type Person,
  type Role,
} from "@/api/queries";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { DataTable, TableFooter } from "@/ui/table";
import { count, since } from "@/ui/format";
import { ConfirmDialog, Modal } from "@/ui/panel";
import { Select } from "@/ui/select";
import { INVITATION_STATE, StatusLine, USER_STATUS } from "@/ui/status";
import { EmptyState, RegionError } from "@/ui/states";
import { useLocalGrid } from "@/ui/use-grid";
import { useListKeys } from "@/ui/use-list-keys";
import { useToast } from "@/ui/toast";
import { roleLabel } from "@/shell/nav";
import { SectionHeading } from "./section";

type Row =
  | { kind: "person"; id: string; person: Person }
  | { kind: "invitation"; id: string; invitation: Invitation };

/**
 * §B.8.4·4 · **Ajustes · Personas** -- the roster and the outstanding
 * invitations, in one list. A roster that hides the people who were invited and
 * never arrived is a roster nobody trusts.
 *
 * §B.7.3 · status is a **dot plus label with no pill**, because this is a status
 * shown incidentally rather than the column the surface is about.
 */
export function PeopleSection({ me }: { me: Me }) {
  const grid = useLocalGrid({ sort: "name", order: "asc" });
  const people = usePeople({
    page: grid.page,
    page_size: grid.pageSize,
    sort: grid.sort,
    order: grid.order,
  });
  const invitations = useInvitations();
  const locations = useLocations();
  const toast = useToast();

  const update = useUpdatePerson();
  const remove = useDeletePerson();
  const resend = useResendInvitation();
  const revoke = useRevokeInvitation();
  const link = useInvitationLink();

  const [inviting, setInviting] = useState(false);
  const [deleting, setDeleting] = useState<Person | null>(null);

  const owner = me.role === "owner" || me.role === "platform_admin";

  const outstanding = useMemo(
    () => (invitations.data ?? []).filter((one) => one.state !== "accepted"),
    [invitations.data],
  );

  const rows = useMemo<Row[]>(() => {
    return [
      ...(people.data?.rows ?? []).map((person): Row => ({
        kind: "person",
        id: person.id,
        person,
      })),
      ...outstanding.map((invitation): Row => ({
        kind: "invitation",
        id: invitation.id,
        invitation,
      })),
    ];
  }, [people.data, outstanding]);

  const keys = useListKeys({
    rowCount: rows.length,
    rowId: (index) => `person-row-${index}`,
    pageKey: grid.page,
  });

  if (people.isError || invitations.isError) {
    const failure = (people.error ?? invitations.error) as unknown;
    return (
      <RegionError
        title="No pudimos cargar las personas de la droguería."
        detail={
          failure instanceof ApiError
            ? failure.message
            : "El servidor no respondió."
        }
        requestId={failure instanceof ApiError ? failure.requestId : undefined}
        onRetry={() => {
          void people.refetch();
          void invitations.refetch();
        }}
      />
    );
  }

  const sedes = locations.data ?? [];

  function say(error: unknown, fallback: string) {
    toast(error instanceof ApiError ? error.message : fallback);
  }

  return (
    <section className="flex h-full min-h-0 flex-col">
      <SectionHeading
        title="Personas"
        description="Quién entra a esta droguería, con qué perfil y desde qué sede."
        action={
          <Button variant="primary" onClick={() => setInviting(true)}>
            Invitar
          </Button>
        }
      />

      <DataTable<Row>
        rows={rows}
        rowId={(row) => row.id}
        density="standard"
        minWidth={900}
        loading={people.isPending || invitations.isPending}
        refetching={people.isFetching && !people.isPending}
        containerProps={keys.containerProps}
        skeletonWidths={["60%", "40%", "40%", "40%", "40%", "30%"]}
        rowProps={(_row, index) => ({
          id: `person-row-${index}`,
          cursor: keys.cursor === index,
        })}
        sort={grid.sort}
        order={grid.order}
        onSort={grid.toggleSort}
        empty={
          <EmptyState
            title="Todavía no hay nadie más en esta droguería"
            body="Invitar a alguien le envía un enlace para crear su contraseña y entrar con el perfil que usted elija."
            actionLabel="Invitar"
            onAction={() => setInviting(true)}
          />
        }
        footer={
          <TableFooter
            page={grid.page}
            pageSize={grid.pageSize}
            rowCount={people.data?.row_count}
            loading={people.isPending}
            onPage={grid.setPage}
            onPageSize={grid.setPageSize}
            annotation={
              outstanding.length > 0
                ? `${count(outstanding.length)} invitación${outstanding.length === 1 ? "" : "es"} sin aceptar`
                : undefined
            }
          />
        }
        columns={[
          {
            key: "name",
            label: "Persona",
            width: "22%",
            sortable: true,
            truncate: true,
            render: (row) =>
              row.kind === "person" ? (
                <span className="flex flex-col">
                  <span className="truncate text-ink">{row.person.name}</span>
                  <span className="truncate text-11 text-ink-label">
                    {row.person.email}
                  </span>
                </span>
              ) : (
                <span className="flex flex-col">
                  <span className="truncate text-ink">
                    {row.invitation.email}
                  </span>
                  <span className="truncate text-11 text-ink-label">
                    Invitación enviada
                  </span>
                </span>
              ),
          },
          {
            key: "role",
            label: "Perfil",
            width: "18%",
            sortable: true,
            render: (row) =>
              row.kind === "person" && owner && row.person.id !== me.id ? (
                <Select
                  size="sm"
                  aria-label={`Perfil de ${row.person.name}`}
                  value={row.person.role}
                  options={[
                    { value: "owner", label: "Propietaria" },
                    { value: "admin", label: "Administradora" },
                    { value: "cashier", label: "Mostrador" },
                  ]}
                  onValueChange={(next) =>
                    update.mutate(
                      {
                        id: row.person.id,
                        body: {
                          role: next as Role,
                          location_id:
                            next === "cashier"
                              ? (row.person.location_id ?? sedes[0]?.id ?? null)
                              : null,
                          clear_location: next !== "cashier",
                        },
                      },
                      {
                        onError: (error) =>
                          say(error, "No pudimos cambiar el perfil."),
                      },
                    )
                  }
                />
              ) : (
                <span>
                  {roleLabel(
                    row.kind === "person"
                      ? row.person.role
                      : row.invitation.role,
                  )}
                </span>
              ),
          },
          {
            key: "location",
            label: "Sede",
            width: "15%",
            truncate: true,
            // A cashier's home sede is a control; an office role has none, and
            // "Toda la red" is a statement rather than a choice (A2).
            render: (row) =>
              row.kind === "person" &&
              row.person.role === "cashier" &&
              owner ? (
                <Select
                  size="sm"
                  aria-label={`Sede de ${row.person.name}`}
                  value={row.person.location_id ?? ""}
                  options={sedes.map((sede) => ({
                    value: sede.id,
                    label: sede.name,
                  }))}
                  onValueChange={(next) =>
                    update.mutate(
                      { id: row.person.id, body: { location_id: next } },
                      {
                        onError: (error) =>
                          say(error, "No pudimos cambiar la sede."),
                      },
                    )
                  }
                />
              ) : row.kind === "person" ? (
                (row.person.location_name ?? "Toda la red")
              ) : (
                (row.invitation.location_name ?? "Toda la red")
              ),
          },
          {
            key: "status",
            label: "Estado",
            width: "14%",
            sortable: true,
            render: (row) => {
              const meaning =
                row.kind === "person"
                  ? USER_STATUS[row.person.status]
                  : INVITATION_STATE[row.invitation.state];
              return meaning ? (
                <StatusLine
                  family={meaning.family}
                  dot={meaning.dot}
                  label={meaning.label}
                />
              ) : null;
            },
          },
          {
            key: "last_login",
            label: "Último ingreso",
            width: "13%",
            sortable: true,
            numeric: true,
            align: "right",
            render: (row) =>
              row.kind === "person" && row.person.last_login_at
                ? since(row.person.last_login_at)
                : // §B.9.2 · never a zero standing in for "we don't know".
                  "—",
          },
          {
            key: "actions",
            label: "",
            width: "18%",
            align: "right",
            render: (row) =>
              row.kind === "invitation" ? (
                row.invitation.status !== "pending" ? null : (
                  <span className="flex items-center justify-end gap-1">
                    <Button
                      size="xs"
                      variant="ghost"
                      busy={resend.isPending}
                      busyLabel="Enviando…"
                      onClick={() =>
                        resend.mutate(row.invitation.id, {
                          onSuccess: () => toast("Se reenvió la invitación."),
                          onError: (error) =>
                            say(error, "No pudimos reenviar la invitación."),
                        })
                      }
                    >
                      Reenviar
                    </Button>
                    {row.invitation.state === "expired" ? null : (
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() =>
                          link.mutate(row.invitation.id, {
                            onSuccess: async (data) => {
                              await navigator.clipboard?.writeText(
                                data.accept_url,
                              );
                              toast("Se copió el enlace de la invitación.");
                            },
                            onError: (error) =>
                              say(error, "No pudimos copiar el enlace."),
                          })
                        }
                      >
                        Copiar enlace
                      </Button>
                    )}
                    {owner ? (
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() =>
                          revoke.mutate(row.invitation.id, {
                            onSuccess: () => toast("Se revocó la invitación."),
                            onError: (error) =>
                              say(error, "No pudimos revocar la invitación."),
                          })
                        }
                      >
                        Revocar
                      </Button>
                    ) : null}
                  </span>
                )
              ) : owner && row.person.id !== me.id ? (
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => setDeleting(row.person)}
                >
                  Eliminar
                </Button>
              ) : null,
          },
        ]}
      />

      <InviteDialog
        open={inviting}
        me={me}
        sedes={sedes.map((sede) => ({ value: sede.id, label: sede.name }))}
        onClose={() => setInviting(false)}
      />

      <ConfirmDialog
        open={!!deleting}
        title="Eliminar a esta persona"
        body={
          deleting
            ? `${deleting.name} deja de tener acceso a esta droguería de inmediato. ` +
              "Los registros que dejó se conservan con su nombre y su correo."
            : ""
        }
        confirmLabel={deleting ? `Eliminar a ${deleting.name}` : "Eliminar"}
        busyLabel="Eliminando…"
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast(`Se eliminó a ${deleting.name}.`);
              setDeleting(null);
            },
            onError: (error) =>
              say(error, "No pudimos eliminar a esta persona."),
          })
        }
      />
    </section>
  );
}

/**
 * §B.8.3 · an `admin` opens this with the role fixed at **Mostrador** --
 * absent, not disabled. Letting an `admin` mint an `owner` is a privilege
 * escalation that no audit row undoes.
 */
function InviteDialog({
  open,
  me,
  sedes,
  onClose,
}: {
  open: boolean;
  me: Me;
  sedes: { value: string; label: string }[];
  onClose: () => void;
}) {
  const invite = useInvite();
  const toast = useToast();
  const owner = me.role === "owner" || me.role === "platform_admin";
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("cashier");
  const [locationId, setLocationId] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const needsSede = role === "cashier";
  const emailError =
    submitted && !email.trim() ? "Escriba un correo electrónico." : undefined;
  const sedeError =
    submitted && needsSede && !locationId
      ? "Una cuenta de mostrador necesita una sede."
      : undefined;

  return (
    <Modal
      open={open}
      title="Invitar a alguien"
      busy={invite.isPending}
      onClose={onClose}
      footer={
        <>
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={invite.isPending}
          >
            Cancelar
          </Button>
          <Button
            variant="primary"
            busy={invite.isPending}
            busyLabel="Enviando…"
            onClick={() => {
              setSubmitted(true);
              if (!email.trim() || (needsSede && !locationId)) return;
              invite.mutate(
                {
                  email: email.trim(),
                  role,
                  location_id: needsSede ? locationId : null,
                },
                {
                  onSuccess: () => {
                    toast("Se envió la invitación.");
                    setEmail("");
                    setSubmitted(false);
                    onClose();
                  },
                  onError: (error) =>
                    toast(
                      error instanceof ApiError
                        ? error.message
                        : "No pudimos enviar la invitación.",
                    ),
                },
              );
            }}
          >
            Enviar invitación
          </Button>
        </>
      }
    >
      <div className="mt-4 flex flex-col gap-4">
        <Field
          label="Correo electrónico"
          htmlFor="invite-email"
          error={emailError}
          required
        >
          <Input
            id="invite-email"
            type="email"
            value={email}
            invalid={!!emailError}
            onChange={(event) => setEmail(event.currentTarget.value)}
          />
        </Field>

        {owner ? (
          <Field label="Perfil" htmlFor="invite-role">
            <Select
              id="invite-role"
              value={role}
              options={[
                { value: "owner", label: "Propietaria" },
                { value: "admin", label: "Administradora" },
                { value: "cashier", label: "Mostrador" },
              ]}
              onValueChange={(next) => setRole(next as Role)}
            />
          </Field>
        ) : (
          <Field label="Perfil" htmlFor="invite-role-fixed">
            <Input id="invite-role-fixed" value="Mostrador" readOnly />
          </Field>
        )}

        {needsSede ? (
          <Field
            label="Sede"
            htmlFor="invite-location"
            error={sedeError}
            required
          >
            <Select
              id="invite-location"
              value={locationId}
              placeholder="Elija una sede"
              options={sedes}
              invalid={!!sedeError}
              onValueChange={setLocationId}
            />
          </Field>
        ) : null}
      </div>
    </Modal>
  );
}
