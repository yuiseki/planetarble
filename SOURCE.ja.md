# Planetarble 対応ソース一覧

planetarble が取り込めるデータソースごとの ID・正式名称・最大解像度・対応する Web メルカトル最大ズームレベルのまとめ。

## ズームレベル換算の前提

256px タイルにおける赤道での地上解像度は `156543.03 / 2^z` m/px。ソースのネイティブ解像度 R に対し、計算上の最大ズームは `z = log2(156543 / R)` を丸めたもの。緯度 φ では同じズームでも実解像度が cos φ 倍細かくなる（日本付近 φ≈35° で約 0.82 倍）ため、実用上の判断は ±1 ズームの幅を持つ。

| z | 赤道での解像度 (m/px) |
|---|---|
| 6 | 2,446 |
| 8 | 611 |
| 11 | 76.4 |
| 12 | 38.2 |
| 14 | 9.55 |
| 18 | 0.597 |

## 衛星・航空画像ソース（タイルの絵柄になるもの）

| ID | 正式名称 | 最大解像度 | 最大ズーム |
|---|---|---|---|
| `gsi_orthophotos`（product: `seamlessphoto` / `ort`） | 地理院タイル 全国最新写真（シームレス）/ 電子国土基本図（オルソ画像） | 原データ 0.2〜0.4 m 級。タイル提供は概ね z18（約 0.5 m/px @ 緯度35°、一部地域 z19） | **z18**（提供上限） |
| `sentinel2`（STAC collection: `sentinel-2-l2a`） | Copernicus Sentinel-2 Level-2A（Microsoft Planetary Computer 経由、`visual`=TCI 10m） | 10 m | **z14**（9.55 m/px） |
| `copernicus`（asset: `copernicus_sentinel2_true_color` ほか、layer: `TRUE_COLOR`） | Copernicus Data Space Ecosystem Sentinel Hub WMS（Sentinel-2 由来） | 10 m | **z14** |
| `hls`（STAC collections: `hls2-s30` / `hls2-l30`） | NASA/USGS Harmonized Landsat and Sentinel-2 (HLS) v2.0 S30/L30 | 30 m（公称） | z12（計算値）/ **運用 z11**（合成由来の実効解像度を考慮、z12 はクライアント側オーバーサンプリング） |
| STAC collection: `landsat-c2-l2`（`hls.fallback_collections`） | USGS Landsat Collection 2 Level-2 Surface Reflectance | 30 m | z12 |
| `bmng`（assets: `bmng_2004_aug_500m_a1`〜`d2`） | NASA Blue Marble Next Generation（2004年8月、topo+bathy 合成、500m 8パネル） | 500 m | **z8**（611 m/px） |
| `bmng`（asset: `bmng_2004_aug_2km_global`） | NASA Blue Marble Next Generation（2004年8月、topo+bathy 合成、2km 単一フレーム） | 約 2 km | **z6**（2,446 m/px） |
| `modis`（product: `MCD43A4.061`、assets: `modis_mcd43a4_*`） | MODIS Nadir BRDF-Adjusted Reflectance (NBAR) MCD43A4 v6.1（AppEEARS 経由） | 500 m | z8 |
| `viirs`（product: `VNP09GA.002`、assets: `viirs_vnp09ga_*`） | VIIRS/NPP Surface Reflectance Daily L2G VNP09GA v2（AppEEARS 経由） | 500 m（I バンド）/ 1 km（M バンド） | z7〜8 |

## 標高・水深ソース（海洋陰影・ハイトシェード用）

| ID | 正式名称 | 最大解像度 | 最大ズーム |
|---|---|---|---|
| `etopo_2022_15s_bedrock_cog`（config 参照名: `etopo_2022_15arcsec_geotiff`） | NOAA ETOPO 2022 Global Relief Model（15 秒角 bedrock、CC0） | 15 秒角 ≈ 464 m | z8〜9 相当 |
| `gebco_latest_grid` | GEBCO 2024 Grid（GEBCO Compilation Group、NetCDF） | 15 秒角 ≈ 464 m | z8〜9 相当 |

## 補助ソース（画像ではないもの）

| ID | 正式名称 | 内容 |
|---|---|---|
| `natural_earth_land_10m` / `natural_earth_ocean_10m` / `natural_earth_coastline_10m` / `natural_earth_admin_0_10m` / `natural_earth_admin_1_10m` | Natural Earth 1:10m Physical / Cultural Vectors | 陸海マスク生成、plan_region の行政界フィルタ、land_only クリップに使用するベクター。ズーム概念は適用外（カートグラフィ目安 z5 前後） |

## まとめ: ズーム別に使えるソース

| 目標最大ズーム | 使えるソース |
|---|---|
| z6 | BMNG 2km |
| z8 | BMNG 500m、MODIS、(VIIRS)、ETOPO/GEBCO 陰影 |
| z11〜12 | HLS v2、Landsat C2 L2（陸域のみ） |
| z14 | Sentinel-2 L2A、Copernicus WMS（陸域中心） |
| z18 | GSI 航空写真（日本国内のみ） |

全球は低ズーム（BMNG/HLS + ETOPO 海洋）、高ズームは地域限定ソース（Sentinel-2、GSI）を `merge-mbtiles` で重畳する、という段階構成が前提（README の Regional HLS Planning / Roadmap 参照）。
