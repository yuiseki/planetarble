.PHONY: japan-kanto japan-kanto-acquire japan-kanto-process japan-kanto-tile

CONFIG ?= configs/base/pipeline.yaml
HLS_MIN_ZOOM ?= 11
HLS_MAX_ZOOM ?= 11

JAPAN_KANTO_REGIONS := tokyo_land kanagawa_land chiba_land saitama_land ibaraki_land tochigi_land gunma_land

japan-kanto: japan-kanto-acquire japan-kanto-process japan-kanto-tile

japan-kanto-acquire:
	@set -e; \
	for region in $(JAPAN_KANTO_REGIONS); do \
		echo "==> acquire $$region"; \
		planetarble acquire --config $(CONFIG) --plan-region $$region; \
	done

japan-kanto-process:
	@set -e; \
	for region in $(JAPAN_KANTO_REGIONS); do \
		echo "==> process $$region"; \
		planetarble process --config $(CONFIG) --plan-region $$region; \
	done

japan-kanto-tile:
	@set -e; \
	for region in $(JAPAN_KANTO_REGIONS); do \
		echo "==> tile $$region (z$(HLS_MIN_ZOOM)-$(HLS_MAX_ZOOM))"; \
		planetarble tile --config $(CONFIG) --plan-region $$region --min-zoom $(HLS_MIN_ZOOM) --max-zoom $(HLS_MAX_ZOOM); \
	done
