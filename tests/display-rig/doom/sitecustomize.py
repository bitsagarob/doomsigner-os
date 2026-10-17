# Sandbox only. The wallet picks its board from /proc/device-tree/model, which
# UML does not have; answer that one read the way a Pi Zero would.
import builtins
import io

_open = builtins.open


def _open_like_a_pi_zero(file, *args, **kwargs):
    if file == "/proc/device-tree/model":
        return io.StringIO("Raspberry Pi Zero Rev 1.3")
    return _open(file, *args, **kwargs)


builtins.open = _open_like_a_pi_zero
