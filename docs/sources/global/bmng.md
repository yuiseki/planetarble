---
id: bmng
title: NASA Blue Marble Next Generation (August 2004)
status: supported (base)
coverage: global
resolution: 500 m (8 panels) or ~2 km (single frame)
max_zoom: 8
license: "NASA Earth Observatory, credit NASA"
attribution: "Image courtesy NASA Earth Observatory"
access: HTTPS download of the panels listed in configs/base/assets.yaml
assets:
  - bmng_2004_aug_500m_a1 to bmng_2004_aug_500m_d2
  - bmng_2004_aug_2km_global
---
# NASA Blue Marble Next Generation

Topography and bathymetry true-colour composite for August 2004. The 500 m panels support z8 (611 m/px); the 2 km single frame supports z6, which `BMNGAdapter` reports when `resolution: 2km`. Used as the global floor of a planet.
