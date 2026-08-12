# CoC base segmentation model

- Source: https://huggingface.co/nihatxp/clash-of-clans-base-segmentation
- File: `coc_deployable_seg.pt`
- SHA-256: `3b1d5343650a3d442d2b96d1a75abce0a5a6878b584fc96c6e24be57607ca74f`
- Model license: Apache-2.0

The bot uses only the model's `BaseArea` mask as a guard. HSV remains the
source of the red deployment boundary; a candidate red polygon is rejected
when it clips a significant part of the model-detected base.
