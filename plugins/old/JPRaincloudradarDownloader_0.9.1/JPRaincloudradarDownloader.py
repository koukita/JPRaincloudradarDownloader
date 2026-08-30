# -*- coding: utf-8 -*-

import os
import json
import shutil
import urllib.request
from urllib.parse import urlparse
from datetime import datetime, timedelta

from osgeo import gdal, osr

from qgis.PyQt.QtCore import Qt, QCoreApplication, QDateTime
from qgis.PyQt.QtGui import QImage, QPainter, QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QProgressDialog, QDockWidget

from qgis.core import (
    QgsRasterLayer,
    QgsProject,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsDateTimeRange,
    QgsInterval,
    Qgis
)

from qgis.gui import QgsTemporalControllerWidget
from qgis.utils import iface

from .JPRaincloudradarDownloader_dialog import JPRaincloudradarDownloaderDialog


ZOOM_LEVEL = 10
RASTER_OPACITY = 0.7
HTTP_TIMEOUT = 30
DOWNLOAD_RETRY_COUNT = 3
MAX_TILE_COUNT = 1000
TILE_SIZE = 256
WEB_MERCATOR_LIMIT = 20037508.342789244


def process_events():
    QCoreApplication.processEvents()


def safe_urlopen(req, timeout):
    """安全な URL オープン（http/https のみ許可）"""
    parsed = urlparse(req.full_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    return urllib.request.urlopen(req, timeout=timeout)


def create_http_request(url):
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 QGIS-JMA-Nowcast",
            "Accept": "image/png,image/*,application/json,*/*",
            "Connection": "close"
        }
    )


def get_json(url):
    req = create_http_request(url)
    with safe_urlopen(req, timeout=HTTP_TIMEOUT) as res:
        return json.loads(res.read().decode("utf-8"))


def get_n2_target_times():
    """未来（N2）のJSONデータを取得"""
    try:
        return get_json("https://www.jma.go.jp/bosai/jmatile/data/nowc/targetTimes_N2.json")
    except Exception as e:
        print(f"Error loading N2 target times: {e}")
        return None


def parse_target_time_entry(entry, time_map):
    if not isinstance(entry, dict):
        return

    basetime = entry.get("basetime")
    if not basetime:
        return

    validtime = entry.get("validtime")
    if validtime:
        time_map[validtime] = basetime

    elements = entry.get("elements", [])
    for elem in elements:
        if isinstance(elem, str):
            time_map[elem] = basetime
        elif isinstance(elem, dict) and elem.get("validtime"):
            time_map[elem.get("validtime")] = basetime


def extract_basetime_map(json_data):
    """JSONから { validtime: basetime } のマップと最新basetimeを抽出し返却"""
    time_map = {}
    latest_basetime = None

    if isinstance(json_data, list):
        if len(json_data) > 0 and isinstance(json_data[0], dict):
            latest_basetime = json_data[0].get("basetime")
        for entry in json_data:
            parse_target_time_entry(entry, time_map)
    elif isinstance(json_data, dict):
        latest_basetime = json_data.get("basetime")
        parse_target_time_entry(json_data, time_map)

    return time_map, latest_basetime


def download_png(url, output_path):
    for _ in range(DOWNLOAD_RETRY_COUNT):
        try:
            req = create_http_request(url)
            with safe_urlopen(req, timeout=HTTP_TIMEOUT) as res:
                data = res.read()

            if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
                print("Invalid PNG header")
                continue

            with open(output_path, "wb") as f:
                f.write(data)

            if QImage(output_path).isNull():
                print("Downloaded PNG is invalid")
                os.remove(output_path)
                continue

            return True

        except Exception as e:
            print(f"Download error: {e}")
            continue

    return False


def clamp_web_mercator(v):
    return max(-WEB_MERCATOR_LIMIT, min(WEB_MERCATOR_LIMIT, v))


def transform_rect_to_3857(rect, src_crs):
    if rect.isEmpty() or rect.isNull():
        raise RuntimeError("取得範囲が正しく指定されていません。マップキャンバスまたはレイヤから範囲を設定してください。")

    dst_crs = QgsCoordinateReferenceSystem("EPSG:3857")

    if src_crs.authid() == dst_crs.authid():
        return QgsRectangle(
            clamp_web_mercator(rect.xMinimum()),
            clamp_web_mercator(rect.yMinimum()),
            clamp_web_mercator(rect.xMaximum()),
            clamp_web_mercator(rect.yMaximum())
        )

    tr = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
    rect_3857 = tr.transformBoundingBox(rect)

    return QgsRectangle(
        clamp_web_mercator(rect_3857.xMinimum()),
        clamp_web_mercator(rect_3857.yMinimum()),
        clamp_web_mercator(rect_3857.xMaximum()),
        clamp_web_mercator(rect_3857.yMaximum())
    )


def mercator_to_tile_x(x, zoom):
    tile_count = 2 ** zoom
    normalized_x = (x + WEB_MERCATOR_LIMIT) / (2.0 * WEB_MERCATOR_LIMIT)
    return max(0, min(tile_count - 1, int(normalized_x * tile_count)))


def mercator_to_tile_y(y, zoom):
    tile_count = 2 ** zoom
    normalized_y = (WEB_MERCATOR_LIMIT - y) / (2.0 * WEB_MERCATOR_LIMIT)
    return max(0, min(tile_count - 1, int(normalized_y * tile_count)))


def tile_bounds_3857(tx, ty, zoom):
    tile_count = 2 ** zoom
    world_width = 2.0 * WEB_MERCATOR_LIMIT
    tile_width = world_width / tile_count

    xmin = -WEB_MERCATOR_LIMIT + tx * tile_width
    xmax = xmin + tile_width
    ymax = WEB_MERCATOR_LIMIT - ty * tile_width
    ymin = ymax - tile_width

    return xmin, ymin, xmax, ymax


def calculate_tile_range(extent_3857, zoom):
    eps = 0.000001

    tx_min = mercator_to_tile_x(extent_3857.xMinimum(), zoom)
    tx_max = mercator_to_tile_x(extent_3857.xMaximum() - eps, zoom)
    if tx_max < tx_min:
        tx_max = tx_min

    ty_min = mercator_to_tile_y(extent_3857.yMaximum(), zoom)
    ty_max = mercator_to_tile_y(extent_3857.yMinimum() + eps, zoom)
    if ty_max < ty_min:
        ty_max = ty_min

    return tx_min, tx_max, ty_min, ty_max


def download_and_merge_tiles(base_utc_str, target_utc_str, extent_3857, zoom, temp_dir):
    tx_min, tx_max, ty_min, ty_max = calculate_tile_range(extent_3857, zoom)

    count_x = tx_max - tx_min + 1
    count_y = ty_max - ty_min + 1
    total = count_x * count_y

    if total <= 0 or total > MAX_TILE_COUNT:
        raise RuntimeError(f"タイル数が多すぎます（{total}枚）。範囲を狭めてください。")

    mosaic = QImage(count_x * TILE_SIZE, count_y * TILE_SIZE, QImage.Format.Format_ARGB32)
    mosaic.fill(Qt.GlobalColor.transparent)

    painter = QPainter(mosaic)
    tile_dir = os.path.join(temp_dir, f"tiles_{target_utc_str}")
    os.makedirs(tile_dir, exist_ok=True)

    layer_type = "hrpns"
    downloaded = 0

    for ty in range(ty_min, ty_max + 1):
        for tx in range(tx_min, tx_max + 1):
            process_events()

            url = (
                f"https://www.jma.go.jp/bosai/jmatile/data/nowc/"
                f"{base_utc_str}/none/{target_utc_str}/surf/{layer_type}/"
                f"{zoom}/{tx}/{ty}.png"
            )
            path = os.path.join(tile_dir, f"{tx}_{ty}.png")

            if not download_png(url, path):
                print(f"Tile download failed: {url}")
                continue

            img = QImage(path)
            if img.isNull():
                print(f"Invalid tile image: {path}")
                continue

            img = img.convertToFormat(QImage.Format.Format_ARGB32)
            painter.drawImage((tx - tx_min) * TILE_SIZE, (ty - ty_min) * TILE_SIZE, img)
            downloaded += 1

    painter.end()
    shutil.rmtree(tile_dir, ignore_errors=True)

    if downloaded == 0:
        return None, None

    mosaic_path = os.path.join(temp_dir, f"mosaic_{target_utc_str}.png")
    mosaic.save(mosaic_path)

    first = tile_bounds_3857(tx_min, ty_min, zoom)
    last = tile_bounds_3857(tx_max, ty_max, zoom)
    bounds = (first[0], last[1], last[2], first[3])

    return mosaic_path, bounds


def create_geopackage_raster(mosaic_png, mosaic_bounds, extent_3857, target_utc_str, zoom, gpkg_path, temp_dir):
    xmin, ymin, xmax, ymax = mosaic_bounds
    tif_path = os.path.join(temp_dir, f"full_{target_utc_str}.tif")

    src_ds = gdal.Open(mosaic_png, gdal.GA_ReadOnly)
    if src_ds is None:
        raise RuntimeError("PNG を開けません。")

    px = (xmax - xmin) / src_ds.RasterXSize
    py = (ymax - ymin) / src_ds.RasterYSize

    driver = gdal.GetDriverByName("GTiff")
    dst_ds = driver.CreateCopy(tif_path, src_ds, strict=0, options=["TILED=YES", "COMPRESS=DEFLATE"])
    dst_ds.SetGeoTransform((xmin, px, 0, ymax, 0, -py))

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    dst_ds.SetProjection(srs.ExportToWkt())

    dst_ds.FlushCache()
    dst_ds = None
    src_ds = None

    table_name = f"rain_{target_utc_str}"

    warp_opt = gdal.WarpOptions(
        format="GPKG",
        outputBounds=[extent_3857.xMinimum(), extent_3857.yMinimum(),
                      extent_3857.xMaximum(), extent_3857.yMaximum()],
        outputBoundsSRS="EPSG:3857",
        srcSRS="EPSG:3857",
        dstSRS="EPSG:3857",
        resampleAlg="near",
        creationOptions=[f"RASTER_TABLE={table_name}", "APPEND_SUBDATASET=YES"]
    )

    gdal.Warp(gpkg_path, tif_path, options=warp_opt)

    if os.path.exists(tif_path):
        os.remove(tif_path)
    if os.path.exists(mosaic_png):
        os.remove(mosaic_png)

    return table_name


def add_gpkg_layer_with_temporal(gpkg_path, table_name, layer_name, jst_time, group):
    uri = f"GPKG:{gpkg_path}:{table_name}"
    layer = QgsRasterLayer(uri, layer_name, "gdal")
    if not layer.isValid():
        print(f"Layer invalid: {uri}")
        return None

    layer.setOpacity(RASTER_OPACITY)

    start = QDateTime(jst_time.year, jst_time.month, jst_time.day,
                      jst_time.hour, jst_time.minute, jst_time.second)
    end = start.addSecs(4 * 60 + 59)

    tp = layer.temporalProperties()
    tp.setMode(Qgis.RasterTemporalMode.FixedTemporalRange)
    tp.setFixedTemporalRange(QgsDateTimeRange(start, end))
    tp.setIsActive(True)

    QgsProject.instance().addMapLayer(layer, False)
    group.addLayer(layer)

    return layer


def run_nowcast_process(base_jst, hours_back, extent_3857):
    n2_data = get_n2_target_times()
    n2_map, n2_latest_base = extract_basetime_map(n2_data)

    if base_jst.minute % 5 != 0:
        raise RuntimeError("5分単位で指定してください。")

    steps = hours_back * 12
    time_steps = [base_jst - timedelta(minutes=5 * i) for i in range(steps, -1, -1)]
    start_jst = time_steps[0]
    start_str = start_jst.strftime("%Y%m%d%H%M")

    project_path = QgsProject.instance().fileName()
    if not project_path:
        raise RuntimeError("プロジェクトファイルを保存してください。")

    gpkg_path = os.path.join(os.path.dirname(project_path), f"rain_{start_str}.gpkg")
    if os.path.exists(gpkg_path):
        os.remove(gpkg_path)

    temp_dir = os.path.dirname(gpkg_path)

    root = QgsProject.instance().layerTreeRoot()
    group_name = f"雨雲 開始{start_str}"
    group = root.findGroup(group_name)
    if not group:
        group = root.insertGroup(0, group_name)

    progress = QProgressDialog(f"{hours_back}時間分のデータ取得中...", "キャンセル", 0, len(time_steps), iface.mainWindow())
    progress.setWindowTitle("気象庁 ナウキャスト時系列取得")
    progress.show()

    added = []
    now_utc = datetime.utcnow()

    for idx, jst_dt in enumerate(time_steps):
        process_events()
        if progress.wasCanceled():
            break

        target_utc_dt = jst_dt - timedelta(hours=9)
        target_utc_str = target_utc_dt.strftime("%Y%m%d%H%M%S")

        is_future = target_utc_dt > now_utc

        if is_future:
            base_utc_str = n2_map.get(target_utc_str) or n2_latest_base
            if not base_utc_str:
                rounded_min = (now_utc.minute // 5) * 5
                base_utc_str = datetime(now_utc.year, now_utc.month, now_utc.day, now_utc.hour, rounded_min).strftime("%Y%m%d%H%M%S")
        else:
            base_utc_str = target_utc_str

        progress.setValue(idx)
        progress.setLabelText(f"{jst_dt.strftime('%Y/%m/%d %H:%M')} JST を処理中")

        mosaic_png, mosaic_bounds = download_and_merge_tiles(
            base_utc_str, target_utc_str, extent_3857, ZOOM_LEVEL, temp_dir
        )
        if not mosaic_png:
            print(f"No mosaic for {target_utc_str}")
            continue

        table_name = create_geopackage_raster(
            mosaic_png,
            mosaic_bounds,
            extent_3857,
            target_utc_str,
            ZOOM_LEVEL,
            gpkg_path,
            temp_dir
        )

        layer_name = f"雨雲 {jst_dt.strftime('%H:%M')}"
        layer = add_gpkg_layer_with_temporal(gpkg_path, table_name, layer_name, jst_dt, group)
        if layer:
            added.append(layer)

    progress.close()

    if not added:
        raise RuntimeError("GeoPackageへのデータ出力に失敗しました。")

    temporal_dock = iface.mainWindow().findChild(QDockWidget, "TemporalController")
    if temporal_dock:
        temporal_dock.setVisible(True)
        temporal_dock.raise_()

    controller_widget = iface.mainWindow().findChild(QgsTemporalControllerWidget)
    if controller_widget:
        controller = controller_widget.temporalController()

        start_qdt = QDateTime(
            time_steps[0].year, time_steps[0].month, time_steps[0].day,
            time_steps[0].hour, time_steps[0].minute, time_steps[0].second
        )
        end_qdt = QDateTime(
            time_steps[-1].year, time_steps[-1].month, time_steps[-1].day,
            time_steps[-1].hour, time_steps[-1].minute, time_steps[-1].second
        ).addSecs(4 * 60 + 59)

        total_range = QgsDateTimeRange(start_qdt, end_qdt)
        interval = QgsInterval(5, Qgis.TemporalUnit.Minutes)

        controller.setFrameInterval(interval)
        controller.setTemporalExtents(total_range)

        controller_widget.setTemporalRange(total_range)
        controller_widget.setTimeStep(interval)

        try:
            controller_widget.setAnimationMode()
        except Exception as e:
            print(f"Animation mode error: {e}")

    iface.mapCanvas().refresh()

    QMessageBox.information(
        iface.mainWindow(),
        "完了",
        f"{hours_back}時間分（{len(added)}時点）のデータをGeoPackageに統合・追加しました。\n\n"
        f"保存先ファイル: {gpkg_path}\n"
        f"グループ名: {group_name}"
    )


class JPRaincloudradarDownloader:

    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path)

        self.action = QAction(icon, "JPRaincloudradarDownloader", self.iface.mainWindow())
        self.action.triggered.connect(self.show_panel)

        self.iface.addPluginToMenu("JPRaincloudradarDownloader", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("JPRaincloudradarDownloader", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock = None

    def show_panel(self):
        if self.dock is None:
            self.dock = JPRaincloudradarDownloaderDialog()

            dock_area = Qt.DockWidgetArea.LeftDockWidgetArea
            self.iface.addDockWidget(dock_area, self.dock)


            now = datetime.utcnow() + timedelta(hours=9)
            rounded_min = (now.minute // 5) * 5

            self.dock.dateEdit.setDate(now.date())
            self.dock.lineEdit_hour.setText(str(now.hour))
            self.dock.lineEdit_min.setText(str(rounded_min))
            self.dock.lineEdit_hours_back.setText("1")

            canvas = iface.mapCanvas()
            self.dock.extentWidget.setMapCanvas(canvas)
            self.dock.extentWidget.setOutputExtentFromCurrent()

            self.dock.button_download.clicked.connect(self.start_download)

        self.dock.show()

    def start_download(self):
        try:
            date = self.dock.dateEdit.date().toPyDate()
            hour = int(self.dock.lineEdit_hour.text())
            minute = int(self.dock.lineEdit_min.text())
            hours_back = int(self.dock.lineEdit_hours_back.text())

            base_jst = datetime(date.year, date.month, date.day, hour, minute, 0)

            rect = self.dock.extentWidget.outputExtent()
            src_crs = self.dock.extentWidget.outputCrs()

            if rect.isEmpty() or rect.isNull():
                canvas = iface.mapCanvas()
                rect = canvas.extent()
                src_crs = canvas.mapSettings().destinationCrs()

            extent_3857 = transform_rect_to_3857(rect, src_crs)

            run_nowcast_process(base_jst, hours_back, extent_3857)

        except Exception as e:
            QMessageBox.critical(self.iface.mainWindow(), "エラー", str(e))
