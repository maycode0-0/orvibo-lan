# 发布指南

发布版本只有一个来源：`custom_components/orvibo_lan/manifest.json`。不要从工作流输入、文件名或 Git 标签反向生成组件版本。

## 发布前

1. 更新 `manifest.json` 中的版本。
2. 在 `CHANGELOG.md` 中将待发布内容归入同一版本。
3. 运行 [CONTRIBUTING.md](CONTRIBUTING.md) 中的全部检查。
4. 合并到 `main` 并等待 Validate 工作流通过。

## 创建发布

更新 `manifest.json` 中的版本并合并到 `main` 后，发布工作流会自动创建与 manifest 完全一致的 `vX.Y.Z` 标签，并创建或修复对应的 GitHub Release。工作流构建固定名称 `orvibo_lan.zip` 并上传为唯一发布资产。

## 手动补发

`workflow_dispatch` 只允许从 `main` 运行，用于修复已经存在的同版本标签或 Release。若 Release 缺失则创建；若固定资产已存在，则替换同名资产，避免产生多个漂移文件名。若同版本标签已经指向其他提交，自动发布会失败并要求先提升 manifest 版本，不会覆盖已有历史。

## 发布后检查

- Release 标签与 manifest 版本一致。
- Release 只有一个有效 `orvibo_lan.zip`。
- ZIP 根目录包含 `manifest.json`，没有额外目录层。
- HACS 能识别并下载该 Release。
- `CHANGELOG.md` 包含对应版本和用户可见变化。
