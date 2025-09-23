# Repository Structure

Repository root
- README.md — project overview, installation, and quickstart pipeline instructions
- LICENSE — open source license for the reproducibility toolkit
- pyproject.toml / poetry.lock — Python packaging metadata for the CLI and modules
- Makefile — shortcuts for linting, testing, and running pipeline stages
- .gitignore — excludes `data/`, `tmp/`, build artifacts, and virtual environments
- configs/ — version-controlled configuration templates and schema definitions
- docs/ — written documentation (architecture, operations, tutorials)
- examples/ — sample configuration files and minimal scripts for demonstrations
- scripts/ — thin wrappers for orchestration tasks (e.g., bootstrap, verification)
- src/ — Python package implementation for the Planetarble pipeline
- tests/ — automated unit, integration, and performance tests with synthetic fixtures
- tools/ — helper utilities (e.g., schema validators, profiling helpers)
- viewer/ — static MapLibre PMTiles viewer assets for offline validation
- data/ — runtime workspace for downloaded datasets (git-ignored)
- tmp/ — transient processing workspace, caches, and intermediate artifacts (git-ignored)

configs/
- base/ — default YAML/JSON pipeline configurations
- profiles/ — tuned parameter sets for different resolutions or quality targets
- schema/ — JSON Schema definitions for validating configuration files

docs/
- architecture/ — system diagrams, module overviews, data flow descriptions
- operations/ — deployment guides, air-gap procedures, troubleshooting
- reference/ — API documentation generated from the source code
- tutorials/ — step-by-step walkthroughs for running the pipeline end-to-end

examples/
- pipelines/ — example end-to-end scripts chaining modules together
- notebooks/ — optional Jupyter notebooks using synthetic data for experimentation

scripts/
- bootstrap_env.py — environment checks for GDAL, PMTiles CLI, and dependencies
- run_pipeline.py — convenience entry point executing the full workflow
- verify_output.py — automated checksumming and pmtiles verification helpers

src/planetarble/
- __init__.py — package exports
- config/ — configuration loading, validation, and defaults
- logging/ — structured logging setup shared across modules
- acquisition/ — asset catalog, manifests, and data downloaders
- processing/ — raster normalization, hillshade, mask generation, COG utilities
- tiling/ — reprojection, tile pyramid generation, MBTiles creation, optimization
- packaging/ — PMTiles conversion, metadata management, distribution packaging
- deployment/ — CLI wrappers for `pmtiles serve` and viewer preparation
- cli/ — argparse/Typer-based command-line interface definitions
- orchestrator/ — pipeline coordination, checkpointing, and resume logic
- qa/ — quality assurance checks, benchmarks, and license compliance validators
- utils/ — shared helpers (e.g., path management, checksum utilities)

tests/
- unit/ — fine-grained tests with mocked data sources
- integration/ — end-to-end flows using synthetic fixtures stored under `tests/fixtures`
- performance/ — benchmarks for critical processing segments
- fixtures/ — synthetic raster/tile assets small enough for version control

viewer/
- index.html — standalone MapLibre + pmtiles.js viewer
- assets/ — CSS, JavaScript bundles, and attribution templates
- examples/ — sample HTML configurations referencing generated PMTiles

tools/
- schemas/ — validation tools for manifests and metadata
- profiling/ — scripts for memory/time profiling of processing steps
- dev/ — developer productivity aids (pre-commit hooks, lint configurations)

Supporting files
- .pre-commit-config.yaml — linting/formatting automation
- noxfile.py or tox.ini — test automation entry points
- CONTRIBUTING.md — guidelines for collaborators
- CHANGELOG.md — tracked release notes for reproducibility updates
