# Product Overview

Planetarble is a comprehensive open-source solution for generating global planetary imagery tiles from open data sources. The name combines "planet" and "Blue Marble" (NASA's famous Earth satellite imagery), representing the project's mission to make planetary-scale satellite imagery accessible to everyone through completely open data and tools.

## Key Characteristics
- **Complete Open Source Stack**: Uses only NASA Blue Marble Next Generation (2004), GEBCO bathymetry, and Natural Earth data
- **Single-File Distribution**: Packages global imagery into a single PMTiles file (14-56GB) for easy deployment
- **Air-Gap Capable**: Designed for offline environments with no internet dependencies after initial build
- **Web-Standard Compatible**: Generates Web Mercator tiles (EPSG:3857) compatible with MapLibre GL and standard web mapping libraries
- **Reproducible Pipeline**: Fully automated build process using GDAL and PMTiles CLI
- **Global Coverage**: Provides complete Earth imagery from zoom levels 0-10 (optionally 0-12)

## Target Users

### Primary Target
- **Interactive Web Map Visualization Developers**: Building web applications with MapLibre GL, Leaflet, or similar libraries who need self-hosted satellite imagery tiles

### Technical Skill Requirements

#### For Consumers (Map Display)
- Basic knowledge of MapLibre GL JS or similar web mapping libraries
- Understanding of PMTiles integration with web mapping frameworks
- No specialized geospatial expertise required

#### For Producers (Tile Generation)
- Advanced knowledge of geospatial technologies: GeoTIFF, COG, GDAL, PMTiles
- Experience with cloud storage solutions: S3, GCS, Cloudflare
- Understanding of raster data processing and tile generation workflows

### Secondary Users
- **Organizations requiring data sovereignty**: Companies and agencies needing complete control over their mapping infrastructure
- **Cost-conscious developers**: Teams seeking to eliminate per-request API fees and traffic-based billing
- **Air-gapped environments**: Secure facilities, remote locations, or offline-first applications (extreme use case)

## Core Value Proposition

### For Developers
- **Zero Vendor Lock-in**: Complete independence from proprietary mapping services
- **Predictable Costs**: One-time processing cost instead of per-request API fees
- **Full Control**: Customize quality, format, and zoom levels for specific needs
- **Legal Clarity**: Clear open data licensing without usage restrictions

### For Organizations
- **Security Compliance**: Deploy in air-gapped environments without external dependencies
- **Data Sovereignty**: Host and control your own global mapping data
- **Cost Efficiency**: Eliminate ongoing API costs for high-volume applications
- **Reliability**: No network dependencies or service outages for core functionality

### For the Community
- **Democratized Access**: Proves that anyone can create Earth-scale imagery with open tools
- **Educational Resource**: Demonstrates modern geospatial data processing techniques
- **Foundation for Innovation**: Provides base layer for specialized mapping applications
- **Open Science**: Promotes reproducible research with documented methodologies

## Technical Innovation
- **Hybrid Data Fusion**: Combines satellite imagery with bathymetric hillshading for enhanced ocean visualization
- **Efficient Packaging**: Uses PMTiles format for optimized storage and serving
- **Quality Optimization**: Balances visual quality with file size through configurable compression
- **Modular Architecture**: Separates data acquisition, processing, tiling, and deployment phases

## Use Cases
- Offline mapping applications for remote locations
- Educational tools for geography and Earth sciences
- Research platforms requiring reproducible basemaps
- Government and military applications in secure environments
- Custom cartographic projects with global scope
- Backup mapping solutions for critical infrastructure