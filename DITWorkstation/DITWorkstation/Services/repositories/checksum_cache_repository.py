"""Persistent checksum cache storage."""

from DITWorkstation.Utils import now_local

from .base_repository import BaseRepository


class ChecksumCacheRepository(BaseRepository):
    """Stores checksum results keyed by path metadata and algorithm."""

    def get(
        self, file_path: str, file_size: int, mtime_ns: int, algorithm: str,
    ) -> str | None:
        try:
            with self._transaction() as conn:
                row = conn.execute(
                    "SELECT hash_value FROM checksum_cache "
                    "WHERE file_path = ? AND file_size = ? AND mtime_ns = ? AND algorithm = ?",
                    (file_path, file_size, mtime_ns, algorithm),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "UPDATE checksum_cache SET accessed_at = ? "
                    "WHERE file_path = ? AND file_size = ? AND mtime_ns = ? AND algorithm = ?",
                    (now_local().isoformat(), file_path, file_size, mtime_ns, algorithm),
                )
                return row["hash_value"]
        except Exception as exc:
            logger.debug("校验和缓存查询失败: %s", exc)
            return None

    def put(
        self, file_path: str, file_size: int, mtime_ns: int, algorithm: str,
        hash_value: str, limit: int,
    ) -> None:
        limit = max(1, int(limit))
        now = now_local().isoformat()
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO checksum_cache "
                    "(file_path, file_size, mtime_ns, algorithm, hash_value, accessed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (file_path, file_size, mtime_ns, algorithm, hash_value, now),
                )
                conn.execute(
                    "DELETE FROM checksum_cache WHERE rowid IN ("
                    "SELECT rowid FROM checksum_cache ORDER BY accessed_at ASC, rowid ASC "
                    "LIMIT MAX(0, (SELECT COUNT(*) FROM checksum_cache) - ?)"
                    ")",
                    (limit,),
                )
        except Exception as exc:
            logger.debug("校验和缓存操作失败: %s", exc)
            return

    def clear(self) -> None:
        try:
            with self._transaction() as conn:
                conn.execute("DELETE FROM checksum_cache")
        except Exception as exc:
            logger.debug("校验和缓存操作失败: %s", exc)
            return

    def count(self) -> int:
        try:
            with self._connection() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM checksum_cache").fetchone()[0])
        except Exception as exc:
            logger.debug("校验和缓存计数失败: %s", exc)
            return 0
