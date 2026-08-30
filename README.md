# JPRaincloudradarDownloader

## このプラグインについて
気象庁が公開する雨雲レーダー画像をダウンロードして、Geopackageに保存し、時系列コントロールパネルでアニメーション化する事ができます。
![demo](/img/amagumo.gif)

## インストール方法
インストール用ZIPファイル「JPRaincloudradarDownloader_x.x.zip」をダウンロードして、プラグインをインストールしてください。
インストールすると、プラグインツールバーにアイコンが追加されます。

## 使用方法
プラグインを実行すると、日付と時間を指定し、そのして時間から何時間前からダウンロードするか指定できます。
5分単位で画像をダウンロードします。
追加されたレイヤには、タイムスタンプが記録されているので、時系列コントロールでアニメーションとして動かすことができます。
時系列コントロールパネルの更新ボタン<img width="23" height="25" alt="image" src="https://github.com/user-attachments/assets/c5a7f845-9b46-477c-b18d-886b91346ddd" />
をクリックすると、時間が更新されます。間隔を5分間隔にしてください。
<img width="1522" height="161" alt="image" src="https://github.com/user-attachments/assets/ad6f9b64-1b68-4fe1-9a9a-ba0bfd6d0505" />
※時系列コントロールパネルの時間は世界標準時間なので、日本時間より9時間前になります。

## 気象庁のデータ利用規約
ダウンロードしたデータを公開、利用する場合には、必ず利用規約を確認し、出典等を明示してください。
例）出典：気象庁ホームページ　雨雲レーダー画像(https://www.jma.go.jp/bosai/nowc/)
https://www.jma.go.jp/jma/kishou/info/coment.html
