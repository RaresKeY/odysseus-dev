"""Bounded read-only evidence for directly declared local script operands.

This is not shell interpretation or a dependency scanner. Complex commands and
transitive imports remain unverified; the semantic reviewer must stop when
their contents matter. No subprocess, network, or out-of-authority file reads.
"""
from dataclasses import dataclass
import hashlib
import os
import shlex
import stat

from src.agent_runtime.resources import FilesystemResource
from src.decisions.contracts import DecisionError

MAX_SCRIPT_BYTES = 8192


@dataclass(frozen=True)
class ScriptEvidence:
    resource: FilesystemResource
    content: str
    sha256: str

    def validate(self):
        return read_script(self.resource) == self

    def to_dict(self):
        return {"source": "server_read_declared_script", "path": self.resource.path,
                "sha256": self.sha256, "content": self.content,
                "trust": "untrusted_file_contents"}


def read_script(resource):
    resource.validate()
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("No no-follow script observation on this platform")
    fd = os.open(resource.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SCRIPT_BYTES
                or (before.st_dev, before.st_ino) != (resource.identity.device, resource.identity.inode)):
            raise ValueError("Script evidence exceeds the review boundary")
        raw = os.read(fd, MAX_SCRIPT_BYTES + 1)
        after = os.fstat(fd)
        if len(raw) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Script changed during observation")
        resource.validate()
        return ScriptEvidence(resource, raw.decode("utf-8"), hashlib.sha256(raw).hexdigest())
    finally:
        os.close(fd)


def declared_scripts(tool, content, authority):
    if tool != "bash":
        return ()
    try:
        parts = shlex.split(content)
    except ValueError:
        return ()  # Exact source is still supplied to the reviewer.
    if not parts or any(token in {";", "&&", "||", "|", "&"} for token in parts):
        return ()
    interpreter = os.path.basename(parts[0]) in {"bash", "sh", "python", "python3"}
    path = parts[1] if interpreter and len(parts) > 1 and not parts[1].startswith("-") else parts[0] if "/" in parts[0] and not interpreter else None
    if not path:
        return ()
    # Absolute system executables are backend-owned, not user scripts.
    if not interpreter and path.startswith(("/usr/", "/bin/", "/sbin/")):
        return ()
    try:
        absolute = path if os.path.isabs(path) else os.path.join(authority.workspace, path)
        for root in authority.resource_roots:
            try:
                resource = FilesystemResource.resolve(root, absolute, allow_missing=False)
            except (ValueError, OSError):
                continue
            return (read_script(resource),)
    except (ValueError, OSError, UnicodeError):
        pass
    raise DecisionError("approval_script_unavailable")
