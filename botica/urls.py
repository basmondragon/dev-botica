import re

from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, HttpResponse
from django.urls import include, path, re_path
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from core.api import api

shell = ensure_csrf_cookie(TemplateView.as_view(template_name="index.html"))

DIST = settings.BASE_DIR / "web" / "dist"


#: The built files served from the origin root rather than from `/static/`.
#:
#: The service worker is here because a worker's scope is its own directory:
#: one served from `/static/sw.js` would control the assets and not the routes,
#: and an offline reload of `/inventory` would reach the browser's own offline
#: page instead of the precached shell. `workbox-*.js` follows it because
#: `sw.js` imports it by a sibling path -- and a shell HTML page served in its
#: place is a worker that installs nothing and precaches nothing, silently.
ROOT_ASSETS = r"^(?P<name>sw\.js|workbox-[0-9a-f]+\.js|manifest\.webmanifest)$"

CONTENT_TYPES = {
    ".js": "text/javascript",
    ".webmanifest": "application/manifest+json",
}


def _built_asset(_request, name):
    """Serve one built file from `web/dist` at the origin root."""
    target = DIST / name
    if not target.exists():
        raise Http404(f"{name} is not built.")
    suffix = target.suffix
    return FileResponse(
        target.open("rb"),
        content_type=CONTENT_TYPES.get(suffix, "application/octet-stream"),
    )


# The only allauth routes this product implements. Nothing here registers an
# account: creation is by invitation and by nothing else (architecture §3).
ALLAUTH_SURFACE = (
    "browser/v1/auth/login",
    "browser/v1/auth/session",
    "browser/v1/config",
)


def _refuse_unmounted_allauth(request, *args, **kwargs):
    """404 for an allauth route this product does not implement."""
    raise Http404("Esa ruta de autenticación no es parte de esta aplicación.")


_unmounted_allauth = re_path(
    r"^_allauth/(?!(?:%s)/?$).*$"
    % "|".join(re.escape(route) for route in ALLAUTH_SURFACE),
    _refuse_unmounted_allauth,
)

urlpatterns = [
    path("healthz", lambda _r: HttpResponse("ok"), name="healthz"),
    re_path(ROOT_ASSETS, _built_asset, name="root-asset"),
    path("admin/", admin.site.urls),
    _unmounted_allauth,
    path("_allauth/", include("allauth.headless.urls")),
    path("api/", api.urls),
    # Everything else is the shell -- but never a root asset: a worker or a
    # manifest answered with HTML fails in a way nothing reports.
    re_path(
        r"^(?!api/|admin/|_allauth/|static/|healthz|sw\.js|workbox-|"
        r"manifest\.webmanifest).*$",
        shell,
    ),
]
