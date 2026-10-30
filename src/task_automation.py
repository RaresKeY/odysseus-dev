"""Deployment admission for automatic ScheduledTask and event execution.

This is independent of the in-process timer flag: external-worker deployments
may disable that timer while keeping automatic events enabled. Explicit manual
task runs retain their existing authorization path.
"""
import os


def task_automation_enabled() -> bool:
    return os.environ.get("ODYSSEUS_TASK_AUTOMATION_ENABLED", "1").strip().lower() in {
        "1", "true", "yes", "on",
    }
