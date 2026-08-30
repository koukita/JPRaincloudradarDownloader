# -*- coding: utf-8 -*-

import os
from datetime import datetime, timedelta

from qgis.PyQt.QtCore import QDateTime, QCoreApplication, QVariant, Qt
from qgis.PyQt.QtWidgets import QProgressDialog, QMessageBox, QDockWidget
from qgis.gui import QgsTemporalControllerWidget
from qgis.utils import iface

from qgis.core import (
    QgsVectorLayer,
    QgsProject,
    QgsVectorFileWriter,
    QgsCoordinateTransformContext,
    QgsCoordinateReferenceSystem,
    QgsField,
    QgsFeature,
    QgsDateTimeRange,
    QgsInterval,
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsMarkerSymbol
)

def process_events():
    QCoreApplication.processEvents()

def apply_lightning_style(layer):
    """雷カテゴリに応じたスタイル設定（対地放電: 四角, 雲放電: バツ）"""
    categories = []
    
    # 1) type = "0" : 対地放電（落雷） (黄色い四角)
    symbol_0 = QgsMarkerSymbol.createSimple({
        'name': 'square',
        'color': '255,204,0',
        'outline_color': '0,0,0',
        'outline_width': '0.2',
        'size': '4'
    })
    cat_0 = QgsRendererCategory("0", symbol_0, "対地放電（落雷）")
    categories.append(cat_0)
    
    # 2) type = "4" : 雲放電 (45度回転させたバツ)
    symbol_4 = QgsMarkerSymbol.createSimple({
        'name': 'cross_fill',
        'color': '255,204,0',
        'outline_color': '0,0,0',
        'outline_width': '0.2',
        'size': '4',
        'angle': '45'
    })
    cat_4 = QgsRendererCategory("4", symbol_4, "雲放電")
    categories.append(cat_4)
    
    renderer = QgsCategorizedSymbolRenderer("type", categories)
    layer.setRenderer(renderer)
    layer.triggerRepaint()

def run_lightning_process(base_jst, hours_back):
    if base_jst.minute % 5 != 0:
        raise RuntimeError("5分単位で指定してください。")

    steps = hours_back * 12
    time_steps_jst = [base_jst - timedelta(minutes=5 * i) for i in range(steps, -1, -1)]
    start_jst = time_steps_jst[0]
    start_str = start_jst.strftime("%Y%m%d%H%M")

    project_path = QgsProject.instance().fileName()
    if not project_path:
        raise RuntimeError("プロジェクトファイルを保存してください。")

    gpkg_path = os.path.join(os.path.dirname(project_path), f"rain_{start_str}.gpkg")
    layer_name = "Lightning"

    # ファイルロック解除のため、同名の既存レイヤをQGISプロジェクトから削除
    existing_layers = QgsProject.instance().mapLayersByName(layer_name)
    for el in existing_layers:
        QgsProject.instance().removeMapLayer(el.id())

    progress = QProgressDialog(f"{hours_back}時間分の雷データ取得中...", "キャンセル", 0, len(time_steps_jst), iface.mainWindow())
    progress.setWindowTitle("気象庁 雷ナウキャスト取得")
    progress.show()

    # メモリ上に統合用のベクターレイヤ（WGS84）を作成
    combined_layer = QgsVectorLayer("Point?crs=EPSG:4326", layer_name, "memory")
    pr = combined_layer.dataProvider()

    processed_count = 0
    total_features = 0
    fields_initialized = False

    for idx, jst_dt in enumerate(time_steps_jst):
        process_events()
        if progress.wasCanceled():
            break

        # JST から UTC へ変換
        target_utc_dt = jst_dt - timedelta(hours=9)
        target_utc_str = target_utc_dt.strftime("%Y%m%d%H%M%S")

        progress.setValue(idx)
        progress.setLabelText(f"{jst_dt.strftime('%Y/%m/%d %H:%M')} JST の雷データを処理中")

        geojson_url = f"https://www.jma.go.jp/bosai/jmatile/data/nowc/{target_utc_str}/none/{target_utc_str}/surf/liden/data.geojson?id=liden"
        temp_layer = QgsVectorLayer(geojson_url, "temp_geojson", "ogr")

        if not temp_layer.isValid() or temp_layer.featureCount() == 0:
            continue

        # 初回ヒット時にフィールド構造を初期化（obstimeUTC を日付時刻型で追加）
        if not fields_initialized:
            new_fields = temp_layer.fields()
            new_fields.append(QgsField("obstimeUTC", QVariant.DateTime))
            pr.addAttributes(new_fields)
            combined_layer.updateFields()
            fields_initialized = True

        # 「2026/08/30 13:09:04 (UTC)」形式の文字列を作成してQDateTimeに変換
        utc_formatted_str = target_utc_dt.strftime("%Y/%m/%d %H:%M:%S (UTC)")
        qdt_utc = QDateTime.fromString(utc_formatted_str, "yyyy/MM/dd HH:mm:ss (UTC)")

        new_features = []

        for feat in temp_layer.getFeatures():
            attrs = feat.attributes() + [qdt_utc]
            new_feat = QgsFeature(combined_layer.fields())
            new_feat.setGeometry(feat.geometry())
            new_feat.setAttributes(attrs)
            new_features.append(new_feat)

        pr.addFeatures(new_features)
        processed_count += 1
        total_features += temp_layer.featureCount()

    progress.close()

    if total_features > 0:
        combined_layer.updateExtents()

        # 書き込みオプションの設定
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer_name
        options.fileEncoding = "UTF-8"
        
        if os.path.exists(gpkg_path):
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        else:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

        res = QgsVectorFileWriter.writeAsVectorFormatV3(
            combined_layer,
            gpkg_path,
            QgsCoordinateTransformContext(),
            options
        )

        err = res[0]
        err_msg = res[1] if len(res) > 1 else ""

        if err != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"GeoPackageの保存に失敗しました: {err_msg}")

        # エクスポートしたGeoPackageから描画用レイヤを読み込み
        gpkg_uri = f"{gpkg_path}|layername={layer_name}"
        vector_layer = QgsVectorLayer(gpkg_uri, layer_name, "ogr")

        if vector_layer.isValid():
            # 1. スタイル（カテゴリシンボル）の適用
            apply_lightning_style(vector_layer)

            # 2. 時系列（Temporal）設定 - フィールドを 「obstimeUTC」 に指定
            tp = vector_layer.temporalProperties()
            tp.setMode(Qgis.VectorTemporalMode.FeatureDateTimeInstantFromField)
            tp.setStartField("obstimeUTC")
            tp.setIsActive(True)

            # 3. マップに追加
            QgsProject.instance().addMapLayer(vector_layer)

            # 4. 時系列コントローラの表示・範囲設定
            temporal_dock = iface.mainWindow().findChild(QDockWidget, "TemporalController")
            if temporal_dock:
                temporal_dock.setVisible(True)
                temporal_dock.raise_()

            controller_widget = iface.mainWindow().findChild(QgsTemporalControllerWidget)
            if controller_widget:
                controller = controller_widget.temporalController()

                start_utc_dt = time_steps_jst[0] - timedelta(hours=9)
                end_utc_dt = time_steps_jst[-1] - timedelta(hours=9)

                start_qdt = QDateTime(
                    start_utc_dt.year, start_utc_dt.month, start_utc_dt.day,
                    start_utc_dt.hour, start_utc_dt.minute, start_utc_dt.second
                )
                end_qdt = QDateTime(
                    end_utc_dt.year, end_utc_dt.month, end_utc_dt.day,
                    end_utc_dt.hour, end_utc_dt.minute, end_utc_dt.second
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
                f"雷データのダウンロードが完了しました。\n"
                f"取得時点数: {processed_count}\n"
                f"検出された雷の合計: {total_features} 点\n\n"
                f"保存先: {gpkg_path}\n"
                f"レイヤ名: {layer_name}"
            )
    else:
        QMessageBox.warning(
            iface.mainWindow(),
            "情報",
            f"指定された時間帯（{hours_back}時間前まで）に発生した雷データはありませんでした。"
        )