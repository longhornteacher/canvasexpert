"""Private monitored student settings used by the roster runtime."""
from . import _io as _io_mod


def get_monitored_students() -> dict:
    return _io_mod._synced_state().get("monitored_students", {})


def set_monitored_student(user_id: str, name: str, note: str = ""):
    _io_mod._modify_synced(
        lambda state: state.setdefault("monitored_students", {}).__setitem__(
            str(user_id), {"name": name, "note": note}
        )
    )


def remove_monitored_student(user_id: str):
    def mutate(state):
        state.setdefault("monitored_students", {}).pop(str(user_id), None)

    _io_mod._modify_synced(mutate)
