# 发布指南

发布版本只有一个来源：`custom_components/orvibo_lan/manifest.json`。不要从工作流输入、文件名或 Git 标签反向生成组件版本。

## 发布前

1. 更新 `manifest.json` 中的版本。
2. 在 `CHANGELOG.md` 中将待发布内容归入同一版本。
3. 运行 [CONTRIBUTING.md](CONTRIBUTING.md) 中的全部检查。
4. 合并到 `main` 并等待 Validate 工作流通过。

## 创建发布

在 `main` 的发布提交创建与 manifest 完全一致的标签：

```bash
git tag v0.0.1
git push origin v0.0.1
```

发布工作流校验标签、manifest 版本和提交目标，构建固定名称 `orvibo_lan.zip`，然后创建或修复对应的 GitHub Release。工作流不会自动创建版本标签。

## 手动补发

`workflow_dispatch` 只允许从 `main` 运行，用于修复已经存在的同版本标签或 Release。若 Release 缺失则创建；若固定资产已存在，则替换同名资产，避免产生多个漂移文件名。

## 发布后检查

- Release 标签与 manifest 版本一致。
- Release 只有一个有效 `orvibo_lan.zip`。
- ZIP 根目录包含 `manifest.json`，没有额外目录层。
- HACS 能识别并下载该 Release。
- `CHANGELOG.md` 包含对应版本和用户可见变化。
