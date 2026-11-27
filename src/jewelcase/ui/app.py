"""Starting the window."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ..core.session import Session, open_session
from . import style
from .controller import Controller
from .window import MainWindow


def run(session: Session, screenshot: str | None = None, selftest: bool = False,
        view: str | None = None, theme: str | None = None) -> int:
    from ..core import integrate
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Jewelcase")
    app.setDesktopFileName(integrate.desktop_name())
    from PySide6.QtGui import QIcon
    app.setWindowIcon(QIcon(str(integrate.ICON)))
    style.apply(app, theme)
    offline = bool(screenshot or selftest)
    single = None
    if not offline:
        import os
        from . import shell
        single = shell.SingleInstance(integrate.instance_name(
            f"jewelcase-{os.getuid()}" + ("-demo" if session.demo_device is not None else "")))
        if single.already_running():
            return 0                            # the running copy shows its window instead
        holder = {}
        single.listen(lambda: holder.get("window") and holder["window"].reopen())
    controller = Controller(session, poll=not offline)
    if offline:
        controller.refresh_blocking()
    window = MainWindow(controller)
    window.resize(1180, 760)
    if not offline:
        from . import shell
        state = Path(session.config_path).parent / "window.ini" if session.config_path else session.data_dir / "window.ini"
        window.state_store = shell.WindowState(state)
        window.state_store.restore(window)
        controller.desktop = True
        controller.auto_backup = True
        holder["window"] = window
        app.setQuitOnLastWindowClosed(False)    # a sync may finish after the window closes
        controller.quit_requested.connect(app.quit)
    window.show()
    if view:
        window.select_scope(view)
    if not offline:
        controller.refresh()        # first run shows a welcome screen in the window, never a dialog
        code = app.exec()
        if not controller.shutdown(2.0):
            # A long device call (hashing a large file) is still in flight. Every step is journaled
            # and safe to repeat, so leave now; the next launch offers Resume.
            import os
            os._exit(code)
        return code
    for _ in range(5):
        app.processEvents()
    if screenshot:
        window.grab().save(screenshot)
    if selftest:
        from ..core import net
        from PySide6.QtGui import QImageReader
        readable = {bytes(f).decode() for f in QImageReader.supportedImageFormats()}
        missing = [f for f in ("png", "jpeg", "webp") if f not in readable]
        print(f"games {len(controller.rows)}")
        print(f"https {net.probe()}")
        print("pictures " + ("missing " + ",".join(missing) if missing else "ok"))
        if missing:                     # covers and system images could not be shown
            window.close()
            return 1
        print("selftest ok")
    window.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the packaged app: same as `jewelcase gui`, but accepts the CLI too."""
    from ..cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
