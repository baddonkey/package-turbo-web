"""Request-scoped package permissions checked before workspace side effects."""

from fastapi import Request

from .database import Permission


def package_permission(request: Request) -> Permission:
    route = request.scope["route"].name
    if route in ("preview_cleanup", "apply_cleanup", "get_sharing", "update_sharing"):
        return "manage"
    if request.method in ("GET", "HEAD") or route in ("validate", "advanced_search_package"):
        return "view"
    return "edit"


def authorize_package_request(request: Request) -> None:
    package_id = request.path_params.get("package_id")
    if package_id is not None:
        request.app.state.store.database.authorize(
            package_id, request.state.principal, package_permission(request),
        )