# miniplanets を plan_region に採用する設計

`@geolonia/osm-miniplanets` / `duodecim` の分割スキームを planetarble の HLS plan_region に組み込むための設計メモ。

## e2e 検証ログ（2026-06-03, Python 3.9 + GDAL 3.13 バイナリ環境）

機能本体（miniplanet plan + split）と下流 machinery を実 CLI で確認:

1. **miniplanet 機能（GDAL/ネットワーク不要）**: `tile_source: hls` / `plan_regions` 空 / `ocean` 無効の最小 config で `acquire` → z6 グローバルプラン **3544 land タイル**を生成、各エントリに `miniplanet` タグ。`split-plan` → **18 shard**に分割。合計 3544 で欠落・重複ゼロ、全 shard のタグが shard ID と一致、shard サイズ 180〜217（平均 ~197 の ±10%、RCB 均衡が実データで確認）。
2. **下流 tiling/packaging machinery（実 BMNG データ）**: BMNG 2km 全球 JPG(2.3MB) を取得 → `gdal_translate` で EPSG:4326 GeoTIFF 化 → `tiling pmtiles`（実 `PmtilesTilingManager`: `gdal raster tile` → `mb-util` → `pmtiles convert` → verify）で z0-5 を生成。出力 `*.pmtiles`(3.7MB, spec v3, webp, 全球 Web Mercator, 1365 タイル, clustered) を `pmtiles show`/`pmtiles tile` で検証、抽出タイルは正常な 256x256 WEBP RGB（実画素 mean71/std60）。

補足:
- レガシー BMNG の `process`（および `acquire` のレガシー経路）は海洋陰影合成のため **GEBCO 2024 NetCDF ~7.5GB + Natural Earth を必須**とする。今回は機能検証に不要なため `process` を迂回し、BMNG ラスターを直接 `tiling pmtiles` に投入した。フル海洋合成まで通すなら GEBCO 取得が前提。
- ローカル検証用に user-site へ `requests/pyyaml/pystac/pystac-client/mbutil` を導入、`pmtiles` は brew で導入（いずれも実行時の追加依存で本体コードには影響しない）。

## 背景: 3 リポジトリの読み込み結果

| リポジトリ | 役割 | 核 |
|---|---|---|
| `unvt-duodecim` | 全球を 12 分割（`index.js` に z6 タイル座標の bbox 配列） | 行政界に依存しない決定論的分割 |
| `osm-miniplanets` | duodecim 改良版。全球を **18 分割**（`SUBDIVISIONS`）。`tileToMiniplanetId(tile)` / `generateGeoJSON()` / `calculateMiniplanetTiles()` | サイズ均衡・安定 ID・タイルマージ |
| `osm-miniplanets-util` | ID → bbox → `osmium extract` で planet.osm.pbf を切り出す運用 CLI | ID 単位の抽出ワークフロー |

`SUBDIVISIONS` は z6 タイル座標系（64×64 グリッド）での 18 個の bbox `[minx, miny, maxx, maxy]`。

## 決定的な発見: 厳密な全球分割である

`SUBDIVISIONS` の 18 bbox を z6 グリッドに展開して検証した結果:

- z6 タイル総数 4096（= 64×64）を **全て**カバー
- **重複 0** タイル / **欠落 0** タイル

つまり 18 の miniplanet は地球全体を**互いに素に分割**している。各 z6 タイルは必ずちょうど 1 つの miniplanet に属す。

miniplanet あたりの z6 タイル数（ラスターのおおよその処理量の代理指標）:

```
00:128 01:512 02:128 03:128 04:16 05:4 06:4 07:4 08:4
09:96 10:512 11:64 12:64 13:64 14:64 15:256 16:1024 17:1024
```

(16=南半球低緯度帯, 17=北極帯 が広いのは Web Mercator の面積歪み由来。OSM pbf サイズで均衡化された分割なので、ラスター量とは完全一致しない。後述)

## 現状の plan_region との比較

planetarble は現在 `hls.plan_regions` を **Natural Earth admin_1（都道府県を手で列挙）** で定義している（`configs/base/pipeline.yaml` に 23 都府県）。

処理フロー（`acquisition/manager.py` → `acquisition/hls.py`）:

1. `acquire --plan-region <name>` → `build_hls_plans(selected_region=name)`
2. `_build_hls_plan_for_region`: `load_region_geometry(region)` で bbox or Natural Earth ジオメトリを取得
3. `land_only` なら `load_land_geometry`（`ne_10m_land` を region で clip）
4. `write_plan`: 全 z10 タイルを走査し、`region_geometry.Intersects(tile_bbox)` で絞り込んで ndjson 出力
5. 出力名: `hls_z10_plan_{region.name}.ndjson` / タイルは `planet_hls_{region}_{z}z.mbtiles`

### 行政界方式の問題点

- Natural Earth ダウンロード + 空間フィルタが前提（依存が重い）
- 区画サイズが極端に不均一（東京 vs 北海道で桁違い）= 並列ワーカーの負荷偏在
- 都道府県を手で足し続ける運用 = グローバル展開でスケールしない
- 隣接県の境界 z10 タイルが**複数 region に二重計上**される（無駄な再取得・再処理）

### miniplanet 方式の利点

- **全球を漏れ・重複なく ~18 ユニットに分割**（境界の二重計上が原理的に起きない）
- Natural Earth 非依存で決定論的（ID だけで bbox が決まる）
- レート制限下のインクリメンタル取得（`acquire` の本来の狙い）と噛み合う
- **OSM ベクター miniplanets と同一 ID** で raster planet.pmtiles をシャーディングできる
  → geolonia の tile-generate エコシステムでベクター/ラスターの運用単位が揃う（最大の相互運用メリット）

## 設計

### Phase 1 — miniplanet を plan_region として使えるようにする（最小実装）

**1. `SUBDIVISIONS` を Python に移植**: `src/planetarble/acquisition/miniplanets.py`（新規）

```python
BASE_ZOOM = 6
SUBDIVISIONS = (  # z6 タイル座標 (minx, miny, maxx, maxy)。HLS land 量で再均衡化（後述）
    (0,0,16,11), (0,12,16,25), (17,0,31,13), ...,  # 18 領域、tools/gen_miniplanets.py が生成
)

def miniplanet_ids() -> list[str]: ...          # ["00".."17"]
def miniplanet_geo_bbox(mp_id: str) -> tuple:   # z6タイルbbox → 経緯度 bbox
def tile_to_miniplanet_id(z, x, y) -> str|None: # z>=6 のタイル → ID（z6祖先の内包判定）
def compute_subdivisions(weight, n, extent): ... # RCB 分割（純関数）
```

技術（z6 bbox 分割 + 内包判定）は public な osm-miniplanets / duodecim の `index.ts` / `index.js` を参照。区画値そのものは `compute_subdivisions`(RCB) で planetarble の land 量に合わせて再生成する。Node 実行時依存は不要。

**2. `HLSPlanRegion` に `miniplanet` フィールド追加**（`core/models.py`）

```python
@dataclass(frozen=True)
class HLSPlanRegion:
    name: str
    bbox: Optional[Tuple[float,float,float,float]] = None
    natural_earth: Optional[NaturalEarthRegion] = None
    miniplanet: Optional[str] = None   # 追加: "00".."17"
    land_only: bool = False
```

**3. `load_region_geometry` を拡張**（`acquisition/hls.py`）: `region.miniplanet` があれば `miniplanet_geo_bbox(id)` → `_bbox_to_geometry`。bbox/natural_earth より優先 or 排他。

**4. config loader 対応**（`config/loader.py`）: `hls.plan_regions[].miniplanet` を読む（`sentinel2` 側も同様）。

**5. config 例**（手列挙の都道府県を置換 or 併存）:

```yaml
hls:
  plan_regions:
    - { name: "mp_00", miniplanet: "00", land_only: true }
    - { name: "mp_01", miniplanet: "01", land_only: true }
    # ... mp_17 まで（全球を land_only で網羅）
```

これで `acquire`（region 無指定）→ 18 プラン生成、`acquire --plan-region mp_00` → 単一 miniplanet をインクリメンタル取得。`land_only` の clip は既存 `load_land_geometry` がそのまま機能する（region ジオメトリ = miniplanet bbox）。

### Phase 2 — plan エントリに miniplanet ID を付与しシャーディング（発展）

- `HLSMosaicTask` に `miniplanet` を追加し、`write_plan` で `tile_to_miniplanet_id(z,x,y)` を計算して ndjson に記録
- tiling / packaging を miniplanet ID 単位でグルーピング → **per-miniplanet MBTiles → planet.pmtiles へマージ**
- osm-miniplanets-util の「ID → 抽出」運用とミラーになり、巨大全球ビルドの再開・分散がしやすくなる
- ベクター miniplanets と ID が一致するので、`planet_{id}.pmtiles`（ラスター）と vector tiles を同じ単位で配信・差分更新できる

## TDD 方針

- `tests/unit/acquisition/test_miniplanets.py`
  - **分割不変条件**: 18 bbox が z6 4096 タイルを重複 0・欠落 0 でカバー（本メモで検証済みの内容を回帰テスト化）
  - **整合性**: `tile_to_miniplanet_id` と `calculateMiniplanetTiles` 相当の一貫性（osm-miniplanets の同名テストに対応）
  - **golden**: Python `miniplanet_geo_bbox` の出力が `@geolonia/osm-miniplanets` の `generateGeoJSON()` と一致（npm 出力を fixture 化して突き合わせ → 表のドリフト検知）
- `tests/unit/config/test_loader_hls_plan_regions.py` に miniplanet フィールドのロードを追加
- `tests/unit/acquisition/test_manager_hls_plan_regions.py` に miniplanet region のプラン生成を追加

## 決定事項（確定済み）

1. **18 分割**（miniplanets と同じ区画数）
2. **HLS ラスター量で再均衡化** — osm-miniplanets の表（OSM pbf バイト数で均衡）はそのまま使わず、planetarble がプラン化する land ZL10 タイル数で再計算。結果 18 領域すべてが land の **5.2%〜5.8%**（理想 5.56%）に収まる（元の osm-miniplanets は z6 タイル数で 4〜1024 と極端にばらついていた）。ID はベクター miniplanets とは一致しない（負荷均衡を優先）
3. **都道府県 plan_regions と併存**（`configs/base/pipeline.yaml` に 23 都道府県 + 18 miniplanet = 41 領域）。日本国内検証は都道府県、全球は miniplanet、と使い分け
4. **Python へ移植**（Node 実行時依存なし）。分割生成アルゴリズム(RCB)も Python 実装し、golden 不変条件テストでドリフト防止

## 実装状況（Phase 1 完了）

- `src/planetarble/acquisition/miniplanets.py` — `SUBDIVISIONS`（凍結表）/ `miniplanet_ids` / `subdivision_z6_bbox` / `miniplanet_geo_bbox` / `tile_to_miniplanet_id` / `compute_subdivisions`（RCB, 純関数）。GDAL・ネットワーク非依存
- `tools/gen_miniplanets.py` — land ZL10 タイル数の重みグリッドを作り `compute_subdivisions` で表を再生成するオフラインツール。`PYTHONPATH=src python3 tools/gen_miniplanets.py` で再生成可能（より精緻な land mask に差し替えても再均衡化できる）
- `core/models.py` — `HLSPlanRegion.miniplanet: Optional[str]` 追加
- `config/loader.py` — `plan_regions[].miniplanet`（hls/sentinel2 両方）。`0` / `"0"` を `"00"` に正規化
- `acquisition/hls.py` — `load_region_geometry` が `region.miniplanet` を最優先で `miniplanet_geo_bbox` → 矩形ジオメトリに変換
- `configs/base/pipeline.yaml` — `mp_00`〜`mp_17`（land_only）を追記
- テスト: `tests/unit/acquisition/test_miniplanets.py`（分割不変条件・tile→id 整合性・geo_bbox・RCB 均衡/決定性/過分割拒否、GDAL guard 付き wiring）、`tests/unit/config/test_loader_hls_plan_regions.py` に miniplanet ロードを追加。全 16 ケース通過（GDAL 依存 1 件は GDAL 未導入環境で skip）

### 使い方

```bash
# 全 miniplanet のプランを生成（region 無指定で plan_regions 全件）
planetarble acquire --config configs/base/pipeline.yaml

# 単一 miniplanet をインクリメンタル取得
planetarble acquire --config configs/base/pipeline.yaml --plan-region mp_00
planetarble process --config configs/base/pipeline.yaml --plan-region mp_00
```

## 実装状況（Phase 2 進行中）

完了:

- `HLSMosaicTask.miniplanet` フィールド追加。`HLSMosaicPlanner.iter_tasks` が `tile_to_miniplanet_id(z,x,y)` で各タイルに ID を付与し、`to_mapping`/`from_mapping` で round-trip（z<BASE_ZOOM の場合は省略）
- `split_plan_by_miniplanet(plan_path, out_dir)` — 単一グローバルプランを miniplanet 単位の ndjson に分割（osm-miniplanets-util の「ID 単位 extract」に対応）。`task_miniplanet_id` は未タグのエントリをタイルから再解決し、解決不能は `unassigned` に集約
- CLI: `planetarble split-plan --config ... [--plan ...] [--out ...]`
- テスト: `tests/unit/acquisition/test_hls_plan_miniplanet.py`（タグ付け・round-trip・分割・unassigned）、`tests/unit/cli/test_split_plan_command.py`

これにより「グローバルプランを1回生成 → miniplanet 単位に分割 → shard ごとに process/tile/package → `tiling merge-mbtiles` で統合」というワークフローが組める。

```bash
planetarble acquire --config configs/base/pipeline.yaml            # 全球プラン（各エントリに miniplanet タグ）
planetarble split-plan --config configs/base/pipeline.yaml         # data/plans/shards/ に分割
# shard ごとに process/tile（--plan-region mp_NN は既存の名前ベース機構で対応）
```

残課題:

- 全 shard を順次 build → 1 つの `planet.pmtiles` に集約するオーケストレーション（個別の `tiling merge-mbtiles` は既存）
- `gen_miniplanets.py` を精緻な `ne_10m_land`（z10）land mask 対応にして再均衡化（現状は planetarble と同じ `LAND_APPROX_BBOXES` ヒューリスティックで重み付け）
- 3.9 環境では既存 `tests/unit/acquisition/test_copernicus_rate_limit.py` が `str | None`(PEP 604) で collection error（本変更とは無関係、プロジェクトは 3.10+ 前提）
