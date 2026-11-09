# Planetarble

[![Image from Gyazo](https://i.gyazo.com/aefeffdeb3c3575ff02037a8509c4d7c.png)](https://pmtiles.io/#url=https%3A%2F%2Fz.yuiseki.net%2Fstatic%2Fplanetarble%2Fplanet.pmtiles&map=1.88/0/0)

Planetarble は、完全にオープンなデータだけから地球規模のラスター地図タイルを作り、オフラインで配布できる単一の PMTiles アーカイブにまとめるプロジェクトです。

アポロ 17 号が撮影した「The Blue Marble」の写真は、地球が青と白の繊細な渦を持つ美しい惑星であることを世界に示しました。Planetarble はその精神を受け継ぎ、NASA の Blue Marble Next Generation (BMNG) をはじめとするオープンな画像から地球全体のビューを作ります。

パイプラインは次の 4 段階です。

1. acquire: 元データを取得するか、取得の計画を作ります。
2. process: 画像をモザイクし、海の陰影を作ります。
3. tile: モザイクを MBTiles のタイルピラミッドに切ります。
4. package: MBTiles を PMTiles に変換し、TileJSON と出典のファイルを添えて配布用にまとめます。

どの画像を使うかは `processing.tile_source` で選びます。`hls` (設定しないときの既定。HLS v2 の 30 m 画像)、`bmng` (BMNG と GEBCO と Natural Earth による全球の地図)、`sentinel2` (Microsoft Planetary Computer の Sentinel-2 L2A)、`gsi_orthophotos` (国土地理院のシームレス空中写真) のほか、試験的な `modis`、`viirs`、`copernicus` があります。

## 5 分で試す

NASA Blue Marble (2 km) から、小さいけれど全球そろった PMTiles を作ります。テラバイト級のデータは要らず、イメージをビルドしたあとは 1 分もかかりません。

```bash
docker build -t planetarble .
mkdir -p workspace
docker run --rm -v "$PWD/workspace:/workspace" --entrypoint bash planetarble -c '
  curl -sL -o bmng.jpg "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73776/world.topo.bathy.200408.3x5400x2700.jpg" &&
  gdal_translate -q -a_srs EPSG:4326 -a_ullr -180 90 180 -90 bmng.jpg bmng.tif &&
  planetarble tiling pmtiles --input bmng.tif --out . --min-zoom 0 --max-zoom 5 --format webp --bounds-mode global'
```

`workspace/bmng_0-5.pmtiles` (約 4 MB、ズーム 0-5、全球) ができます。<https://pmtiles.io> にドロップすると見られます。[必要要件](#必要要件) の道具がそろっていれば、Docker なしでも同じ 3 つのコマンドで動きます。

## ローカル環境の構築 (uv)

Python の依存は [uv](https://docs.astral.sh/uv/) で管理し、`uv.lock` で版を固定しています。ただし GDAL の Python バインディング (`osgeo`) と numpy は OS のパッケージから使います。バインディングはシステムの libgdal と版をそろえる必要があり、`gdal_array` はシステムの numpy に合わせてビルドされているためです。venv はシステムの Python で、system site-packages が見える状態で作ります。

```bash
sudo apt install gdal-bin python3-gdal   # GDAL の CLI とバインディング (python3-numpy も入る)
uv venv --python /usr/bin/python3 --system-site-packages
uv sync                                  # プロジェクトの依存と dev グループ (pytest)
uv run pytest
uv run planetarble --help
```

地理院タイルの収集だけをするホストでは GDAL も numpy も不要で、`uv sync` だけで動きます (`docs/operations/collection-node.md` を参照)。その場合 GDAL と numpy を使うテストはスキップされます。

conda の環境が有効になっていると、`GDAL_DATA`、`GDAL_DRIVER_PATH`、`PROJ_DATA` がシステムの GDAL に conda のプラグインとデータを読ませてしまい、GDAL を使うテストが失敗します。先に conda を無効にするか、`env -u GDAL_DATA -u GDAL_DRIVER_PATH -u PROJ_DATA uv run pytest` のように外してください。

### 開発時の検査

CI は push と pull request のたびに次を実行します。

```bash
uv run ruff check   # pyflakes、pycodestyle、import の並び、bugbear
uv run mypy         # src/ の段階的な型検査
uv run pytest
```

モジュールの構成は `docs/architecture.md`、設計の判断は `docs/adr/` にまとめています。

リファクタリングで変えてはいけない振る舞いを、golden ファイルで固定しています。`tests/unit/cli/golden/parser.json` は CLI のすべてのオプションと既定値と help の文面、`tests/unit/processing/golden/` は `ProcessingManager` が組み立てる GDAL のコマンド列、`tests/unit/tiling/golden/` は `planetarble tile` のコマンド列です。意図して変えるときは `PLANETARBLE_UPDATE_GOLDEN=1 uv run pytest` で作り直し、差分を確かめてください。

設定ファイルは読み込むときに検査します。知らないセクションやキー、型の違う値 (たとえば `max_zoom: twelve`) があると、その名前を示して止まります。

## クイックスタート

全球の BMNG 地図 (ズーム 0-8、下の配布物と同じもの) を作る例です。`configs/profiles/` のほかのレシピも同じ流れで、各ファイルの冒頭に実行するコマンドが書いてあります。

```bash
# 「ローカル環境の構築 (uv)」の手順で venv を作ってから有効にする
source .venv/bin/activate

# data/、tmp/、output/ は、今いるディレクトリでなく設定ファイルのあるディレクトリからの相対パスになる
# そのため、プロファイルを作業ディレクトリにコピーしてから使う
mkdir -p workspace && cp configs/profiles/bmng-global-z8.yaml workspace/ && cd workspace

# BMNG 500 m、GEBCO、Natural Earth と (ocean.enabled なら) ETOPO を取得し、output/MANIFEST.json を書く
# (aria2c が PATH にあれば再開できるダウンロードに使う。--no-aria2 で無効にできる)
planetarble acquire --config bmng-global-z8.yaml

# モザイク、陰影、正規化 (--dry-run を付けるとコマンドを表示するだけ)
planetarble process --config bmng-global-z8.yaml

# output/tiling/planet_2024_8z.mbtiles を作る
planetarble tile --config bmng-global-z8.yaml

# PMTiles に変換し、output/distribution/ にまとめる (PMTiles、TileJSON、出典の <名前>.LICENSE_AND_CREDITS.txt)
planetarble package --config bmng-global-z8.yaml

# 同梱の MapLibre ビューアで見る (http://localhost:8081/viewer/)
planetarble serve --pmtiles output/distribution/planet_2024_8z.pmtiles --open
```

パッケージを入れずに動かすときは、リポジトリの直下で `PYTHONPATH=src python -m planetarble.cli.main` を使います。

用意してあるレシピは `bmng-global-z8.yaml` (全球 BMNG 500 m と GEBCO の海、ズーム 0-8)、`hls-regional-z11.yaml` (都道府県や miniplanet ごとの HLS v2、ズーム 11)、`sentinel2-tokyo-z14.yaml` (東京周辺の Sentinel-2 L2A、ズーム 14) です。コピーして自分の設定を作ってください。`configs/base/pipeline.yaml` は開発者の作業用の設定で、今は国土地理院の空中写真を選んでいるため、全球のパイプラインの出発点には向きません。`ocean.enabled` が true のとき、`planetarble acquire` は NOAA ETOPO 2022 の 15 秒角の GeoTIFF (圧縮で約 9 GB) も取得します。`gsi_orthophotos` のときは何も取得しません。国土地理院のタイルは `planetarble process` が直接取りに行くためです。

地域ごとの HLS の作り方、AOI ごとに高解像度の画像を重ねる `planetarble build`、画質の調整は、英語版の `README.md` を参照してください。

## キャッシュと再ダウンロード

- 各アセットは `data/` の下の決まった場所に保存し、次の実行では既存のファイルを使います。アセットの目録にチェックサムがあるときは、ファイルの SHA256 がそれと一致しなければなりません。
- ファイルの破損や上流の更新が疑われるときは、`planetarble acquire --force` で取り直せます。
- `output/MANIFEST.json` に、使った URL、ファイルサイズ、SHA256 を記録します。
- 数 GB の転送は時間がかかるため、`screen` や `tmux` の中で動かすと SSH が切れても続きます。

## ロードマップ

全球の土台に AOI ごとの高解像度の画像を重ねる仕組みはできています (`planetarble build`、[ADR 0001](docs/adr/0001-aoi-overlay-architecture.md))。残っているのは次のことです。

- 再現性を保ったまま、地図全体をより高い解像度にする。
- 新しい画像が必要な地域だけを更新する仕組みを作る。
- 見た目の差分を自動で比べる検査を足し、`planetarble tiling pmtiles` と同じように `planetarble package` でも `pmtiles verify` を走らせる。

## 必要要件

同梱の `Dockerfile` には以下がすべて入っています。ホストで動かしたいときだけ手で入れてください。

- GDAL 3.11 以上 (`gdalbuildvrt`、`gdal_translate`、`gdaldem`、`gdalwarp`、`gdaladdo` と、`gdal raster tile` に使う `gdal` コマンド)。
- 最終的な PMTiles を作る go-pmtiles の CLI (`pmtiles convert`)。
- XYZ タイルを MBTiles にまとめる `mb-util` (`pip install mbutil`)。
- 再開できるダウンロードのための `aria2c`。無ければ Python の標準のダウンローダーを使いますが、転送の中断に備えて入れておくことを勧めます。
- Python の依存は `pyproject.toml` にあります。地域ごとの HLS / Sentinel-2 の計画には GDAL の Python バインディング (`osgeo`) も要ります。

## データソース

日本以外でも、重ね合わせ (overlay) に、ログイン不要の ESA WorldCover の 10 m 合成画像 (陸域ならどこでも)、米国の NAIP、ニュージーランドの LINZ の画像、OpenAerialMap、URL を指定した任意のオープンな COG を使えます。見本は `configs/overlays/world-cities-example.yaml`、設計は ADR 0003 です。使ったデータソースの出典は、作った PMTiles に書き込まれます。

対応しているデータソースの解像度、最大ズーム、ライセンス、出典表記は [docs/sources/](docs/sources/README.ja.md) にまとめています。
