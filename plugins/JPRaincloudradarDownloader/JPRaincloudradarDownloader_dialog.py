import os
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDockWidget

ui_path = os.path.join(os.path.dirname(__file__), "JPRaincloudradarDownloader_dialog_base.ui")
FORM_CLASS, _ = uic.loadUiType(ui_path)

class JPRaincloudradarDownloaderDialog(QDockWidget, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
