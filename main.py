from app.ui.project_launcher import ProjectLauncher
from PySide6.QtWidgets import QApplication


def run():
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    window = ProjectLauncher()
    window.show()
    code = app.exec()
    return code


if __name__ == '__main__':
    raise SystemExit(run())
