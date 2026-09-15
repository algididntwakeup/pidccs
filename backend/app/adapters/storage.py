from typing import Protocol, runtime_checkable, BinaryIO
import os
import shutil


@runtime_checkable
class StorageAdapter(Protocol):
    """Protocol for storing and retrieving drawing files and generated deliverables."""
    def save_file(self, destination_path: str, data: bytes | BinaryIO) -> str:
        ...

    def get_file_path(self, relative_path: str) -> str:
        ...

    def exists(self, relative_path: str) -> bool:
        ...

    def delete(self, relative_path: str) -> bool:
        ...


class LocalStorageAdapter:
    """Stores files on the local filesystem or mapped Docker volume."""

    def __init__(self, base_directory: str):
        self.base_directory = os.path.abspath(base_directory)
        os.makedirs(self.base_directory, exist_ok=True)

    def _resolve(self, relative_path: str) -> str:
        # Prevent directory traversal attacks
        normalized = os.path.normpath(relative_path).lstrip("/\\")
        return os.path.join(self.base_directory, normalized)

    def save_file(self, destination_path: str, data: bytes | BinaryIO) -> str:
        target_path = self._resolve(destination_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        if isinstance(data, bytes):
            with open(target_path, "wb") as f:
                f.write(data)
        else:
            with open(target_path, "wb") as f:
                shutil.copyfileobj(data, f)

        return target_path

    def get_file_path(self, relative_path: str) -> str:
        return self._resolve(relative_path)

    def exists(self, relative_path: str) -> bool:
        return os.path.exists(self._resolve(relative_path))

    def delete(self, relative_path: str) -> bool:
        target = self._resolve(relative_path)
        if os.path.exists(target):
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
            return True
        return False
