# Mahjong Annotation Editor / 学習データ管理

麻雀牌認識AIの画像をバッチで取り込み、BBoxを確認・編集し、canonical37 COCOの不変な学習バージョン、ZIP、Google Drive公開用スナップショットを作るローカルアプリです。既存のYOLO編集画面と旧データの閲覧・編集は維持します。学習モデル、学習、評価、ONNX生成は行いません。

## 保存場所とGit

標準のデータ保存先はリポジトリの **`../data/`** です。`ANNOTATION_DATA_ROOT=/別のパス ./start.sh` で変更できます。新機能は次を必要時に作ります。既存の別構成フォルダーは移動・削除しません。

```text
../data/
├── sources/<バッチ名>/             # 取り込み元の複製、または手動配置したバッチ
├── registry/registry.sqlite       # ハッシュ、確認状態、由来、履歴
├── versions/<v001など>/           # 独立したCOCOバージョン
├── exports/{*.zip,latest.json}    # ZIPスナップショット
├── backups/                       # 新しいCOCO保存前バックアップ
└── tmp/                           # 一時領域
```

Git管理対象はコード、テスト、設定例、ドキュメントだけです。画像、ラベル、SQLite、ZIP、バックアップ、認証情報は追加しないでください。`../data/` はこのリポジトリの外です。旧エディターの `state/` と `backups/` は自動移行せず残します。新規登録画像の確認状態はSQLiteで管理します。

## 初期セットアップと起動

Python 3.10以上とNode.js 20以上が必要です。

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
./start.sh
```

<http://127.0.0.1:5173> を開きます。APIは127.0.0.1:8000です。`Ctrl+C`で停止します。既存の作業を開くにはフォルダーツリーから選択し、新機能を使うには右上の「データ管理」を押します。

## 取り込み

「データ取り込み」で元フォルダー欄または「参照…」をクリックすると、OSのフォルダー選択ダイアログが開きます（ローカル環境の `zenity` を使用）。パスの直接入力もできます。フォルダーを選ぶと、そのフォルダー名がバッチ名の初期値になります。手入力したバッチ名は後からフォルダーを選び直しても保持します。画像種類、バッチ名、取得元を確認して「検証・プレビュー」を押します。画像破損、BBox、クラス、COCO構造、画像・ラベルの対応、重複、annotation競合、manifestの件数・ハッシュを表示します。通常の検証エラーは修正してから登録してください。コピー取り込みでは、既存画像とannotationが異なる画像と対応するラベルを複製先から除外し、残りを再検証して登録します。元フォルダーと既存annotationは変更しません。競合が全件なら登録できません。`sources内を登録` と prepared / merged / 完成dataset の競合は除外できないため、元データを修正してから登録してください。

- **コピー**: 標準。元フォルダーを残し、検証済み複製を `sources/` へ登録します。
- **移動**: 明示的に選んだ場合だけ、一時複製・検証・DB登録後に指定元を削除します。競合画像を除外した場合は元画像を守るため、元フォルダー全体を残します。
- **sources内を登録**: `../data/sources/<バッチ名>` に手動配置したフォルダーをそのまま登録します。同じバッチの再登録は冪等です。

対応形式は画像のみ、YOLO Detection、COCO Detection、Roboflow YOLO/COCO、`mahjong-tile-ai` prepared、merged、完成datasetです。画像のみの取り込み後はエディターでBBoxを追加できます。YOLOの `data.yaml` は37クラスの `names` が必要です。正規名は `red5m/red5p/red5s` です。旧名 `5mr/5pr/5sr` と `0m/0p/0s` は読み込み時に対応し、取り込み先・編集保存先・新規バージョンのクラス定義は正規名で書きます。既存のクラスIDは変更せず、完全な `class_mapping` がある場合は対応関係も正規化します。COCOも37クラス以外は完全なmappingを要求します。クラスを推測しません。YOLO class IDは0～36、学習用COCO category IDは1～37です。0は学習モデル内部のbackgroundです。

混在バッチではCOCO画像レコードの `source_type`、またはルートの `source_types.json` に `{"images/a.jpg":"real","images/b.jpg":"synthetic"}` と書けます。欠けた画像種類には画面の選択値を使います。

preparedは `images/`、`annotations/instances.json`、`manifest.json` を持つフォルダーを選びます。由来manifestはバッチの管理情報へ保持します。mergedは同じ構成でmanifestの `dataset_type: merged` を識別し、`versions/` の未分割バージョンとして登録します。完成datasetは `train/val/test` と `dataset_manifest.json` を持つフォルダーを選び、完成バージョンとして登録します。どちらも取り込み画面からプレビュー後に登録します。これらを登録しても元フォルダーはコピー操作なら残ります。取り込み後、元データを削除するかどうかはユーザーが確認して判断してください。アプリは外部の分散データを自動整理しません。

## VPSから学習データを取得

「VPSから取得」でSSH接続先（`user@host` または `~/.ssh/config` のホスト名）とVPSの保存ルートを入力します。VPSの `<保存ルート>/data-versions/` から `READY` がある `samplesYYYYMMDD-NN` 形式の版を一覧表示します。版を選ぶと、`rsync` で `sources/<版名>/` に直接転送し、`meta/checksums.sha256` の全ファイルを検証してバッチ登録します。画像はローカルに1ファイルだけ保存します。元の `annotations/yolo/` から編集用の `labels/` と、スマホの元予測を保管する `labels.org/` を同じバッチ内に作り、`data.yaml` を生成します。`labels.org/` はエディターで変更せず、再取得時も上書きしません。登録成功後に重複する元の `annotations/yolo/` を削除します。転送失敗時の `.partial` フォルダーは再実行時に利用します。スマホ由来のアノテーションは「仮アノテーション」から始まり、編集画面で確認・修正できます。登録済みの版を再実行しても編集済みラベルを上書きしません。以前に取り込んで元の `annotations/yolo/` を削除済みの版には、元予測を後から復元できません。

PCに `ssh` と `rsync` をインストールし、アプリを起動するユーザーのSSH鍵とVPSのホスト鍵を事前に設定してください。GUIに認証情報は保存しません。`TRAINING_DATA_SSH_HOST` と `TRAINING_DATA_HOST_DIR` で接続先と保存ルートの初期値を指定できます。保存先は `../data/sources/<版名>/` です。確認済みの画像だけを学習用の `versions/vNNN` に組み込めます。作成済みの学習用バージョンは独立したスナップショットとして画像を保存します。

VPSの版一覧を取得できた接続先と保存ルートはブラウザの `localStorage` に保存し、次回はその値を入力欄に表示します。接続に失敗した値は保存しません。ブラウザに保存値がない場合は、上記の環境変数を初期値に使います。

## YOLO / COCO編集と確認状態

編集画面ではBBoxの追加、移動、サイズ変更、牌種変更、削除、Undo/Redo、拡大縮小、前後移動、キーボード操作を利用できます。未保存変更のまま画面を切り替えると確認が出ます。YOLOは従来どおり `.txt` に保存します。COCOは元JSON内の対象画像のannotationだけ更新し、`bbox`、`area`、一意のannotation IDを保存します。両形式とも保存前バックアップ、外部更新との競合検出、原子的置換を使います。元画像は更新しません。

状態は未アノテーション、仮アノテーション、未確認、確認済み、保留です。既存の `unreviewed/reviewed/held` も使用できます。YOLO取り込みは仮アノテーション、COCO取り込みは未確認から始まり、自動で確認済みにはなりません。「確認済み・次へ」で確認します。確認済みのannotationを保存し直すと未確認に戻ります。バージョン作成時は確認済みだけ選ばれます。

データ管理の各バッチにある「アノテーションでDBを更新」は、現在のラベルファイルを検証して登録済みDBのBBoxを同期します。内容が変わった画像は未確認に戻り、丸め誤差だけの更新では確認状態を維持します。画像の増減や不正なラベルがある場合は、DBを変更せずに失敗します。

全件確認などの処理中は画面を切り替えられます。処理はバックエンドで継続し、同じタブでデータ管理画面へ戻ると進捗と完了・失敗結果を再表示します。全件確認中にannotationが変更された場合は一括更新を取り消します。完成バージョン作成、既存datasetと同じ分割での完成バージョン作成、未分割バージョン作成中は進捗モーダルが表示され、完了または失敗までアプリ内の画面切り替えと他の操作ができません。ブラウザを再読み込みした場合も同じタブで進捗表示を復元します。

## バージョン、TEST、ZIP

「バージョン管理」で対象バッチ、実写TEST枚数、合成TEST枚数、Validation比率、seedを指定して新規作成します。TESTを後で決める場合は「未分割バージョン作成」を選び、`images/`、`annotations/instances.json`、`manifest.json` の不変なスナップショットを先に保存できます。この版から完成版を作るには、各画像の内容が現在の確認済みannotationと一致する必要があります。検証済みのバッチをまとめて確認する場合は、データ管理画面の「全件確認済み」を明示操作します。枚数不足や不正データでは完成版を作成しません。親バージョンを選んで追加すると、新しい `vNNN` を作ります。標準の「既存TESTを固定」は親のTEST画像、annotation、COCOファイル、TEST構成IDを完全に継承し、追加画像はtrain/valにのみ入れます。チェックを外すと実写・合成の指定枚数でTESTを再作成し、以前との単純比較ができなくなります。未分割mergedを親にした場合は分割して完成版を作ります。

既存の完成datasetと**同じ画像・annotation・train/val/test分割**を元データから作り直す場合は、全画像を確認済みにした後、親に未分割バージョンを選び、「既存datasetと同じ分割で新規作成」に参照datasetのフォルダーを入力します。参照datasetは読み取り専用で、画像本体は登録済みの `sources/` からコピーします。画像ハッシュ、BBox、source_type、全画像集合が一致しない場合は作成を中止します。TEST画像とannotationのハッシュ、TEST構成ID、dataset IDを参照datasetと一致させます。元の完成datasetを取り込んだり変更したりする必要はありません。後続のバージョンでTESTを固定する場合は、この新しい完成版を親に選びます。

各完成版は `versions/vNNN/{train,val,test}/{images,labels/instances.json}` と `dataset_manifest.json` を持ちます。画像は安全性を優先して通常コピーし、バージョン作成後の元annotation変更の影響を受けません。COCOはcanonical37です。「検証」で構成・ハッシュを確認し、「ZIP」で `exports/` にColab互換ZIPと `latest.json` を生成します。ZIP直下は `train/`、`val/`、`test/`、`dataset_manifest.json` です。Colabは `latest.json` が示すZIPのSHA-256を検証して展開できます。

## Google Drive

`rclone` をインストールし、`rclone config` で `gdrive` remoteを設定してください。認証情報はrclone側に保管し、ブラウザへ返しません。アップロード先は `gdrive:MahjongTileAI/dataset/` です。「Driveへアップロード」で確認ダイアログ後に実行します。通常の閲覧・検証・ZIP作成ではアップロードしません。

アップロード時はバージョン検証、ZIP生成、SHA-256計算、`rclone copyto --immutable` によるZIP転送、リモートのサイズ・ハッシュ検証を行い、最後に `latest.json` を転送します。失敗時は `latest.json` を更新しません。過去ZIPは残します。ローカルの `latest.json` にはsnapshot ID、ファイル名、日時、各splitと合計件数、ZIP SHA-256、dataset ID、manifest SHA-256を保存します。rclone未設定時は画面/APIに設定案内を出します。

## バックアップ、復旧、整理

`../data/` 全体を定期的に別媒体へバックアップしてください。個別annotationの復旧には `../data/backups/`、旧YOLO編集分にはリポジトリ内の従来の `backups/` を確認します。SQLiteと `sources/`、`versions/` をセットで復旧します。アプリは既存データや過去バージョンを自動削除しません。不要な元フォルダーやZIPは内容とバックアップを確認してから手動で整理してください。

## テスト

```bash
cd backend && ../.venv/bin/python -m pytest tests -q
cd ../frontend && npm test && npm run build
```

テストは一時ディレクトリに少量の画像を生成します。学習側の `dataset_validation.validate_coco_split` を利用できる環境では生成ZIPを展開して照合します。実学習、GPU、実Driveアップロード、既存実データの移行や変更は行いません。
