"""Explicit fixture owners for the real model-admission boundary."""
from types import SimpleNamespace


def register_model_owners(monkeypatch, owner_models):
    from src import model_admission

    users = {owner: {"allowed_models": sorted(models), "allowed_models_restricted": True}
             for owner, models in owner_models.items()}
    manager = SimpleNamespace(users=users, get_privileges=lambda owner: users[owner])
    monkeypatch.setattr(model_admission, "_AUTH_MANAGER", manager)
    return manager
