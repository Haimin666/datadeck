"""跨领域安全文件访问原语（自 Yuxi utils/paths.py 照搬，文件名 fs_paths 避免歧义）。"""

from .fs_paths import ensure_within_root, open_directory_fd, open_regular_file_fd

__all__ = ["open_directory_fd", "open_regular_file_fd", "ensure_within_root"]
