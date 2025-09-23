# Requirements Document

## Introduction

Planetarble is a complete open-source solution for generating global planetary imagery tiles packaged as a single PMTiles file. The name combines "planet" and "Blue Marble" (the famous Earth satellite imagery), representing the project's goal to make planetary-scale satellite imagery accessible to everyone. The system will use only open data sources (NASA Blue Marble Next Generation, GEBCO bathymetry, Natural Earth) to create a self-contained world map that can be distributed and viewed in air-gapped environments. The primary goal is to demonstrate that anyone with computational resources can create Earth-scale satellite imagery raster tiles using completely open data and open-source tools.

## Requirements

### Requirement 1: Global Imagery Data Processing

**User Story:** As a developer, I want to process global satellite imagery from open data sources, so that I can create a comprehensive world map without relying on proprietary data.

#### Acceptance Criteria

1. WHEN processing Blue Marble Next Generation (BMNG) 2004 data THEN the system SHALL support 500m/pixel resolution (86400×43200 pixels) as primary source
2. WHEN BMNG 500m data is unavailable THEN the system SHALL fallback to 2km/pixel version (21600×21600 pixels)
3. WHEN processing GEBCO bathymetry data THEN the system SHALL use the latest available GEBCO Global Grid (15 arc-second resolution)
4. WHEN processing coastline data THEN the system SHALL use Natural Earth land/ocean masks for boundary definition
5. WHEN downloading source data THEN the system SHALL verify file integrity using SHA256 checksums
6. WHEN creating attribution THEN the system SHALL include proper NASA, GEBCO, and Natural Earth credits as required by their licenses

### Requirement 2: Tile Generation and Optimization

**User Story:** As a user, I want the imagery to be packaged as web-compatible tiles, so that I can use standard web mapping libraries to display the data.

#### Acceptance Criteria

1. WHEN generating tiles THEN the system SHALL use EPSG:3857 Web Mercator projection with ±85.0511° latitude clipping
2. WHEN creating tile pyramid THEN the system SHALL generate XYZ scheme tiles with 256px dimensions
3. WHEN setting zoom levels THEN the system SHALL support minzoom=0 and maxzoom=10 as baseline requirement
4. IF storage and processing allow THEN the system SHALL optionally support maxzoom=12
5. WHEN optimizing file size THEN the system SHALL use JPEG format with quality 75-85 as default
6. WHEN further compression is needed THEN the system SHALL support WebP format for 25-34% size reduction
7. WHEN processing ocean areas THEN the system SHALL apply GEBCO hillshade at 10-20% opacity to improve visual distinction
8. WHEN generating tiles THEN the system SHALL skip empty ocean tiles where appropriate to reduce file size

### Requirement 3: PMTiles Packaging and Distribution

**User Story:** As an end user, I want to receive a single file containing all global imagery data, so that I can easily distribute and deploy the map in offline environments.

#### Acceptance Criteria

1. WHEN packaging tiles THEN the system SHALL convert MBTiles to PMTiles format using PMTiles CLI
2. WHEN creating the final package THEN the system SHALL embed proper metadata including name, bounds, center, minzoom, maxzoom, and attribution
3. WHEN distributing THEN the system SHALL provide world_YYYY.pmtiles as the primary deliverable
4. WHEN providing metadata THEN the system SHALL include world_YYYY.tilejson.json with TileJSON-compatible metadata
5. WHEN documenting sources THEN the system SHALL provide LICENSE_AND_CREDITS.txt with complete attribution
6. WHEN tracking provenance THEN the system SHALL provide MANIFEST.json with source URLs, checksums, and generation parameters
7. WHEN estimating file size THEN the system SHALL target 14-56 GB for z≤10 depending on format and quality settings

### Requirement 4: Air-Gap Deployment and Viewing

**User Story:** As a user in an air-gapped environment, I want to view and serve the global imagery without internet connectivity, so that I can use the map in isolated or offline scenarios.

#### Acceptance Criteria

1. WHEN viewing in browser THEN the system SHALL support MapLibre GL with pmtiles.js for direct PMTiles reading
2. WHEN serving tiles THEN the system SHALL support pmtiles serve command to provide HTTP /{z}/{x}/{y}.jpg endpoints
3. WHEN deploying offline THEN the system SHALL require only the PMTiles file and basic web server capabilities
4. WHEN providing viewer THEN the system SHALL optionally include a simple HTML viewer for local verification
5. WHEN running pmtiles serve THEN the system SHALL serve tiles on configurable host and port (default 8080)

### Requirement 5: Reproducible Build Process

**User Story:** As a developer, I want a documented and automated build process, so that I can reproduce the global imagery generation and update it annually.

#### Acceptance Criteria

1. WHEN setting up the build environment THEN the system SHALL require only GDAL ≥3.x and PMTiles CLI
2. WHEN downloading source data THEN the system SHALL provide automated scripts with URL catalogs and integrity verification
3. WHEN processing data THEN the system SHALL use only open-source tools (GDAL, PMTiles CLI) without cloud dependencies
4. WHEN generating tiles THEN the system SHALL provide configurable parameters for quality, format, and zoom levels
5. WHEN updating annually THEN the system SHALL support GEBCO updates while maintaining BMNG 2004 baseline
6. WHEN improving quality THEN the system SHALL support future MODIS MCD43A4 integration for annual updates
7. WHEN validating output THEN the system SHALL run pmtiles verify and sample tile verification
8. WHEN documenting process THEN the system SHALL maintain build logs and parameter records

### Requirement 6: Quality and Performance Standards

**User Story:** As an end user, I want high-quality imagery with good performance characteristics, so that the map is visually appealing and responsive.

#### Acceptance Criteria

1. WHEN viewing at z=0-8 THEN the system SHALL provide smooth visual transitions without artifacts
2. WHEN viewing at z=9-10 THEN the system SHALL minimize edge roughness and banding effects
3. WHEN displaying ocean areas THEN the system SHALL show subtle bathymetric detail without overwhelming the base imagery
4. WHEN verifying metadata THEN the system SHALL ensure correct bounds (-180,-85.0511,180,85.0511) and center coordinates
5. WHEN checking attribution THEN the system SHALL display proper NASA, GEBCO, and Natural Earth credits
6. WHEN validating integrity THEN the system SHALL pass pmtiles verify checks
7. WHEN sampling quality THEN the system SHALL pass visual inspection of 100 random tiles
8. WHEN optimizing performance THEN the system SHALL leverage PMTiles deduplication for repeated tiles

### Requirement 7: Legal Compliance and Open Source

**User Story:** As a project maintainer, I want to ensure full legal compliance with open data licenses, so that the project can be freely distributed and used.

#### Acceptance Criteria

1. WHEN using NASA data THEN the system SHALL include required NASA credit without using NASA logos
2. WHEN using GEBCO data THEN the system SHALL include proper GEBCO attribution as required
3. WHEN using Natural Earth data THEN the system SHALL acknowledge public domain status
4. WHEN distributing THEN the system SHALL avoid any third-party copyrighted materials mixed in NASA sources
5. WHEN creating documentation THEN the system SHALL clearly state redistribution rights and requirements
6. WHEN packaging THEN the system SHALL include complete license information in LICENSE_AND_CREDITS.txt
7. WHEN making the project available THEN the system SHALL use open source license for all custom code and scripts