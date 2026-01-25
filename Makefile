.PHONY: japan-kanto japan-kanto-acquire japan-kanto-process japan-kanto-tile japan-chubu japan-chubu-acquire japan-chubu-process japan-chubu-tile japan-kinki japan-kinki-acquire japan-kinki-process japan-kinki-tile

CONFIG ?= configs/base/pipeline.yaml
HLS_MIN_ZOOM ?= 11
HLS_MAX_ZOOM ?= 11

JAPAN_KANTO_REGIONS := tokyo_land kanagawa_land chiba_land saitama_land ibaraki_land tochigi_land gunma_land
JAPAN_CHUBU_REGIONS := niigata_land toyama_land ishikawa_land fukui_land yamanashi_land nagano_land gifu_land shizuoka_land aichi_land
JAPAN_KINKI_REGIONS := mie_land shiga_land kyoto_land osaka_land hyogo_land nara_land wakayama_land

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

japan-chubu: japan-chubu-acquire japan-chubu-process japan-chubu-tile

japan-chubu-acquire:
	@set -e; \
	for region in $(JAPAN_CHUBU_REGIONS); do \
		echo "==> acquire $$region"; \
		planetarble acquire --config $(CONFIG) --plan-region $$region; \
	done

japan-chubu-process:
	@set -e; \
	for region in $(JAPAN_CHUBU_REGIONS); do \
		echo "==> process $$region"; \
		planetarble process --config $(CONFIG) --plan-region $$region; \
	done

japan-chubu-tile:
	@set -e; \
	for region in $(JAPAN_CHUBU_REGIONS); do \
		echo "==> tile $$region (z$(HLS_MIN_ZOOM)-$(HLS_MAX_ZOOM))"; \
		planetarble tile --config $(CONFIG) --plan-region $$region --min-zoom $(HLS_MIN_ZOOM) --max-zoom $(HLS_MAX_ZOOM); \
	done

japan-kinki: japan-kinki-acquire japan-kinki-process japan-kinki-tile

japan-kinki-acquire:
	@set -e; \
	for region in $(JAPAN_KINKI_REGIONS); do \
		echo "==> acquire $$region"; \
		planetarble acquire --config $(CONFIG) --plan-region $$region; \
	done

japan-kinki-process:
	@set -e; \
	for region in $(JAPAN_KINKI_REGIONS); do \
		echo "==> process $$region"; \
		planetarble process --config $(CONFIG) --plan-region $$region; \
	done

japan-kinki-tile:
	@set -e; \
	for region in $(JAPAN_KINKI_REGIONS); do \
		echo "==> tile $$region (z$(HLS_MIN_ZOOM)-$(HLS_MAX_ZOOM))"; \
		planetarble tile --config $(CONFIG) --plan-region $$region --min-zoom $(HLS_MIN_ZOOM) --max-zoom $(HLS_MAX_ZOOM); \
	done
