"""Django admin -- the platform-admin surface (architecture §2, §9).

It is outside the design system entirely and gets no styling budget
(design-system §B.8.4·7). Two requirements hold and nothing else: nothing in it
is ever shown to a tenant user, and a tenant-scoped model list does not render
until a tenant is selected.
"""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from core.middleware import SESSION_TENANT_KEY, SESSION_USER_KEY
from core.models import AuditLog, Invitation, Location, Role, Tenant, User
from core.tenancy import NO_TENANT, grant_tenant_picker

admin.site.site_header = "Botica · administración de plataforma"
admin.site.site_title = "Botica"
admin.site.index_title = "Redes"


def selected_tenant(request):
    """The network this platform admin is standing in, or None."""
    raw = request.session.get(SESSION_TENANT_KEY)
    return None if not raw or str(raw) == str(NO_TENANT) else raw


class TenantScopedAdmin(admin.ModelAdmin):
    """A list that does not render until a tenant is selected.

    With no selection the request is pinned to nothing and reads zero rows --
    which is correct and would look like an empty database. Saying so is the
    difference between "there is nothing here" and "you have not chosen yet".
    """

    def changelist_view(self, request, extra_context=None):
        if selected_tenant(request) is None:
            self.message_user(
                request,
                "Seleccione una droguería antes de abrir sus registros. "
                "Sin selección la base de datos no devuelve ninguna fila.",
                level=messages.WARNING,
            )
            return redirect(reverse("admin:core_tenant_changelist"))
        return super().changelist_view(request, extra_context)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """The `tenants` CRUD, and the picker every other list depends on."""

    list_display = ("name", "slug", "nit", "status", "enter_link")
    search_fields = ("name", "slug", "nit")
    list_filter = ("status",)
    readonly_fields = ("id", "created_at", "updated_at", "settings")

    def get_queryset(self, request):
        # The picker grant, held for this transaction only. It widens SELECT on
        # `tenants` and nothing else.
        grant_tenant_picker()
        return super().get_queryset(request)

    def get_urls(self):
        return [
            path(
                "<uuid:tenant_id>/enter/",
                self.admin_site.admin_view(self.enter_tenant),
                name="core_tenant_enter",
            ),
            *super().get_urls(),
        ]

    def enter_tenant(self, request, tenant_id):
        """Select a network. The selection lives in the session and is pinned per
        request like any other -- there is no wildcard and no null-tenant pin."""
        request.session[SESSION_TENANT_KEY] = str(tenant_id)
        request.session[SESSION_USER_KEY] = str(request.user.id)
        self.message_user(request, "Droguería seleccionada.")
        return redirect(reverse("admin:core_location_changelist"))

    @admin.display(description="Seleccionar")
    def enter_link(self, obj):
        return format_html(
            '<a href="{}">Entrar</a>',
            reverse("admin:core_tenant_enter", args=[obj.id]),
        )


@admin.register(Location)
class LocationAdmin(TenantScopedAdmin):
    """The `locations` CRUD. A **sede**."""

    list_display = ("code", "name", "type", "city", "status")
    list_filter = ("type", "status")
    search_fields = ("code", "name", "city")
    readonly_fields = ("id", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if not change and obj.tenant_id is None:
            obj.tenant_id = selected_tenant(request)
        super().save_model(request, obj, form, change)


@admin.register(User)
class PlatformUserAdmin(DjangoUserAdmin, TenantScopedAdmin):
    """First-`owner` creation, and nothing a tenant user ever sees."""

    change_password_form = AdminPasswordChangeForm
    ordering = ("name",)
    list_display = ("email", "name", "role", "status", "location")
    list_filter = ("role", "status")
    search_fields = ("email", "name")
    readonly_fields = ("id", "created_at", "updated_at", "last_login")
    filter_horizontal = ()
    fieldsets = (
        (None, {"fields": ("email", "name", "password")}),
        ("Perfil", {"fields": ("role", "location", "status")}),
        ("Plataforma", {"fields": ("platform_admin", "is_staff", "is_superuser")}),
        ("Registro", {"fields": ("id", "created_at", "updated_at", "last_login")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "name",
                    "role",
                    "location",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "role":
            kwargs["choices"] = [
                choice for choice in Role.choices if choice[0] != Role.PLATFORM_ADMIN
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change and obj.tenant_id is None:
            obj.tenant_id = selected_tenant(request)
        super().save_model(request, obj, form, change)


@admin.register(Invitation)
class InvitationAdmin(TenantScopedAdmin):
    list_display = ("email", "role", "status", "expires_at", "location")
    list_filter = ("role", "status")
    search_fields = ("email",)
    readonly_fields = ("id", "token_hash", "created_at", "updated_at")


@admin.register(AuditLog)
class AuditLogAdmin(TenantScopedAdmin):
    """Read-only here as it is everywhere: the runtime role holds no UPDATE or
    DELETE grant on this table."""

    list_display = ("created_at", "actor_email", "action", "entity_type")
    list_filter = ("action", "entity_type")
    search_fields = ("actor_email", "entity_type")
    readonly_fields = tuple(field.name for field in AuditLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
