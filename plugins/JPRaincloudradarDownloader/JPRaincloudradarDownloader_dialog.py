import os
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDockWidget, QMessageBox

ui_path = os.path.join(os.path.dirname(__file__), "JPRaincloudradarDownloader_dialog_base.ui")
FORM_CLASS, _ = uic.loadUiType(ui_path)

class JPRaincloudradarDownloaderDialog(QDockWidget, FORM_CLASS):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        # ズームレベル変更時のチェック処理を接続
        self.spinBox_zoom.valueChanged.connect(self.check_zoom_level)

    def check_zoom_level(self, value):
        # 10を超えている場合
        if value > 10:
            QMessageBox.warning(self, "警告", "最大ズームレベルは10です")
            self.spinBox_zoom.setValue(10)
            return

        # 奇数が入力された場合
        if value % 2 != 0:
            QMessageBox.warning(self, "警告", "ズームレベルは偶数のみ指定可能です")
            # 奇数の場合は1引いて偶数にする（例: 9 -> 8）
            self.spinBox_zoom.setValue(value - 1)