"""Shared repository access to the database service's connection lifecycle."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from DITWorkstation.Services.database_service import DatabaseService


class BaseRepository:
    """Repository base that reuses the facade's connection pool and transactions."""

    def __init__(self, database: "DatabaseService"):
        self._database = database

    def _connection(self):
        return self._database._connection()

    def _transaction(self):
        return self._database._transaction()
