"""Errors a person can act on."""

from __future__ import annotations


class AppError(Exception):
    """A refusal the person can do something about.

    The web app's one error handler shows ``message`` as it is, with ``status``, so the message
    is fixed text written for the person: never an exception's text, a value from the request
    or an upstream response. Anything that isn't an ``AppError`` is logged and shown as a generic
    failure.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
