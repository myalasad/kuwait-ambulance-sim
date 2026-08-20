"""Locate the SUMO installation (pip package `eclipse-sumo`, or a system
install) and expose its binaries."""
import os
import shutil


def ensure_sumo_home() -> str:
    """Return SUMO_HOME, setting the environment variable if needed."""
    home = os.environ.get("SUMO_HOME")
    if home and os.path.isdir(home):
        return home
    try:
        import sumo  # the eclipse-sumo pip package
        os.environ["SUMO_HOME"] = sumo.SUMO_HOME
        return sumo.SUMO_HOME
    except ImportError:
        pass
    exe = shutil.which("sumo")
    if exe:
        home = os.path.dirname(os.path.dirname(os.path.realpath(exe)))
        os.environ["SUMO_HOME"] = home
        return home
    raise RuntimeError(
        "SUMO not found. Install it with `pip install eclipse-sumo` "
        "or set the SUMO_HOME environment variable."
    )


def tool_binary(name: str) -> str:
    """Path to a SUMO binary such as `sumo`, `sumo-gui` or `netconvert`."""
    home = ensure_sumo_home()
    for candidate in (os.path.join(home, "bin", name), shutil.which(name)):
        if candidate and os.path.exists(candidate):
            return candidate
    return name  # let the OS resolve it (and fail loudly if it can't)


def sumo_binary(gui: bool = False) -> str:
    return tool_binary("sumo-gui" if gui else "sumo")


def tools_dir() -> str:
    return os.path.join(ensure_sumo_home(), "tools")
