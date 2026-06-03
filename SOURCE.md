# Planetarble 対応ソース一覧

planetarble が取り込めるデータソースごとの名前・最大解像度・対応する Web メルカトル最大ズームレベルのまとめ。

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

| ソース | 最大解像度 | 計算上の最大ズーム | planetarble での運用 |
|---|---|---|---|
| GSI 航空写真（`seamlessphoto` / `ort`、地理院 XYZ タイル） | 原データ 0.2〜0.4 m 級。タイル提供は概ね z18（約 0.5 m/px @ 緯度35°、一部地域 z19） | **z18**（提供上限） | `gsi-fetch` / `tile_source: gsi_orthophotos`。実績 `planet_2024_18z_taito_gsi.pmtiles` |
| Sentinel-2 L2A（MPC STAC、`visual`=TCI 10m） | 10 m | **z14**（9.55 m/px） | `tile_source: sentinel2`。実績 `planet_2024_14z_tokyo_sentinel2.pmtiles` |
| Copernicus Data Space WMS（TRUE_COLOR、Sentinel-2 由来） | 10 m | **z14** | レガシー `copernicus` ブロック。設定実績 z12〜14 |
| HLS v2（`hls2-s30` / `hls2-l30`、NASA/USGS Harmonized Landsat Sentinel-2） | 30 m（公称） | z12（38.2 m/px ≈ 30 m） | **運用は z11**。合成由来の実効解像度を考慮し z12 はクライアント側オーバーサンプリングで提供（README 準拠）。実績 `planet_hls_*_11z/12z.mbtiles` |
| Landsat Collection 2 Level-2 SR（HLS のフォールバック） | 30 m | z12 | HLS と同じ扱い（`hls.fallback_collections`） |
| BMNG 500m（NASA Blue Marble Next Generation, Aug 2004、8 パネル） | 500 m | **z8**（611 m/px） | レガシー基盤・全球ベース。実績 `planet_2024_8z.pmtiles` |
| BMNG 2km（同・単一フレーム） | 約 2 km | **z6**（2,446 m/px） | スモークテスト・最軽量全球 |
| MODIS MCD43A4.061（NBAR 反射率、AppEEARS 経由） | 500 m | z8 | オプションのレガシーブレンド（`modis` ブロック） |
| VIIRS VNP09GA.002（地表反射率、AppEEARS 経由） | 500 m（I バンド）/ 1 km（M バンド） | z7〜8 | オプションのレガシーブレンド（`viirs` ブロック） |

## 標高・水深ソース（海洋陰影・ハイトシェード用）

| ソース | 最大解像度 | 計算上の最大ズーム | planetarble での運用 |
|---|---|---|---|
| NOAA ETOPO 2022（15 秒角 bedrock GeoTIFF、CC0） | 15 秒角 ≈ 464 m | z8〜9 相当 | HLS ワークフローの海洋レンダリング（`ocean` ブロック、カラーランプ + 陰影） |
| GEBCO 2024 Grid（NetCDF） | 15 秒角 ≈ 464 m | z8〜9 相当 | レガシー BMNG ワークフローの海洋陰影（`gdaldem hillshade`） |

## 補助ソース（画像ではないもの）

| ソース | 内容 | 用途 |
|---|---|---|
| Natural Earth 10m（land / ocean / coastline / admin_0 / admin_1） | 1:1,000万 ベクター | 陸海マスク生成、plan_region の行政界フィルタ、land_only クリップ。ズーム概念は適用外（カートグラフィ目安 z5 前後） |

## まとめ: ズーム別に使えるソース

| 目標最大ズーム | 使えるソース |
|---|---|
| z6 | BMNG 2km |
| z8 | BMNG 500m、MODIS、(VIIRS)、ETOPO/GEBCO 陰影 |
| z11〜12 | HLS v2、Landsat C2 L2（陸域のみ） |
| z14 | Sentinel-2 L2A、Copernicus WMS（陸域中心） |
| z18 | GSI 航空写真（日本国内のみ） |

全球は低ズーム（BMNG/HLS + ETOPO 海洋）、高ズームは地域限定ソース（Sentinel-2、GSI）を `merge-mbtiles` で重畳する、という段階構成が前提（README の Regional HLS Planning / Roadmap 参照）。
