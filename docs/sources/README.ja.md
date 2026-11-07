# データソース

planetarble が取り込めるデータソースは、1 データセットにつき 1 ページを `docs/sources/{地域}/{データセット}.md` に置いています。各ページの先頭の欄 (front matter) が、パイプラインでの ID、範囲、解像度、Web メルカトルの最大ズーム、ライセンス、出典表記の基準です。`src/planetarble/overlay/sources.py` の `SOURCE_REGISTRY` はこれと一致していなければならず、最大ズームはテストで照合しています。各ページは英語で書いています。English: [README.md](README.md)

## ズームレベルの換算

256 px タイルの赤道上の地上解像度は `156543.03 / 2^z` m/px です。解像度 R のソースの最大ズームは `z = log2(156543 / R)` を丸めた値になります。緯度 φ では同じズームが cos φ 倍細かくなる (日本付近の φ≈35° で約 0.82 倍) ので、実際の選択には ±1 ズームの幅があります。

| z | 赤道上の解像度 (m/px) |
|---|---|
| 6 | 2,446 |
| 8 | 611 |
| 11 | 76.4 |
| 12 | 38.2 |
| 14 | 9.55 |
| 18 | 0.597 |

## 画像

| 地域 | データセット | ID | 解像度 | 最大ズーム | ライセンス |
|---|---|---|---|---|---|
| 全球 | [NASA Blue Marble Next Generation](global/bmng.md) | `bmng` | 500 m / 約 2 km | z8 / z6 | NASA、出典表記が必要 |
| 全球 | [HLS v2.0](global/hls.md) | `hls` | 30 m | z12 (運用 z11) | NASA のオープンデータ |
| 全球 | [Landsat C2 L2](global/landsat-c2-l2.md) | `landsat-c2-l2` | 30 m | z12 | パブリックドメイン |
| 全球 | [Sentinel-2 L2A](global/sentinel2.md) | `sentinel2` | 10 m | z14 | Copernicus のオープンデータ |
| 全球 | [Copernicus WMS](global/copernicus.md) | `copernicus` | 10 m | z14 | Copernicus のオープンデータ (アカウントが必要) |
| どこでも | [汎用の COG](global/cog.md) | `cog` | COG による | 重ね合わせごとに指定 | COG による (出典の記載が必須) |
| 全球 | [ESA WorldCover の S2 合成](global/esa-worldcover.md) | `esa-worldcover` | 10 m (2020 年、2021 年) | z14 | CC BY 4.0 |
| 全球 | [MODIS MCD43A4](global/modis.md) | `modis` | 500 m | z8 | NASA のオープンデータ |
| 全球 | [VIIRS VNP09GA](global/viirs.md) | `viirs` | 500 m / 1 km | z8 | NASA のオープンデータ |
| 世界各地 (点在) | [OpenAerialMap](global/openaerialmap.md) | `openaerialmap` | 画像ごと、1 m 未満が多い | z22 (画像ごと) | CC BY 4.0 |
| 日本 | [地理院の写真](japan/gsi-orthophotos.md) | `gsi_orthophotos` | 0.2〜0.4 m | z18 | 公共データ利用規約 1.0 (CC BY 互換) |
| 米国 | [NAIP](united-states/naip.md) | `naip` | 0.3〜1 m | z18 | パブリックドメイン |
| 米国 (Vermont) | [VCGI のオルソ画像](united-states/vermont-orthoimagery.md) | `cog` で | 0.15 / 0.3 m | z19 | パブリックドメイン (出典表記つき) |
| ニュージーランド | [LINZ の画像](new-zealand/nz-imagery.md) | `nz-imagery` | 0.05〜10 m | z21 | CC BY 4.0 (調査ごと) |
| カナダ (Banff) | [Banff 国立公園 2025](canada/banff-national-park.md) | `cog` で | 0.15 m | z20 | カナダ政府オープンライセンス |

## 標高、水深、ベクタ

| 地域 | データセット | 用途 | ライセンス |
|---|---|---|---|
| 全球 | [NOAA ETOPO 2022](global/etopo.md) | 海の色と陰影 (`ocean`) | CC0 1.0 |
| 全球 | [GEBCO 2024](global/gebco.md) | 旧 BMNG の陰影 | パブリックドメイン |
| 全球 | [Natural Earth 1:10m](global/natural-earth.md) | 陸海のマスク、地域の絞り込み | パブリックドメイン |

## まだ組み込んでいない候補

| データセット | 状況 |
|---|---|
| Kentucky の KyFromAbove のオルソ画像 (`s3://kyfromabove/imagery/orthos/`、パブリックドメイン、出典表記つき) | フェーズごとのタイル群で、計画するには図郭の索引を読む必要がある |
| Colorado の公開画像 (`s3://colorado-public-imagery`、CC0) | プロジェクトごとにフォルダの構成がまちまち |
| Digital Earth Africa / Pacific の GeoMAD (Sentinel-2 の年次合成、CC BY 4.0) | ESA WorldCover の地域版の代わり |

## 検討して採用しなかったもの

| データセット | 理由 |
|---|---|
| Maxar Open Data (災害時の画像) | CC BY-NC 4.0 で、商用を含む再配布ができない |
| AWS の NAIP (`s3://naip-*`) | Requester Pays。代わりに Planetary Computer の無償の複製 (`naip`) を読む |
| JAXA ALOS AVNIR-2 ORI (日本、10 m、2006〜2011 年) | ダウンロードに Earthdata のログインが要る。JAXA の利用条件では商用利用に事前連絡が要る |

## ズームごとに使えるソース

| 目標の最大ズーム | 使えるソース |
|---|---|
| z6 | BMNG 2 km |
| z8 | BMNG 500 m、MODIS、(VIIRS)、ETOPO/GEBCO の陰影 |
| z11〜z12 | HLS v2、Landsat C2 L2 (陸域のみ) |
| z14 | Sentinel-2 L2A、Copernicus WMS、ESA WorldCover の合成 (全球、ログイン不要) |
| z18 以上 | 地理院 (日本)、NAIP (米国)、LINZ (ニュージーランド)、OpenAerialMap (画像がある場所) |

低いズームで全球を覆い (BMNG/HLS と ETOPO の海)、高いズームで地域限定のソースを重ねる、段階的な構成を想定しています (ADR 0001 と ADR 0003)。
