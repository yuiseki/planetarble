# Planetarble

[![Image from Gyazo](https://i.gyazo.com/aefeffdeb3c3575ff02037a8509c4d7c.png)](https://gyazo.com/aefeffdeb3c3575ff02037a8509c4d7c)

Planetarble は、完全にオープンな地球規模のラスター地図タイルを生成し、オフライン配布可能な単一の PMTiles アーカイブにまとめるプロジェクトです。

プロジェクトは次の 3 つの主要フェーズで構成されます。

1. 必要なデータセット（NASA BMNG 2004、GEBCO 2024 Global Grid、Natural Earth 10 m レイヤー）を整合性チェック付きで取得します。
2. ラスターを加工し、ズームレベル 0–10 をカバーする Web Mercator タイルピラミッドにブレンドします。
3. 出力を付属メタデータやライセンス情報とともに `world_YYYY.pmtiles` としてパッケージ化します。

アポロ 17 号が撮影した「The Blue Marble」の写真は、地球が青と白の繊細な渦を持つ美しい惑星であることを世界に示しました。Planetarble はその精神を受け継ぎ、NASA の Blue Marble Next Generation 画像を活用し、完全にオープンなデータから構築された地球全体のビューを提供します。

## クイックスタート

```bash
# 編集可能モードでのインストール（Python 3.10 以上が必要）
pip install -e .

# グローバルインストールができない場合のフォールバック（リポジトリを直接利用）
PYTHONPATH=src python -m planetarble.cli.main --help

# ソースデータセットをダウンロードし、出力ディレクトリに MANIFEST.json を生成
planetarble acquire --config configs/base/pipeline.yaml

# パッケージをインストールせずに CLI を実行
PYTHONPATH=src python -m planetarble.cli.main acquire --config configs/base/pipeline.yaml

# aria2c が PATH にある場合は再開可能ダウンロードを自動利用
# 必要に応じて無効化
planetarble acquire --config configs/base/pipeline.yaml --no-aria2

# ラスターを前処理（BMNG のモザイク、GEBCO のヒルシェード生成、Natural Earth の展開）
planetarble process --config configs/base/pipeline.yaml

# コマンドを実行せずプレビュー
planetarble process --config configs/base/pipeline.yaml --dry-run

# MBTiles タイルピラミッドを生成（gdal_translate / gdaladdo が必要）
planetarble tile --config configs/base/pipeline.yaml

# PMTiles への変換と配布バンドルの組み立て（pmtiles CLI が必要）
planetarble package --config configs/base/pipeline.yaml
```

既定の構成では生データは `data/`、一時成果物は `tmp/`、最終成果物は `output/` に保存されます。`configs/base/pipeline.yaml` をコピーして編集することでパスやパラメータを調整できます。初回実行時のダウンロード量は約 4.5 GB（BMNG 500 m パネル、GEBCO netCDF、Natural Earth アーカイブ）で、80 Mbps の回線ではダウンロードフェーズにおおよそ 10 分かかります。

## キャッシュと再ダウンロード方針

- 各アセットは `data/` 配下の決定論的なパスに保存されます。再実行時は SHA256 ハッシュ検証後に既存ファイルを再利用します。
- ファイル破損や上流更新が疑われる場合は `planetarble acquire --force`（または同等の `python -m ... acquire --force`）で再ダウンロードを強制できます。
- マニフェストには利用した URL、ファイルサイズ、ハッシュが記録され、パイプライン完了後に `planetarble verify_checksums` で整合性を再確認できます。
- `aria2c` が利用可能な場合は自動的にレジューム機能付きダウンロードを有効化します。見つからない場合は標準の Python ダウンローダーにフォールバックします。また、必要に応じて `--no-aria2` で無効化できます。
- 数 GB 規模の転送は長時間に及ぶ可能性があるため、`screen` や `tmux` を使用し、SSH 切断でも処理が継続するようにすることを推奨します。

## ロードマップ

- 再現性を維持しながらグローバルベースマップ全体でより高解像度の成果物をサポートします。
- Copernicus サービスを通じて Sentinel-2 を取得し、ソースデータが許す地域で高ズームを解放します。
- 優先地域に限定して高ズームタイルを生成し、グローバルバンドルのサイズを抑えます。
- 更新が必要な地域のみを対象とする領域別リフレッシュワークフローを提供します。
- BMNG 正規化、GEBCO ヒルシェード生成、Natural Earth マスク展開、Cloud Optimized GeoTIFF 変換を行う前処理パイプライン（`ProcessingManager`）を実装します。
- タイリング、PMTiles 変換、出力検証のコマンド群を追加します。

## 必要要件

- GDAL 3.x 以上と PMTiles CLI がローカルにインストールされている必要があります。
- Python 依存関係は `pyproject.toml` に記録されています（設定読み込みには PyYAML が必要）。

## 参考

詳細な手順や追加情報は英語版の `README.md` を参照してください。
