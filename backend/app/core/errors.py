"""Custom exception hierarchy. Routes map these to HTTP via FastAPI handlers.

Usage:
    from app.core.errors import CustomerNotFound
    raise CustomerNotFound(customer_id)
"""

from __future__ import annotations


class AcmeError(Exception):
    """Root for all domain errors."""
    http_status: int = 500
    code: str = "ACME_ERROR"

    def __init__(self, message: str | None = None, **context):
        super().__init__(message or self.__class__.__name__)
        self.context = context


# --- Not-found ----------------------------------------------------------
class NotFoundError(AcmeError):
    http_status = 404
    code = "NOT_FOUND"


class CustomerNotFound(NotFoundError):
    code = "CUSTOMER_NOT_FOUND"

    def __init__(self, customer_id: str):
        super().__init__(f"Customer not found: {customer_id}", customer_id=customer_id)


class CaseNotFound(NotFoundError):
    code = "CASE_NOT_FOUND"

    def __init__(self, case_id: str):
        super().__init__(f"Case not found: {case_id}", case_id=case_id)


class RuleNotFound(NotFoundError):
    code = "RULE_NOT_FOUND"

    def __init__(self, rule_id: str):
        super().__init__(f"Rule not found: {rule_id}", rule_id=rule_id)


# --- Validation / bad request ------------------------------------------
class ValidationFailed(AcmeError):
    http_status = 422
    code = "VALIDATION_FAILED"


class RuleValidationError(ValidationFailed):
    code = "RULE_VALIDATION_ERROR"


class DuplicateRuleName(AcmeError):
    http_status = 409
    code = "DUPLICATE_RULE_NAME"

    def __init__(self, name: str):
        super().__init__(f"Rule name already exists: {name}", name=name)


# --- External-service failures ----------------------------------------
class ExternalServiceError(AcmeError):
    http_status = 502
    code = "EXTERNAL_SERVICE_ERROR"


class EmbeddingFailed(ExternalServiceError):
    code = "EMBEDDING_FAILED"


class BedrockFailed(ExternalServiceError):
    code = "BEDROCK_FAILED"


class MSKProduceFailed(ExternalServiceError):
    code = "MSK_PRODUCE_FAILED"


# --- Infrastructure ---------------------------------------------------
class DatabaseError(AcmeError):
    http_status = 503
    code = "DATABASE_ERROR"
