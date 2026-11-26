"""Editor-specific application errors."""

from app.errors import AppError


class EditorNotFoundError(AppError):
    status_code = 404
    code = "editor_not_found"


class EditorConflictError(AppError):
    status_code = 409
    code = "editor_conflict"


class EditorValidationError(AppError):
    status_code = 422
    code = "editor_validation_error"


class EditorPersistenceError(AppError):
    status_code = 500
    code = "editor_persistence_error"
