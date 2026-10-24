"""Domain error types."""


class DomainError(Exception):
    """Base class for errors raised by domain logic."""


class CurrencyMismatchError(DomainError):
    """Raised when an operation mixes amounts in different currencies."""


class EnvironmentNotAllowedError(DomainError):
    """Raised when a mode and a broker environment may not be combined."""
