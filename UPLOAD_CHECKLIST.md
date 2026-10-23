# 上传仓库前检查

- [ ] 仓库设为私有，确认代码和冻结规则资产的授权范围。
- [ ] 不要添加任何 `.env`、PDF、Excel、SQLite、输出目录或模型raw response。
- [ ] 执行敏感信息扫描，确认没有API Key、密码和Authorization Header。
- [ ] 使用 `05_pipeline_local/.env.example` 创建本机 `.env`，不要提交副本。
- [ ] 首次运行先执行 `--check-only`。
- [ ] 如需公开仓库，先单独确认冻结Rule Store与Prompt是否允许公开。

建议初始化命令：

```powershell
git init -b main
git add .
git status
git commit -m "Initial V0 tender review pipeline"
```
